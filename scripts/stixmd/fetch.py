"""Allowlisted HTTPS download of the spec."""
import hashlib
import http.client
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from .config import (ALLOWED_FETCH_HOST, EXIT_FETCH, EXPECTED_TITLE, FETCH_DEADLINE_S, FETCH_MAX_BYTES,
                     FETCH_TIMEOUT_S, GENERATOR, SOURCE_ENCODING, StixMdError)
from .safepath import atomic_write_text

log = logging.getLogger("stixmd")
CHUNK = 64 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every HTTP redirect."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        """Raise instead of following a redirect."""
        raise StixMdError(EXIT_FETCH, f"redirect refused: HTTP {code} -> {newurl}")


def validate_url(url: str) -> None:
    """Enforce the fetch allowlist."""
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise StixMdError(EXIT_FETCH, f"invalid URL {url!r}: {exc}") from exc
    problems = []
    if parts.scheme != "https":
        problems.append("scheme must be https")
    if (parts.hostname or "").lower() != ALLOWED_FETCH_HOST:
        problems.append(f"host must be {ALLOWED_FETCH_HOST}")
    if parts.username is not None or parts.password is not None:
        problems.append("credentials are not allowed")
    if port is not None and port != 443:
        problems.append("only port 443 is allowed")
    if not parts.path.endswith(".html"):
        problems.append("path must end with .html")
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in url) or parts.netloc.endswith(":"):
        problems.append("whitespace, control characters and empty ports are not allowed")
    if parts.query or parts.fragment or "?" in url or "#" in url:
        problems.append("query and fragment are not allowed")
    if problems:
        raise StixMdError(EXIT_FETCH, f"URL refused {url!r}: " + "; ".join(problems))


def _download(url: str, part: Path) -> tuple[dict, bytes]:
    opener = urllib.request.build_opener(NoRedirect())
    req = urllib.request.Request(url, headers={"User-Agent": GENERATOR.replace(" ", "/")})
    with opener.open(req, timeout=FETCH_TIMEOUT_S) as resp:
        ctype = resp.headers.get("Content-Type", "")
        if not ctype.startswith("text/html"):
            raise StixMdError(EXIT_FETCH, f"unexpected Content-Type {ctype!r}")
        chunks: list[bytes] = []
        total = 0
        part.unlink(missing_ok=True)            # never write through a pre-existing file or link
        with open(part, "xb") as fh:
            deadline = time.monotonic() + FETCH_DEADLINE_S
            while chunk := resp.read(CHUNK):
                if time.monotonic() > deadline:
                    raise StixMdError(EXIT_FETCH, f"download took longer than {FETCH_DEADLINE_S} s")
                total += len(chunk)
                if total > FETCH_MAX_BYTES:
                    raise StixMdError(EXIT_FETCH, f"download exceeds {FETCH_MAX_BYTES} bytes")
                fh.write(chunk)
                chunks.append(chunk)
        headers = {"http_last_modified": resp.headers.get("Last-Modified"),
                   "etag": resp.headers.get("ETag"), "content_type": ctype}
    return headers, b"".join(chunks)


def fetch_spec(url: str, dest: Path) -> dict:
    """Download the spec to dest (via a .part file) and return provenance information."""
    validate_url(url)
    part = dest.with_name(dest.name + ".part")
    try:
        headers, raw = _download(url, part)
        try:
            text = raw.decode(SOURCE_ENCODING, "strict")
        except UnicodeDecodeError as exc:
            raise StixMdError(EXIT_FETCH, f"download does not decode as {SOURCE_ENCODING}: {exc}") from exc
        if f"<title>{EXPECTED_TITLE}</title>" not in text:
            raise StixMdError(EXIT_FETCH, f"download lacks <title>{EXPECTED_TITLE}</title>")
        os.replace(part, dest)
    except StixMdError:
        part.unlink(missing_ok=True)
        raise
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as exc:
        part.unlink(missing_ok=True)
        raise StixMdError(EXIT_FETCH, f"fetch failed: {exc}") from exc
    info = {"url": url,
            "retrieved_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            **headers,
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest()}
    atomic_write_text(dest.with_suffix(".fetch.json"), json.dumps(info, indent=2) + "\n")
    log.info("fetched %s (%d bytes)", url, len(raw))
    return info
