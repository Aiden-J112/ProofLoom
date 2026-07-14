from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path

from jsonschema import Draft202012Validator

from proofloom.assertions import validate_candidates, write_json_atomic
from proofloom.entities import load_dictionary
from proofloom.reviews import current_assertion_status, load_events, replacement_assertions

QUERY_GRAPH_SCHEMA_VERSION = "1"

_SCHEMA = json.loads(
    files("proofloom").joinpath("schemas/query-graph.schema.json").read_text(
        encoding="utf-8"
    )
)
Draft202012Validator.check_schema(_SCHEMA)
_VALIDATOR = Draft202012Validator(_SCHEMA)


class GraphProjectionError(ValueError):
    """A safe, user-facing Query Graph projection failure."""


def project_query_graph(project_path: Path) -> dict[str, object]:
    """Derive and atomically persist the Query Graph for a Knowledge Project."""
    metadata = project_path / ".proofloom"
    if not (metadata / "project.json").is_file():
        raise GraphProjectionError("Knowledge Project does not exist")
    dictionary = load_dictionary(metadata / "entity-dictionary.json")
    candidates = _read_list(metadata / "candidate-assertions.json", "Candidate Assertions")
    fragments = _read_list(metadata / "source-fragments.json", "Source Fragments")
    events = load_events(metadata / "review-events")
    assertions = [*candidates, *replacement_assertions(events)]
    validation = validate_candidates(assertions, dictionary, fragments)
    fragment_by_id = {
        fragment.get("id"): fragment
        for fragment in fragments
        if isinstance(fragment.get("id"), str)
    }

    entities = dictionary["entities"]
    assert isinstance(entities, list)
    nodes = sorted(
        (
            {
                "id": entity["id"],
                "type": entity["type"],
                "name": entity["canonical_name"],
            }
            for entity in entities
        ),
        key=lambda node: str(node["id"]),
    )
    edges = []
    for assertion, result in zip(assertions, validation):
        if (
            not result["valid"]
            or current_assertion_status(
                str(assertion.get("id", "")), candidates, events, fragments
            ) != "accepted"
            or not _has_traceable_evidence(assertion, fragment_by_id)
        ):
            continue
        edges.append(
            {
                "source": assertion["subject_id"],
                "type": assertion["predicate"],
                "target": assertion["object_id"],
                "assertion_id": assertion["id"],
            }
        )
    edges.sort(
        key=lambda edge: (
            str(edge["source"]),
            str(edge["type"]),
            str(edge["target"]),
            str(edge["assertion_id"]),
        )
    )
    graph: dict[str, object] = {
        "schema_version": QUERY_GRAPH_SCHEMA_VERSION,
        "nodes": nodes,
        "edges": edges,
    }
    errors = sorted(
        _VALIDATOR.iter_errors(graph),
        key=lambda error: tuple(map(str, error.absolute_path)),
    )
    if errors:
        error = errors[0]
        field = ".".join(map(str, error.absolute_path)) or "$"
        raise GraphProjectionError(f"Query Graph schema error at {field}: {error.message}")
    write_json_atomic(metadata / "query-graph.json", graph)
    return graph


def _has_traceable_evidence(
    assertion: dict[str, object],
    fragment_by_id: dict[object, dict[str, object]],
) -> bool:
    supporting = assertion.get("supporting_evidence_ids")
    if not isinstance(supporting, list):
        return False
    evidence_ids = [assertion.get("primary_evidence_id"), *supporting]
    for evidence_id in evidence_ids:
        fragment = fragment_by_id.get(evidence_id)
        if not isinstance(fragment, dict):
            return False
        source_file = fragment.get("source_file")
        heading_path = fragment.get("heading_path")
        content = fragment.get("content")
        if (
            not isinstance(source_file, str)
            or not source_file.strip()
            or not isinstance(heading_path, list)
            or not all(isinstance(part, str) for part in heading_path)
            or not isinstance(content, str)
        ):
            return False
    return True


def _read_list(path: Path, label: str) -> list[dict[str, object]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError) as error:
        raise GraphProjectionError(f"Cannot read {label}: {error}") from error
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise GraphProjectionError(f"Stored {label} must be a list of objects")
    return data
