import json
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
from pathlib import Path

from proofloom.app import create_server


class RunningApplication:
    def __init__(self):
        self.server = create_server("127.0.0.1", 0)
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


class KnowledgeProjectUiTests(unittest.TestCase):
    def test_owner_learner_creates_and_reopens_project_after_restart(self):
        with tempfile.TemporaryDirectory() as root:
            project_path = Path(root) / "portable-project"

            with RunningApplication() as app:
                response = submit_form(
                    app,
                    "/projects/create",
                    directory=str(project_path),
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
                "*\n!.gitignore\n",
                (project_path / ".gitignore").read_text(encoding="utf-8"),
            )

            with RunningApplication() as restarted_app:
                response = submit_form(
                    restarted_app,
                    "/projects/open",
                    directory=str(project_path),
                )
                page = response.read().decode()
                self.assertIn("Portable Project", page)
                self.assertIn(str(project_path), page)


if __name__ == "__main__":
    unittest.main()
