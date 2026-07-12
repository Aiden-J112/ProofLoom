from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from typing import Callable

from jsonschema import Draft202012Validator, FormatChecker

from proofloom.assertions import validate_candidates, write_json_atomic

SCHEMA_VERSION = "1"
_EVENT_SCHEMA = json.loads(files("proofloom").joinpath("schemas/review-event.schema.json").read_text(encoding="utf-8"))
Draft202012Validator.check_schema(_EVENT_SCHEMA)
_EVENT_VALIDATOR = Draft202012Validator(_EVENT_SCHEMA, format_checker=FormatChecker())
_LEDGER_SCHEMA = json.loads(files("proofloom").joinpath("schemas/ledger-assertion.schema.json").read_text(encoding="utf-8"))
Draft202012Validator.check_schema(_LEDGER_SCHEMA)
_LEDGER_VALIDATOR = Draft202012Validator(_LEDGER_SCHEMA, format_checker=FormatChecker())


class ReviewError(ValueError):
    pass


def load_events(directory: Path) -> list[dict[str, object]]:
    if not directory.exists():
        return []
    events = []
    for path in sorted(directory.glob("*.json")):
        event = json.loads(path.read_text(encoding="utf-8"))
        errors = list(_EVENT_VALIDATOR.iter_errors(event))
        if errors:
            raise ReviewError(f"Invalid Review Event {path.name}: {errors[0].message}")
        events.append(event)
    return sorted(events, key=lambda item: (str(item["reviewed_at"]), str(item["id"])))


def fold_status(assertion_id: str, events: list[dict[str, object]]) -> str:
    status = "candidate"
    for event in events:
        if event.get("assertion_id") != assertion_id:
            continue
        action = event.get("action")
        status = {"accept": "accepted", "reject": "rejected", "replace": "rejected", "needs_domain_review": "needs_domain_review"}.get(str(action), status)
    return status


def append_event(directory: Path, event: dict[str, object]) -> None:
    errors = list(_EVENT_VALIDATOR.iter_errors(event))
    if errors:
        raise ReviewError(errors[0].message)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{event['id']}.json"
    descriptor, temporary = tempfile.mkstemp(dir=directory, prefix=".review-", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            json.dump(event, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.link(temporary, destination)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def review(
    action: str,
    assertion_id: str,
    candidates: list[dict[str, object]],
    ledger: list[dict[str, object]],
    events_directory: Path,
    ledger_path: Path,
    dictionary: dict[str, object],
    fragments: list[dict[str, object]],
    replacement_fields: dict[str, str] | None = None,
    note: str | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> tuple[list[dict[str, object]], dict[str, object]]:
    if action not in {"accept", "reject", "replace", "needs_domain_review"}:
        raise ReviewError("action: choose accept, reject, replace, or needs_domain_review")
    assertions = [*candidates, *ledger]
    original = next((item for item in assertions if item.get("id") == assertion_id), None)
    if original is None:
        raise ReviewError("assertion_id: Candidate Assertion does not exist")
    replacement = None
    previous_ledger = list(ledger)
    if action == "replace":
        values = replacement_fields or {}
        semantic = {field: values.get(field, "").strip() for field in ("subject_id", "predicate", "object_id")}
        if not all(semantic.values()):
            raise ReviewError("subject_id, predicate, and object_id are required for replacement")
        if all(semantic[field] == original[field] for field in semantic):
            raise ReviewError("replacement must change subject_id, predicate, or object_id")
        replacement = dict(original)
        replacement.update(semantic)
        replacement["id"] = f"ast_replacement_{uuid.uuid4().hex}"
        replacement["replaces_assertion_id"] = assertion_id
        schema_errors = sorted(_LEDGER_VALIDATOR.iter_errors(replacement), key=lambda error: tuple(map(str, error.absolute_path)))
        if schema_errors:
            error = schema_errors[0]
            field = ".".join(map(str, error.absolute_path)) or "$"
            raise ReviewError(f"{field}: {error.message}")
        validation_input = dict(replacement)
        validation_input.pop("replaces_assertion_id")
        result = validate_candidates([validation_input], dictionary, fragments)[0]
        if not result["valid"]:
            reasons = result["reasons"]
            raise ReviewError("; ".join(f"{item['field']}: {item['reason']}" for item in reasons))
        ledger = [*ledger, replacement]
        write_json_atomic(ledger_path, ledger)
    now = clock().astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    event = {
        "id": f"rev_{uuid.uuid4().hex}", "assertion_id": assertion_id, "action": action,
        "reviewer": "local-user", "reviewed_at": now,
        "replacement_assertion_id": replacement["id"] if replacement else None,
        "note": note.strip() if note and note.strip() else None, "schema_version": SCHEMA_VERSION,
    }
    try:
        append_event(events_directory, event)
    except BaseException:
        if replacement is not None:
            if previous_ledger:
                write_json_atomic(ledger_path, previous_ledger)
            else:
                try: ledger_path.unlink()
                except FileNotFoundError: pass
        raise
    return ledger, event
