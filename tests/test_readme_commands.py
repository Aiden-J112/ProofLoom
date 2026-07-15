import os
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class ReadmeCommandTests(unittest.TestCase):
    def test_documented_module_command_exposes_the_public_cli(self):
        result = subprocess.run(
            [sys.executable, "-m", "proofloom.app", "--help"],
            cwd=ROOT,
            env={**os.environ, "PYTHONPATH": "src"},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("{serve,check}", result.stdout)
        self.assertIn("--browse-root", result.stdout)

    def test_readme_uses_venv_interpreters_and_declared_console_entrypoint(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn(
            ".venv\\Scripts\\python.exe -m proofloom.app --browse-root .",
            readme,
        )
        self.assertIn(
            ".venv/bin/python -m proofloom.app --browse-root .",
            readme,
        )
        self.assertIn(".venv\\Scripts\\proofloom.exe check demo-project", readme)
        self.assertIn(".venv/bin/proofloom check demo-project", readme)
        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual("proofloom.app:main", pyproject["project"]["scripts"]["proofloom"])


if __name__ == "__main__":
    unittest.main()
