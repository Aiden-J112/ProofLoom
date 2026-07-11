import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from test_project_ui import (
    RunningApplication,
    hidden_field,
    link_from,
    open_page,
    submit_form,
)


def create_project(app: str, project_path: Path) -> str:
    home = open_page(app, "/")
    return submit_form(
        app,
        "/projects/create",
        csrf_token=hidden_field(home, "csrf_token"),
        directory=str(project_path),
        name="Import Project",
    ).read().decode()


class SourceImportUiTests(unittest.TestCase):
    def test_owner_imports_markdown_and_inspects_persisted_structured_fragments(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            project_path = root_path / "project"
            project_path.mkdir()
            source_path = root_path / "lesson.md"
            source_path.write_text(
                "# Lesson\n\nFirst paragraph.\ncontinued here.\n\n"
                "## Steps\n\n- one\n- two\n  continued\n\n"
                "```python\nprint('one')\nprint('two')\n```\n",
                encoding="utf-8",
            )

            with RunningApplication(root_path) as app:
                project_page = create_project(app, project_path)
                source_browser = open_page(
                    app, link_from(project_page, "Choose Markdown source")
                )
                selected_source = open_page(
                    app, link_from(source_browser, "lesson.md")
                )
                imported = submit_form(
                    app,
                    "/sources/import",
                    csrf_token=hidden_field(selected_source, "csrf_token"),
                    project=hidden_field(selected_source, "project"),
                    source=hidden_field(selected_source, "source"),
                ).read().decode()

                self.assertIn("Source Fragments", imported)
                self.assertIn("lesson.md", imported)
                self.assertIn("Lesson / Steps", imported)
                self.assertIn("- one\n- two\n  continued", imported)
                self.assertIn("print(&#x27;one&#x27;)\nprint(&#x27;two&#x27;)", imported)

            fragments_path = project_path / ".proofloom" / "source-fragments.json"
            fragments = json.loads(fragments_path.read_text(encoding="utf-8"))
            self.assertEqual(["paragraph", "list", "code_block"], [f["kind"] for f in fragments])
            self.assertEqual([1, 1, 2], [f["ordinal"] for f in fragments])
            for fragment in fragments:
                self.assertEqual("lesson.md", fragment["source_file"])
                self.assertEqual("1", fragment["schema_version"])
                self.assertTrue(fragment["id"].startswith("src_"))
                self.assertTrue(fragment["content_hash"].startswith("sha256:"))
                self.assertIn("content", fragment)

            with RunningApplication(root_path) as restarted_app:
                home = open_page(restarted_app, "/")
                reopened = submit_form(
                    restarted_app,
                    "/projects/open",
                    csrf_token=hidden_field(home, "csrf_token"),
                    directory=str(project_path),
                ).read().decode()
                self.assertIn(fragments[0]["id"], reopened)

    def test_reimporting_unchanged_directory_keeps_fragment_ids(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            project_path = root_path / "project"
            project_path.mkdir()
            sources = root_path / "sources"
            sources.mkdir()
            (sources / "a.md").write_text("# A\n\nAlpha.\n", encoding="utf-8")
            (sources / "b.md").write_text("# B\n\nBeta.\n", encoding="utf-8")

            with RunningApplication(root_path) as app:
                page = create_project(app, project_path)
                fields = {
                    "csrf_token": hidden_field(page, "csrf_token"),
                    "project": str(project_path),
                    "source": str(sources),
                }
                submit_form(app, "/sources/import", **fields).read()
                first = json.loads(
                    (project_path / ".proofloom" / "source-fragments.json").read_text()
                )
                submit_form(app, "/sources/import", **fields).read()
                second = json.loads(
                    (project_path / ".proofloom" / "source-fragments.json").read_text()
                )

            self.assertEqual([f["id"] for f in first], [f["id"] for f in second])

    def test_import_error_identifies_the_affected_file(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            project_path = root_path / "project"
            project_path.mkdir()
            invalid = root_path / "invalid.md"
            invalid.write_bytes(b"# Invalid\n\xff")

            with RunningApplication(root_path) as app:
                page = create_project(app, project_path)
                with self.assertRaises(urllib.error.HTTPError) as error:
                    submit_form(
                        app,
                        "/sources/import",
                        csrf_token=hidden_field(page, "csrf_token"),
                        project=str(project_path),
                        source=str(invalid),
                    )
                self.assertEqual(400, error.exception.code)
                self.assertIn("invalid.md", error.exception.read().decode())

    def test_import_rejects_a_source_outside_the_browsing_root(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            root_path = Path(root)
            project_path = root_path / "project"
            project_path.mkdir()
            outside_source = Path(outside) / "outside.md"
            outside_source.write_text("# Outside\n\nNot allowed.\n", encoding="utf-8")

            with RunningApplication(root_path) as app:
                page = create_project(app, project_path)
                with self.assertRaises(urllib.error.HTTPError) as error:
                    submit_form(
                        app,
                        "/sources/import",
                        csrf_token=hidden_field(page, "csrf_token"),
                        project=str(project_path),
                        source=str(outside_source),
                    )
                self.assertEqual(400, error.exception.code)

            self.assertFalse(
                (project_path / ".proofloom" / "source-fragments.json").exists()
            )


if __name__ == "__main__":
    unittest.main()
