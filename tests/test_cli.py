from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from typer.testing import CliRunner

from wenqu.cli import app
from wenqu.models import SearchEnvelope, SearchResult


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    def test_version(self) -> None:
        result = self.runner.invoke(app, ["--version"])

        self.assertEqual(result.exit_code, 0)
        self.assertTrue(result.stdout.startswith("wenqu "))

    def test_library_search_json_uses_stable_envelope(self) -> None:
        envelope = SearchEnvelope(
            query="memory",
            engines=("baidu",),
            results=(SearchResult("Memory", "https://example.com", "desc", "baidu"),),
            partial_failures=(),
        )
        with patch("wenqu.cli.search", return_value=envelope):
            result = self.runner.invoke(app, ["library", "search", "memory", "--engines", "baidu", "--json"])

        self.assertEqual(result.exit_code, 0, result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["data"]["results"][0]["engine"], "baidu")

    def test_setup_dry_run_does_not_create_runtime_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch("wenqu.cli.paths") as paths:
                from wenqu.config import WenquPaths

                root = Path(directory)
                target = WenquPaths(root / "config", root / "data", root / "cache")
                paths.return_value = target
                result = self.runner.invoke(app, ["setup", "library", "--dry-run", "--json"])

        self.assertEqual(result.exit_code, 0, result.stdout)
        self.assertTrue(json.loads(result.stdout)["dryRun"])

    def test_config_init_creates_a_safe_template(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            from wenqu.config import WenquPaths

            root = Path(directory)
            target = WenquPaths(root / "config", root / "data", root / "cache")
            with patch("wenqu.cli.paths", return_value=target):
                result = self.runner.invoke(app, ["config", "init", "--json"])

        self.assertEqual(result.exit_code, 0, result.stdout)
        self.assertEqual(json.loads(result.stdout)["credentialsAction"], "create")

    def test_image_generate_accepts_legacy_output_alias_and_custom_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            from wenqu.config import WenquPaths

            root = Path(directory)
            target = WenquPaths(root / "config", root / "data", root / "cache")
            env_file = root / "provider.env"
            env_file.write_text("OPENAI_API_KEY=test-key\n", encoding="utf-8")
            env_file.chmod(0o600)
            with patch("wenqu.cli.paths", return_value=target), patch("wenqu.cli.generate_image", return_value={"provider": "openai", "model": "gpt-image-1", "outputPath": "/tmp/diagram.png", "url": None, "publishError": None}) as generate:
                result = self.runner.invoke(app, ["image", "generate", "--provider", "openai", "--prompt", "diagram", "--image", "/tmp/diagram", "--env-file", str(env_file), "--timeout-sec", "123", "--json"])

        self.assertEqual(result.exit_code, 0, result.stdout)
        generation = generate.call_args.args[0]
        self.assertEqual((generation.output_path, generation.timeout_seconds), (Path("/tmp/diagram"), 123))


if __name__ == "__main__":
    unittest.main()
