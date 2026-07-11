from __future__ import annotations

import hashlib
import re
from pathlib import Path

SOURCE_FRAGMENT_SCHEMA_VERSION = "1"

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_LIST_ITEM = re.compile(r"^\s*(?:[-+*]|\d+[.)])\s+")
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")


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
            index += 1
            while index < len(lines) and lines[index].strip():
                if _HEADING.match(lines[index]) or _FENCE.match(lines[index]):
                    break
                block.append(lines[index])
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


def import_markdown(source: Path) -> list[dict[str, object]]:
    if source.is_file():
        if source.suffix.lower() != ".md":
            raise SourceImportError(f"{source}: expected a Markdown (.md) file")
        files = [(source.name, source)]
    elif source.is_dir():
        files = [
            (path.relative_to(source).as_posix(), path)
            for path in sorted(source.rglob("*.md"))
            if path.is_file()
        ]
        if not files:
            raise SourceImportError(f"{source}: directory contains no Markdown files")
    else:
        raise SourceImportError(f"{source}: source does not exist")

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
