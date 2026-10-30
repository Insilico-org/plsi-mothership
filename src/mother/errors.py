"""Error types for the CLI envelope: `code`, `message`, `hint`, `violations`; `exit_code` 1 = refused, 2 = usage,
3 = environment."""

from __future__ import annotations


class MotherError(Exception):
    code = "ERROR"
    exit_code = 1

    def __init__(self, message: str, *, code: str | None = None, hint: str | None = None,
                 violations: list[dict[str, str]] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or type(self).code
        self.hint = hint
        self.violations = violations


class GateFailed(MotherError):
    code = "GATE_FAILED"

    def __init__(self, violations: list[dict[str, str]]) -> None:
        super().__init__(f"the message fails the gate ({len(violations)} violation(s))", violations=violations)


class NotFound(MotherError):
    code = "NOT_FOUND"


class Refused(MotherError):
    code = "REFUSED"


class UsageError(MotherError):
    code = "USAGE"
    exit_code = 2


class EnvironmentProblem(MotherError):
    code = "ENVIRONMENT"
    exit_code = 3


class NetworkError(EnvironmentProblem):
    code = "NETWORK_ERROR"

    def __init__(self, message: str, *, status: int | None = None, **kw) -> None:
        super().__init__(message, **kw)
        self.status = status


def violation(path: str, rule: str) -> dict[str, str]:
    """A gate violation: a path built by code from known field names and indexes, and a rule name; never sender text."""
    return {"path": path, "rule": rule}
