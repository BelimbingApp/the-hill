"""Turn a snapshot into one self-contained HTML file.

The data is inlined rather than fetched from a sibling file because a browser
blocks a local page from reading its neighbours — split the two and you need a
web server running before you can look at your own board. Inlining removes that
whole class of problem, and the file stays small enough that it does not matter.

One build overwrites `latest.html`. Timestamped copies are opt-in (`--archive`),
because a directory filling with near-identical snapshots is not a record
anybody reads -- for the live floor view use `hill serve`, which keeps the
current state in memory and never writes a file at all.
"""
from __future__ import annotations

import datetime
import json
import shutil
import subprocess
from pathlib import Path

from . import db

BOARD = db.HOME / "board"
TEMPLATE = Path(__file__).with_name("template.html")
PLACEHOLDER = "/*__DATA__*/null"


def render(snap: dict, open_after: bool = False, archive: bool = False) -> Path:
    BOARD.mkdir(parents=True, exist_ok=True)
    tpl = TEMPLATE.read_text()
    if PLACEHOLDER not in tpl:
        raise RuntimeError(f"{TEMPLATE} has no {PLACEHOLDER} to fill")
    html = tpl.replace(PLACEHOLDER, json.dumps(snap, separators=(",", ":"), default=str))

    latest = BOARD / "latest.html"
    latest.write_text(html)
    if archive:
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        shutil.copyfile(latest, BOARD / f"board-{stamp}.html")

    if open_after:
        for opener in ("xdg-open", "wslview", "open"):
            if shutil.which(opener):
                subprocess.Popen([opener, str(latest)],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                break
    return latest
