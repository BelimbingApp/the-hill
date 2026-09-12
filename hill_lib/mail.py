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
