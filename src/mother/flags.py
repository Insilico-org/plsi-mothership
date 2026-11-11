"""Warnings, never refusals: text that speaks to a bot (`instruction`) or sells (`promotion`)."""

from __future__ import annotations

import re
from typing import Any

from mother import gate, note

FLAGS = {
    "instruction": re.compile(
        r"(?i)\b(?:ignore|disregard|forget|override)\s+(?:all\s+|any\s+|the\s+|your\s+)?(?:previous\s+|prior\s+|above\s+"
        r"|earlier\s+|preceding\s+)?(?:instructions|rules|prompts?|guidelines)\b|\bsystem\s+prompt\b|\byou\s+are\s+now\b"
        r"|\bnew\s+instructions\b|\bdeveloper\s+mode\b|\bjailbreak|\b(?:as\s+an?\s+)?(?:ai|assistant|bot|model|mother"
        r"|steward)s?,?\s+(?:must|should|will)\s+(?:accept|approve|forward|send|reply|publish)\b"),
    "promotion": re.compile(
        r"(?i)(?:[$€£]\s?\d|\b\d+(?:[.,]\d+)?\s?(?:usd|eur|gbp)\b|\b(?:usd|eur|gbp)\s?\d)|\bbuy\s+(?:now|it|here|online)\b"
        r"|\border\s+(?:now|today|here)\b|\b(?:discount|promo|coupon|voucher)\s*code\b|\buse\s+code\b|\b\d{1,2}\s?%\s+off\b"
        r"|\bfree\s+shipping\b|\blimited[-\s]time\b|\bper\s+bottle\b"),
}


def external(text: str) -> dict[str, str]:
    """Text from outside, as the CLI hands it on: data, never instructions."""
    return {"kind": "external_text", "text": text}


def warnings(doc: dict[str, Any]) -> list[dict[str, str]]:
    """`{path, flag}` once per place; a report by line. Matched like the gate, on gate.clean(text)."""
    out = []
    for path, plain, text, is_key, _ in gate.strings(doc):
        if is_key:
            continue
        text = gate.clean(text)
        for flag, pattern in FLAGS.items():
            if plain == "report":
                out += [{"path": f"report[{n}]", "flag": flag} for n in note.lines(pattern, text)]
            elif pattern.search(text):
                out.append({"path": path, "flag": flag})
    return out
