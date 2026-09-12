"""One way to talk to GitHub, and one way to read an agent label.

Three modules had grown their own copy of each, and the copies disagreed:

  - `Unreachable` existed twice, so a caller catching one did not catch the
    other.
  - The agent-label parser lowercased in `floor` and did not in `collect`, so
    `agent:Opus-Max` was a different agent in the board's table than in the
    claim check.
  - The subprocess wrapper existed three times with two different failure
    behaviours: raise, or return None and say nothing.

The last one matters most. A helper that turns every failure into an empty
result makes an unreachable repository look like a quiet one, which is the
single mistake this tool exists to avoid.
"""
from __future__ import annotations

import json
import subprocess

TIMEOUT = 30


class Unreachable(RuntimeError):
    """GitHub could not be read or written.

    Never the same as "there is nothing there". Callers must keep those apart;
    that is the whole reason this is an exception rather than a None.
    """


def run(args: list[str], stdin: str | None = None, timeout: int = TIMEOUT) -> str:
    """`gh <args>`, raising Unreachable rather than returning emptiness."""
    try:
        proc = subprocess.run(["gh", *args], capture_output=True, text=True,
                              input=stdin, timeout=timeout)
    except Exception as exc:
        raise Unreachable(f"{type(exc).__name__}: {exc}") from None
    if proc.returncode != 0:
        raise Unreachable((proc.stderr or "gh failed").strip()[:200])
    return proc.stdout


def agent_labels(labels) -> list[str]:
    """Every `agent:<id>` on a GitHub label list, normalised.

    Lowercased and stripped, everywhere, so one label means one agent no
    matter which part of the system read it.
    """
    out = []
    for label in labels or []:
        name = label["name"] if isinstance(label, dict) else str(label)
        if name.startswith("agent:"):
            out.append(name[len("agent:"):].strip().lower())
    return out


def agent_of(labels) -> str | None:
    """The single agent on a lane, or None. First wins, as the gate reads it."""
    found = agent_labels(labels)
    return found[0] if found else None
