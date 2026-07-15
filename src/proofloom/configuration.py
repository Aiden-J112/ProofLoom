from __future__ import annotations

import json
import ipaddress
from dataclasses import dataclass
from pathlib import Path

from proofloom.assertions import CodexCliExtractor, ExtractionError, Extractor, OpenAICompatibleExtractor


class ConfigurationError(ValueError):
    """A secret-safe runtime configuration error."""


@dataclass(frozen=True)
class RuntimeConfiguration:
    host: str = "127.0.0.1"
    port: int = 8000
    browse_root: Path = Path.home()
    extractor: Extractor | None = None


def load_runtime_configuration(path: Path) -> RuntimeConfiguration:
    path = path.expanduser().resolve()
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ConfigurationError(f"Unable to load configuration file: {type(error).__name__}") from None
    if not isinstance(document, dict):
        raise ConfigurationError("Configuration root must be a JSON object")
    _reject_unknown(document, {"server", "llm"}, "configuration")
    server = document.get("server", {})
    if not isinstance(server, dict):
        raise ConfigurationError("server must be a JSON object")
    _reject_unknown(server, {"host", "port", "browse_root"}, "server")
    host = server.get("host", "127.0.0.1")
    port = server.get("port", 8000)
    browse_value = server.get("browse_root", str(Path.home()))
    if not isinstance(host, str) or not host.strip() or not _is_loopback(host):
        raise ConfigurationError("server.host must be localhost or a loopback IP address")
    if host.casefold() == "localhost":
        host = "localhost"
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ConfigurationError("server.port must be an integer from 0 through 65535")
    if not isinstance(browse_value, str) or not browse_value.strip():
        raise ConfigurationError("server.browse_root must be a non-empty path string")
    browse_root = Path(browse_value).expanduser()
    if not browse_root.is_absolute():
        browse_root = path.parent / browse_root
    browse_root = browse_root.resolve()
    if not browse_root.is_dir():
        raise ConfigurationError("server.browse_root must identify an existing directory")
    extractor = None
    llm = document.get("llm")
    if llm is not None:
        if not isinstance(llm, dict):
            raise ConfigurationError("llm must be a JSON object")
        backend = llm.get("backend")
        try:
            if backend == "codex-cli":
                _reject_unknown(llm, {"backend", "model", "reasoning", "timeout"}, "llm")
                extractor = CodexCliExtractor(
                    llm.get("model", ""),
                    llm.get("reasoning", ""),
                    timeout=llm.get("timeout", 120),
                )
            elif backend == "openai-compatible":
                _reject_unknown(llm, {"backend", "api_key", "model", "endpoint", "base_url", "provider", "timeout"}, "llm")
                endpoint = llm.get("endpoint")
                base_url = llm.get("base_url")
                if endpoint is not None and base_url is not None:
                    raise ConfigurationError("llm.endpoint and llm.base_url are mutually exclusive")
                if base_url is not None:
                    if not isinstance(base_url, str) or not base_url.strip():
                        raise ConfigurationError("llm.base_url must be a non-empty URL string")
                    endpoint = f"{base_url.rstrip('/')}/chat/completions"
                extractor = OpenAICompatibleExtractor(
                    endpoint or "https://api.openai.com/v1/chat/completions",
                    llm.get("model", ""),
                    llm.get("api_key", ""),
                    llm.get("provider", "openai"),
                    timeout=llm.get("timeout", 30),
                )
            else:
                raise ConfigurationError("llm.backend must be 'openai-compatible' or 'codex-cli'")
        except ExtractionError as error:
            raise ConfigurationError(str(error)) from None
    return RuntimeConfiguration(host, port, browse_root, extractor)


def _reject_unknown(document: dict[str, object], allowed: set[str], location: str) -> None:
    unknown = sorted(set(document) - allowed)
    if unknown:
        raise ConfigurationError(f"{location} contains unknown field(s): {', '.join(unknown)}")


def _is_loopback(host: str) -> bool:
    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
