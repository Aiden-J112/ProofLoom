from __future__ import annotations

import argparse
import html
import ipaddress
import json
import secrets
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from proofloom.assertions import FixtureExtractor, validate_candidates, write_extraction_results
from proofloom.entities import (
    ENTITY_TYPES,
    EntityConflictError,
    EntityDictionaryError,
    accept_candidate,
    load_dictionary,
    submit_candidate,
    update_entity,
    write_dictionary,
)
from proofloom.sources import (
    SourceImportError,
    import_markdown,
    merge_source_fragments,
    write_source_fragments,
)

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


def _csrf_field(token: str) -> str:
    return f'<input type="hidden" name="csrf_token" value="{html.escape(token)}">'


def _directory_field(purpose: str, selected: str | None) -> str:
    if selected:
        return (
            f'<p>Selected directory: {html.escape(selected)}</p>'
            f'<input type="hidden" name="directory" value="{html.escape(selected)}">'
            f'<a href="/directories?purpose={purpose}">Choose another directory</a>'
        )
    label = "Choose a directory" if purpose == "create" else "Choose a project directory"
    return f'<a href="/directories?purpose={purpose}">{label}</a>'


def _home_page(
    csrf_token: str,
    purpose: str | None = None,
    selected: str | None = None,
) -> bytes:
    create_directory = selected if purpose == "create" else None
    open_directory = selected if purpose == "open" else None
    return _page(f"""
<section>
  <h2>Create a Knowledge Project</h2>
  <form method="post" action="/projects/create">
    {_csrf_field(csrf_token)}
    <label>Project name <input name="name" required></label>
    {_directory_field("create", create_directory)}
    <button type="submit">Create project</button>
  </form>
</section>
<section>
  <h2>Open a Knowledge Project</h2>
  <form method="post" action="/projects/open">
    {_csrf_field(csrf_token)}
    {_directory_field("open", open_directory)}
    <button type="submit">Open project</button>
  </form>
</section>
""")


def _directory_page(root: Path, current: Path, purpose: str, csrf_token: str) -> bytes:
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
        '<h3>Create a new directory here</h3>'
        '<form method="post" action="/directories/create">'
        f'{_csrf_field(csrf_token)}'
        f'<input type="hidden" name="parent" value="{html.escape(str(current))}">'
        f'<input type="hidden" name="purpose" value="{purpose}">'
        '<label>Directory name <input name="name" required></label>'
        '<button type="submit">Create and select</button></form>'
    )


def _fragments_path(project_path: Path) -> Path:
    return project_path / METADATA_DIRECTORY / "source-fragments.json"


def _entities_path(project_path: Path) -> Path:
    return project_path / METADATA_DIRECTORY / "entity-dictionary.json"


def _assertions_path(project_path: Path) -> Path:
    return project_path / METADATA_DIRECTORY / "candidate-assertions.json"


def _validation_path(project_path: Path) -> Path:
    return project_path / METADATA_DIRECTORY / "assertion-validation.json"


def _assertion_page(project_path: Path, csrf_token: str) -> bytes:
    def load(path: Path, default):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default
    candidates = load(_assertions_path(project_path), [])
    validation = load(_validation_path(project_path), [])
    fragments = load(_fragments_path(project_path), [])
    fragment_by_id = {item.get("id"): item for item in fragments if isinstance(item, dict)}
    cards = []
    for candidate in candidates:
        evidence_ids = [candidate["primary_evidence_id"], *candidate["supporting_evidence_ids"]]
        evidence = []
        for evidence_id in evidence_ids:
            fragment = fragment_by_id.get(evidence_id, {})
            evidence.append(
                f"<li>{html.escape(str(evidence_id))}: {html.escape(str(fragment.get('source_file', 'missing')))} — "
                f"{html.escape(' / '.join(map(str, fragment.get('heading_path', []))))}<pre>{html.escape(str(fragment.get('content', '')))}</pre></li>"
            )
        extraction = candidate["extraction"]
        cards.append(
            "<article>"
            f"<h3>{html.escape(str(candidate['id']))}</h3>"
            f"<p>{html.escape(str(candidate['subject_id']))} — {html.escape(str(candidate['predicate']))} → {html.escape(str(candidate['object_id']))}</p>"
            f"<p>provider={html.escape(str(extraction['provider']))}; model={html.escape(str(extraction['model']))}; prompt_version={html.escape(str(extraction['prompt_version']))}; schema_version={html.escape(str(extraction['schema_version']))}; generated_at={html.escape(str(extraction['generated_at']))}; mode={html.escape(str(extraction['mode']))}</p>"
            f"<h4>Evidence References</h4><ul>{''.join(evidence)}</ul></article>"
        )
    validation_items = []
    for item in validation:
        outcome = "valid"
        if not item.get("valid"):
            outcome = "; ".join(
                f"{reason['field']}: {reason['reason']}"
                for reason in item.get("reasons", [])
            )
        validation_items.append(
            f"<li>{html.escape(str(item.get('candidate_id')))}: {html.escape(outcome)}</li>"
        )
    validation_html = "".join(validation_items)
    return _page(
        "<h2>Candidate Assertions</h2>"
        '<form method="post" action="/assertions/extract-fixture">'
        f"{_csrf_field(csrf_token)}<input type=\"hidden\" name=\"project\" value=\"{html.escape(str(project_path))}\">"
        "<button type=\"submit\">Run offline synthetic fixture extraction</button></form>"
        f"{''.join(cards) or '<p>No valid Candidate Assertions.</p>'}"
        f"<h2>Validation output</h2><ul>{validation_html or '<li>Not run.</li>'}</ul>"
    )


def _entity_dictionary_page(
    project_path: Path, dictionary: dict[str, object], csrf_token: str
) -> bytes:
    project = html.escape(str(project_path))
    accepted = "".join(
        "<article>"
        f"<h4>{html.escape(str(entity['canonical_name']))}</h4>"
        f"<p>ID: {html.escape(str(entity['id']))}</p>"
        f"<p>Type: {html.escape(str(entity['type']))}</p><p>Status: accepted</p>"
        '<form method="post" action="/entities/update">'
        f"{_csrf_field(csrf_token)}"
        f'<input type="hidden" name="project" value="{project}">'
        f'<input type="hidden" name="entity_id" value="{html.escape(str(entity["id"]))}">'
        f'<label>Display name <input name="display_name" value="{html.escape(str(entity["canonical_name"]))}" required></label>'
        f'<label>Aliases (comma separated) <input name="aliases" value="{html.escape(", ".join(map(str, entity["aliases"]))) }"></label>'
        '<button type="submit">Update entity</button></form></article>'
        for entity in dictionary["entities"]
    )
    options = "".join(f'<option value="{kind}">{kind}</option>' for kind in ENTITY_TYPES)
    candidates = "".join(
        "<article>"
        f"<h4>{html.escape(str(candidate['name']))}</h4><p>Status: candidate</p>"
        '<form method="post" action="/entities/accept">'
        f"{_csrf_field(csrf_token)}"
        f'<input type="hidden" name="project" value="{project}">'
        f'<input type="hidden" name="candidate_id" value="{html.escape(str(candidate["id"]))}">'
        f'<label>Controlled type <select name="entity_type">{options}</select></label>'
        '<button type="submit">Accept entity</button></form></article>'
        for candidate in dictionary["candidates"]
    )
    return _page(
        "<h2>Entity Dictionary</h2>"
        '<h3>Submit an unknown name</h3><form method="post" action="/entities/candidates">'
        f"{_csrf_field(csrf_token)}"
        f'<input type="hidden" name="project" value="{project}">'
        '<label>Name <input name="name" required></label>'
        '<button type="submit">Submit candidate</button></form>'
        f"<h3>Candidate entities</h3>{candidates or '<p>No candidate entities.</p>'}"
        f"<h3>Accepted entities</h3>{accepted or '<p>No accepted entities.</p>'}"
    )


def _source_page(
    root: Path,
    current: Path,
    project_path: Path,
    csrf_token: str,
    selected: Path | None = None,
) -> bytes:
    project_query = quote(str(project_path))
    entries = []
    for path in sorted(current.iterdir(), key=lambda item: item.name.lower()):
        if path.is_dir() or (path.is_file() and path.suffix.lower() == ".md"):
            href = f"/sources?project={project_query}&path={quote(str(path))}"
            if path.is_file():
                href += "&select=file"
            entries.append(
                f'<li><a href="{html.escape(href)}">{html.escape(path.name)}</a></li>'
            )
    parent_link = ""
    if current != root:
        parent_link = (
            f'<p><a href="/sources?project={project_query}&path={quote(str(current.parent))}">'
            "Parent directory</a></p>"
        )
    chosen = selected or current
    return _page(
        "<h2>Choose UTF-8 Markdown</h2>"
        f"<p>Current directory: {html.escape(str(current))}</p>"
        f'{parent_link}<ul>{"".join(entries)}</ul>'
        '<form method="post" action="/sources/import">'
        f'{_csrf_field(csrf_token)}'
        f'<input type="hidden" name="project" value="{html.escape(str(project_path))}">'
        f'<input type="hidden" name="source" value="{html.escape(str(chosen))}">'
        f'<p>Selected source: {html.escape(str(chosen))}</p>'
        '<button type="submit">Import selected source</button></form>'
    )


def _project_page(
    project_path: Path,
    metadata: dict[str, object],
    csrf_token: str,
    fragments: list[dict[str, object]] | None = None,
) -> bytes:
    project = metadata["project"]
    assert isinstance(project, dict)
    if fragments is None:
        try:
            loaded = json.loads(_fragments_path(project_path).read_text(encoding="utf-8"))
            fragments = loaded if isinstance(loaded, list) else []
        except (OSError, json.JSONDecodeError):
            fragments = []
    fragment_items = "".join(
        "<article>"
        f"<h4>{html.escape(str(fragment['id']))}</h4>"
        f"<p>{html.escape(str(fragment['source_file']))} — "
        f"{html.escape(' / '.join(str(part) for part in fragment['heading_path']))}</p>"
        f"<p>Ordinal: {fragment['ordinal']} · Kind: {html.escape(str(fragment['kind']))}</p>"
        f"<pre>{html.escape(str(fragment['content']))}</pre>"
        "</article>"
        for fragment in fragments
    )
    return _page(
        "<h2>Knowledge Project</h2>"
        f"<p>Name: {html.escape(str(project['name']))}</p>"
        f"<p>Directory: {html.escape(str(project_path))}</p>"
        f"<p>Schema version: {html.escape(str(metadata['schema_version']))}</p>"
        "<h3>Import Markdown</h3>"
        '<form method="post" action="/sources/import">'
        f'{_csrf_field(csrf_token)}'
        f'<input type="hidden" name="project" value="{html.escape(str(project_path))}">'
        '<label>UTF-8 Markdown file or directory '
        '<input name="source" required></label>'
        '<button type="submit">Import</button></form>'
        f'<p><a href="/sources?project={quote(str(project_path))}">Choose Markdown source</a></p>'
        f'<p><a href="/entities?project={quote(str(project_path))}">Review Entity Dictionary</a></p>'
        f'<p><a href="/assertions?project={quote(str(project_path))}">Extract Candidate Assertions</a></p>'
        f"<h3>Source Fragments</h3>{fragment_items or '<p>No Source Fragments imported.</p>'}"
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
                    self.server.csrf_token,
                    query.get("purpose", [None])[0],
                    query.get("directory", [None])[0],
                )
            )
        elif request.path == "/directories":
            self._browse_directories(query)
        elif request.path == "/sources":
            self._browse_sources(query)
        elif request.path == "/entities":
            self._show_entities(query)
        elif request.path == "/assertions":
            self._show_assertions(query)
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        form = self._read_form()
        if not secrets.compare_digest(
            form.get("csrf_token", ""),
            self.server.csrf_token,
        ):
            self.send_error(HTTPStatus.FORBIDDEN, "Invalid CSRF token")
        elif self.path == "/projects/create":
            self._create_project(form)
        elif self.path == "/projects/open":
            self._open_project(form)
        elif self.path == "/directories/create":
            self._create_directory(form)
        elif self.path == "/sources/import":
            self._import_sources(form)
        elif self.path == "/entities/candidates":
            self._submit_entity_candidate(form)
        elif self.path == "/entities/accept":
            self._accept_entity(form)
        elif self.path == "/entities/update":
            self._update_entity(form)
        elif self.path == "/assertions/extract-fixture":
            self._extract_fixture(form)
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
        self._send_page(
            _directory_page(root, current, purpose, self.server.csrf_token)
        )

    def _local_path(self, raw_path: str) -> Path:
        path = Path(raw_path).expanduser().resolve()
        if not path.is_relative_to(self.server.browse_root):
            raise ValueError("Directory is outside the local browsing root")
        return path

    def _browse_sources(self, query: dict[str, list[str]]) -> None:
        try:
            project_path = self._local_path(query.get("project", [""])[0])
            if not _metadata_path(project_path).is_file():
                raise ValueError("Knowledge Project does not exist")
            chosen = self._local_path(
                query.get("path", [str(self.server.browse_root)])[0]
            )
            selected = chosen if query.get("select", [""])[0] == "file" else None
            current = chosen.parent if selected else chosen
            if selected and selected.suffix.lower() != ".md":
                raise ValueError("Select a Markdown (.md) file")
            if not current.is_dir():
                raise ValueError("Source directory does not exist")
        except ValueError as error:
            self.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        self._send_page(
            _source_page(
                self.server.browse_root,
                current,
                project_path,
                self.server.csrf_token,
                selected,
            )
        )

    def _entity_project(self, raw_path: str) -> Path:
        project_path = self._local_path(raw_path)
        if not _metadata_path(project_path).is_file():
            raise EntityDictionaryError("Knowledge Project does not exist")
        return project_path

    def _show_entities(self, query: dict[str, list[str]]) -> None:
        try:
            project_path = self._entity_project(query.get("project", [""])[0])
            dictionary = load_dictionary(_entities_path(project_path))
        except (OSError, ValueError, EntityDictionaryError) as error:
            self.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        self._send_page(_entity_dictionary_page(project_path, dictionary, self.server.csrf_token))

    def _show_assertions(self, query: dict[str, list[str]]) -> None:
        try:
            project_path = self._entity_project(query.get("project", [""])[0])
        except (ValueError, EntityDictionaryError) as error:
            self.send_error(HTTPStatus.BAD_REQUEST, str(error)); return
        self._send_page(_assertion_page(project_path, self.server.csrf_token))

    def _extract_fixture(self, form: dict[str, str]) -> None:
        try:
            project_path = self._entity_project(form.get("project", ""))
            dictionary = load_dictionary(_entities_path(project_path))
            fragments = json.loads(_fragments_path(project_path).read_text(encoding="utf-8"))
            if not isinstance(fragments, list):
                raise ValueError("Stored Source Fragments must be a list")
            candidates = self.server.fixture_extractor.extract(dictionary, fragments)
            validation = validate_candidates(candidates, dictionary, fragments)
            valid_indices = {
                item["candidate_index"] for item in validation if item["valid"]
            }
            valid_candidates = [
                item for index, item in enumerate(candidates) if index in valid_indices
            ]
            with self.server.assertion_lock:
                write_extraction_results(
                    _validation_path(project_path),
                    _assertions_path(project_path),
                    validation,
                    valid_candidates,
                )
        except (OSError, json.JSONDecodeError, ValueError, EntityDictionaryError) as error:
            self.send_error(HTTPStatus.BAD_REQUEST, str(error)); return
        self._send_page(_assertion_page(project_path, self.server.csrf_token))

    def _mutate_dictionary(self, form: dict[str, str], operation) -> None:
        try:
            project_path = self._entity_project(form.get("project", ""))
            path = _entities_path(project_path)
            with self.server.entity_dictionary_lock:
                dictionary = load_dictionary(path)
                operation(dictionary)
                write_dictionary(path, dictionary)
        except EntityConflictError as error:
            self.send_error(HTTPStatus.CONFLICT, str(error))
            return
        except (OSError, ValueError, EntityDictionaryError) as error:
            self.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        self._send_page(_entity_dictionary_page(project_path, dictionary, self.server.csrf_token))

    def _submit_entity_candidate(self, form: dict[str, str]) -> None:
        self._mutate_dictionary(form, lambda data: submit_candidate(data, form.get("name", "")))

    def _accept_entity(self, form: dict[str, str]) -> None:
        self._mutate_dictionary(
            form,
            lambda data: accept_candidate(
                data, form.get("candidate_id", ""), form.get("entity_type", "")
            ),
        )

    def _update_entity(self, form: dict[str, str]) -> None:
        aliases = form.get("aliases", "").split(",")
        self._mutate_dictionary(
            form,
            lambda data: update_entity(
                data,
                form.get("entity_id", ""),
                form.get("display_name", ""),
                aliases,
            ),
        )

    def _create_directory(self, form: dict[str, str]) -> None:
        purpose = form.get("purpose", "")
        name = form.get("name", "").strip()
        if purpose not in {"create", "open"}:
            self.send_error(HTTPStatus.BAD_REQUEST, "Unknown directory selection purpose")
            return
        if (
            not name
            or name in {".", ".."}
            or Path(name).name != name
            or "/" in name
            or "\\" in name
        ):
            self.send_error(HTTPStatus.BAD_REQUEST, "Use a single directory name")
            return
        try:
            parent = self._local_path(form.get("parent", ""))
            if not parent.is_dir():
                raise ValueError("Parent directory does not exist")
            created = parent / name
            created.mkdir()
        except (OSError, ValueError) as error:
            self.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        self._send_page(_home_page(self.server.csrf_token, purpose, str(created)))

    def _create_project(self, form: dict[str, str]) -> None:
        name = form.get("name", "").strip()
        directory = form.get("directory", "").strip()
        if not name or not directory:
            self.send_error(HTTPStatus.BAD_REQUEST, "Project name and directory are required")
            return

        try:
            project_path = self._local_path(directory)
        except ValueError as error:
            self.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return
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
        self._send_page(
            _project_page(project_path, metadata, self.server.csrf_token),
            HTTPStatus.CREATED,
        )

    def _open_project(self, form: dict[str, str]) -> None:
        directory = form.get("directory", "").strip()
        if not directory:
            self.send_error(HTTPStatus.BAD_REQUEST, "Project directory is required")
            return

        try:
            project_path = self._local_path(directory)
        except ValueError as error:
            self.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return
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

        self._send_page(_project_page(project_path, metadata, self.server.csrf_token))

    def _import_sources(self, form: dict[str, str]) -> None:
        try:
            project_path = self._local_path(form.get("project", ""))
            source_path = self._local_path(form.get("source", ""))
            metadata = json.loads(_metadata_path(project_path).read_text(encoding="utf-8"))
            if metadata.get("schema_version") != PROJECT_SCHEMA_VERSION:
                raise ValueError("Unsupported project schema version")
            imported = import_markdown(source_path, self.server.browse_root)
            fragments_path = _fragments_path(project_path)
            try:
                existing = json.loads(fragments_path.read_text(encoding="utf-8"))
                if not isinstance(existing, list):
                    raise ValueError("Stored Source Fragments must be a list")
            except FileNotFoundError:
                existing = []
            source_locator = source_path.relative_to(
                self.server.browse_root
            ).as_posix()
            fragments = merge_source_fragments(
                existing,
                imported,
                source_locator,
                source_path.is_dir(),
            )
            write_source_fragments(fragments_path, fragments)
        except (OSError, json.JSONDecodeError, SourceImportError, ValueError) as error:
            self.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        self._send_page(
            _project_page(project_path, metadata, self.server.csrf_token, fragments)
        )

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
        self.csrf_token = secrets.token_urlsafe(32)
        self.entity_dictionary_lock = threading.Lock()
        self.assertion_lock = threading.Lock()
        self.fixture_extractor = FixtureExtractor()
        super().__init__(address, ProofLoomRequestHandler)


def create_server(
    host: str = "127.0.0.1",
    port: int = 8000,
    browse_root: Path | None = None,
) -> ProofLoomServer:
    if host != "localhost" and not ipaddress.ip_address(host).is_loopback:
        raise ValueError("ProofLoom may only bind to a loopback address")
    return ProofLoomServer((host, port), browse_root or Path.home())


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local ProofLoom interface")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--browse-root", type=Path, default=Path.home())
    args = parser.parse_args()
    server = create_server(args.host, args.port, browse_root=args.browse_root)
    print(f"ProofLoom is available at http://{args.host}:{server.server_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
