"""
Pure rules for Schedule 13D events: which filings count and which filer looks like a fund.

``FUND_RE`` and ``NAME_RE`` are copied verbatim from scripts/onetime_event_13d_study.py (pinned by a test), so the
live scanner classifies filers exactly as the study that evidenced the strategy did. They were fixed before any
result was read. The classifier is crude: a keyword list on the cover-page reporting-person name.
"""

from __future__ import annotations

import re

# SC 13D until Dec 2024, SCHEDULE 13D after. Amendments (/A) are not new positions and are dropped.
FORMS_13D = frozenset({"SC 13D", "SCHEDULE 13D"})

FUND_RE = re.compile(
    r"\b(capital|partners|management|fund|funds|advisors|advisers|investment|investments|investors|asset|"
    r"l\.?p\.?|master|offshore|value|opportunit\w*|activist|trian|icahn|elliott|starboard|jana|pershing)\b",
    re.I,
)
NAME_RE = re.compile(
    r"NAMES?\s+OF\s+REPORTING\s+PERSONS?\W{0,10}(.{3,300}?)(?:I\.?R\.?S\.?\s|CHECK\s+THE\s+APPROPRIATE|\b2\s*\.?\s+CHECK)",
    re.I | re.S,
)


def is_initial_13d(form: object) -> bool:
    """True for an initial Schedule 13D (not an amendment, not a 13G)."""
    return str(form).upper().strip() in FORMS_13D


def reporting_names(text: str | None) -> str | None:
    """The reporting-person name(s) from a 13D cover page, or None when the cover cannot be parsed."""
    m = NAME_RE.search(text or "")
    return m.group(1).strip() if m else None


def is_fund_like(names: str | None) -> bool:
    """True when a parsed filer name matches the fund keyword list. An unparsed name is never fund-like."""
    return isinstance(names, str) and bool(FUND_RE.search(names))
