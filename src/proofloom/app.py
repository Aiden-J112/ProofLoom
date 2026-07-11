from __future__ import annotations

import argparse
import html
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

METADATA_DIRECTORY = ".proofloom"
METADATA_FILE = "project.json"
PROJECT_SCHEMA_VERSION = "1"


def _page(content: str) -> bytes:
    return f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>ProofLoom</title></head>
<body>
  <main>
    <h1>ProofLoom</h1>
    {content}
  </main>
</body>
</html>
""".encode("utf-8")


def _home_page() -> bytes:
    return _page("""
<section>
  <h2>Create a Knowledge Project</h2>
  <form method="post" action="/projects/create">
    <label>Project name <input name="name" required></label>
    <label>Local directory <input name="directory" required></label>
    <button type="submit">Create project</button>
  </form>
</section>
<section>
  <h2>Open a Knowledge Project</h2>
  <form method="post" action="/projects/open">
    <label>Local directory <input name="directory" required></label>
    <button type="submit">Open project</button>
  </form>
</section>
""")


def _project_page(project_path: Path, metadata: dict[str, object]) -> bytes:
    project = metadata["project"]
    assert isinstance(project, dict)
    return _page(
        "<h2>Knowledge Project</h2>"
        f"<p>Name: {html.escape(str(project['name']))}</p>"
        f"<p>Directory: {html.escape(str(project_path))}</p>"
        f"<p>Schema version: {html.escape(str(metadata['schema_version']))}</p>"
    )


def _metadata_path(project_path: Path) -> Path:
    return project_path / METADATA_DIRECTORY / METADATA_FILE


class ProofLoomRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path != "/":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self._send_page(_home_page())

    def do_POST(self) -> None:
        form = self._read_form()
        if self.path == "/projects/create":
            self._create_project(form)
        elif self.path == "/projects/open":
            self._open_project(form)
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def _read_form(self) -> dict[str, str]:
        length = int(self.headers.get("Content-Length", "0"))
        values = parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
        return {key: items[0] for key, items in values.items()}

    def _create_project(self, form: dict[str, str]) -> None:
        name = form.get("name", "").strip()
        directory = form.get("directory", "").strip()
        if not name or not directory:
            self.send_error(HTTPStatus.BAD_REQUEST, "Project name and directory are required")
            return

        project_path = Path(directory).expanduser().resolve()
        metadata_path = _metadata_path(project_path)
        if metadata_path.exists():
            self.send_error(HTTPStatus.CONFLICT, "A Knowledge Project already exists there")
            return

        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        metadata = {
            "schema_version": PROJECT_SCHEMA_VERSION,
            "project": {"name": name},
        }
        metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        project_gitignore = project_path / ".gitignore"
        if not project_gitignore.exists():
            project_gitignore.write_text(
                "*\n!.gitignore\n",
                encoding="utf-8",
            )
        self._send_page(_project_page(project_path, metadata), HTTPStatus.CREATED)

    def _open_project(self, form: dict[str, str]) -> None:
        directory = form.get("directory", "").strip()
        if not directory:
            self.send_error(HTTPStatus.BAD_REQUEST, "Project directory is required")
            return

        project_path = Path(directory).expanduser().resolve()
        metadata_path = _metadata_path(project_path)
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if metadata.get("schema_version") != PROJECT_SCHEMA_VERSION:
                raise ValueError("Unsupported project schema version")
            if not isinstance(metadata.get("project", {}).get("name"), str):
                raise ValueError("Project name is missing")
        except (OSError, json.JSONDecodeError, ValueError, AttributeError) as error:
            self.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return

        self._send_page(_project_page(project_path, metadata))

    def _send_page(self, body: bytes, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def create_server(host: str = "127.0.0.1", port: int = 8000) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), ProofLoomRequestHandler)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local ProofLoom interface")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    server = create_server(args.host, args.port)
    print(f"ProofLoom is available at http://{args.host}:{server.server_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
