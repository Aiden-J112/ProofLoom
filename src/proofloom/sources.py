from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

SOURCE_FRAGMENT_SCHEMA_VERSION = "1"

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_LIST_ITEM = re.compile(r"^\s*(?:[-+*]|\d+[.)])\s+")
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
_SETEXT = re.compile(r"^\s*(=+|-+)\s*$")


class SourceImportError(ValueError):
    pass


def _normalized(content: str) -> str:
    return "\n".join(line.rstrip() for line in content.replace("\r\n", "\n").split("\n")).strip()


def _fragment(
    source_file: str,
    heading_path: list[str],
    ordinal: int,
    kind: str,
    content: str,
) -> dict[str, object]:
    normalized = _normalized(content)
    content_digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    identity = "\0".join(
        [source_file, *heading_path, str(ordinal), kind, content_digest]
    )
    stable_digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return {
        "id": f"src_{stable_digest}",
        "source_file": source_file,
        "heading_path": heading_path.copy(),
        "ordinal": ordinal,
        "kind": kind,
        "content": content,
        "content_hash": f"sha256:{content_digest}",
        "schema_version": SOURCE_FRAGMENT_SCHEMA_VERSION,
    }


def parse_markdown(markdown: str, source_file: str) -> list[dict[str, object]]:
    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    headings: list[str] = []
    ordinals: dict[tuple[str, ...], int] = {}
    fragments: list[dict[str, object]] = []
    index = 0

    def add(kind: str, content_lines: list[str]) -> None:
        key = tuple(headings)
        ordinal = ordinals.get(key, 0) + 1
        ordinals[key] = ordinal
        fragments.append(
            _fragment(source_file, headings, ordinal, kind, "\n".join(content_lines))
        )

    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        heading = _HEADING.match(line)
        if heading:
            level = len(heading.group(1))
            headings[level - 1 :] = [heading.group(2)]
            index += 1
            continue
        if (
            index + 1 < len(lines)
            and _SETEXT.match(lines[index + 1])
            and not _LIST_ITEM.match(line)
            and not _FENCE.match(line)
        ):
            underline = _SETEXT.match(lines[index + 1])
            assert underline is not None
            level = 1 if underline.group(1).startswith("=") else 2
            headings[level - 1 :] = [line.strip()]
            index += 2
            continue
        fence = _FENCE.match(line)
        if fence:
            marker = fence.group(1)
            block = [line]
            index += 1
            while index < len(lines):
                block.append(lines[index])
                if re.match(rf"^\s*{re.escape(marker[0])}{{{len(marker)},}}\s*$", lines[index]):
                    break
                index += 1
            else:
                raise SourceImportError(f"{source_file}: unterminated fenced code block")
            index += 1
            add("code_block", block)
            continue
        if _LIST_ITEM.match(line):
            block = [line]
            base_indent = len(line) - len(line.lstrip())
            index += 1
            pending_blanks: list[str] = []
            while index < len(lines):
                candidate = lines[index]
                if not candidate.strip():
                    pending_blanks.append(candidate)
                    index += 1
                    continue
                indent = len(candidate) - len(candidate.lstrip())
                is_heading = bool(_HEADING.match(candidate)) or (
                    index + 1 < len(lines) and bool(_SETEXT.match(lines[index + 1]))
                )
                if pending_blanks and not (
                    _LIST_ITEM.match(candidate) or indent > base_indent
                ):
                    break
                if is_heading and indent <= base_indent:
                    break
                block.extend(pending_blanks)
                pending_blanks.clear()
                block.append(candidate)
                index += 1
            add("list", block)
            continue

        block = [line]
        index += 1
        while index < len(lines) and lines[index].strip():
            if _HEADING.match(lines[index]) or _FENCE.match(lines[index]) or _LIST_ITEM.match(lines[index]):
                break
            block.append(lines[index])
            index += 1
        add("paragraph", block)

    return fragments


def import_markdown(
    source: Path,
    locator_root: Path | None = None,
) -> list[dict[str, object]]:
    source = source.resolve()
    root = (locator_root or source.parent).resolve()
    if source.is_file():
        if source.suffix.lower() != ".md":
            raise SourceImportError(f"{source}: expected a Markdown (.md) file")
        candidates = [source]
    elif source.is_dir():
        candidates = [path for path in sorted(source.rglob("*.md")) if path.is_file()]
        if not candidates:
            raise SourceImportError(f"{source}: directory contains no Markdown files")
    else:
        raise SourceImportError(f"{source}: source does not exist")

    files: list[tuple[str, Path]] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if not resolved.is_relative_to(root):
            raise SourceImportError(
                f"{candidate}: resolved source is outside the local browsing root"
            )
        files.append((candidate.relative_to(root).as_posix(), resolved))

    fragments: list[dict[str, object]] = []
    for source_file, path in files:
        try:
            markdown = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise SourceImportError(f"{path}: {error}") from error
        try:
            fragments.extend(parse_markdown(markdown, source_file))
        except SourceImportError as error:
            raise SourceImportError(f"{path}: {error}") from error
    return fragments


def merge_source_fragments(
    existing: list[dict[str, object]],
    imported: list[dict[str, object]],
    source_locator: str,
    source_is_directory: bool,
) -> list[dict[str, object]]:
    if source_is_directory:
        prefix = "" if source_locator == "." else source_locator.rstrip("/") + "/"
        retained = [
            fragment
            for fragment in existing
            if not str(fragment.get("source_file", "")).startswith(prefix)
        ]
    else:
        retained = [
            fragment
            for fragment in existing
            if fragment.get("source_file") != source_locator
        ]
    return sorted(
        retained + imported,
        key=lambda fragment: (
            str(fragment.get("source_file", "")),
            tuple(fragment.get("heading_path", [])),
            int(fragment.get("ordinal", 0)),
        ),
    )


def write_source_fragments(
    destination: Path,
    fragments: list[dict[str, object]],
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(fragments, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, destination)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
