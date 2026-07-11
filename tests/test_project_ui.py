import html
import json
import re
import tempfile
import threading
import unittest
import urllib.parse
import urllib.error
import urllib.request
from pathlib import Path

from proofloom.app import create_server


class RunningApplication:
    def __init__(self, browse_root: Path):
        self.server = create_server("127.0.0.1", 0, browse_root=browse_root)
        self.thread = threading.Thread(target=self.server.serve_forever)

    def __enter__(self):
        self.thread.start()
        return f"http://127.0.0.1:{self.server.server_port}"

    def __exit__(self, *_):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()


def submit_form(base_url: str, route: str, **fields: str):
    data = urllib.parse.urlencode(fields).encode()
    request = urllib.request.Request(base_url + route, data=data, method="POST")
    return urllib.request.urlopen(request)


def open_page(base_url: str, route: str) -> str:
    return urllib.request.urlopen(base_url + route).read().decode()


def link_from(page: str, label: str) -> str:
    match = re.search(rf'<a href="([^"]+)">{re.escape(label)}</a>', page)
    if not match:
        raise AssertionError(f"No link labelled {label!r}")
    return html.unescape(match.group(1))


def hidden_field(page: str, name: str) -> str:
    match = re.search(
        rf'<input type="hidden" name="{re.escape(name)}" value="([^"]+)">',
        page,
    )
    if not match:
        raise AssertionError(f"No hidden field named {name!r}")
    return html.unescape(match.group(1))


class KnowledgeProjectUiTests(unittest.TestCase):
    def test_owner_learner_creates_and_reopens_project_after_restart(self):
        with tempfile.TemporaryDirectory() as root:
            project_path = Path(root) / "portable-project"
            project_path.mkdir()

            with RunningApplication(Path(root)) as app:
                home = open_page(app, "/")
                directory_browser = open_page(app, link_from(home, "Choose a directory"))
                selected_directory = open_page(
                    app,
                    link_from(
                        open_page(app, link_from(directory_browser, "portable-project")),
                        "Use this directory",
                    ),
                )
                response = submit_form(
                    app,
                    "/projects/create",
                    csrf_token=hidden_field(selected_directory, "csrf_token"),
                    directory=hidden_field(selected_directory, "directory"),
                    name="Portable Project",
                )
                self.assertIn("Portable Project", response.read().decode())

            metadata_path = project_path / ".proofloom" / "project.json"
            self.assertEqual(
                {
                    "schema_version": "1",
                    "project": {"name": "Portable Project"},
                },
                json.loads(metadata_path.read_text(encoding="utf-8")),
            )
            self.assertEqual(
                "# ProofLoom managed project data\n.proofloom/\n",
                (project_path / ".gitignore").read_text(encoding="utf-8"),
            )

            with RunningApplication(Path(root)) as restarted_app:
                home = open_page(restarted_app, "/")
                directory_browser = open_page(
                    restarted_app,
                    link_from(home, "Choose a project directory"),
                )
                selected_directory = open_page(
                    restarted_app,
                    link_from(
                        open_page(
                            restarted_app,
                            link_from(directory_browser, "portable-project"),
                        ),
                        "Use this directory",
                    ),
                )
                response = submit_form(
                    restarted_app,
                    "/projects/open",
                    csrf_token=hidden_field(selected_directory, "csrf_token"),
                    directory=hidden_field(selected_directory, "directory"),
                )
                page = response.read().decode()
                self.assertIn("Portable Project", page)
                self.assertIn(str(project_path), page)

    def test_creation_preserves_existing_gitignore_and_ignores_managed_data(self):
        with tempfile.TemporaryDirectory() as root:
            project_path = Path(root) / "existing-project"
            project_path.mkdir()
            gitignore = project_path / ".gitignore"
            gitignore.write_text("notes.tmp\n", encoding="utf-8")

            with RunningApplication(Path(root)) as app:
                home = open_page(app, "/")
                submit_form(
                    app,
                    "/projects/create",
                    csrf_token=hidden_field(home, "csrf_token"),
                    directory=str(project_path),
                    name="Existing Project",
                ).read()

            self.assertEqual(
                "notes.tmp\n\n# ProofLoom managed project data\n.proofloom/\n",
                gitignore.read_text(encoding="utf-8"),
            )

    def test_post_rejects_tampered_path_outside_browse_root(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / "escaped-project"
            existing = Path(outside) / "existing-project"
            (existing / ".proofloom").mkdir(parents=True)
            (existing / ".proofloom" / "project.json").write_text(
                '{"schema_version":"1","project":{"name":"Outside"}}',
                encoding="utf-8",
            )
            with RunningApplication(Path(root)) as app:
                token = hidden_field(open_page(app, "/"), "csrf_token")
                with self.assertRaises(urllib.error.HTTPError) as error:
                    submit_form(
                        app,
                        "/projects/create",
                        csrf_token=token,
                        directory=str(target),
                        name="Escaped Project",
                    )
                self.assertEqual(400, error.exception.code)
                with self.assertRaises(urllib.error.HTTPError) as error:
                    submit_form(
                        app,
                        "/projects/open",
                        csrf_token=token,
                        directory=str(existing),
                    )
                self.assertEqual(400, error.exception.code)
            self.assertFalse(target.exists())

    def test_post_requires_csrf_token(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "cross-site-project"
            with RunningApplication(Path(root)) as app:
                with self.assertRaises(urllib.error.HTTPError) as error:
                    submit_form(
                        app,
                        "/projects/create",
                        directory=str(target),
                        name="Cross-site Project",
                    )
                self.assertEqual(403, error.exception.code)
            self.assertFalse(target.exists())

    def test_directory_browser_creates_and_selects_new_directory(self):
        with tempfile.TemporaryDirectory() as root:
            with RunningApplication(Path(root)) as app:
                browser = open_page(app, link_from(open_page(app, "/"), "Choose a directory"))
                created = submit_form(
                    app,
                    "/directories/create",
                    csrf_token=hidden_field(browser, "csrf_token"),
                    parent=str(root),
                    purpose="create",
                    name="new-project",
                ).read().decode()
                self.assertEqual(str(Path(root) / "new-project"), hidden_field(created, "directory"))

                with self.assertRaises(urllib.error.HTTPError) as error:
                    submit_form(
                        app,
                        "/directories/create",
                        csrf_token=hidden_field(browser, "csrf_token"),
                        parent=str(root),
                        purpose="create",
                        name="../escape",
                    )
                self.assertEqual(400, error.exception.code)
            self.assertTrue((Path(root) / "new-project").is_dir())
            self.assertFalse((Path(root).parent / "escape").exists())

    def test_server_rejects_non_loopback_binding(self):
        with self.assertRaises(ValueError):
            create_server("0.0.0.0", 0, browse_root=Path.home())


if __name__ == "__main__":
    unittest.main()
