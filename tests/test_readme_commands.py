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

    def test_readme_documents_copyable_local_json_startup(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for command in (
            r"Copy-Item .\proofloom.example.json .\proofloom.local.json",
            r".venv\Scripts\proofloom.exe --config .\proofloom.local.json",
            "cp ./proofloom.example.json ./proofloom.local.json",
            ".venv/bin/proofloom --config ./proofloom.local.json",
        ):
            with self.subTest(command=command):
                self.assertIn(command, readme)
        self.assertIn("proofloom.local.json", readme)
        self.assertIn(".gitignore", readme)

    def test_readme_documents_the_governed_web_workflow(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for text in (
            "本机回环地址",
            "不是上传到云端",
            "Choose a directory",
            "Choose a project directory",
            "Use this directory",
            "Create project",
            "Open project",
            "Choose Markdown source",
            "Import selected source",
            "Inspector | Component",
            "Inspection Report | Artifact",
            "Safety Gate | Component",
            "Risky Command | Artifact",
            "accept",
            "reject",
            "replace",
            "needs_domain_review",
            "COMPOSED_OF",
            "PROMPTS",
            "CALLS_TOOL",
            "PRODUCES",
            "VERIFIES",
            "BLOCKS",
            "只有当前有效且已接受",
        ):
            with self.subTest(text=text):
                self.assertIn(text, readme)
        self.assertNotIn("点击 **Import Markdown**", readme)

    def test_readme_documents_codex_and_openai_compatible_modes(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for text in (
            "codex login status",
            "0.144.5",
            '"backend": "codex-cli"',
            '"model": "gpt-5.6-luna"',
            '"reasoning": "medium"',
            '"backend": "openai-compatible"',
            '"api_key"',
            '"endpoint"',
            '"base_url"',
            '"provider"',
            '"timeout"',
        ):
            with self.subTest(text=text):
                self.assertIn(text, readme)


if __name__ == "__main__":
    unittest.main()
