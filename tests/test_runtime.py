from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from wenqu.runtime import _chromium_path


class RuntimeTests(unittest.TestCase):
    def test_doctor_uses_playwright_dry_run_without_starting_a_driver(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="Chrome\n  Install location:    /tmp/chromium-1234\n",
            stderr="",
        )
        with patch("wenqu.runtime.subprocess.run", return_value=completed) as run:
            location = _chromium_path()

        self.assertEqual(str(location), "/tmp/chromium-1234")
        self.assertIn("--dry-run", run.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
