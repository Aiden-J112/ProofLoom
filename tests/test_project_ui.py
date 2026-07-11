import html
import json
import re
import tempfile
import threading
import unittest
import urllib.parse
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
                directory = re.search(
                    r'<input type="hidden" name="directory" value="([^"]+)">',
                    selected_directory,
                )
                self.assertIsNotNone(directory)
                response = submit_form(
                    app,
                    "/projects/create",
                    directory=html.unescape(directory.group(1)),
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
                directory = re.search(
                    r'<input type="hidden" name="directory" value="([^"]+)">',
                    selected_directory,
                )
                self.assertIsNotNone(directory)
                response = submit_form(
                    restarted_app,
                    "/projects/open",
                    directory=html.unescape(directory.group(1)),
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
                submit_form(
                    app,
                    "/projects/create",
                    directory=str(project_path),
                    name="Existing Project",
                ).read()

            self.assertEqual(
                "notes.tmp\n\n# ProofLoom managed project data\n.proofloom/\n",
                gitignore.read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
