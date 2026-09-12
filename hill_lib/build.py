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
STYLES = Path(__file__).with_name("board.css")
SCRIPT = Path(__file__).with_name("board.js")
PLACEHOLDER = "/*__DATA__*/null"


def assemble() -> str:
    """The page with its stylesheet and script inlined.

    They live in board.css and board.js so they can be edited as CSS and
    JavaScript rather than as strings inside HTML, and are folded back in here
    because a saved board must work from file:// with nothing running -- a
    browser will not let a local page read its neighbours.
    """
    page = TEMPLATE.read_text()
    for mark, path in (("/*__CSS__*/", STYLES), ("/*__JS__*/", SCRIPT)):
        if mark not in page:
            raise RuntimeError(f"{TEMPLATE} has no {mark} to fill")
        page = page.replace(mark, path.read_text(), 1)
    if PLACEHOLDER not in page:
        raise RuntimeError(f"{SCRIPT} has no {PLACEHOLDER} to fill")
    return page


def render(snap: dict, open_after: bool = False, archive: bool = False) -> Path:
    BOARD.mkdir(parents=True, exist_ok=True)
    html = assemble().replace(
        PLACEHOLDER, json.dumps(snap, separators=(",", ":"), default=str))

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
