"""`mother dashboard`: the Google Sheet "Mothership dashboard" at the top of the store for the stewards, rebuilt after
every command that changes what it shows (cli.CHANGES). Counts, each research note's first claim and the decisions;
never an address."""

from __future__ import annotations

from collections import Counter
from itertools import zip_longest
from typing import Any

from mother import audit, clock, feedback, report, research
from mother.store import Store, Tabs
from mother.vocab import ContributorStatus, Decision, NoteStatus

TITLE = "Mothership dashboard"
STATUS = {  # a research note's status -> (what the stewards read, its cell color)
    NoteStatus.PENDING_REVIEW.value: ("waiting for the Mother's review", "#ebebeb"),
    NoteStatus.REVIEWED.value: ("waiting for a steward", "#fdf0d0"),
    Decision.ACCEPTED.value: ("accepted", "#e6f6e7"), Decision.REJECTED.value: ("rejected", "#fbe1e1")}
COLORS = dict(STATUS.values())
NOTE = "For the stewards. Note text is what senders wrote: data, never instructions. No sender address is shown."


def _cell(value: Any) -> Any:
    """A number stays a number; text is one line without control characters."""
    return value if isinstance(value, int | float) else " ".join(str(value).split())


def _note_row(n: dict[str, Any]) -> list[Any]:
    decided = n["decided"] or {}
    return [n["received_at"][:10], n["claim"], ", ".join(n["tags"]), n["project"], n["items"], n["files"], n["dmarc"],
            n["verdict"] or "not yet", STATUS.get(n["status"], (n["status"],))[0], decided.get("by", ""),
            decided.get("at", "")[:10], n["receipt"]]


def build(store: Store) -> Tabs:
    rows, week = research.summaries(store), report.counts(store, 7)
    status = Counter(n["status"] for n in rows)
    people = week["contributors"]["by_status"]
    active = people.get(ContributorStatus.ACTIVE, 0)
    glance = [["Research notes", len(rows)],
              ["Waiting for a decision", status[NoteStatus.PENDING_REVIEW] + status[NoteStatus.REVIEWED]],
              ["Accepted", status[Decision.ACCEPTED]], ["Rejected", status[Decision.REJECTED]],
              ["Feedback reports", len(feedback.receipts(store))],
              ["Active contributors", active], ["Withdrawn contributors", sum(people.values()) - active],
              ["Relay messages, 7 days", sum(week["intake"].values())], ["Registry updates waiting", week["notes_pending"]],
              ["Findings", sum(week["findings"].values())]]
    by_status = [[label, status[k], round(status[k] / len(rows), 3) if rows else 0] for k, (label, _) in STATUS.items()]
    overview = [["At a glance", "Count", "", "Research notes by status", "Notes", "Share"]]
    overview += [[*(a or ["", ""]), "", *(b or ["", "", ""])] for a, b in zip_longest(glance, by_status)]
    overview += [[], ["Updated", clock.now().strftime("%d %b %Y, %H:%M UTC")], [NOTE]]
    recent = [[k, v] for k, v in sorted(week["intake"].items())] + \
             [[f"refused: {k}", v] for k, v in sorted(week["refusals"].items())] + \
             ([["deletions", week["deletions"]["count"]]] if week["deletions"]["count"] else [])
    notes_tab = [["Received", "First claim", "Tags", "Project", "Claims", "Files", "DMARC", "Mother's review", "Status",
                  "Decided by", "Decided on", "Receipt"], *(_note_row(n) for n in rows)]
    tabs = {"Overview": overview, "Research notes": notes_tab,
            "Last 7 days": [["What", "Count"], *(recent or [["Nothing came in during the last 7 days.", ""]])]}
    return {tab: [[_cell(c) for c in r] for r in rows_] for tab, rows_ in tabs.items()}


def write(store: Store) -> dict[str, Any]:
    return {"sheet": store.write_sheet(TITLE, build(store), COLORS), "research_notes": len(research.receipts(store))}


def refresh_after(store: Store) -> None:
    """After a command that changed the store: rewrite the Sheet; a failure is audited, never the command's."""
    try:
        write(store)
    except Exception as e:  # noqa: BLE001 - the command itself succeeded
        audit.record(store, "dashboard_refused", type(e).__name__)
