from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path

from jsonschema import Draft202012Validator

from proofloom.assertions import validate_candidates
from proofloom.entities import EntityDictionaryError, load_dictionary
from proofloom.reviews import (
    ReviewError,
    current_assertion_status,
    fold_status,
    load_events,
    replacement_assertions,
    resolve_assertion_evidence,
)

_GRAPH_SCHEMA = json.loads(
    files("proofloom").joinpath("schemas/query-graph.schema.json").read_text(
        encoding="utf-8"
    )
)
Draft202012Validator.check_schema(_GRAPH_SCHEMA)
_GRAPH_VALIDATOR = Draft202012Validator(_GRAPH_SCHEMA)
_SOURCE_FRAGMENT_SCHEMA = json.loads(
    files("proofloom").joinpath("schemas/source-fragment.schema.json").read_text(
        encoding="utf-8"
    )
)
Draft202012Validator.check_schema(_SOURCE_FRAGMENT_SCHEMA)
_SOURCE_FRAGMENT_VALIDATOR = Draft202012Validator(_SOURCE_FRAGMENT_SCHEMA)


class ReleaseIntegrityError(ValueError):
    """A public release check failed for a persisted Knowledge Project."""


def check_project_integrity(project_path: Path) -> dict[str, int]:
    """Verify persisted graph edges against the governed local source of truth."""
    metadata = project_path.expanduser().resolve() / ".proofloom"
    if not (metadata / "project.json").is_file():
        raise ReleaseIntegrityError("Knowledge Project does not exist")
    try:
        graph = _read_json(metadata / "query-graph.json", "Query Graph")
        candidates = _read_object_list(
            metadata / "candidate-assertions.json", "Candidate Assertions"
        )
        fragments = _read_object_list(
            metadata / "source-fragments.json", "Source Fragments"
        )
        dictionary = load_dictionary(metadata / "entity-dictionary.json")
        events = load_events(metadata / "review-events")
    except (EntityDictionaryError, ReviewError, OSError, json.JSONDecodeError) as error:
        raise ReleaseIntegrityError(str(error)) from error

    graph_errors = sorted(
        _GRAPH_VALIDATOR.iter_errors(graph),
        key=lambda error: tuple(map(str, error.absolute_path)),
    )
    if graph_errors:
        error = graph_errors[0]
        field = ".".join(map(str, error.absolute_path)) or "$"
        raise ReleaseIntegrityError(
            f"Query Graph schema error at {field}: {error.message}"
        )
    assert isinstance(graph, dict)
    edges = graph["edges"]
    assert isinstance(edges, list)

    assertions = [*candidates, *replacement_assertions(events)]
    validation = validate_candidates(assertions, dictionary, fragments)
    assertions_by_id: dict[str, list[tuple[dict[str, object], dict[str, object]]]] = {}
    for assertion, result in zip(assertions, validation):
        assertion_id = assertion.get("id")
        if isinstance(assertion_id, str):
            assertions_by_id.setdefault(assertion_id, []).append((assertion, result))
    fragments_by_id: dict[object, list[dict[str, object]]] = {}
    for fragment in fragments:
        fragments_by_id.setdefault(fragment.get("id"), []).append(fragment)

    for index, edge in enumerate(edges):
        assert isinstance(edge, dict)
        assertion_id = str(edge["assertion_id"])
        matches = assertions_by_id.get(assertion_id, [])
        if len(matches) != 1:
            raise ReleaseIntegrityError(
                f"Graph edge {index} assertion_id {assertion_id!r} does not resolve "
                "exactly once in the Assertion Ledger"
            )
        assertion, result = matches[0]
        if any(
            event.get("assertion_id") == assertion_id
            and event.get("action") == "replace"
            for event in events
        ):
            raise ReleaseIntegrityError(
                f"Graph edge {index} references replaced assertion {assertion_id}"
            )
        ledger_status = fold_status(assertion_id, events)
        if ledger_status != "accepted":
            raise ReleaseIntegrityError(
                f"Graph edge {index} references {ledger_status} assertion {assertion_id}"
            )
        if not result["valid"]:
            reasons = result["reasons"]
            assert isinstance(reasons, list)
            details = "; ".join(
                f"{reason['field']}: {reason['reason']}"
                for reason in reasons
                if isinstance(reason, dict)
            )
            prefix = (
                "missing evidence"
                if any(
                    isinstance(reason, dict)
                    and reason.get("rule") == "evidence"
                    for reason in reasons
                )
                else "invalid assertion"
            )
            raise ReleaseIntegrityError(
                f"Graph edge {index} has {prefix} for assertion {assertion_id}: {details}"
            )
        supporting = assertion.get("supporting_evidence_ids")
        assert isinstance(supporting, list)
        for evidence_id in [assertion.get("primary_evidence_id"), *supporting]:
            source_matches = fragments_by_id.get(evidence_id, [])
            if len(source_matches) != 1:
                raise ReleaseIntegrityError(
                    f"Graph edge {index} evidence {evidence_id!r} does not resolve "
                    "exactly once to a Source Fragment"
                )
            source_errors = sorted(
                _SOURCE_FRAGMENT_VALIDATOR.iter_errors(source_matches[0]),
                key=lambda error: tuple(map(str, error.absolute_path)),
            )
            if source_errors:
                error = source_errors[0]
                field = ".".join(map(str, error.absolute_path)) or "$"
                raise ReleaseIntegrityError(
                    f"Graph edge {index} Source Fragment {evidence_id!r} schema "
                    f"error at {field}: {error.message}"
                )
        try:
            current_status = current_assertion_status(
                assertion_id, candidates, events, fragments
            )
        except ReviewError as error:
            raise ReleaseIntegrityError(str(error)) from error
        if current_status != "accepted":
            raise ReleaseIntegrityError(
                f"Graph edge {index} references {current_status} assertion {assertion_id}"
            )
        expected = {
            "source": assertion.get("subject_id"),
            "type": assertion.get("predicate"),
            "target": assertion.get("object_id"),
        }
        for field, value in expected.items():
            if edge.get(field) != value:
                raise ReleaseIntegrityError(
                    f"Graph edge {index} field {field} does not match Assertion Ledger "
                    f"assertion {assertion_id}"
                )
        try:
            _, evidence = resolve_assertion_evidence(
                assertion_id, candidates, events, fragments
            )
        except ReviewError as error:
            raise ReleaseIntegrityError(str(error)) from error
        if not evidence or any(
            reference.get("source_file") == "missing"
            or not isinstance(reference.get("source_file"), str)
            or not str(reference.get("source_file", "")).strip()
            or not isinstance(reference.get("heading_path"), list)
            or not isinstance(reference.get("content"), str)
            or not str(reference.get("content", "")).strip()
            for reference in evidence
        ):
            raise ReleaseIntegrityError(
                f"Graph edge {index} has missing evidence passage for assertion {assertion_id}"
            )
    return {"checked_edges": len(edges)}


def _read_json(path: Path, label: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ReleaseIntegrityError(f"{label} does not exist; project the graph first") from error
    except (OSError, json.JSONDecodeError) as error:
        raise ReleaseIntegrityError(f"Cannot read {label}: {error}") from error


def _read_object_list(path: Path, label: str) -> list[dict[str, object]]:
    value = _read_json(path, label)
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ReleaseIntegrityError(f"Stored {label} must be a list of objects")
    return value
