"""Write named sections of eval/results.md.

Each experiment owns one section, delimited by HTML comments, and rewrites only that section, so
results.md can only ever contain numbers a script produced.
"""

from __future__ import annotations

from pathlib import Path

from config.loader import ROOT

RESULTS = ROOT / "eval" / "results.md"
HEADER = """# Results

Every number below was written by a script in this repository (`make eval`), never typed by hand.
Data is synthetic (see `docs/real-vs-mocked.md`), so these results describe how the methods behave
on simulated reports and consumption, not on real Army data.
"""


def write_section(name: str, body: str, path: Path = RESULTS) -> None:
    start, end = f"<!-- {name}:start -->", f"<!-- {name}:end -->"
    block = f"{start}\n{body.strip()}\n{end}"
    text = path.read_text(encoding="utf-8") if path.exists() else HEADER
    if start in text and end in text:
        head, rest = text.split(start, 1)
        _, tail = rest.split(end, 1)
        text = head + block + tail
    else:
        text = text.rstrip() + "\n\n" + block + "\n"
    path.write_text(text, encoding="utf-8")
