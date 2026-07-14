from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from typing import Callable

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from proofloom.assertions import validate_candidates

SCHEMA_VERSION = "1"
_EVENT_SCHEMA = json.loads(files("proofloom").joinpath("schemas/review-event.schema.json").read_text(encoding="utf-8"))
_CANDIDATE_SCHEMA = json.loads(files("proofloom").joinpath("schemas/candidate-assertion.schema.json").read_text(encoding="utf-8"))
_SCHEMA_REGISTRY = Registry().with_resource(_CANDIDATE_SCHEMA["$id"], Resource.from_contents(_CANDIDATE_SCHEMA))
Draft202012Validator.check_schema(_EVENT_SCHEMA)
Draft202012Validator.check_schema(_CANDIDATE_SCHEMA)
_EVENT_VALIDATOR = Draft202012Validator(_EVENT_SCHEMA, registry=_SCHEMA_REGISTRY, format_checker=FormatChecker())
_EVENT_FILENAME = re.compile(r"^(?P<sequence>[0-9]{20})\.json$")


class ReviewError(ValueError):
    pass


class ReviewConflict(ReviewError):
    pass


def _field_error(error) -> ReviewError:
    field = ".".join(map(str, error.absolute_path)) or "$"
    return ReviewError(f"{field}: {error.message}")


def _validate_event(event: dict[str, object]) -> None:
    errors = sorted(_EVENT_VALIDATOR.iter_errors(event), key=lambda error: tuple(map(str, error.absolute_path)))
    if errors:
        raise _field_error(errors[0])
    replacement = event.get("replacement_assertion")
    replacement_id = event.get("replacement_assertion_id")
    if isinstance(replacement, dict) and replacement.get("id") != replacement_id:
        raise ReviewError("replacement_assertion_id: must equal replacement_assertion.id")
    if isinstance(replacement, dict) and replacement.get("replaces_assertion_id") != event.get("assertion_id"):
        raise ReviewError("replacement_assertion.replaces_assertion_id: must equal assertion_id")


def load_events(directory: Path) -> list[dict[str, object]]:
    if not directory.exists():
        return []
    events: list[dict[str, object]] = []
    seen: set[int] = set()
    for path in directory.iterdir():
        if path.name.startswith(".review-") and path.suffix == ".tmp":
            continue
        match = _EVENT_FILENAME.fullmatch(path.name)
        if not match or not path.is_file():
            raise ReviewError(f"Review Event filename: unexpected entry {path.name!r}")
        sequence = int(match.group("sequence"))
        event = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(event, dict):
            raise ReviewError(f"Invalid Review Event {path.name}: expected object")
        _validate_event(event)
        if event["sequence"] != sequence:
            raise ReviewError(f"sequence: {event['sequence']} does not match filename {path.name}")
        if sequence in seen:
            raise ReviewError(f"sequence: duplicate value {sequence}")
        seen.add(sequence)
        events.append(event)
    return sorted(events, key=lambda item: int(item["sequence"]))


def replacement_assertions(events: list[dict[str, object]]) -> list[dict[str, object]]:
    return [event["replacement_assertion"] for event in sorted(events, key=lambda item: int(item["sequence"])) if isinstance(event.get("replacement_assertion"), dict)]


def resolve_assertion_evidence(
    assertion_id: str,
    candidates: list[dict[str, object]],
    events: list[dict[str, object]],
    fragments: list[dict[str, object]],
) -> tuple[dict[str, object], list[dict[str, object]]]:
    """Resolve an Assertion Ledger record and its ordered Evidence References."""
    assertion = next(
        (
            item
            for item in [*candidates, *replacement_assertions(events)]
            if item.get("id") == assertion_id
        ),
        None,
    )
    if assertion is None:
        raise ReviewError("assertion_id: assertion is missing from the Assertion Ledger")
    supporting = assertion.get("supporting_evidence_ids")
    if not isinstance(supporting, list):
        raise ReviewError("supporting_evidence_ids: expected an array")
    fragment_by_id = {item.get("id"): item for item in fragments}
    evidence = []
    for index, evidence_id in enumerate(
        [assertion.get("primary_evidence_id"), *supporting]
    ):
        fragment = fragment_by_id.get(evidence_id, {})
        heading_path = fragment.get("heading_path", [])
        evidence.append(
            {
                "role": "primary" if index == 0 else "supporting",
                "evidence_id": evidence_id,
                "source_file": fragment.get("source_file", "missing"),
                "heading_path": heading_path if isinstance(heading_path, list) else [],
                "content": fragment.get("content", ""),
            }
        )
    return assertion, evidence


def fold_status(assertion_id: str, events: list[dict[str, object]]) -> str:
    status = "candidate"
    for event in sorted(events, key=lambda item: int(item["sequence"])):
        if event.get("assertion_id") != assertion_id:
            continue
        action = event.get("action")
        if action == "replace":
            return "rejected"
        status = {"accept": "accepted", "reject": "rejected", "needs_domain_review": "needs_domain_review"}.get(str(action), status)
    return status


def current_assertion_status(
    assertion_id: str,
    candidates: list[dict[str, object]],
    events: list[dict[str, object]],
    fragments: list[dict[str, object]],
) -> str:
    """Fold review history and current evidence into the Assertion Ledger state."""
    status = fold_status(assertion_id, events)
    if status != "accepted":
        return status
    assertion = next(
        (
            item
            for item in [*candidates, *replacement_assertions(events)]
            if item.get("id") == assertion_id
        ),
        None,
    )
    if assertion is None:
        return status
    supporting = assertion.get("supporting_evidence_ids")
    evidence_ids = [assertion.get("primary_evidence_id")]
    if isinstance(supporting, list):
        evidence_ids.extend(supporting)
    fragment_by_id: dict[object, dict[str, object]] = {}
    for fragment in fragments:
        fragment_id = fragment.get("id")
        if fragment_id in fragment_by_id:
            raise ReviewError(f"Source Fragment id: duplicate value {fragment_id!r}")
        fragment_by_id[fragment_id] = fragment
    if any(
        evidence_id not in fragment_by_id
        or fragment_by_id[evidence_id].get("status") == "changed"
        for evidence_id in evidence_ids
    ):
        return "stale"
    return status


def append_event(
    directory: Path,
    event: dict[str, object],
    expected_prior_sequence: int | None = None,
) -> dict[str, object]:
    directory.mkdir(parents=True, exist_ok=True)
    while True:
        existing = load_events(directory)
        current_sequence = int(existing[-1]["sequence"]) if existing else 0
        if expected_prior_sequence is not None and current_sequence != expected_prior_sequence:
            raise ReviewConflict(
                f"sequence: expected prior sequence {expected_prior_sequence}, found {current_sequence}"
            )
        sequence = current_sequence + 1
        persisted = dict(event, sequence=sequence)
        _validate_event(persisted)
        destination = directory / f"{sequence:020d}.json"
        descriptor, temporary = tempfile.mkstemp(dir=directory, prefix=".review-", suffix=".tmp")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
                json.dump(persisted, output, ensure_ascii=False, indent=2)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            try:
                os.link(temporary, destination)
            except FileExistsError:
                continue
            event.clear()
            event.update(persisted)
            return event
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def review(
    action: str,
    assertion_id: str,
    candidates: list[dict[str, object]],
    events_directory: Path,
    dictionary: dict[str, object],
    fragments: list[dict[str, object]],
    replacement_fields: dict[str, str] | None = None,
    note: str | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, object]:
    if action not in {"accept", "reject", "replace", "needs_domain_review"}:
        raise ReviewError("action: choose accept, reject, replace, or needs_domain_review")
    events = load_events(events_directory)
    expected_prior_sequence = int(events[-1]["sequence"]) if events else 0
    assertions = [*candidates, *replacement_assertions(events)]
    original = next((item for item in assertions if item.get("id") == assertion_id), None)
    if original is None:
        raise ReviewError("assertion_id: Candidate Assertion does not exist")
    if original.get("replaces_assertion_id") is not None:
        validation = validate_candidates([original], dictionary, fragments)[0]
        if not validation["valid"]:
            raise ReviewError("; ".join(f"replacement_assertion.{item['field']}: {item['reason']}" for item in validation["reasons"]))
    if any(event.get("assertion_id") == assertion_id and event.get("action") == "replace" for event in events):
        raise ReviewConflict("assertion_id: replaced assertion is terminal")
    replacement = None
    if action == "replace":
        values = replacement_fields or {}
        semantic = {field: values.get(field, "").strip() for field in ("subject_id", "predicate", "object_id")}
        if not all(semantic.values()):
            raise ReviewError("subject_id, predicate, and object_id are required for replacement")
        if all(semantic[field] == original[field] for field in semantic):
            raise ReviewError("replacement_assertion: subject_id, predicate, or object_id must change")
        replacement = dict(original)
        replacement.update(semantic)
        replacement["id"] = f"ast_replacement_{uuid.uuid4().hex}"
        replacement["replaces_assertion_id"] = assertion_id
        result = validate_candidates([replacement], dictionary, fragments)[0]
        if not result["valid"]:
            raise ReviewError("; ".join(f"replacement_assertion.{item['field']}: {item['reason']}" for item in result["reasons"]))
    now = clock().astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    event = {
        "id": f"rev_{uuid.uuid4().hex}", "assertion_id": assertion_id, "action": action,
        "reviewer": "local-user", "reviewed_at": now,
        "replacement_assertion_id": replacement["id"] if replacement else None,
        "replacement_assertion": replacement,
        "note": note.strip() if note and note.strip() else None, "schema_version": SCHEMA_VERSION,
    }
    return append_event(
        events_directory,
        event,
        expected_prior_sequence=expected_prior_sequence,
    )
