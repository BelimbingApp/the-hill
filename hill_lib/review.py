"""Canonical review records: the grammar is emitted, never typed.

A PR verdict is not prose. `review_gate.sh` reads three bold markers out of the
review body, each anchored to a whole line, and what it cannot parse it treats
as absent. On 2026-09-11 two capable reviewers proved how sharp that edge is:
one stated the reviewed SHA in a sentence instead of a `**HEAD reviewed:**`
line, the other wrote `## Verdict: Approve` instead of `**Verdict:** accept`.
Neither review was wrong about the code. Both cost a round trip, and the second
was a genuine approval the gate could not see.

The conclusion in ai-team#128 was that nobody -- human or agent -- should hand
type the grammar. So this module takes structured arguments and emits the
record. Handwritten JSON would fail exactly the same way, which is why the
interface is arguments in, canonical text out: every field that can be wrong is
checked here, where the error is a ValueError on the caller's screen rather
than a silent non-verdict discovered a round trip later.

Two rules here are load bearing beyond formatting. A verdict is bound to the
SHA it reviewed, so submit() refuses a head that has already moved rather than
posting a record the gate will reject. And an unknown answer is never promoted
to agreement: if the current head cannot be determined, the result says so
instead of implying the binding was confirmed.
"""
from __future__ import annotations

import json
import re
import subprocess

# `hill review --verdict` takes the short word; the gate reads the long one.
# Only these two: "approve" and "request changes" are GitHub's vocabulary and
# the gate tolerates them, but one spelling per decision is the whole point.
_VERDICT_WORDS = {"accept": "accept", "changes": "changes required"}

_SHA_RE = re.compile(r"[0-9a-f]{40}")

# The gate captures the author with [a-z0-9]+([._-][a-z0-9]+)* and requires
# whitespace or end-of-line right after it. An id outside that alphabet -- a
# space, a colon, an "agent:" prefix -- does not produce a malformed record, it
# produces an *unattributed* one that the gate drops without complaint, which is
# the failure mode this module exists to prevent. So it is refused here, loudly.
_AGENT_RE = re.compile(r"[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*")

# A line shaped like one of our markers, under any emphasis or casing. Same
# shape the gate uses to tell "no verdict was cast" from "a verdict was cast and
# could not be read".
_MARKER_SHAPE_RE = re.compile(r"^[ \t]*\**[ \t]*(?:From|HEAD reviewed|Verdict)[ \t]*:", re.I)

_FENCE_RE = re.compile(r"^[ \t]*```")
_QUOTE_RE = re.compile(r"^[ \t]*>")

_GH_TIMEOUT = 60


def _unquoted_lines(text: str) -> list[str]:
    """Body lines the gate will actually parse.

    The gate strips fenced and blockquoted lines before reading any marker, so a
    review that *quotes* the grammar is discussing it rather than casting a
    verdict (#119, #359). We mirror that exactly: the check below must consider
    the same line set the gate does, or it would reject a body the gate reads
    perfectly well -- or worse, pass one it misreads.
    """
    out: list[str] = []
    fenced = False
    for line in text.split("\n"):
        if _FENCE_RE.match(line):
            fenced = not fenced          # the fence line itself is never a marker
            continue
        if fenced or _QUOTE_RE.match(line):
            continue
        out.append(line)
    return out


def render(agent: str, head: str, verdict: str, body: str = "") -> str:
    """The canonical record for one verdict, ready to post verbatim.

    `verdict` is "accept" or "changes". Everything that can silently cost a
    round trip is a ValueError here instead.
    """
    agent = (agent or "").strip()
    if not agent:
        raise ValueError("agent id is empty: pass --agent or set HILL_AGENT")
    if not _AGENT_RE.fullmatch(agent):
        raise ValueError(
            f"agent id {agent!r} is not one the gate can attribute. It must be a bare id "
            "matching [a-z0-9]+([._-][a-z0-9]+)* -- no spaces, '*', ':' or newlines, and "
            "no 'agent:' prefix (write 'fable-5.1-medium', not 'agent:fable-5.1-medium'). "
            "An id outside that shape is not rejected by the gate, it is ignored, and an "
            "unattributed review counts for nothing."
        )

    # Shell captures (`$(gh ... --jq .head.sha)`) arrive with a trailing newline;
    # trimming that is not guessing, but anything else about the value must be
    # exactly right because the gate compares it to the head byte for byte.
    head = (head or "").strip()
    if not _SHA_RE.fullmatch(head):
        raise ValueError(
            f"head {head!r} is not a reviewed SHA: the marker needs the full 40-character "
            "lowercase commit sha (git rev-parse HEAD), not an abbreviation, not uppercase"
        )

    key = (verdict or "").strip().lower()
    if key not in _VERDICT_WORDS:
        raise ValueError(
            f"unknown verdict {verdict!r}: expected 'accept' or 'changes' "
            f"(rendered as {_VERDICT_WORDS['accept']!r} / {_VERDICT_WORDS['changes']!r})"
        )

    body = (body or "").strip()
    for i, line in enumerate(_unquoted_lines(body), 1):
        if _MARKER_SHAPE_RE.match(line):
            raise ValueError(
                f"body line {i} is shaped like a marker and would be parsed as one: "
                f"{line.strip()!r}. A record must cast exactly one verdict -- put the "
                "quoted grammar in a code fence or a blockquote, which the gate skips."
            )

    # Blank lines between markers are not cosmetic: Markdown would otherwise fold
    # the three lines into one paragraph, and a marker that is not alone on its
    # line is not a marker.
    text = (f"**From:** {agent}\n"
            f"\n**HEAD reviewed:** `{head}`\n"
            f"\n**Verdict:** {_VERDICT_WORDS[key]}\n")
    if body:
        text += "\n" + body + "\n"
    return text


def current_head(repo: str, pr: int) -> str | None:
    """The PR's head SHA according to GitHub, or None if we could not find out.

    None means unknown -- offline, unauthenticated, no such PR, a shape we did
    not recognise. It never means unchanged; the caller has to keep those apart.
    """
    try:
        proc = subprocess.run(
            ["gh", "api", f"repos/{repo}/pulls/{pr}", "--jq", ".head.sha"],
            capture_output=True, text=True, timeout=_GH_TIMEOUT,
        )
    except Exception:
        return None                      # gh missing, timed out, not executable
    if proc.returncode != 0:
        return None
    sha = (proc.stdout or "").strip().lower()
    return sha if _SHA_RE.fullmatch(sha) else None


def submit(repo: str, pr: int, head: str, verdict: str, agent: str,
           body: str = "", dry_run: bool = False, force: bool = False) -> dict:
    """Render the record and post it as a review comment on `pr`.

    Render happens first so a typo fails before any network call. Then, unless
    `force`, the reviewed head is checked against the live one: a verdict bound
    to a stale head is precisely what the gate rejects, so catching it here saves
    the round trip that catching it there costs.
    """
    text = render(agent=agent, head=head, verdict=verdict, body=body)
    reviewed = head.strip().lower()      # render() already proved the shape

    extra: dict = {}
    if not force:
        cur = current_head(repo, pr)
        if cur is None:
            # Unknown must not quietly become agreement: say the binding is
            # unconfirmed so the caller can decide, rather than implying we checked.
            extra["head_unverified"] = True
        elif cur != reviewed:
            return {"ok": False, "error": "head moved", "current": cur, "reviewed": reviewed}

    if dry_run:
        return {"ok": True, "dry_run": True, "marker": text, **extra}

    # A COMMENTED review, never APPROVED: several agents share one write account,
    # and GitHub refuses an approving review on a PR opened by that same account.
    # The verdict lives in the markers, so the review *event* carries no decision.
    # --body-file - keeps the record off the command line, where a shell would
    # mangle the backticks around the sha.
    try:
        proc = subprocess.run(
            ["gh", "pr", "review", str(pr), "--repo", repo, "--comment", "--body-file", "-"],
            input=text, capture_output=True, text=True, timeout=_GH_TIMEOUT,
        )
    except Exception as exc:
        return {"ok": False, "marker": text, "url": None,
                "stderr": f"{type(exc).__name__}: {exc}", **extra}

    if proc.returncode != 0:
        return {"ok": False, "marker": text, "url": None,
                "stderr": (proc.stderr or "").strip(), **extra}

    # gh prints nothing on a successful review, so there is no URL to scrape --
    # the old regex over stdout always produced null here and made a landed
    # verdict read like a failed one. Read the record back instead: the verdict
    # only counts if the gate can see it bound to this exact head, so confirming
    # that is the same check the gate will make, not a cosmetic lookup.
    posted = _find_review(repo, pr, head, text)
    if posted is None:
        return {"ok": False, "marker": text, "url": None,
                "stderr": "gh reported success but no review bound to "
                          f"{head} was found on re-read; check the pull request",
                **extra}
    return {"ok": True, "marker": text, "url": posted.get("html_url"),
            "review_id": posted.get("id"), "stderr": "", **extra}


def _find_review(repo, pr, head, text):
    """The review we just posted, as the gate would see it: bound to this head."""
    marker = text.strip().splitlines()[0].strip()
    try:
        out = subprocess.run(
            ["gh", "api", f"repos/{repo}/pulls/{pr}/reviews", "--paginate"],
            capture_output=True, text=True, timeout=_GH_TIMEOUT,
        )
        if out.returncode != 0:
            return None
        reviews = json.loads(out.stdout or "[]")
    except Exception:
        return None
    hits = [r for r in reviews
            if r.get("commit_id") == head and marker in (r.get("body") or "")]
    return hits[-1] if hits else None
