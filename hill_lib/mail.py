"""Messages that cross machines, carried by GitHub.

`hill send` wrote SQLite, which no other machine can read, so a message to an
agent on another host went nowhere and looked delivered. GitHub is the shared
floor for everything else here; it carries this too.

A message is a comment on the board issue carrying **both** a `**From:**` and a
`**To:**` marker. That is the whole discriminator, and it reuses the grammar
already in use rather than inventing a second one: an ordinary status post
carries `**From:**` alone, so it is not mail and is not read as mail.

Read state stays local. It is genuinely per-machine -- what this host has shown
you -- and pushing it to GitHub would mean writing a comment every time someone
reads one. The cost is that the same agent on a second machine sees the backlog
again, which is stated rather than hidden.

Nothing here can carry a verdict. A body containing `**Verdict:**` or
`**HEAD reviewed:**` is refused, because the gate reports verdict markers found
in issue comments and a message that trips that warning is worse than no
message.
"""
from __future__ import annotations

import json
import os
import re
import subprocess

_GH_TIMEOUT = 30
_AGENT = r"[a-z0-9]+(?:[._-][a-z0-9]+)*"
_FROM = re.compile(rf"^\*\*From:\*\*[ \t]*({_AGENT})[ \t]*$", re.M | re.I)
_TO = re.compile(rf"^\*\*To:\*\*[ \t]*({_AGENT})[ \t]*$", re.M | re.I)
_REF = re.compile(r"^\*\*Ref:\*\*[ \t]*(\S+)[ \t]*$", re.M | re.I)
_FORBIDDEN = re.compile(r"^\*\*(Verdict|HEAD reviewed):\*\*", re.M | re.I)
_BOARD_RE = re.compile(r"^(?P<repo>[A-Za-z0-9._-]+/[A-Za-z0-9._-]+)#(?P<num>\d+)$")


class Unreachable(RuntimeError):
    """GitHub could not be read or written. Not the same as no messages."""


def board() -> tuple[str, int] | None:
    """The issue that carries mail, from HILL_BOARD (`owner/repo#number`).

    None when unset. The caller must say so rather than silently falling back
    to this machine, because a message that never left the host while reporting
    success is the defect this module exists to fix.
    """
    raw = (os.environ.get("HILL_BOARD") or "").strip()
    m = _BOARD_RE.match(raw)
    return (m.group("repo"), int(m.group("num"))) if m else None


def render(sender: str, to: str, body: str, ref: str | None = None) -> str:
    if _FORBIDDEN.search(body or ""):
        raise ValueError(
            "a message may not carry **Verdict:** or **HEAD reviewed:** — the "
            "gate reports verdict markers found in comments; post a verdict "
            "with `hill review` instead")
    text = f"**From:** {sender}\n\n**To:** {to}\n"
    if ref:
        text += f"\n**Ref:** {ref}\n"
    return text + "\n" + (body or "").strip() + "\n"


def _gh(args: list[str], stdin: str | None = None) -> str:
    try:
        proc = subprocess.run(["gh", *args], capture_output=True, text=True,
                              input=stdin, timeout=_GH_TIMEOUT)
    except Exception as exc:
        raise Unreachable(f"{type(exc).__name__}: {exc}") from None
    if proc.returncode != 0:
        raise Unreachable((proc.stderr or "gh failed").strip()[:200])
    return proc.stdout


def post(repo: str, number: int, sender: str, to: str, body: str,
         ref: str | None = None) -> str:
    text = render(sender, to, body, ref)
    _gh(["issue", "comment", str(number), "--repo", repo, "--body-file", "-"], stdin=text)
    # gh prints the URL, but confirming by re-reading is the same discipline the
    # verdict path uses: posted-but-unverifiable must not read as delivered.
    for m in reversed(fetch(repo, number)):
        if m["sender"] == sender.lower() and m["to"] == to.lower() and m["body"] == body.strip():
            return m["url"]
    raise Unreachable("gh reported success but the message was not found on re-read")


def fetch(repo: str, number: int) -> list[dict]:
    raw = _gh(["api", f"repos/{repo}/issues/{number}/comments", "--paginate",
               "--jq", "[.[] | {id, body, url: .html_url, at: .created_at}]"])
    out = []
    for chunk in raw.strip().splitlines():
        if not chunk.strip():
            continue
        for c in json.loads(chunk):
            body = c.get("body") or ""
            mf, mt = _FROM.search(body), _TO.search(body)
            if not (mf and mt):
                continue          # a status post is not mail
            mr = _REF.search(body)
            stripped = _TO.sub("", _FROM.sub("", _REF.sub("", body))).strip()
            out.append({"id": c["id"], "sender": mf.group(1).lower(),
                        "to": mt.group(1).lower(), "ref": mr.group(1) if mr else None,
                        "body": stripped, "url": c.get("url"), "at": c.get("at")})
    out.sort(key=lambda m: (m["at"] or "", m["id"]))
    return out


# --- peer liveness ---------------------------------------------------------
# A Factory Manager is told to read "the floor", and liveness lived in a
# per-machine directory nothing else could see. You cannot notice that a
# harness went rate-limited if you cannot see the harness.
#
# Each peer keeps ONE comment on the board and edits it in place. Appending a
# comment per tick would bury the run's actual conversation within a day, and
# the interesting fact is the current state, not its history.

_PEER = re.compile(r"^\*\*Peer:\*\*[ \t]*(\S+)[ \t]*$", re.M | re.I)
_MARK = "<!-- hill:peer -->"


def render_peer(host: str, agents: list[dict], note: str | None = None) -> str:
    lines = [_MARK, f"**Peer:** {host}", ""]
    if note:
        lines += [note.strip(), ""]
    if not agents:
        lines.append("No agent has ticked on this peer.")
    for a in sorted(agents, key=lambda r: r.get("agent") or ""):
        bits = [f"- **{a.get('agent')}** last tick {a.get('at')}"]
        if a.get("freshness"):
            bits.append(f"({a['freshness']})")
        if a.get("lane"):
            bits.append(f"lane {a['lane']}")
        if a.get("quota"):
            bits.append(f"capacity {a['quota']}")
        lines.append(" ".join(bits))
    lines += ["", "_Edited in place each cycle; it is state, not history._"]
    return "\n".join(lines) + "\n"


def publish_peer(repo: str, number: int, host: str, agents: list[dict],
                 note: str | None = None) -> str:
    """Create or update this peer's liveness comment. Returns its URL."""
    body = render_peer(host, agents, note)
    existing = None
    for c in _raw_comments(repo, number):
        text = c.get("body") or ""
        m = _PEER.search(text)
        if _MARK in text and m and m.group(1) == host:
            existing = c
            break
    if existing is None:
        _gh(["issue", "comment", str(number), "--repo", repo, "--body-file", "-"], stdin=body)
    else:
        # PATCH on a COMMENT, never on the issue. The issue is the run and the
        # run is not ours to edit; this is our own status line.
        _gh(["api", f"repos/{repo}/issues/comments/{existing['id']}",
             "--method", "PATCH", "-f", f"body={body}"])
    for c in _raw_comments(repo, number):
        if _MARK in (c.get("body") or "") and (_PEER.search(c.get("body") or "") or [None]) \
                and _PEER.search(c.get("body") or "").group(1) == host:
            return c.get("url") or ""
    raise Unreachable("published but the peer record was not found on re-read")


def peers(repo: str, number: int) -> list[dict]:
    """Every peer's last published state, as the floor can see it."""
    out = []
    for c in _raw_comments(repo, number):
        text = c.get("body") or ""
        m = _PEER.search(text)
        if _MARK not in text or not m:
            continue
        out.append({"host": m.group(1), "url": c.get("url"),
                    "updated_at": c.get("updated_at") or c.get("at"),
                    "body": text.replace(_MARK, "").strip()})
    out.sort(key=lambda p: p["host"])
    return out


def _raw_comments(repo: str, number: int) -> list[dict]:
    raw = _gh(["api", f"repos/{repo}/issues/{number}/comments", "--paginate",
               "--jq", "[.[] | {id, body, url: .html_url, at: .created_at, updated_at}]"])
    rows = []
    for chunk in raw.strip().splitlines():
        if chunk.strip():
            rows.extend(json.loads(chunk))
    return rows
