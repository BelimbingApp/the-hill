"""Liveness and capacity: one file per agent, no database.

These are uncontended writes — an agent only ever overwrites its own record —
so a file is simpler than a table and cannot clobber a neighbour. The write is
atomic (temp file + rename) so a reader never sees a half-written record.

What this deliberately does not do is decide whether an agent is alive. It
records when an agent last said something and how old that is. A stale record
means unknown, not stopped: an agent can be alive and idle, or gone and recently
ticked. ai-team#128 settled that elapsed silence starts an investigation rather
than granting write authority, so nothing here returns a boolean "dead".
"""
from __future__ import annotations

import json
import os
import socket
import datetime
from pathlib import Path

from . import db

LIVE = db.HOME / "live"


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def tick(agent: str, session: str | None = None, **extra) -> dict:
    """Record that `agent` is running, plus anything it knows about its capacity."""
    LIVE.mkdir(parents=True, exist_ok=True)
    rec = {
        "agent": agent,
        "at": _now().isoformat(timespec="seconds"),
        "session": session or os.environ.get("HILL_SESSION"),
        "host": socket.gethostname(),
        "pid": os.getpid(),
    }
    rec.update({k: v for k, v in extra.items() if v is not None})
    path = LIVE / f"{agent}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, indent=1))
    os.replace(tmp, path)  # atomic; a reader sees old or new, never half
    return rec


def read_all() -> list[dict]:
    """Every agent record on this machine, newest first, with an age."""
    out = []
    if not LIVE.is_dir():
        return out
    now = _now()
    for p in sorted(LIVE.glob("*.json")):
        try:
            rec = json.loads(p.read_text())
        except Exception:
            continue
        try:
            age = (now - datetime.datetime.fromisoformat(rec["at"])).total_seconds()
            rec["age_s"] = int(age)
            # Investigation thresholds from ai-team#128. "unknown" is a state in
            # its own right; none of these mean the agent has stopped.
            rec["freshness"] = ("fresh" if age < 600 else
                                "unknown" if age < 900 else
                                "check" if age < 1800 else "stale")
        except Exception:
            rec["age_s"] = None
            rec["freshness"] = "unknown"
        out.append(rec)
    out.sort(key=lambda r: r.get("at") or "", reverse=True)
    return out
