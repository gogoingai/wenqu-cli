"""Managed image-generation runtime for Wenqu skills."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import time
from typing import Any

import httpx

from .config import WenquPaths

PROVIDERS = ("codex", "openai", "openrouter", "dashscope", "seedream")
DEFAULT_MODELS = {"codex": "codex-image-gen", "openai": "gpt-image-1", "openrouter": "openai/gpt-image-1", "dashscope": "qwen-image-2.0-pro", "seedream": "doubao-seedream-5-0-260128"}
DEFAULT_BASE_URLS = {"openai": "https://api.openai.com/v1", "openrouter": "https://openrouter.ai/api/v1", "dashscope": "https://dashscope.aliyuncs.com", "seedream": "https://ark.cn-beijing.volces.com/api/v3"}
SECRET_BY_PROVIDER = {"openai": "OPENAI_API_KEY", "openrouter": "OPENROUTER_API_KEY", "dashscope": "DASHSCOPE_API_KEY", "seedream": "ARK_API_KEY"}
FORBIDDEN_OUTPUT_PREFIXES = ("/bin", "/boot", "/dev", "/etc", "/lib", "/proc", "/sbin", "/sys", "/usr", "/System", "/Library", "/var/root", "/var/log", "/var/db")


@dataclass(frozen=True)
class ImagePaths:
    directory: Path
    config_file: Path
    credentials_file: Path
    cache_dir: Path


@dataclass
class Generation:
    provider: str
    model: str
    prompt: str
    output_path: Path
    aspect_ratio: str
    references: list[str]
    base_url: str | None
    timeout_seconds: int = 300


def image_paths(target: WenquPaths) -> ImagePaths:
    directory = target.config_dir / "image"
    return ImagePaths(directory, directory / "config.json", directory / "credentials.env", target.cache_dir / "image" / "styles")


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Image configuration must be a JSON object: {path}")
    return value


def _read_config(path: Path) -> dict[str, Any]:
    return _read_object(path) if path.exists() else {}


def _load_env(path: Path, environment: dict[str, str]) -> None:
    if not path.exists():
        return
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError(f"Image credentials must be readable only by their owner: chmod 600 {path}")
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise ValueError(f"Invalid environment entry in {path}: {raw}")
        environment.setdefault(key, value.strip().strip("\"'"))


def initialize_image_config(target: WenquPaths, *, dry_run: bool = False) -> dict[str, object]:
    paths = image_paths(target)
    actions: dict[str, str] = {}
    created: list[str] = []
    for name, path, content in (
        ("config", paths.config_file, json.dumps({"defaults": {"aspectRatio": "16:9"}, "providers": {}}, ensure_ascii=False, indent=2) + "\n"),
        ("credentials", paths.credentials_file, "# Optional image provider credentials. Keep this file private.\n# OPENAI_API_KEY=...\n# OPENROUTER_API_KEY=...\n# DASHSCOPE_API_KEY=...\n# ARK_API_KEY=...\n"),
    ):
        actions[name] = "exists" if path.exists() else "create"
        if actions[name] == "create" and not dry_run:
            paths.directory.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            if name == "credentials":
                path.chmod(0o600)
            created.append(str(path))
    return {"dryRun": dry_run, "configFile": str(paths.config_file), "credentialsFile": str(paths.credentials_file), "configAction": actions["config"], "credentialsAction": actions["credentials"], "created": created}


def _ratio(value: str) -> float:
    match = re.fullmatch(r"(\d+(?:\.\d+)?):(\d+(?:\.\d+)?)", value)
    if not match or float(match.group(1)) <= 0 or float(match.group(2)) <= 0:
        raise ValueError(f"Invalid aspect ratio: {value}")
    return float(match.group(1)) / float(match.group(2))


def resolve_generation(*, prompt: str, output_path: Path, environment: dict[str, str], global_config: dict[str, Any], article_config: dict[str, Any] | None = None, provider: str | None = None, model: str | None = None, aspect_ratio: str | None = None, references: list[str] | None = None, base_url: str | None = None, timeout_seconds: int = 300) -> Generation:
    article = article_config or {}
    if any(key in article for key in ("baseUrl", "providers", "credentials")):
        raise ValueError("Article image configuration may not contain endpoints or credentials.")
    defaults = global_config.get("defaults") if isinstance(global_config.get("defaults"), dict) else {}
    selected = provider or article.get("provider") or defaults.get("provider")
    if not selected:
        selected = next((candidate for candidate, secret in (("dashscope", "DASHSCOPE_API_KEY"), ("seedream", "ARK_API_KEY"), ("openai", "OPENAI_API_KEY"), ("openrouter", "OPENROUTER_API_KEY")) if environment.get(secret)), None)
    if selected not in PROVIDERS:
        raise ValueError(f"No image provider is configured. Supported providers: {', '.join(PROVIDERS)}.")
    settings = (global_config.get("providers") or {}).get(selected, {}) if selected != "codex" else {}
    if not isinstance(settings, dict): settings = {}
    default_model = defaults.get("model") if defaults.get("provider") == selected else None
    resolved_model = model or article.get("model") or settings.get("model") or default_model or DEFAULT_MODELS[selected]
    resolved_ratio = aspect_ratio or article.get("aspectRatio") or defaults.get("aspectRatio") or "1:1"
    _ratio(str(resolved_ratio))
    if timeout_seconds < 1:
        raise ValueError("Image timeout must be at least one second.")
    return Generation(selected, str(resolved_model), prompt, output_path, str(resolved_ratio), references or [], base_url or settings.get("baseUrl") or DEFAULT_BASE_URLS.get(selected), timeout_seconds)


def image_doctor(target: WenquPaths, environment: dict[str, str] | None = None, *, credentials_file: Path | None = None) -> dict[str, object]:
    env = dict(os.environ if environment is None else environment)
    paths = image_paths(target)
    _load_env(credentials_file or paths.credentials_file, env)
    return {"paths": {"config": str(paths.config_file), "credentials": str(paths.credentials_file), "cache": str(paths.cache_dir)}, "runtime": {"codex": bool(shutil.which("codex")), "picgo": bool(_find_picgo(env))}, "providers": {provider: {"configured": provider == "codex" and bool(shutil.which("codex")) or bool(env.get(SECRET_BY_PROVIDER.get(provider, ""))), "credential": None if provider == "codex" else (SECRET_BY_PROVIDER[provider] if env.get(SECRET_BY_PROVIDER[provider]) else None)} for provider in PROVIDERS}}


def _validate(generation: Generation) -> None:
    _ratio(generation.aspect_ratio)
    if generation.references and generation.provider == "openai" and "gpt-image" not in generation.model:
        raise ValueError("OpenAI reference images require a GPT Image model.")
    if generation.references and generation.provider == "dashscope" and not re.match(r"^(?:wan2\.7-image(?:-pro)?|qwen-image-2\.0(?:-pro)?)(?:-|$)", generation.model):
        raise ValueError(f"DashScope model {generation.model} does not support reference images; use a wan2.7 image or qwen-image-2.0 model.")
    if generation.references and generation.provider == "seedream" and not re.match(r"^doubao-seedream-(?:5(?:-0|\.0)|4-5|4-0)-", generation.model):
        raise ValueError(f"Seedream model {generation.model} does not support reference images.")


def _image_bytes(response: httpx.Response, client: httpx.Client) -> bytes:
    response.raise_for_status()
    data = response.json()
    first = data.get("data", [{}])[0] if isinstance(data.get("data"), list) else {}
    output = data.get("output", {}) if isinstance(data.get("output"), dict) else {}
    choices = output.get("choices", []) if isinstance(output.get("choices"), list) else []
    content = choices[0].get("message", {}).get("content", []) if choices and isinstance(choices[0], dict) else []
    choice_image = next((item.get("image") for item in content if isinstance(item, dict) and item.get("image")), None)
    image = first.get("b64_json") or first.get("url") or output.get("result_image") or choice_image
    if not image: raise ValueError("Image provider response did not contain image data.")
    return base64.b64decode(image) if not str(image).startswith("http") else client.get(image).raise_for_status().content


def _dashscope_size(aspect_ratio: str) -> str:
    ratio = _ratio(aspect_ratio)
    long_side = 1280
    if ratio >= 1:
        width, height = long_side, max(16, round(long_side / ratio / 16) * 16)
    else:
        width, height = max(16, round(long_side * ratio / 16) * 16), long_side
    return f"{width}*{height}"


def _remote_generate(g: Generation, env: dict[str, str]) -> bytes:
    secret = SECRET_BY_PROVIDER[g.provider]
    if not env.get(secret): raise ValueError(f"{secret} is required. Configure it in ~/.gogoingai/wenqu-skills/image/credentials.env.")
    _validate(g); base = str(g.base_url).rstrip("/"); ratio = _ratio(g.aspect_ratio)
    headers = {"Authorization": f"Bearer {env[secret]}"}
    with httpx.Client(timeout=g.timeout_seconds) as client:
        for attempt in range(3):
            if g.provider == "openai":
                size = "1024x1024" if abs(ratio - 1) < .1 else "1536x1024" if ratio > 1 else "1024x1536"
                if g.references:
                    files = [("image[]", (Path(ref).name, open(ref, "rb"), mimetypes.guess_type(ref)[0] or "image/png")) for ref in g.references]
                    try: response = client.post(f"{base}/images/edits", headers=headers, data={"model": g.model, "prompt": g.prompt, "size": size}, files=files)
                    finally:
                        for _, (_, handle, _) in files: handle.close()
                else: response = client.post(f"{base}/images/generations", headers={**headers, "Content-Type": "application/json"}, json={"model": g.model, "prompt": g.prompt, "size": size})
            elif g.provider == "dashscope":
                content = [{"image": _reference_value(ref)} for ref in g.references] + [{"text": g.prompt}]
                response = client.post(f"{base}/api/v1/services/aigc/multimodal-generation/generation", headers={**headers, "Content-Type": "application/json"}, json={"model": g.model, "input": {"messages": [{"role": "user", "content": content}]}, "parameters": {"size": _dashscope_size(g.aspect_ratio), "watermark": False, "n": 1}})
            elif g.provider == "openrouter":
                payload: dict[str, object] = {"model": g.model, "prompt": g.prompt, "aspect_ratio": g.aspect_ratio, "resolution": "1K", "output_format": "png"}
                if g.references:
                    payload["input_references"] = [{"type": "image_url", "image_url": {"url": _reference_value(ref)}} for ref in g.references]
                response = client.post(f"{base}/images", headers={**headers, "Content-Type": "application/json"}, json=payload)
            else:
                response = client.post(f"{base}/images/generations", headers={**headers, "Content-Type": "application/json"}, json={"model": g.model, "prompt": g.prompt, "size": "2K", "response_format": "url", "watermark": False, **({"image": [_reference_value(ref) for ref in g.references]} if g.references else {})})
            if response.status_code not in {408, 429} and response.status_code < 500: return _image_bytes(response, client)
            if attempt == 2: return _image_bytes(response, client)
            time.sleep(.5 * (attempt + 1))
    raise RuntimeError("Image generation failed.")


def _reference_value(reference: str) -> str:
    if reference.startswith(("http://", "https://")): return reference
    path = Path(reference); mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def _codex_generate(g: Generation) -> bytes:
    executable = shutil.which("codex")
    if not executable: raise RuntimeError("codex CLI is required for the codex provider. Run codex login first.")
    sessions = Path.home() / ".codex" / "sessions"; sessions.mkdir(parents=True, exist_ok=True)
    before = set(sessions.rglob("rollout-*.jsonl")); refs = [Path(ref) for ref in g.references]
    if missing := next((path for path in refs if not path.is_file()), None): raise ValueError(f"Reference image not found: {missing}")
    ratio_note = f" Generate at a {g.aspect_ratio} aspect ratio." if g.aspect_ratio else ""
    prompt = f"Use the imagegen tool to generate the image directly, return only the image.{ratio_note}\n\nRequest:\n{g.prompt}"
    command = [executable, "exec", "--skip-git-repo-check", "--sandbox", "read-only", "--color", "never", "--enable", "image_generation", *sum((["-i", str(ref)] for ref in refs), [])]
    result = subprocess.run(command, input=prompt, text=True, capture_output=True, timeout=g.timeout_seconds)
    if result.returncode: raise RuntimeError(f"Codex image renderer failed: {result.stderr[-1200:]}")
    blob = _find_session_image(set(sessions.rglob("rollout-*.jsonl")) - before)
    if not blob: raise RuntimeError("Codex image renderer did not return an image payload.")
    return base64.b64decode(blob)


def _find_session_image(files: set[Path]) -> str | None:
    best: str | None = None
    for path in files:
        for line in path.read_text(errors="replace").splitlines():
            for blob in re.findall(r'"([A-Za-z0-9+/=]{200,})"', line):
                if blob.startswith(("iVBORw0KGgo", "/9j/", "UklGR")) and (best is None or len(blob) > len(best)): best = blob
    return best


def _image_extension(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    raise ValueError("Image provider returned an unsupported image format.")


def _safe_output_path(path: Path, generation: Generation) -> Path:
    resolved = path.expanduser().resolve()
    if resolved.suffix:
        raise ValueError(f"Image output path must not include a filename extension; Wenqu determines it after generation: {resolved}")
    absolute = str(resolved)
    alternate = absolute[len("/private"):] if absolute.startswith("/private/") else None
    if any(candidate == prefix or candidate.startswith(prefix + "/") for candidate in (absolute, alternate) for prefix in FORBIDDEN_OUTPUT_PREFIXES if candidate):
        raise ValueError(f"Refusing to write image under a system directory: {resolved}")
    return resolved


def generate_image(generation: Generation, target: WenquPaths, *, environment: dict[str, str] | None = None, credentials_file: Path | None = None, upload: bool = False) -> dict[str, object]:
    env = dict(os.environ if environment is None else environment); _load_env(credentials_file or image_paths(target).credentials_file, env); _validate(generation)
    generation.output_path = _safe_output_path(generation.output_path, generation); generation.output_path.parent.mkdir(parents=True, exist_ok=True)
    data = _codex_generate(generation) if generation.provider == "codex" else _remote_generate(generation, env)
    generation.output_path = generation.output_path.with_suffix(_image_extension(data)); generation.output_path.write_bytes(data)
    publication = publish_image(generation.output_path, env) if upload else {"url": None, "publishError": None}
    return {"provider": generation.provider, "model": generation.model, "outputPath": str(generation.output_path), **publication}


def _find_picgo(env: dict[str, str]) -> str | None:
    home = Path(env.get("HOME", str(Path.home())))
    for candidate in (home / "bin" / "picgo-typora", shutil.which("picgo", path=env.get("PATH")), home / "Library" / "pnpm" / "picgo"):
        if candidate and Path(candidate).is_file(): return str(candidate)
    return None


def publish_image(path: Path, env: dict[str, str]) -> dict[str, str | None]:
    executable = _find_picgo(env); home = Path(env.get("HOME", str(Path.home())))
    try: configured = bool(json.loads((home / ".picgo" / "config.json").read_text()).get("picBed", {}).get("uploader"))
    except (OSError, ValueError): configured = False
    if not executable: return {"url": None, "publishError": "picgo is unavailable; install and configure PicGo before publishing"}
    if not configured: return {"url": None, "publishError": "picgo is not configured; run picgo set uploader before publishing"}
    result = subprocess.run([executable, "upload", str(path)], text=True, capture_output=True)
    url = next((line.strip() for line in result.stdout.splitlines() if line.strip().startswith("https://")), None)
    return {"url": url, "publishError": None if result.returncode == 0 and url else "picgo upload failed"}


def fetch_style_reference(relative_path: str, target: WenquPaths, *, environment: dict[str, str] | None = None) -> Path:
    if not relative_path or ".." in Path(relative_path).parts: raise ValueError("Style reference must be a relative path below styles/.")
    env = os.environ if environment is None else environment; rel = relative_path.removeprefix("styles/").lstrip("/")
    destination = Path(env.get("WENQU_IMAGE_CACHE", str(image_paths(target).cache_dir))) / rel
    if destination.is_file() and destination.stat().st_size: return destination
    base = env.get("WENQU_IMAGE_ASSETS_BASE", "https://raw.githubusercontent.com/gogoingai/wenqu-skills/master/wenqu-image-assets/styles").rstrip("/")
    response = httpx.get(f"{base}/{rel}", timeout=30); response.raise_for_status(); destination.parent.mkdir(parents=True, exist_ok=True); destination.write_bytes(response.content)
    return destination
