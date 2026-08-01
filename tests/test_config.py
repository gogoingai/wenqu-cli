from __future__ import annotations

import unittest
from pathlib import Path
import stat
import tempfile

from wenqu.config import WenquPaths, configuration_status, initialize_config, load_credentials, paths


class ConfigTests(unittest.TestCase):
    def test_paths_share_the_existing_gogoingai_wenqu_root(self) -> None:
        resolved = paths({"HOME": "/home/tester"})

        self.assertEqual(str(resolved.config_dir), "/home/tester/.gogoingai/wenqu-skills")
        self.assertEqual(str(resolved.data_dir), "/home/tester/.gogoingai/wenqu-skills/runtime")
        self.assertEqual(str(resolved.cache_dir), "/home/tester/.gogoingai/wenqu-skills/cache")

    def test_init_creates_private_credentials_template_and_loads_only_known_secret(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = WenquPaths(root / "config", root / "data", root / "cache")

            result = initialize_config(target)
            target.credentials_file.write_text("EXA_API_KEY=from-file\nUNKNOWN=value\n", encoding="utf-8")
            target.credentials_file.chmod(0o600)

            self.assertEqual(result["credentialsAction"], "create")
            self.assertEqual(stat.S_IMODE(target.credentials_file.stat().st_mode), 0o600)
            self.assertEqual(load_credentials(target), {"EXA_API_KEY": "from-file"})
            self.assertTrue(configuration_status(target)["optionalEngines"]["exa"]["configured"])
            self.assertIsNone(configuration_status(target)["optionalEngines"]["exa"]["configure"])

    def test_credentials_file_with_broad_permissions_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = WenquPaths(root / "config", root / "data", root / "cache")
            initialize_config(target)
            target.credentials_file.chmod(0o644)

            with self.assertRaisesRegex(ValueError, "chmod 600"):
                load_credentials(target)

if __name__ == "__main__":
    unittest.main()
