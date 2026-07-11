from __future__ import annotations

import argparse
import html
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

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


def _directory_field(purpose: str, selected: str | None) -> str:
    if selected:
        return (
            f'<p>Selected directory: {html.escape(selected)}</p>'
            f'<input type="hidden" name="directory" value="{html.escape(selected)}">'
            f'<a href="/directories?purpose={purpose}">Choose another directory</a>'
        )
    label = "Choose a directory" if purpose == "create" else "Choose a project directory"
    return f'<a href="/directories?purpose={purpose}">{label}</a>'


def _home_page(purpose: str | None = None, selected: str | None = None) -> bytes:
    create_directory = selected if purpose == "create" else None
    open_directory = selected if purpose == "open" else None
    return _page(f"""
<section>
  <h2>Create a Knowledge Project</h2>
  <form method="post" action="/projects/create">
    <label>Project name <input name="name" required></label>
    {_directory_field("create", create_directory)}
    <button type="submit">Create project</button>
  </form>
</section>
<section>
  <h2>Open a Knowledge Project</h2>
  <form method="post" action="/projects/open">
    {_directory_field("open", open_directory)}
    <button type="submit">Open project</button>
  </form>
</section>
""")


def _directory_page(root: Path, current: Path, purpose: str) -> bytes:
    entries = []
    directories = (item for item in current.iterdir() if item.is_dir())
    for path in sorted(directories, key=lambda item: item.name.lower()):
        href = f"/directories?purpose={purpose}&path={quote(str(path))}"
        entries.append(f'<li><a href="{html.escape(href)}">{html.escape(path.name)}</a></li>')

    parent_link = ""
    if current != root:
        parent = quote(str(current.parent))
        parent_link = (
            f'<p><a href="/directories?purpose={purpose}&path={parent}">'
            "Parent directory</a></p>"
        )
    selected = quote(str(current))
    return _page(
        "<h2>Choose a local directory</h2>"
        f"<p>Current directory: {html.escape(str(current))}</p>"
        f'{parent_link}<ul>{"".join(entries)}</ul>'
        f'<a href="/?purpose={purpose}&directory={selected}">Use this directory</a>'
    )


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
        request = urlparse(self.path)
        query = parse_qs(request.query)
        if request.path == "/":
            self._send_page(
                _home_page(
                    query.get("purpose", [None])[0],
                    query.get("directory", [None])[0],
                )
            )
        elif request.path == "/directories":
            self._browse_directories(query)
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

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

    def _browse_directories(self, query: dict[str, list[str]]) -> None:
        purpose = query.get("purpose", [""])[0]
        if purpose not in {"create", "open"}:
            self.send_error(HTTPStatus.BAD_REQUEST, "Unknown directory selection purpose")
            return
        root = self.server.browse_root
        current = Path(query.get("path", [str(root)])[0]).expanduser().resolve()
        if not current.is_relative_to(root) or not current.is_dir():
            self.send_error(HTTPStatus.BAD_REQUEST, "Directory is outside the local browsing root")
            return
        self._send_page(_directory_page(root, current, purpose))

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
        existing_rules = ""
        if project_gitignore.exists():
            existing_rules = project_gitignore.read_text(encoding="utf-8")
        managed_rule = ".proofloom/"
        if managed_rule not in existing_rules.splitlines():
            separator = "" if not existing_rules or existing_rules.endswith("\n") else "\n"
            comment_separator = "" if not existing_rules else "\n"
            project_gitignore.write_text(
                existing_rules
                + separator
                + comment_separator
                + "# ProofLoom managed project data\n"
                + managed_rule
                + "\n",
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


class ProofLoomServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], browse_root: Path):
        self.browse_root = browse_root.expanduser().resolve()
        super().__init__(address, ProofLoomRequestHandler)


def create_server(
    host: str = "127.0.0.1",
    port: int = 8000,
    browse_root: Path | None = None,
) -> ProofLoomServer:
    return ProofLoomServer((host, port), browse_root or Path.home())


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
