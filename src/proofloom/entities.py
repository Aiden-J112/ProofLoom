from __future__ import annotations

import json
import os
import secrets
import tempfile
from pathlib import Path

ENTITY_TYPES = ("Component", "Artifact", "Pattern", "Concept")
ENTITY_DICTIONARY_SCHEMA_VERSION = "1"


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
    value = data.get(key)
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise EntityDictionaryError(f"Entity Dictionary field '{key}' must be a list")
    return value


def _validate_dictionary(data: object) -> None:
    if not isinstance(data, dict):
        raise EntityDictionaryError("Entity Dictionary must be a JSON object")
    if data.get("schema_version") != ENTITY_DICTIONARY_SCHEMA_VERSION:
        raise EntityDictionaryError("Unsupported Entity Dictionary schema version")
    entities = _items(data, "entities")
    candidates = _items(data, "candidates")
    ids: set[str] = set()
    names: dict[str, str] = {}
    for entity in entities:
        entity_id = entity.get("id")
        if not isinstance(entity_id, str) or not entity_id or entity_id in ids:
            raise EntityDictionaryError("Accepted entity IDs must be unique strings")
        ids.add(entity_id)
        if entity.get("type") not in ENTITY_TYPES:
            raise EntityDictionaryError(f"Accepted entity {entity_id} has an invalid type")
        if entity.get("status") != "accepted":
            raise EntityDictionaryError(f"Accepted entity {entity_id} has an invalid status")
        if entity.get("schema_version") != ENTITY_DICTIONARY_SCHEMA_VERSION:
            raise EntityDictionaryError(f"Accepted entity {entity_id} has an invalid schema version")
        canonical = entity.get("canonical_name")
        aliases = entity.get("aliases")
        if not isinstance(canonical, str) or not canonical.strip():
            raise EntityDictionaryError(f"Accepted entity {entity_id} needs a display name")
        if not isinstance(aliases, list) or not all(isinstance(alias, str) for alias in aliases):
            raise EntityDictionaryError(f"Accepted entity {entity_id} aliases must be strings")
        for name in [canonical, *aliases]:
            key = name.strip().casefold()
            owner = names.get(key)
            if not key or (owner is not None and owner != entity_id):
                raise EntityConflictError(f"Name or alias '{name}' resolves to multiple accepted entities")
            names[key] = entity_id
    candidate_ids: set[str] = set()
    for candidate in candidates:
        candidate_id = candidate.get("id")
        name = candidate.get("name")
        if (
            not isinstance(candidate_id, str)
            or not candidate_id
            or candidate_id in candidate_ids
        ):
            raise EntityDictionaryError("Candidate entity IDs must be unique strings")
        candidate_ids.add(candidate_id)
        if not isinstance(name, str) or not name.strip():
            raise EntityDictionaryError(f"Candidate entity {candidate_id} needs a name")
        if candidate.get("status") != "candidate":
            raise EntityDictionaryError(f"Candidate entity {candidate_id} has an invalid status")
