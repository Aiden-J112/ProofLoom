import json
import os
import sys
import tempfile
import unittest
import urllib.error
from contextlib import nullcontext
from pathlib import Path
from unittest import mock

import proofloom.sources as sources_module

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
    def test_loose_list_with_nested_fence_stays_one_complete_fragment(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            project_path = root_path / "project"
            project_path.mkdir()
            source_path = root_path / "lists.md"
            complete_list = (
                "- First item\n\n"
                "  detail paragraph\n\n"
                "  ```python\n  print('nested')\n  ```\n\n"
                "- Second item\n\n  final detail"
            )
            source_path.write_text(
                f"# Lists\n\n{complete_list}\n\nAfter the list.\n",
                encoding="utf-8",
            )

            with RunningApplication(root_path) as app:
                page = create_project(app, project_path)
                submit_form(
                    app,
                    "/sources/import",
                    csrf_token=hidden_field(page, "csrf_token"),
                    project=str(project_path),
                    source=str(source_path),
                ).read()

            fragments = json.loads(
                (project_path / ".proofloom" / "source-fragments.json").read_text()
            )
            self.assertEqual(["list", "paragraph"], [item["kind"] for item in fragments])
            self.assertEqual(complete_list, fragments[0]["content"])
            self.assertEqual("After the list.", fragments[1]["content"])

    def test_setext_headings_update_heading_paths_and_ordinals(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            project_path = root_path / "project"
            project_path.mkdir()
            source_path = root_path / "setext.md"
            source_path.write_text(
                "Title\n=====\n\nIntro.\n\nSubtitle\n--------\n\nOne.\n\nTwo.\n",
                encoding="utf-8",
            )

            with RunningApplication(root_path) as app:
                page = create_project(app, project_path)
                submit_form(
                    app,
                    "/sources/import",
                    csrf_token=hidden_field(page, "csrf_token"),
                    project=str(project_path),
                    source=str(source_path),
                ).read()

            fragments = json.loads(
                (project_path / ".proofloom" / "source-fragments.json").read_text()
            )
            self.assertEqual(
                [["Title"], ["Title", "Subtitle"], ["Title", "Subtitle"]],
                [item["heading_path"] for item in fragments],
            )
            self.assertEqual([1, 1, 2], [item["ordinal"] for item in fragments])

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
                self.assertEqual("current", fragment["status"])
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
            self.assertEqual(first, second)
            self.assertTrue(all(fragment["status"] == "current" for fragment in second))

    def test_imports_merge_distinct_sources_and_reimport_replaces_only_that_source(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            project_path = root_path / "project"
            project_path.mkdir()
            first_dir = root_path / "first"
            second_dir = root_path / "second"
            first_dir.mkdir()
            second_dir.mkdir()
            first = first_dir / "lesson.md"
            second = second_dir / "lesson.md"
            first.write_text("# First\n\nOld first.\n", encoding="utf-8")
            second.write_text("# Second\n\nKeep second.\n", encoding="utf-8")

            with RunningApplication(root_path) as app:
                page = create_project(app, project_path)
                common = {
                    "csrf_token": hidden_field(page, "csrf_token"),
                    "project": str(project_path),
                }
                submit_form(app, "/sources/import", source=str(first), **common).read()
                submit_form(app, "/sources/import", source=str(second), **common).read()
                first.write_text("# First\n\nNew first.\n", encoding="utf-8")
                submit_form(app, "/sources/import", source=str(first), **common).read()

            fragments = json.loads(
                (project_path / ".proofloom" / "source-fragments.json").read_text()
            )
            current = [item for item in fragments if item["status"] == "current"]
            self.assertEqual(
                ["first/lesson.md", "second/lesson.md"],
                sorted(item["source_file"] for item in current),
            )
            contents = {item["source_file"]: item["content"] for item in current}
            self.assertEqual("New first.", contents["first/lesson.md"])
            self.assertEqual("Keep second.", contents["second/lesson.md"])
            self.assertEqual(
                ["Old first."],
                [item["content"] for item in fragments if item["status"] == "changed"],
            )

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

    def test_directory_import_rejects_symlinked_file_outside_browsing_root(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            root_path = Path(root)
            project_path = root_path / "project"
            project_path.mkdir()
            sources = root_path / "sources"
            sources.mkdir()
            outside_source = Path(outside) / "outside.md"
            outside_source.write_text("# Synthetic\n\nMust not be read.\n", encoding="utf-8")
            link = sources / "linked.md"
            try:
                os.symlink(outside_source, link)
                resolved_path = nullcontext()
            except OSError:
                link.write_text("# Link placeholder\n", encoding="utf-8")
                original_resolve = Path.resolve

                def resolve_link(path: Path, *args, **kwargs):
                    if path == link:
                        return outside_source
                    return original_resolve(path, *args, **kwargs)

                resolved_path = mock.patch.object(Path, "resolve", resolve_link)

            with resolved_path:
                with RunningApplication(root_path) as app:
                    page = create_project(app, project_path)
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        submit_form(
                            app,
                            "/sources/import",
                            csrf_token=hidden_field(page, "csrf_token"),
                            project=str(project_path),
                            source=str(sources),
                        )
                    self.assertEqual(400, error.exception.code)
                    self.assertIn("linked.md", error.exception.read().decode())

    def test_atomic_storage_failure_preserves_previous_fragments(self):
        with tempfile.TemporaryDirectory() as root:
            destination = Path(root) / "source-fragments.json"
            previous = [{"id": "src_previous"}]
            destination.write_text(json.dumps(previous), encoding="utf-8")

            replacement = sources_module.parse_markdown(
                "# Synthetic\n\nReplacement passage.", "synthetic.md"
            )[0]

            with mock.patch(
                "proofloom.sources.os.replace", side_effect=OSError("disk full")
            ):
                with self.assertRaises(OSError):
                    sources_module.write_source_fragments(
                        destination, [replacement]
                    )

            self.assertEqual(previous, json.loads(destination.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
