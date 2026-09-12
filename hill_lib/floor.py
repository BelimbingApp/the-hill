"""Who holds a lane, according to the only thing every machine can see.

SQLite settles races between agents on *this* machine. It cannot settle
anything between machines, because no other machine can read it -- so a local
claim is an observation about one host, not a decision about a lane. the-hill's
own rule says SQLite holds observations and messages, never decisions; `hill
claim` was breaking that rule the moment a second machine existed.

GitHub is the shared floor, and ai-team already settled how to read it. This
mirrors `claim.sh`'s two sources rather than inventing a third:

  1. the issue's `agent:<id>` label
  2. the open-PR registry -- any open pull request whose title or body carries
     the `(#N)` reference, or whose branch follows the claim convention while
     the PR carries an owner label

Neither is a lock. There is a window between reading them and writing your own
claim, and ai-team does not pretend otherwise: it detects a collision and names
the holder instead of preventing it. That is the honest guarantee and it is the
one reproduced here.

The third state matters most. Unreachable is not free: when GitHub cannot be
read, this says so, and the caller decides. Treating silence as "nobody holds
it" is how two machines end up on one lane.
"""
from __future__ import annotations

import json
import re
import subprocess

_GH_TIMEOUT = 30
_KEY_RE = re.compile(r"^(?P<repo>[A-Za-z0-9._/-]+)#(?P<num>\d+)$")
_FROM_RE = re.compile(
    r"^\*\*From:\*\*[ \t]*([a-z0-9]+(?:[._-][a-z0-9]+)*)(?:[ \t]|$)", re.M | re.I)


class Unreachable(RuntimeError):
    """GitHub could not be read. Not the same as nobody holding the lane."""


def parse_key(key: str, repos: list[str] | None = None) -> tuple[str, int] | None:
    """`blb-people#476` or `Owner/repo#476` -> ("Owner/repo", 476).

    A short name is resolved against the configured repositories so the same
    key a human types on one machine means the same lane on another.
    """
    m = _KEY_RE.match(key.strip())
    if not m:
        return None
    repo, num = m.group("repo"), int(m.group("num"))
    if "/" not in repo:
        from .collect import REPOS
        for full in (repos if repos is not None else REPOS):
            if full.split("/")[-1] == repo:
                return full, num
        return None
    return repo, num


def _gh(args: list[str]) -> str:
    try:
        proc = subprocess.run(["gh", *args], capture_output=True, text=True,
                              timeout=_GH_TIMEOUT)
    except Exception as exc:
        raise Unreachable(f"{type(exc).__name__}: {exc}") from None
    if proc.returncode != 0:
        raise Unreachable((proc.stderr or "gh failed").strip()[:200])
    return proc.stdout


def _agent_labels(labels) -> list[str]:
    out = []
    for label in labels or []:
        name = label["name"] if isinstance(label, dict) else str(label)
        if name.startswith("agent:"):
            out.append(name[len("agent:"):].strip().lower())
    return out


def holders(repo: str, number: int) -> dict:
    """Every agent GitHub says is on this lane, and where it saw them.

    Raises Unreachable rather than returning an empty answer, because an empty
    answer reads as "free" and that is the one thing it must never say by
    accident.
    """
    found: dict[str, list[str]] = {}

    issue = json.loads(_gh(["api", f"repos/{repo}/issues/{number}",
                            "--jq", "{labels: [.labels[].name], state: .state}"]) or "{}")
    for agent in _agent_labels(issue.get("labels")):
        found.setdefault(agent, []).append(f"{repo}#{number} label")

    prs = json.loads(_gh(["pr", "list", "--repo", repo, "--state", "open",
                          "--limit", "100", "--json",
                          "number,title,body,headRefName,labels,url"]) or "[]")
    reference = f"(#{number})"
    branch_re = re.compile(rf"(^|[-_/])issue-?{number}($|[-_/])")
    for pr in prs:
        text = (pr.get("title") or "") + "\n" + (pr.get("body") or "")
        labelled = _agent_labels(pr.get("labels"))
        # Same two shapes claim.sh accepts. The branch convention only counts
        # when the PR carries an owner label, so an unrelated branch cannot
        # block the queue.
        by_reference = reference in text
        by_branch = bool(labelled) and bool(branch_re.search(pr.get("headRefName") or ""))
        if not (by_reference or by_branch):
            continue
        where = pr.get("url") or f"{repo}#{pr.get('number')}"
        if labelled:
            for agent in labelled:
                found.setdefault(agent, []).append(where)
        else:
            # A claim PR with no agent label is claim.sh's half-claim: the body
            # marker is the only identity that survived. Reported under that
            # name so a half-claim is visible rather than reading as free.
            marker = _FROM_RE.search(pr.get("body") or "")
            found.setdefault(marker.group(1).lower() if marker else "(unlabelled)",
                             []).append(where + " (half-claim: no agent label)")

    return {"repo": repo, "number": number, "state": issue.get("state"),
            "holders": {a: sorted(set(w)) for a, w in found.items()}}


def lanes(repos: list[str] | None = None) -> dict:
    """Every lane the shared floor says is held, across the configured repos.

    `hill claims` used to answer from SQLite alone, which is this machine's
    opinion presented as the floor's. On a second host it shows an empty board
    that is not empty -- the same defect the claim path had.

    Partial is reported, never silently dropped: a repository that cannot be
    read appears under `unreachable` rather than contributing nothing and
    looking quiet.
    """
    if repos is None:
        from .collect import REPOS
        repos = REPOS

    held: list[dict] = []
    unreachable: list[dict] = []
    for repo in repos:
        try:
            prs = json.loads(_gh(["pr", "list", "--repo", repo, "--state", "open",
                                  "--limit", "100", "--json",
                                  "number,title,labels,url"]) or "[]")
            issues = json.loads(_gh(["issue", "list", "--repo", repo, "--state", "open",
                                     "--limit", "100", "--json",
                                     "number,title,labels,url"]) or "[]")
        except Unreachable as exc:
            unreachable.append({"repo": repo, "why": str(exc)})
            continue
        for kind, rows in (("pr", prs), ("issue", issues)):
            for row in rows:
                agents = _agent_labels(row.get("labels"))
                if not agents:
                    continue
                held.append({
                    "repo": repo, "kind": kind, "number": row.get("number"),
                    "agents": agents, "url": row.get("url"),
                    "title": (row.get("title") or "")[:70],
                    "task": sorted(
                        (label["name"] if isinstance(label, dict) else str(label))
                        for label in row.get("labels") or []
                        if str(label["name"] if isinstance(label, dict) else label)
                        .startswith("task:")),
                })
    held.sort(key=lambda r: (r["repo"], r["number"]))
    return {"held": held, "unreachable": unreachable, "repos": list(repos)}
