"""The only module that opens network connections: authenticated JSON POSTs to the relay's admin API at
MOTHER_RELAY_URL (https only; a redirect is refused, so a key never follows one). `/admin/body` and `/admin/ack`
(intake) use MOTHER_RELAY_INTAKE_KEY, the bot's key; everything else (publish, invites, revocation) uses
MOTHER_RELAY_ADMIN_KEY, which only a steward's terminal holds. No key is ever printed. Callers use `net.post` and
`net.call` as module attributes so tests can fake them."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from mother import __version__
from mother.errors import EnvironmentProblem, NetworkError

TIMEOUT_S = 60
INTAKE_PATHS = frozenset({"/admin/body", "/admin/ack"})


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:  # the 3xx becomes an HTTPError
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _env(name: str, where: str = "set it in the Grok secret store") -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise EnvironmentProblem(f"{name} is not set: {where}", code="KEY_MISSING", hint=where)
    return value


def _key(path: str) -> str:
    if path in INTAKE_PATHS:
        return _env("MOTHER_RELAY_INTAKE_KEY")
    return _env("MOTHER_RELAY_ADMIN_KEY", "run this from a steward's terminal (the bot never holds the admin key)")


def relay() -> str:
    """The relay base the Mother reads, named in a message so a wrong one cannot hide; no key is ever shown."""
    return os.environ.get("MOTHER_RELAY_URL", "").strip().rstrip("/") or "the relay (MOTHER_RELAY_URL is not set)"


def post(path: str, body: dict[str, Any]) -> dict[str, Any]:
    """POST `body` to `<MOTHER_RELAY_URL><path>` with the path's key; the reply, a JSON object."""
    base = _env("MOTHER_RELAY_URL").rstrip("/")
    if not base.lower().startswith("https://"):
        raise EnvironmentProblem("MOTHER_RELAY_URL is not an https:// URL", code="RELAY_URL_NOT_HTTPS",
                                 hint="the relay's https:// address, in the Grok secret store")
    request = urllib.request.Request(base + path, data=json.dumps(body).encode("utf-8"), method="POST", headers={
        "authorization": f"Bearer {_key(path)}", "content-type": "application/json",
        "user-agent": f"plsi-mother/{__version__}"})
    try:
        with _OPENER.open(request, timeout=TIMEOUT_S) as reply:
            doc = json.loads(reply.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            reason = json.loads(exc.read().decode("utf-8")).get("error", "")
        except (ValueError, AttributeError):
            reason = ""
        raise NetworkError(f"the relay refused {path}: HTTP {exc.code} {reason}".rstrip(), status=exc.code) from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise NetworkError(f"the relay is unreachable for {path}: {type(exc).__name__}") from None
    except ValueError:  # not JSON, or not UTF-8
        raise NetworkError(f"the relay's reply to {path} is not JSON") from None
    if not isinstance(doc, dict):
        raise NetworkError(f"the relay's reply to {path} is not a JSON object")
    return doc


def call(path: str, body: dict[str, Any], what: str, *required: str) -> dict[str, Any]:
    """`post`, refused (`RELAY_REFUSED`: the relay did not `what`) unless the reply is ok and holds `required`."""
    reply = post(path, body)
    if not reply.get("ok") or not all(reply.get(k) for k in required):
        raise NetworkError(f"the relay did not {what}", code="RELAY_REFUSED")
    return reply
