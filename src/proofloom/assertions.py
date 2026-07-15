from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from typing import Callable, Protocol
from urllib import error, request
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator, FormatChecker

SCHEMA_VERSION = "1"
_SCHEMA = json.loads(files("proofloom").joinpath("schemas/candidate-assertion.schema.json").read_text(encoding="utf-8"))
Draft202012Validator.check_schema(_SCHEMA)
_VALIDATOR = Draft202012Validator(_SCHEMA, format_checker=FormatChecker())
_FIXTURE_CATALOG = json.loads(files("proofloom").joinpath("fixtures/synthetic-extraction.json").read_text(encoding="utf-8"))

TYPE_CONTRACTS = {
    "COMPOSED_OF": {("Concept", "Component")},
    "PROMPTS": {("Artifact", "Component")},
    "CALLS_TOOL": {("Component", "Component")},
    "PRODUCES": {("Component", "Artifact")},
    "VERIFIES": {("Component", "Artifact")},
    "BLOCKS": {("Component", "Artifact")},
}

PROMPT_VERSION = "1"

_CODEX_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["candidates"],
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "id", "subject_id", "predicate", "object_id",
                    "primary_evidence_id", "supporting_evidence_ids",
                ],
                "properties": {
                    "id": {"type": "string", "minLength": 1},
                    "subject_id": {"type": "string", "minLength": 1},
                    "predicate": {"enum": list(TYPE_CONTRACTS)},
                    "object_id": {"type": "string", "minLength": 1},
                    "primary_evidence_id": {"type": "string", "minLength": 1},
                    "supporting_evidence_ids": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                        "uniqueItems": True,
                    },
                },
            },
        }
    },
}


class ExtractionError(ValueError):
    """A safe, user-facing extraction adapter failure."""


class Extractor(Protocol):
    def extract(self, dictionary: dict[str, object], fragments: list[dict[str, object]]) -> list[object]: ...


class _NoRedirectHandler(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _default_transport(http_request: request.Request, timeout: float) -> bytes:
    opener = request.build_opener(_NoRedirectHandler())
    with opener.open(http_request, timeout=timeout) as response:
        return response.read()


def _resolve_codex_executable() -> str | None:
    """Resolve a directly executable Codex binary without invoking a shell wrapper."""
    if sys.platform != "win32":
        return shutil.which("codex")
    wrapper = shutil.which("codex.cmd") or shutil.which("codex")
    if not wrapper:
        return None
    machine = platform.machine().casefold()
    if machine in {"amd64", "x86_64"}:
        package, target = "codex-win32-x64", "x86_64-pc-windows-msvc"
    elif machine in {"arm64", "aarch64"}:
        package, target = "codex-win32-arm64", "aarch64-pc-windows-msvc"
    else:
        return None
    candidate = (
        Path(wrapper).resolve().parent
        / "node_modules" / "@openai" / "codex" / "node_modules"
        / "@openai" / package / "vendor" / target / "bin" / "codex.exe"
    )
    if candidate.suffix.casefold() != ".exe" or candidate.is_symlink() or not candidate.is_file():
        return None
    return str(candidate.resolve())


class OpenAICompatibleExtractor:
    """Candidate-only adapter for OpenAI-compatible chat completions APIs."""

    def __init__(self, endpoint: str, model: str, api_key: str, provider: str = "openai", *, timeout: float = 30.0, transport: Callable[[request.Request, float], bytes] = _default_transport, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        if not all(isinstance(value, str) and value.strip() for value in (endpoint, model, api_key, provider)):
            raise ExtractionError("endpoint, model, API key, and provider must be non-empty")
        parsed = urlsplit(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ExtractionError("endpoint must be an absolute HTTP(S) URL with a host")
        if parsed.username is not None or parsed.password is not None:
            raise ExtractionError("endpoint must not contain user information")
        if parsed.scheme == "http":
            try:
                local = ipaddress.ip_address(parsed.hostname).is_loopback
            except ValueError:
                local = parsed.hostname.casefold() == "localhost"
            if not local:
                raise ExtractionError("HTTP endpoints are allowed only for localhost or loopback providers")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ExtractionError("timeout must be a positive finite number")
        self.endpoint = endpoint
        self.model = model
        self.provider = provider
        self._api_key = api_key
        self._timeout = timeout
        self._transport = transport
        self._clock = clock

    @classmethod
    def from_environment(cls, **overrides):
        values = {
            "endpoint": os.environ.get("PROOFLOOM_OPENAI_ENDPOINT", ""),
            "model": os.environ.get("PROOFLOOM_OPENAI_MODEL", ""),
            "api_key": os.environ.get("PROOFLOOM_OPENAI_API_KEY", ""),
            "provider": os.environ.get("PROOFLOOM_OPENAI_PROVIDER", "openai"),
        }
        if not values["endpoint"]:
            base_url = os.environ.get("PROOFLOOM_OPENAI_BASE_URL")
            values["endpoint"] = f"{base_url.rstrip('/')}/chat/completions" if base_url else "https://api.openai.com/v1/chat/completions"
        missing = [name for name, key in (("PROOFLOOM_OPENAI_MODEL", "model"), ("PROOFLOOM_OPENAI_API_KEY", "api_key")) if not values[key]]
        if missing:
            raise ExtractionError(f"Missing environment configuration: {', '.join(missing)}")
        return cls(**values, **overrides)

    def extract(self, dictionary: dict[str, object], fragments: list[dict[str, object]]) -> list[object]:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "Return JSON only: an object with a candidates array. Each candidate must contain only id, subject_id, predicate, object_id, primary_evidence_id, and supporting_evidence_ids. Propose candidates only; never accepted knowledge."},
                {"role": "user", "content": json.dumps({"entity_dictionary": dictionary, "source_fragments": fragments}, ensure_ascii=False)},
            ],
            "response_format": {"type": "json_object"},
        }
        http_request = request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            raw = self._transport(http_request, self._timeout)
        except error.HTTPError as failure:
            status = failure.code
            failure.close()
            raise ExtractionError(f"OpenAI-compatible endpoint returned HTTP status {status}") from None
        except (error.URLError, TimeoutError, OSError) as failure:
            raise ExtractionError(f"OpenAI-compatible endpoint request failed: {type(failure).__name__}") from None
        try:
            envelope = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
            raise ExtractionError("OpenAI-compatible response JSON is invalid") from None
        try:
            content = envelope["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise ExtractionError("OpenAI-compatible response is missing choices.0.message.content") from None
        if not isinstance(content, str):
            raise ExtractionError("OpenAI-compatible response field choices.0.message.content must be a string")
        try:
            output = json.loads(content)
        except json.JSONDecodeError:
            raise ExtractionError("Model content is not valid response JSON") from None
        items = output.get("candidates") if isinstance(output, dict) else None
        if not isinstance(items, list):
            raise ExtractionError("Model response field candidates must be an array")
        generated_at = self._clock().astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        provenance = {"provider": self.provider, "model": self.model, "prompt_version": PROMPT_VERSION, "schema_version": SCHEMA_VERSION, "generated_at": generated_at, "mode": "api"}
        return [dict(item, status="candidate", extraction=dict(provenance)) if isinstance(item, dict) else item for item in items]


class CodexCliExtractor:
    """Candidate-only adapter for an authenticated local Codex CLI."""

    def __init__(self, model: str, reasoning: str, *, timeout: float = 120, runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run, executable_resolver: Callable[[], str | None] = _resolve_codex_executable, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        if not all(isinstance(value, str) and value.strip() for value in (model, reasoning)):
            raise ExtractionError("Codex model and reasoning must be non-empty")
        if reasoning not in {"minimal", "low", "medium", "high", "xhigh"}:
            raise ExtractionError("Codex reasoning must be one of minimal, low, medium, high, or xhigh")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ExtractionError("timeout must be a positive finite number")
        self.model = model
        self.reasoning = reasoning
        self._timeout = timeout
        self._runner = runner
        self._executable_resolver = executable_resolver
        self._clock = clock

    def extract(self, dictionary: dict[str, object], fragments: list[dict[str, object]]) -> list[object]:
        try:
            executable = self._executable_resolver()
        except OSError:
            executable = None
        if not executable:
            raise ExtractionError("Codex CLI executable was not found")
        prompt = (
            "Propose Candidate Assertions only; never accepted knowledge and never a Query Graph. "
            "Use only IDs from the supplied Entity Dictionary and Source Fragments. "
            "Return an object matching the provided output schema.\n\n"
            + json.dumps(
                {"entity_dictionary": dictionary, "source_fragments": fragments},
                ensure_ascii=False,
            )
        )
        with tempfile.TemporaryDirectory(prefix="proofloom-codex-") as temporary_name:
            isolated = Path(temporary_name)
            schema_path = isolated / "candidate-output.schema.json"
            output_path = isolated / "candidate-output.json"
            schema_path.write_text(json.dumps(_CODEX_OUTPUT_SCHEMA), encoding="utf-8")
            command = [
                executable, "exec", "--ephemeral", "--skip-git-repo-check",
                "--ignore-user-config", "--ignore-rules", "--strict-config",
                "--model", self.model,
                "-c", f'model_reasoning_effort="{self.reasoning}"',
                "-c", 'default_permissions="proofloom"',
                "-c", 'permissions.proofloom.filesystem.":minimal"="read"',
                "-c", 'permissions.proofloom.filesystem.":workspace_roots"="read"',
                "-c", "permissions.proofloom.network.enabled=false",
                "-c", 'web_search="disabled"',
                "-c", 'shell_environment_policy.inherit="none"',
                "-c", "allow_login_shell=false",
                "--output-schema", str(schema_path), "-o", str(output_path), "-",
            ]
            try:
                completed = self._runner(
                    command,
                    input=prompt,
                    text=True,
                    capture_output=True,
                    cwd=isolated,
                    timeout=self._timeout,
                    check=False,
                )
            except FileNotFoundError:
                raise ExtractionError("Codex CLI executable was not found") from None
            except subprocess.TimeoutExpired:
                raise ExtractionError("Codex CLI extraction timed out") from None
            except OSError as error:
                raise ExtractionError(f"Codex CLI could not start: {type(error).__name__}") from None
            if completed.returncode != 0:
                raise ExtractionError(f"Codex CLI extraction failed with exit status {completed.returncode}")
            try:
                output = json.loads(output_path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                raise ExtractionError("Codex CLI did not produce structured output") from None
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise ExtractionError("Codex CLI structured output is invalid JSON") from None
        items = output.get("candidates") if isinstance(output, dict) else None
        if not isinstance(items, list):
            raise ExtractionError("Codex CLI structured output field candidates must be an array")
        provenance = {
            "provider": "codex-cli",
            "model": self.model,
            "prompt_version": PROMPT_VERSION,
            "schema_version": SCHEMA_VERSION,
            "generated_at": self._clock().astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "mode": "codex-cli",
        }
        return [
            dict(item, status="candidate", extraction=dict(provenance))
            if isinstance(item, dict) else item
            for item in items
        ]


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

        generated_at = self._clock().astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        recipes = _FIXTURE_CATALOG["recipes"]

        def locate(recipe: dict[str, object]) -> dict[str, object] | None:
            locator = recipe["evidence"]
            assert isinstance(locator, dict)
            return next(
                (
                    fragment
                    for fragment in fragments
                    if isinstance(fragment, dict)
                    and fragment.get("source_file") == locator["source_file"]
                    and fragment.get("heading_path") == locator["heading_path"]
                    and fragment.get("ordinal") == locator["ordinal"]
                ),
                None,
            )

        matched = [(recipe, locate(recipe)) for recipe in recipes]
        selected = [(recipe, evidence) for recipe, evidence in matched if evidence]
        if not selected:
            selected = [matched[0]]

        candidates = []
        for recipe, evidence in selected:
            subject_name = str(recipe["subject_name"])
            object_name = str(recipe["object_name"])
            subject = resolve_entity(subject_name)
            obj = resolve_entity(object_name)
            evidence_locator = recipe["evidence"]
            assert isinstance(evidence_locator, dict)
            subject_id = str(subject["id"]) if subject and isinstance(subject.get("id"), str) else f"fixture.unresolved.subject:{subject_name}"
            object_id = str(obj["id"]) if obj and isinstance(obj.get("id"), str) else f"fixture.unresolved.object:{object_name}"
            heading_locator = "/".join(map(str, evidence_locator["heading_path"]))
            evidence_id = str(evidence["id"]) if evidence and isinstance(evidence.get("id"), str) else f"fixture.unresolved.evidence:{evidence_locator['source_file']}#{heading_locator}:p{evidence_locator['ordinal']}"
            predicate = str(recipe["predicate"])
            digest = hashlib.sha256(f"{subject_id}\0{predicate}\0{object_id}\0{evidence_id}".encode()).hexdigest()[:24]
            candidates.append({
                "id": f"ast_fixture_{digest}", "subject_id": subject_id, "predicate": predicate, "object_id": object_id,
                "primary_evidence_id": evidence_id, "supporting_evidence_ids": [], "status": "candidate",
                "extraction": {"provider": _FIXTURE_CATALOG["provider"], "model": recipe["model"], "prompt_version": _FIXTURE_CATALOG["prompt_version"], "schema_version": SCHEMA_VERSION, "generated_at": generated_at, "mode": "fixture"},
            })
        return candidates


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
            extraction = candidate.get("extraction")
            if candidate.get("replaces_assertion_id") is not None and isinstance(extraction, dict) and extraction.get("mode") in {"api", "codex-cli"}:
                reasons.append({"field": "replaces_assertion_id", "reason": "LLM extraction cannot create replacement lineage; replacements require a Review Event", "rule": "reserved"})
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


_EXTRACTION_RESULT_FIELDS = (
    "subject_id",
    "predicate",
    "object_id",
    "primary_evidence_id",
    "supporting_evidence_ids",
    "status",
    "replaces_assertion_id",
    "extraction",
)


def prepare_extracted_candidates(
    existing: list[dict[str, object]],
    extracted: list[object],
) -> list[object]:
    """Assign fresh deterministic ledger IDs to materially changed ID collisions."""
    by_id = {
        item.get("id"): item
        for item in existing
        if isinstance(item.get("id"), str)
    }
    extracted_id_counts: dict[str, int] = {}
    for item in extracted:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            item_id = str(item["id"])
            extracted_id_counts[item_id] = extracted_id_counts.get(item_id, 0) + 1
    prepared: list[object] = []
    for item in extracted:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            prepared.append(item)
            continue
        if extracted_id_counts[str(item["id"])] > 1:
            prepared.append(item)
            continue
        collision = by_id.get(item["id"])
        if collision is None:
            prepared.append(item)
            continue
        if _same_extraction_result(collision, item):
            prepared.append(collision)
            continue
        base_id = str(item["id"])
        payload = json.dumps(
            {field: item.get(field) for field in _EXTRACTION_RESULT_FIELDS},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        attempt = 0
        while True:
            material = f"{base_id}\0{payload}\0{attempt}"
            digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
            fresh_id = f"{base_id}_reextracted_{digest}"
            prior = by_id.get(fresh_id)
            rekeyed = dict(item, id=fresh_id)
            if prior is None:
                by_id[fresh_id] = rekeyed
                prepared.append(rekeyed)
                break
            if _same_extraction_result(prior, rekeyed):
                prepared.append(prior)
                break
            attempt += 1
    return prepared


def append_new_candidates(
    existing: list[dict[str, object]],
    extracted: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Preserve the Candidate Assertion ledger while adding new proposals."""
    merged = list(existing)
    known = {item.get("id") for item in existing}
    for item in extracted:
        if item.get("id") not in known:
            merged.append(item)
            known.add(item.get("id"))
    return merged


def _same_extraction_result(left: dict[str, object], right: dict[str, object]) -> bool:
    return all(left.get(field) == right.get(field) for field in _EXTRACTION_RESULT_FIELDS)


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
