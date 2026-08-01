"""Public ``wenqu`` command line interface."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import typer

from .config import configuration_status, initialize_config, library_proxy, load_credentials, paths
from .crawler import crawl_page, crawl_site, crawl_wechat_article
from .image import _load_env, _read_config, fetch_style_reference, generate_image, image_doctor, image_paths, initialize_image_config, resolve_generation
from .runtime import library_doctor, setup_library
from .search import ENGINE_NAMES, RequestOptions, search


app = typer.Typer(no_args_is_help=True, add_completion=False, help="Runtime CLI for Wenqu skills.")
library_app = typer.Typer(no_args_is_help=True, add_completion=False, help="Search and collect article materials.")
setup_app = typer.Typer(no_args_is_help=True, add_completion=False, help="Prepare Wenqu capabilities with explicit side effects.")
config_app = typer.Typer(no_args_is_help=True, add_completion=False, help="Initialize and inspect local Wenqu configuration.")
image_app = typer.Typer(no_args_is_help=True, add_completion=False, help="Generate and publish article images.")
app.add_typer(library_app, name="library")
app.add_typer(setup_app, name="setup")
app.add_typer(config_app, name="config")
app.add_typer(image_app, name="image")


def _version_callback(value: bool) -> None:
    if value:
        try:
            installed = version("wenqu-cli")
        except PackageNotFoundError:
            installed = "0.1.0+local"
        typer.echo(f"wenqu {installed}")
        raise typer.Exit()


@app.callback()
def main(
    show_version: bool = typer.Option(False, "--version", callback=_version_callback, is_eager=True, help="Show Wenqu CLI version."),
) -> None:
    """Wenqu's managed runtime for article collection and image generation."""


@app.command()
def doctor(
    as_json: bool = typer.Option(False, "--json", help="Print the complete diagnostic report as JSON."),
) -> None:
    """Report whether Wenqu's installed library runtime is usable."""
    report = library_doctor(paths())
    if as_json:
        _emit(report)
        return
    typer.echo(f"Library runtime: {'ready' if report['ready'] else 'needs setup'}")
    for name, component in report["components"].items():
        typer.echo(f"- {name}: {'ok' if component['available'] else 'missing'}" + (f" ({component['version']})" if component["version"] else ""))
    typer.echo(f"Data: {report['paths']['data']}")


@setup_app.command("library")
def setup_library_command(
    skip_browser: bool = typer.Option(False, "--skip-browser", help="Create Wenqu state without downloading Chromium."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show downloads without creating files or running commands."),
    as_json: bool = typer.Option(False, "--json", help="Print result as JSON."),
) -> None:
    """Install the Chromium runtime used by Wenqu's Crawl4AI adapter."""
    try:
        result = setup_library(paths(), install_browser=not skip_browser, dry_run=dry_run)
    except Exception as error:
        raise typer.Exit(_error(str(error))) from error
    if as_json:
        _emit(result)
    elif dry_run:
        typer.echo("Would run:")
        for command in result["commands"]:
            typer.echo("  " + " ".join(command))
    else:
        typer.echo("Wenqu library runtime is configured.")


@config_app.command("init")
def config_init_command(
    dry_run: bool = typer.Option(False, "--dry-run", help="Show paths without creating configuration files."),
    as_json: bool = typer.Option(False, "--json", help="Print result as JSON."),
) -> None:
    """Create a non-secret config file and private credentials template."""
    try:
        result = initialize_config(paths(), dry_run=dry_run)
    except Exception as error:
        raise typer.Exit(_error(str(error))) from error
    if as_json:
        _emit(result)
        return
    typer.echo(f"Configuration: {result['configFile']}")
    typer.echo(f"Credentials: {result['credentialsFile']}")
    typer.echo("To enable Exa, add EXA_API_KEY to the credentials file. Wenqu never prints its value.")


@config_app.command("status")
def config_status_command(
    as_json: bool = typer.Option(False, "--json", help="Print result as JSON."),
) -> None:
    """Show which optional integrations are configured without exposing secrets."""
    try:
        result = configuration_status(paths())
    except Exception as error:
        raise typer.Exit(_error(str(error))) from error
    if as_json:
        _emit(result)
        return
    exa = result["optionalEngines"]["exa"]
    typer.echo(f"Exa: {'configured' if exa['configured'] else 'not configured'}")
    if not exa["configured"]:
        typer.echo(exa["configure"])


@library_app.command("engines")
def engines() -> None:
    """List all built-in candidate discovery channels."""
    typer.echo("\n".join(ENGINE_NAMES))


@library_app.command("search")
def search_command(
    query: str = typer.Argument(..., help="Search query."),
    engines: str | None = typer.Option(None, "--engines", help="Comma-separated engine codes. Defaults to all built-in engines."),
    limit: int = typer.Option(10, "--limit", min=1, help="Maximum merged result count."),
    no_browser_fallback: bool = typer.Option(False, "--no-browser-fallback", help="Do not recover supported engines with Crawl4AI."),
    as_json: bool = typer.Option(False, "--json", help="Print the stable machine-readable result envelope."),
) -> None:
    """Find candidates across Wenqu's ten managed search channels."""
    try:
        target = paths()
        envelope = search(
            query,
            engines=engines,
            limit=limit,
            options=RequestOptions(proxy_url=library_proxy(target), exa_api_key=load_credentials(target).get("EXA_API_KEY")),
            browser_fallback=not no_browser_fallback,
        )
    except Exception as error:
        raise typer.Exit(_error(str(error))) from error
    payload = envelope.as_dict()
    if as_json:
        _emit(payload)
        return
    for result in payload["data"]["results"]:
        typer.echo(f"[{result['engine']}/{result['channel']}] {result['title']}\n{result['url']}")
    for failure in payload["data"]["partialFailures"]:
        typer.echo(f"warning: {failure['engine']}: {failure['message']}", err=True)
    for skipped in payload["data"]["skippedEngines"]:
        typer.echo(f"skipped: {skipped['engine']}: {skipped['reason']} {skipped['configure']}", err=True)


@library_app.command("fetch")
def fetch_command(
    url: str = typer.Argument(..., help="Public HTTP(S) URL to download."),
    out: Path = typer.Option(..., "--out", help="Markdown output path."),
    max_pages: int = typer.Option(1, "--max-pages", min=1, help="Bounded same-origin crawl limit."),
    as_json: bool = typer.Option(False, "--json", help="Print written URLs and path as JSON."),
) -> None:
    """Download one page or a bounded same-origin site as Markdown."""
    if not url.startswith(("http://", "https://")):
        raise typer.BadParameter("URL must use http:// or https://")
    try:
        is_wechat = urlparse(url).netloc == "mp.weixin.qq.com"
        pages = [crawl_wechat_article(url)] if is_wechat else (crawl_site(url, max_pages) if max_pages > 1 else [crawl_page(url)])
        out.parent.mkdir(parents=True, exist_ok=True)
        content = "\n\n".join(page.markdown if len(pages) == 1 else f"====== {page.url}\n\n{page.markdown}" for page in pages)
        out.write_text(content, encoding="utf-8")
    except Exception as error:
        raise typer.Exit(_error(str(error))) from error
    result: dict[str, Any] = {"outputPath": str(out), "urls": [page.url for page in pages], "count": len(pages)}
    if as_json:
        _emit(result)
    else:
        typer.echo(f"Saved {len(pages)} page(s): {out}")


@image_app.command("doctor")
def image_doctor_command(
    env_file: Path | None = typer.Option(None, "--env-file", help="Private provider credentials file; defaults to the managed file."),
    as_json: bool = typer.Option(False, "--json", help="Print provider and runtime availability as JSON."),
) -> None:
    """Inspect image providers without exposing credentials."""
    try:
        report = image_doctor(paths(), credentials_file=env_file)
    except Exception as error:
        raise typer.Exit(_error(str(error))) from error
    if as_json: _emit(report)
    else:
        typer.echo(json.dumps(report, ensure_ascii=False, indent=2))


@image_app.command("config-init")
def image_config_init_command(dry_run: bool = typer.Option(False, "--dry-run"), as_json: bool = typer.Option(False, "--json")) -> None:
    """Create editable image defaults and a private provider-credentials template."""
    result = initialize_image_config(paths(), dry_run=dry_run)
    if as_json: _emit(result)
    else: typer.echo(f"Image configuration: {result['configFile']}\nImage credentials: {result['credentialsFile']}")


@image_app.command("generate")
def image_generate_command(
    prompt: str | None = typer.Option(None, "--prompt", "-p"),
    prompt_file: Path | None = typer.Option(None, "--prompt-file"),
    out: Path = typer.Option(..., "--out", "--image", help="Image output path."),
    provider: str | None = typer.Option(None, "--provider"),
    model: str | None = typer.Option(None, "--model"),
    reference: list[Path] = typer.Option([], "--ref", "--reference"),
    aspect_ratio: str | None = typer.Option(None, "--ar"),
    base_url: str | None = typer.Option(None, "--base-url"),
    article_config: Path | None = typer.Option(None, "--article-config"),
    global_config: Path | None = typer.Option(None, "--global-config"),
    env_file: Path | None = typer.Option(None, "--env-file", help="Private provider credentials file; defaults to the managed file."),
    timeout_sec: int = typer.Option(300, "--timeout-sec", min=1, help="Maximum generation request duration in seconds."),
    upload: bool = typer.Option(False, "--upload"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Generate one image through Codex, OpenAI, OpenRouter, DashScope, or Seedream."""
    try:
        text = prompt if prompt is not None else (prompt_file.read_text(encoding="utf-8") if prompt_file else None)
        if not text or not text.strip(): raise ValueError("Provide --prompt or --prompt-file.")
        target = paths(); img_paths = image_paths(target); env = dict(__import__("os").environ); credentials = env_file or img_paths.credentials_file; _load_env(credentials, env)
        generation = resolve_generation(prompt=text, output_path=out, environment=env, global_config=_read_config(global_config or img_paths.config_file), article_config=_read_config(article_config) if article_config else None, provider=provider, model=model, aspect_ratio=aspect_ratio, references=[str(item) for item in reference], base_url=base_url, timeout_seconds=timeout_sec)
        result = generate_image(generation, target, environment=env, credentials_file=credentials, upload=upload)
    except Exception as error:
        raise typer.Exit(_error(str(error))) from error
    if as_json: _emit(result)
    else: typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@image_app.command("fetch-ref")
def image_fetch_ref_command(style_path: str = typer.Argument(...), as_json: bool = typer.Option(False, "--json")) -> None:
    """Download a versioned Wenqu style reference into the managed cache."""
    try: path = fetch_style_reference(style_path, paths())
    except Exception as error: raise typer.Exit(_error(str(error))) from error
    if as_json: _emit({"path": str(path)})
    else: typer.echo(path)


def _emit(value: object) -> None:
    typer.echo(json.dumps(value, ensure_ascii=False, indent=2))


def _error(message: str) -> int:
    typer.echo(f"wenqu: {message}", err=True)
    return 1
