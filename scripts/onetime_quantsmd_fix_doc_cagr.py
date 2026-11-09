#!/usr/bin/env python3
"""One-time script to fix the CAGR column in quantsmd backtest docs.

The original docs reported a total-return multiple printed with a percent sign
(e.g., 4.7% for a 4.7x total return) in the CAGR column. This script recomputes
true CAGR to 2026-11-06, renames the column header to 'CAGR (true)', leaves
intraday rows as 'n/a', adds a note under the method section, and updates the
lag sentence in quantsmd-2026-11.md.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# Ensure POSTGRES_URI is set before any imports that touch db
os.environ.setdefault("POSTGRES_URI", "stub:stub@localhost:5432/stub")

try:
    from scripts.onetime_quantsmd_backtest import true_cagr
except ImportError:
    import pandas as pd

    def true_cagr(total_return: float | None, start, end) -> float | None:
        if total_return is None or start is None or end is None:
            return None
        try:
            s = pd.to_datetime(start)
            e = pd.to_datetime(end)
            if getattr(s, "tzinfo", None) is not None:
                s = s.tz_localize(None) if hasattr(s, "tz_localize") else s.replace(tzinfo=None)
            if getattr(e, "tzinfo", None) is not None:
                e = e.tz_localize(None) if hasattr(e, "tz_localize") else e.replace(tzinfo=None)
        except Exception:
            return None
        days = (e - s).days
        if days <= 0:
            return None
        years = days / 365.25
        if 1 + total_return <= 0:
            return -1.0 if 1 + total_return == 0 else None
        return float((1 + total_return) ** (1 / years) - 1)

END_DATE = "2026-11-06"
METHOD_NOTE = (
    "Note: the old column was total return, corrected 2026-11-09, "
    "values are approximate (start year, not exact first bar)."
)

DOC_PATHS = [
    Path("docs/backtests/quantsmd-2026-11.md"),
    Path("docs/backtests/quantsmd-2026-11-all.md"),
]


def recompute_cagr_cell(start_val: str, cagr_val: str, end_date: str = END_DATE) -> str:
    """Recompute true CAGR for a given start and total-return multiple string."""
    start_str = start_val.strip()
    cagr_raw = cagr_val.strip()

    # Intraday rows have month-day start like '08-11' or '10-23'
    if re.match(r"^\d{2}-\d{2}$", start_str):
        return "n/a"

    # Year format (e.g. 2000, 2006, 2019)
    if re.match(r"^\d{4}$", start_str):
        try:
            tr = float(cagr_raw.rstrip("%"))
            if tr == -100.0:
                tr = -1.0
            cagr = true_cagr(tr, start_str, end_date)
            if cagr is not None:
                return f"{cagr * 100:.1f}%"
        except (ValueError, TypeError):
            pass
        return "n/a"

    return "n/a"


def fix_doc(content: str, end_date: str = END_DATE) -> str:
    """Fix CAGR column, method note, and lag sentence in markdown document.

    Idempotent: calling fix_doc multiple times produces the exact same output.
    """
    # 1. Fix sentence in discussion if present
    content = content.replace("lag QQQ by 5-15 points of CAGR", "lag QQQ by about 7-10 points")

    # 2. Add note under method section (above the table) if not already present
    if METHOD_NOTE not in content:
        lines = content.split("\n")
        table_idx = None
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("|") and "Bot" in stripped:
                table_idx = i
                break
        if table_idx is not None:
            # Ensure an empty line before and after the note
            insertions = []
            if table_idx > 0 and lines[table_idx - 1].strip() != "":
                insertions.append("")
            insertions.append(METHOD_NOTE)
            insertions.append("")
            for offset, item in enumerate(insertions):
                lines.insert(table_idx + offset, item)
            content = "\n".join(lines)

    # 3. Fix table columns
    lines = content.split("\n")
    new_lines = []
    in_target_table = False
    cagr_col = None
    start_col = None

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.split("|")[1:-1]]
            if "Bot (source Sharpe)" in cells:
                if "CAGR" in cells:
                    cagr_col = cells.index("CAGR")
                    start_col = cells.index("Start")
                    cells[cagr_col] = "CAGR (true)"
                    line = "| " + " | ".join(cells) + " |"
                    in_target_table = True
                elif "CAGR (true)" in cells:
                    # Already converted, don't re-convert
                    in_target_table = False
                new_lines.append(line)
                continue
            elif in_target_table:
                if all(re.match(r"^-+$", c) for c in cells):
                    # Table separator row
                    new_lines.append(line)
                    continue
                # Table data row
                if cagr_col is not None and start_col is not None and len(cells) > max(cagr_col, start_col):
                    start = cells[start_col]
                    cagr_val = cells[cagr_col]
                    cells[cagr_col] = recompute_cagr_cell(start, cagr_val, end_date)
                    line = "| " + " | ".join(cells) + " |"
                new_lines.append(line)
                continue
        else:
            in_target_table = False
        new_lines.append(line)

    return "\n".join(new_lines)


def fix_file(path: str | Path, end_date: str = END_DATE) -> bool:
    """Read file, apply fix_doc, write back if changed. Return True if changed."""
    p = Path(path)
    if not p.is_file():
        print(f"File not found: {p}", file=sys.stderr)
        return False
    original = p.read_text(encoding="utf-8")
    updated = fix_doc(original, end_date=end_date)
    if updated != original:
        p.write_text(updated, encoding="utf-8")
        return True
    return False


def main() -> None:
    targets = [Path(arg) for arg in sys.argv[1:]] if len(sys.argv) > 1 else DOC_PATHS
    for target in targets:
        changed = fix_file(target)
        status = "updated" if changed else "unchanged"
        print(f"{target}: {status}")


if __name__ == "__main__":
    main()
