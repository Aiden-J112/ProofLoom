from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from typing import Callable

from jsonschema import Draft202012Validator, FormatChecker

SCHEMA_VERSION = "1"
_SCHEMA = json.loads(files("proofloom").joinpath("schemas/candidate-assertion.schema.json").read_text(encoding="utf-8"))
Draft202012Validator.check_schema(_SCHEMA)
_VALIDATOR = Draft202012Validator(_SCHEMA, format_checker=FormatChecker())
_FIXTURE = json.loads(files("proofloom").joinpath("fixtures/synthetic-extraction.json").read_text(encoding="utf-8"))

TYPE_CONTRACTS = {
    "COMPOSED_OF": {("Concept", "Component")},
    "PROMPTS": {("Artifact", "Component")},
    "CALLS_TOOL": {("Component", "Component")},
    "PRODUCES": {("Component", "Artifact")},
    "VERIFIES": {("Component", "Artifact")},
    "BLOCKS": {("Component", "Artifact")},
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class FixtureExtractor:
    """Deterministic, offline adapter for the bundled synthetic extraction recipe."""

    def __init__(self, clock: Callable[[], datetime] = _utc_now):
        self._clock = clock

    def extract(self, dictionary: dict[str, object], fragments: list[dict[str, object]]) -> list[dict[str, object]]:
        entities = dictionary.get("entities", []) if isinstance(dictionary, dict) else []

        def resolve_entity(name: str) -> dict[str, object] | None:
            key = name.casefold()
            for entity in entities if isinstance(entities, list) else []:
                if not isinstance(entity, dict) or entity.get("status") != "accepted":
                    continue
                names = [entity.get("canonical_name"), *(entity.get("aliases", []) if isinstance(entity.get("aliases"), list) else [])]
                if any(isinstance(value, str) and value.casefold() == key for value in names):
                    return entity
            return None

        subject = resolve_entity(_FIXTURE["subject_name"])
        obj = resolve_entity(_FIXTURE["object_name"])
        evidence_locator = _FIXTURE["evidence"]
        evidence = next(
            (
                fragment for fragment in fragments
                if isinstance(fragment, dict)
                and fragment.get("source_file") == evidence_locator["source_file"]
                and fragment.get("heading_path") == evidence_locator["heading_path"]
                and fragment.get("ordinal") == evidence_locator["ordinal"]
            ),
            None,
        )
        subject_id = str(subject["id"]) if subject and isinstance(subject.get("id"), str) else f"fixture.unresolved.subject:{_FIXTURE['subject_name']}"
        object_id = str(obj["id"]) if obj and isinstance(obj.get("id"), str) else f"fixture.unresolved.object:{_FIXTURE['object_name']}"
        heading_locator = "/".join(map(str, evidence_locator["heading_path"]))
        evidence_id = str(evidence["id"]) if evidence and isinstance(evidence.get("id"), str) else f"fixture.unresolved.evidence:{evidence_locator['source_file']}#{heading_locator}:p{evidence_locator['ordinal']}"
        predicate = _FIXTURE["predicate"]
        digest = hashlib.sha256(f"{subject_id}\0{predicate}\0{object_id}\0{evidence_id}".encode()).hexdigest()[:24]
        generated_at = self._clock().astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        return [{
            "id": f"ast_fixture_{digest}", "subject_id": subject_id, "predicate": predicate, "object_id": object_id,
            "primary_evidence_id": evidence_id, "supporting_evidence_ids": [], "status": "candidate",
            "extraction": {"provider": _FIXTURE["provider"], "model": _FIXTURE["model"], "prompt_version": _FIXTURE["prompt_version"], "schema_version": SCHEMA_VERSION, "generated_at": generated_at, "mode": "fixture"},
        }]


def validate_candidates(candidates: object, dictionary: object, fragments: object) -> list[dict[str, object]]:
    raw_entities = dictionary.get("entities", []) if isinstance(dictionary, dict) else []
    if not isinstance(raw_entities, list):
        raw_entities = []
    entities = {
        e["id"]: e for e in raw_entities
        if isinstance(e, dict) and e.get("status") == "accepted" and isinstance(e.get("id"), str)
    }
    raw_fragments = fragments if isinstance(fragments, list) else []
    fragment_ids = {f["id"] for f in raw_fragments if isinstance(f, dict) and isinstance(f.get("id"), str)}
    results = []
    candidate_items = candidates if isinstance(candidates, list) else [candidates]
    for index, candidate in enumerate(candidate_items):
        reasons: list[dict[str, str]] = []
        for error in sorted(_VALIDATOR.iter_errors(candidate), key=lambda e: tuple(map(str, e.absolute_path))):
            field = ".".join(map(str, error.absolute_path)) or "$"
            reasons.append({"field": field, "reason": error.message, "rule": "schema"})
        if isinstance(candidate, dict):
            subject = entities.get(str(candidate.get("subject_id", "")))
            obj = entities.get(str(candidate.get("object_id", "")))
            if subject is None:
                reasons.append({"field": "subject_id", "reason": f"{candidate.get('subject_id')!r} must reference an accepted Entity Dictionary entry", "rule": "entity_dictionary"})
            if obj is None:
                reasons.append({"field": "object_id", "reason": f"{candidate.get('object_id')!r} must reference an accepted Entity Dictionary entry", "rule": "entity_dictionary"})
            primary = candidate.get("primary_evidence_id")
            if not isinstance(primary, str) or primary not in fragment_ids:
                reasons.append({"field": "primary_evidence_id", "reason": f"{primary!r} must reference a Source Fragment", "rule": "evidence"})
            for i, evidence_id in enumerate(candidate.get("supporting_evidence_ids", []) if isinstance(candidate.get("supporting_evidence_ids"), list) else []):
                if not isinstance(evidence_id, str) or evidence_id not in fragment_ids:
                    reasons.append({"field": f"supporting_evidence_ids.{i}", "reason": f"{evidence_id!r} must reference a Source Fragment", "rule": "evidence"})
            predicate = candidate.get("predicate")
            if subject is not None and obj is not None:
                subject_type = subject.get("type")
                object_type = obj.get("type")
                if not isinstance(subject_type, str) or not isinstance(object_type, str):
                    reasons.append({"field": "predicate", "reason": "referenced Entity Dictionary entries must have valid string types", "rule": "type_contract"})
                elif (subject_type, object_type) not in TYPE_CONTRACTS.get(str(predicate), set()):
                    reasons.append({"field": "predicate", "reason": f"{predicate} does not allow {subject_type} -> {object_type}", "rule": "type_contract"})
        results.append({"candidate_index": index, "candidate_id": candidate.get("id") if isinstance(candidate, dict) else None, "candidate": candidate, "valid": not reasons, "reasons": reasons})
    id_counts: dict[str, int] = {}
    for result in results:
        candidate_id = result["candidate_id"]
        if isinstance(candidate_id, str):
            id_counts[candidate_id] = id_counts.get(candidate_id, 0) + 1
    for result in results:
        candidate_id = result["candidate_id"]
        if isinstance(candidate_id, str) and id_counts.get(candidate_id, 0) > 1:
            reasons = result["reasons"]
            assert isinstance(reasons, list)
            reasons.append({"field": "id", "reason": f"duplicate Candidate Assertion id {candidate_id!r} appears {id_counts[candidate_id]} times", "rule": "duplicate"})
            result["valid"] = False
    return results


def write_json_atomic(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", text=True)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            json.dump(data, output, ensure_ascii=False, indent=2)
            output.write("\n"); output.flush(); os.fsync(output.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try: os.unlink(temporary_name)
        except FileNotFoundError: pass
        raise


def write_extraction_results(
    validation_path: Path,
    candidates_path: Path,
    validation: object,
    candidates: object,
) -> None:
    """Persist the two-file public view as one recoverable consistency unit."""
    missing = object()
    previous_validation = _read_json_or_missing(validation_path, missing)
    previous_candidates = _read_json_or_missing(candidates_path, missing)
    try:
        write_json_atomic(validation_path, validation)
        write_json_atomic(candidates_path, candidates)
    except BaseException:
        _restore_json(validation_path, previous_validation, missing)
        _restore_json(candidates_path, previous_candidates, missing)
        raise


def _read_json_or_missing(path: Path, missing: object) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return missing


def _restore_json(path: Path, previous: object, missing: object) -> None:
    if previous is missing:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    else:
        write_json_atomic(path, previous)
