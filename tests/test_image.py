from __future__ import annotations

import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from wenqu.config import WenquPaths
from wenqu.image import _dashscope_size, _image_bytes, _load_env, _safe_output_path, _validate, fetch_style_reference, generate_image, image_doctor, image_paths, initialize_image_config, resolve_generation


class ImageTests(unittest.TestCase):
    def test_image_configuration_lives_under_gogoingai_wenqu_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = WenquPaths(root / "wenqu-skills", root / "wenqu-skills" / "runtime", root / "wenqu-skills" / "cache")
            result = initialize_image_config(target)
            paths = image_paths(target)

            self.assertEqual(paths.config_file, root / "wenqu-skills" / "image" / "config.json")
            self.assertEqual(paths.credentials_file, root / "wenqu-skills" / "image" / "credentials.env")
            self.assertEqual(stat.S_IMODE(paths.credentials_file.stat().st_mode), 0o600)
            self.assertEqual(json.loads(paths.config_file.read_text())["defaults"]["aspectRatio"], "16:9")
            self.assertEqual(result["credentialsAction"], "create")

    def test_command_options_take_precedence_without_cross_provider_models(self) -> None:
        generation = resolve_generation(
            prompt="diagram",
            output_path=Path("/tmp/output.png"),
            environment={"OPENAI_API_KEY": "test"},
            global_config={"defaults": {"provider": "dashscope", "model": "qwen-image-2.0-pro", "aspectRatio": "16:9"}, "providers": {"openai": {"baseUrl": "https://gateway.example/v1"}}},
            article_config={"provider": "seedream", "model": "doubao-seedream-5-0-260128", "aspectRatio": "4:3"},
            provider="openai",
            model="gpt-image-1",
            aspect_ratio="1:1",
        )
        self.assertEqual((generation.provider, generation.model, generation.aspect_ratio, generation.base_url), ("openai", "gpt-image-1", "1:1", "https://gateway.example/v1"))

    def test_openrouter_has_its_own_image_endpoint_defaults(self) -> None:
        generation = resolve_generation(prompt="diagram", output_path=Path("/tmp/output"), environment={"OPENROUTER_API_KEY": "test"}, global_config={}, provider="openrouter")
        self.assertEqual((generation.model, generation.base_url), ("openai/gpt-image-1", "https://openrouter.ai/api/v1"))

    def test_reference_model_validation_accepts_supported_models(self) -> None:
        generation = resolve_generation(prompt="diagram", output_path=Path("/tmp/a.png"), environment={"DASHSCOPE_API_KEY": "test"}, global_config={}, provider="dashscope", model="qwen-image-max", references=["/tmp/ref.png"])
        with self.assertRaisesRegex(ValueError, "does not support reference"):
            _validate(generation)

        _validate(resolve_generation(prompt="diagram", output_path=Path("/tmp/a.png"), environment={"DASHSCOPE_API_KEY": "test"}, global_config={}, provider="dashscope", model="qwen-image-2.0-pro", references=["/tmp/ref.png"]))
        _validate(resolve_generation(prompt="diagram", output_path=Path("/tmp/a.png"), environment={"ARK_API_KEY": "test"}, global_config={}, provider="seedream", model="doubao-seedream-5.0-lite", references=["/tmp/ref.png"]))

    def test_doctor_does_not_expose_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = WenquPaths(root / "config", root / "data", root / "cache")
            initialize_image_config(target)
            image_paths(target).credentials_file.write_text("ARK_API_KEY=secret-value\n", encoding="utf-8")
            image_paths(target).credentials_file.chmod(0o600)
            with patch("wenqu.image.shutil.which", return_value=None):
                report = image_doctor(target, {})
        self.assertTrue(report["providers"]["seedream"]["configured"])
        self.assertNotIn("secret-value", json.dumps(report))

    def test_style_reference_uses_a_managed_or_explicit_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = WenquPaths(root / "config", root / "data", root / "cache")
            httpx = __import__("httpx")
            response = httpx.Response(200, content=b"png", request=httpx.Request("GET", "https://assets.example/test.png"))
            with patch("wenqu.image.httpx.get", return_value=response):
                result = fetch_style_reference("styles/mono-marker/example.png", target, environment={"WENQU_IMAGE_CACHE": str(root / "style-cache")})
        self.assertEqual(result, root / "style-cache" / "mono-marker" / "example.png")

    def test_custom_env_file_preserves_a_process_credential(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / "credentials.env"
            env_file.write_text("OPENAI_API_KEY=file-key\nDASHSCOPE_API_KEY=dashscope-key\n", encoding="utf-8")
            env_file.chmod(0o600)
            environment = {"OPENAI_API_KEY": "process-key"}

            _load_env(env_file, environment)

        self.assertEqual(environment, {"OPENAI_API_KEY": "process-key", "DASHSCOPE_API_KEY": "dashscope-key"})

    def test_output_path_is_extensionless_and_format_is_detected_after_generation(self) -> None:
        remote = resolve_generation(prompt="diagram", output_path=Path("/tmp/diagram"), environment={"ARK_API_KEY": "test"}, global_config={}, provider="seedream", model="doubao-seedream-5.0-lite")
        self.assertEqual(_safe_output_path(remote.output_path, remote), Path("/tmp/diagram").resolve())
        with self.assertRaisesRegex(ValueError, "must not include"):
            _safe_output_path(Path("/tmp/diagram.png"), remote)
        with self.assertRaisesRegex(ValueError, "system directory"):
            _safe_output_path(Path("/etc/diagram"), remote)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = WenquPaths(root / "config", root / "data", root / "cache")
            output = root / "diagram"
            generation = resolve_generation(prompt="diagram", output_path=output, environment={"ARK_API_KEY": "test"}, global_config={}, provider="seedream", model="doubao-seedream-5.0-lite")
            with patch("wenqu.image._remote_generate", return_value=b"\xff\xd8\xff\xe0test"):
                result = generate_image(generation, target, environment={"ARK_API_KEY": "test"})
            self.assertEqual(result["outputPath"], str(output.resolve().with_suffix(".jpg")))
            self.assertTrue(output.resolve().with_suffix(".jpg").is_file())

    def test_dashscope_choice_response_is_decoded(self) -> None:
        httpx = __import__("httpx")
        response = httpx.Response(200, json={"output": {"choices": [{"message": {"content": [{"image": "AQID"}]}}]}}, request=httpx.Request("POST", "https://provider.example/generate"))

        self.assertEqual(_image_bytes(response, object()), b"\x01\x02\x03")

    def test_dashscope_size_uses_a_valid_image_baseline(self) -> None:
        self.assertEqual(_dashscope_size("1:1"), "1280*1280")
        self.assertEqual(_dashscope_size("16:9"), "1280*720")
        self.assertEqual(_dashscope_size("9:16"), "720*1280")


if __name__ == "__main__":
    unittest.main()
