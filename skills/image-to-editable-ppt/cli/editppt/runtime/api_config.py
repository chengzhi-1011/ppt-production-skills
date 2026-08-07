"""Validation helpers for user-selected OpenAI-compatible API endpoints."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit, urlunsplit


class BaseUrlError(ValueError):
    """Raised when an API base URL could expose credentials or use unsafe transport."""


def validate_base_url(value: str) -> str:
    """Return a normalized safe API base URL or raise ``BaseUrlError``.

    Remote endpoints must use HTTPS. Plain HTTP is accepted only for a loopback
    development service. Credentials, query strings, and fragments are forbidden
    because they are liable to be persisted in configuration or copied into logs.
    """

    raw = str(value).strip()
    if not raw:
        raise BaseUrlError("the URL cannot be empty")
    if any(character.isspace() or ord(character) < 0x20 for character in raw):
        raise BaseUrlError("whitespace and control characters are not allowed")

    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise BaseUrlError("the URL host or port is invalid") from exc

    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise BaseUrlError("the scheme must be https, or http for a loopback service")
    if not parsed.hostname:
        raise BaseUrlError("the URL must include a host")
    if parsed.username is not None or parsed.password is not None:
        raise BaseUrlError("embedded usernames and passwords are not allowed")
    if parsed.query:
        raise BaseUrlError("query parameters are not allowed")
    if parsed.fragment:
        raise BaseUrlError("fragments are not allowed")

    hostname = parsed.hostname.lower()
    is_loopback = hostname == "localhost"
    if not is_loopback:
        try:
            is_loopback = ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            is_loopback = False
    if scheme == "http" and not is_loopback:
        raise BaseUrlError("remote endpoints must use https")

    host_for_url = f"[{hostname}]" if ":" in hostname else hostname
    netloc = f"{host_for_url}:{port}" if port is not None else host_for_url
    path = parsed.path.rstrip("/")
    return urlunsplit((scheme, netloc, path, "", ""))
