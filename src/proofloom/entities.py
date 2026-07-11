from __future__ import annotations

import json
import os
import secrets
import tempfile
from importlib.resources import files
from pathlib import Path

from jsonschema import Draft202012Validator

ENTITY_DICTIONARY_SCHEMA_VERSION = "1"

_SCHEMA = json.loads(
    files("proofloom").joinpath("schemas/entity-dictionary.schema.json").read_text(
        encoding="utf-8"
    )
)
Draft202012Validator.check_schema(_SCHEMA)
_SCHEMA_VALIDATOR = Draft202012Validator(_SCHEMA)
ENTITY_TYPES = tuple(_SCHEMA["$defs"]["acceptedEntity"]["properties"]["type"]["enum"])


class EntityDictionaryError(ValueError):
    pass


class EntityConflictError(EntityDictionaryError):
    pass


def empty_dictionary() -> dict[str, object]:
    return {
        "schema_version": ENTITY_DICTIONARY_SCHEMA_VERSION,
        "entities": [],
        "candidates": [],
    }


def load_dictionary(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return empty_dictionary()
    except (OSError, json.JSONDecodeError) as error:
        raise EntityDictionaryError(f"Cannot read Entity Dictionary: {error}") from error
    _validate_dictionary(data)
    return data


def submit_candidate(data: dict[str, object], name: str) -> str:
    name = name.strip()
    if not name:
        raise EntityDictionaryError("Candidate name is required")
    candidate_id = f"candidate_{secrets.token_hex(12)}"
    candidates = _items(data, "candidates")
    candidates.append({"id": candidate_id, "name": name, "status": "candidate"})
    return candidate_id


def accept_candidate(data: dict[str, object], candidate_id: str, entity_type: str) -> str:
    if entity_type not in ENTITY_TYPES:
        raise EntityDictionaryError(
            "Entity type must be Component, Artifact, Pattern, or Concept"
        )
    candidates = _items(data, "candidates")
    candidate = next((item for item in candidates if item.get("id") == candidate_id), None)
    if candidate is None:
        raise EntityDictionaryError("Candidate entity does not exist")
    name = str(candidate["name"])
    _ensure_names_available(data, [name])
    entity_id = f"entity_{secrets.token_hex(12)}"
    _items(data, "entities").append(
        {
            "id": entity_id,
            "canonical_name": name,
            "type": entity_type,
            "aliases": [],
            "status": "accepted",
            "schema_version": ENTITY_DICTIONARY_SCHEMA_VERSION,
        }
    )
    candidates.remove(candidate)
    return entity_id


def update_entity(
    data: dict[str, object], entity_id: str, display_name: str, aliases: list[str]
) -> None:
    display_name = display_name.strip()
    aliases = _deduplicate_names(aliases)
    if not display_name:
        raise EntityDictionaryError("Display name is required")
    entities = _items(data, "entities")
    entity = next((item for item in entities if item.get("id") == entity_id), None)
    if entity is None:
        raise EntityDictionaryError("Accepted entity does not exist")
    _ensure_names_available(data, [display_name, *aliases], except_entity_id=entity_id)
    entity["canonical_name"] = display_name
    entity["aliases"] = aliases


def write_dictionary(path: Path, data: dict[str, object]) -> None:
    _validate_dictionary(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", text=True
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            json.dump(data, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _deduplicate_names(names: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in names:
        value = value.strip()
        key = value.casefold()
        if value and key not in seen:
            result.append(value)
            seen.add(key)
    return result


def _ensure_names_available(
    data: dict[str, object], names: list[str], except_entity_id: str | None = None
) -> None:
    requested = {name.strip().casefold(): name.strip() for name in names if name.strip()}
    for entity in _items(data, "entities"):
        if entity.get("id") == except_entity_id:
            continue
        owned = [str(entity["canonical_name"]), *map(str, entity["aliases"])]
        for name in owned:
            if name.casefold() in requested:
                raise EntityConflictError(
                    f"Name or alias '{requested[name.casefold()]}' already belongs to "
                    f"accepted entity {entity['id']}"
                )


def _items(data: dict[str, object], key: str) -> list[dict[str, object]]:
    return data[key]  # type: ignore[return-value]


def _validate_dictionary(data: object) -> None:
    errors = sorted(
        _SCHEMA_VALIDATOR.iter_errors(data),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        error = errors[0]
        path = ".".join(str(part) for part in error.absolute_path) or "$"
        raise EntityDictionaryError(
            f"Entity Dictionary schema error at {path}: {error.message}"
        )

    assert isinstance(data, dict)
    entities = _items(data, "entities")
    candidates = _items(data, "candidates")
    ids: set[str] = set()
    names: dict[str, str] = {}
    for entity in entities:
        entity_id = entity.get("id")
        assert isinstance(entity_id, str)
        if entity_id in ids:
            raise EntityDictionaryError(f"Entity Dictionary duplicate ID: {entity_id}")
        ids.add(entity_id)
        canonical = entity["canonical_name"]
        aliases = entity["aliases"]
        assert isinstance(canonical, str) and isinstance(aliases, list)
        for name in [canonical, *aliases]:
            key = name.strip().casefold()
            owner = names.get(key)
            if not key or (owner is not None and owner != entity_id):
                raise EntityConflictError(
                    f"Name or alias '{name}' resolves to multiple accepted entities"
                )
            names[key] = entity_id
    candidate_ids: set[str] = set()
    for candidate in candidates:
        candidate_id = candidate.get("id")
        assert isinstance(candidate_id, str)
        if candidate_id in candidate_ids or candidate_id in ids:
            raise EntityDictionaryError(f"Entity Dictionary duplicate ID: {candidate_id}")
        candidate_ids.add(candidate_id)
