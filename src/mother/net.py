"""The only module that opens network connections: authenticated JSON POSTs to the relay's admin API. Callers use
`net.post` as a module attribute so tests can fake it. `/admin/body` and `/admin/ack` (intake) use env
MOTHER_RELAY_INTAKE_KEY, the bot's key; everything else (publish, invites, revocation) uses MOTHER_RELAY_ADMIN_KEY,
which only a steward's terminal holds, never the bot. The relay base is MOTHER_RELAY_URL; no key is ever printed."""

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


def _env(name: str, where: str = "set it in the Grok secret store") -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise EnvironmentProblem(f"{name} is not set: {where}", code="KEY_MISSING", hint=where)
    return value


def _key(path: str) -> str:
    if path in INTAKE_PATHS:
        return _env("MOTHER_RELAY_INTAKE_KEY")
    return _env("MOTHER_RELAY_ADMIN_KEY", "run this from a steward's terminal (the bot never holds the admin key)")


def post(path: str, body: dict[str, Any]) -> dict[str, Any]:
    """POST `body` to `<MOTHER_RELAY_URL><path>` with the path's key; the parsed JSON reply."""
    url = _env("MOTHER_RELAY_URL").rstrip("/") + path
    request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST", headers={
        "authorization": f"Bearer {_key(path)}", "content-type": "application/json",
        "user-agent": f"plsi-mother/{__version__}"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as reply:
            return json.loads(reply.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        try:
            reason = json.loads(exc.read().decode("utf-8")).get("error", "")
        except (ValueError, AttributeError):
            reason = ""
        raise NetworkError(f"the relay refused {path}: HTTP {exc.code} {reason}".rstrip(), status=exc.code) from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise NetworkError(f"the relay is unreachable for {path}: {type(exc).__name__}") from None
