#!/usr/bin/env python3
"""Collect a board snapshot: GitHub delivery state, local workspaces, real usage.

Every figure names its source. Where a source cannot answer, the field says so
rather than defaulting to zero -- an unknown reported as 0 is the failure this
board exists to avoid (ai-team#128).

Reads GitHub and the local machine, writes one JSON snapshot. Every field
records where it came from and what it does NOT check, because a dashboard
that looks confident about a number it never verified is worse than one that
says "unknown" (ai-team#128).
"""
import json, subprocess, datetime, os, re, sys
from collections import defaultdict

from . import db as _db
from . import live as _live

REPOS = ["BelimbingApp/blb-people", "BelimbingApp/ai-team",
         "BelimbingApp/belimbing", "BelimbingApp/blb-people-connector",
         "SB-Tape/blb-sbg"]

def gh(path, jq=None):
    cmd = ["gh", "api", path]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if out.returncode != 0:
            return None
        return json.loads(out.stdout) if out.stdout.strip() else None
    except Exception:
        return None

def agent_of(labels):
    for l in labels:
        n = l["name"] if isinstance(l, dict) else l
        if n.startswith("agent:"):
            return n[6:]
    return None



GATE_CHECK = "Independent review"


def waiting_on(draft, red, pending, mergeable_state, head_verdict_):
    """Who the lane is actually waiting on.

    The review gate is not CI. Bucketing it as CI sends a reader off to hunt for
    a broken test when the lane is in fact waiting on a person -- which is what
    this board reported for blb-people#483, the first live lane it ever saw.
    """
    broken = [n for n in red if n != GATE_CHECK]
    gate_red = GATE_CHECK in red
    if draft:
        return "author (draft)"
    if broken:
        return "CI"
    if gate_red and head_verdict_ == "changes":
        return "author (changes requested)"
    if gate_red and head_verdict_ == "accept":
        return "gate disagrees (accept at head, gate red)"
    if gate_red:
        return "a reviewer (none at this head)"
    if pending:
        return "CI (running)"
    if mergeable_state == "blocked":
        return "human (approval)"
    if mergeable_state == "clean":
        return "nothing — landable"
    return "unknown"


_FROM = re.compile(r"^\**\s*From\s*:\**\s*(.+?)\s*$", re.M)
_VERD = re.compile(r"^\**\s*Verdict\s*:\**\s*(.+?)\s*$", re.M)
# The gate binds a verdict to a head through BOTH the marker and the review's
# commit_id, because a rebase can rewrite commit_id on an older review.
_HEADM = re.compile(r"^\**\s*HEAD reviewed\s*:\**\s*`?([0-9a-f]{40})`?\s*$", re.M | re.I)


def head_verdict(gh, full, number, sha, author):
    """Is there a verdict from someone other than the author, bound to THIS head?

    Deliberately weaker than review_gate.sh and not a second copy of it: the
    gate also handles carry-forward across a clean base merge, reviewer-supplied
    finding tests, and unbound approvals. This answers only "has anyone looked at
    this exact commit yet", which is what separates "waiting on a reviewer" from
    "waiting on the author". Where the two disagree, the gate is right and the
    disagreement is itself worth surfacing -- so it is, as "gate disagrees".
    """
    if not sha:
        return None
    for r in (gh(f"repos/{full}/pulls/{number}/reviews?per_page=100") or []):
        if r.get("state") == "DISMISSED" or r.get("commit_id") != sha:
            continue
        body = r.get("body") or ""
        mf, mv, mh = _FROM.search(body), _VERD.search(body), _HEADM.search(body)
        if not (mf and mv and mh and mh.group(1) == sha):
            continue
        if mf.group(1).strip().strip("*").strip() == author:
            continue
        return "accept" if "accept" in mv.group(1).lower() or "approve" in mv.group(1).lower() else "changes"
    return None


def collect():
    now = datetime.datetime.now(datetime.timezone.utc)
    snap = {
        "collected_at": now.isoformat(timespec="seconds"),
        "repos": {}, "agents": {}, "open_lanes": [], "daily": {},
        "workspaces": [], "quota": {}, "coverage": [],
    }

    # ---- GitHub: open PRs and recent merges -------------------------------------
    for full in REPOS:
        short = full.split("/")[1]
        rec = {"full": full, "open": 0, "merged_24h": 0, "merged_total_seen": 0}
        opens = gh(f"repos/{full}/pulls?state=open&per_page=100") or []
        rec["open"] = len(opens)
        for pr in opens:
            labels = [l["name"] for l in pr.get("labels", [])]
            agent = agent_of(pr.get("labels", []))
            det = gh(f"repos/{full}/pulls/{pr['number']}") or {}
            sha = (det.get("head") or {}).get("sha", "")
            checks = gh(f"repos/{full}/commits/{sha}/check-runs?per_page=100") if sha else None
            latest = {}
            for c in ((checks or {}).get("check_runs") or []):
                k = c["name"]
                if k not in latest or (c.get("started_at") or "") > (latest[k].get("started_at") or ""):
                    latest[k] = c
            red = [n for n, c in latest.items()
                   if c.get("conclusion") not in ("success", "skipped", "neutral", None)]
            pending = [n for n, c in latest.items() if c.get("conclusion") is None]
            ms = det.get("mergeable_state") or "unknown"
            updated = pr["updated_at"]
            age_h = round((now - datetime.datetime.fromisoformat(updated.replace("Z", "+00:00"))).total_seconds() / 3600, 1)
            hv = (head_verdict(gh, full, pr["number"], sha, agent)
                  if GATE_CHECK in red else None)
            waiting = waiting_on(pr.get("draft", False), red, pending, ms, hv)
            snap["open_lanes"].append({
                "repo": short, "number": pr["number"],
                "title": pr["title"][:80] + ("…" if len(pr["title"]) > 80 else ""),
                "agent": agent, "draft": pr.get("draft", False), "state": ms,
                "red": red, "pending": pending, "waiting_on": waiting,
                "head_verdict": hv,
                "idle_hours": age_h, "labels": labels, "url": pr["html_url"],
            })
        closed = gh(f"repos/{full}/pulls?state=closed&sort=updated&direction=desc&per_page=100") or []
        oldest = None
        for pr in closed:
            if not pr.get("merged_at"): continue
            rec["merged_total_seen"] += 1
            if oldest is None or pr["merged_at"] < oldest: oldest = pr["merged_at"]
            try:
                _o = datetime.datetime.fromisoformat(pr["created_at"].replace("Z","+00:00"))
                _m = datetime.datetime.fromisoformat(pr["merged_at"].replace("Z","+00:00"))
                snap.setdefault("_lead", []).append({"h": (_m-_o).total_seconds()/3600,
                    "agent": agent_of(pr.get("labels", [])) or "(unlabelled)", "day": pr["merged_at"][:10]})
            except Exception: pass
            day = pr["merged_at"][:10]
            snap["daily"][day] = snap["daily"].get(day, 0) + 1
            age = (now - datetime.datetime.fromisoformat(pr["merged_at"].replace("Z", "+00:00"))).total_seconds()
            agent = agent_of(pr.get("labels", [])) or "(unlabelled)"
            a = snap["agents"].setdefault(agent, {"merged": 0, "merged_24h": 0, "accepts": 0,
                                                  "changes": 0, "blocked_prs": 0, "last_seen": None, "repos": []})
            a["merged"] += 1
            if short not in a["repos"]: a["repos"].append(short)
            if not a["last_seen"] or pr["merged_at"] > a["last_seen"]: a["last_seen"] = pr["merged_at"]
            if age <= 86400:
                rec["merged_24h"] += 1; a["merged_24h"] += 1
        rec["oldest_merge_seen"] = oldest
        snap["repos"][short] = rec

    # A repo's read is capped at 100 closed PRs, so before its oldest seen merge we
    # have no data -- which is not the same as no deliveries. Complete coverage
    # starts at the LATEST of those per-repo horizons.
    _h = [r["oldest_merge_seen"][:10] for r in snap["repos"].values() if r.get("oldest_merge_seen")]
    snap["coverage_from"] = max(_h) if _h else None

    # ---- verdicts given, over the recent set ------------------------------------
    FROM, VERD = _FROM, _VERD
    for full in REPOS:
        short = full.split("/")[1]
        closed = gh(f"repos/{full}/pulls?state=closed&sort=updated&direction=desc&per_page=25") or []
        for pr in closed[:25]:
            if not pr.get("merged_at"): continue
            revs = gh(f"repos/{full}/pulls/{pr['number']}/reviews?per_page=100") or []
            for r in revs:
                body = r.get("body") or ""
                mf, mv = FROM.search(body), VERD.search(body)
                if not (mf and mv): continue
                who = mf.group(1).strip().strip("*").strip()
                verdict = mv.group(1).lower()
                a = snap["agents"].setdefault(who, {"merged": 0, "merged_24h": 0, "accepts": 0,
                                                    "changes": 0, "blocked_prs": 0, "last_seen": None, "repos": []})
                if "accept" in verdict: a["accepts"] += 1
                else: a["changes"] += 1
                if r.get("submitted_at") and (not a["last_seen"] or r["submitted_at"] > a["last_seen"]):
                    a["last_seen"] = r["submitted_at"]

    # ---- halts -------------------------------------------------------------------
    snap["halts"] = []
    for full in REPOS:
        hs = gh(f"repos/{full}/issues?labels=ops:halt&state=open&per_page=10") or []
        for h in hs:
            snap["halts"].append({"repo": full.split("/")[1], "number": h["number"], "title": h["title"][:70]})

    # ---- lead time: how long a delivery takes from opening to landing -----------
    _lead = snap.pop("_lead", [])
    def _pct(v, p):
        if not v: return None
        v = sorted(v); k = (len(v)-1)*p
        lo, hi = int(k), min(int(k)+1, len(v)-1)
        return round(v[lo] + (v[hi]-v[lo])*(k-lo), 1)
    snap["lead_time"] = {
        "n": len(_lead),
        "median_h": _pct([x["h"] for x in _lead], .5),
        "p90_h": _pct([x["h"] for x in _lead], .9),
        "by_agent": {},
    }
    _byag = defaultdict(list)
    for x in _lead: _byag[x["agent"]].append(x["h"])
    for a, v in _byag.items():
        snap["lead_time"]["by_agent"][a] = {"n": len(v), "median_h": _pct(v, .5)}

    # ---- model usage: measured from Claude Code transcripts on this machine -----
    # Tokens are read from each session's own records. Money is NOT derived here:
    # no price is known to this collector, so the page multiplies by a rate the
    # owner supplies and labels the result an estimate.
    import glob
    IDLE_CAP = 300  # seconds; a longer gap is idle time, not work
    sessions = []
    cutoff = (now - datetime.timedelta(days=14)).isoformat()
    for path in glob.glob(os.path.expanduser("~/.claude/projects/*/*.jsonl")):
        try:
            if datetime.datetime.fromtimestamp(os.path.getmtime(path), datetime.timezone.utc).isoformat() < cutoff:
                continue
        except Exception:
            continue
        tok = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "thinking": 0}
        agent = None; model = None; stamps = []; turns = 0
        try:
            for line in open(path, encoding="utf-8", errors="replace"):
                if '"usage"' not in line and '"agentName"' not in line and '"timestamp"' not in line:
                    continue
                try: o = json.loads(line)
                except Exception: continue
                if o.get("agentName"): agent = o["agentName"]
                if o.get("timestamp"): stamps.append(o["timestamp"])
                m = o.get("message") or {}
                if m.get("model") and not str(m["model"]).startswith("<"): model = m["model"]
                u = m.get("usage")
                if not u: continue
                turns += 1
                tok["input"] += u.get("input_tokens", 0) or 0
                tok["output"] += u.get("output_tokens", 0) or 0
                tok["cache_read"] += u.get("cache_read_input_tokens", 0) or 0
                tok["cache_write"] += u.get("cache_creation_input_tokens", 0) or 0
                tok["thinking"] += ((u.get("output_tokens_details") or {}).get("thinking_tokens", 0)) or 0
        except Exception:
            continue
        if not turns: continue
        stamps.sort()
        span = active = 0.0
        if len(stamps) > 1:
            try:
                pts = [datetime.datetime.fromisoformat(t.replace("Z", "+00:00")) for t in stamps]
                span = (pts[-1] - pts[0]).total_seconds()
                active = sum(min((pts[i+1]-pts[i]).total_seconds(), IDLE_CAP) for i in range(len(pts)-1))
            except Exception: pass
        sessions.append({
            "id": os.path.basename(path)[:8], "project": os.path.basename(os.path.dirname(path)),
            "agent": agent, "model": model, "turns": turns, "tokens": tok,
            "first": stamps[0] if stamps else None, "last": stamps[-1] if stamps else None,
            "span_h": round(span/3600, 2), "active_h": round(active/3600, 2),
        })
    sessions.sort(key=lambda s: s["last"] or "", reverse=True)
    snap["sessions"] = sessions
    agg = {"turns": 0, "span_h": 0.0, "active_h": 0.0,
           "tokens": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "thinking": 0}}
    by_model = {}
    for s_ in sessions:
        agg["turns"] += s_["turns"]; agg["span_h"] += s_["span_h"]; agg["active_h"] += s_["active_h"]
        for k, v in s_["tokens"].items(): agg["tokens"][k] += v
        m = s_["model"] or "unknown"
        bm = by_model.setdefault(m, {"turns": 0, "active_h": 0.0,
             "tokens": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "thinking": 0}})
        bm["turns"] += s_["turns"]; bm["active_h"] += s_["active_h"]
        for k, v in s_["tokens"].items(): bm["tokens"][k] += v
    agg["span_h"] = round(agg["span_h"], 1); agg["active_h"] = round(agg["active_h"], 1)
    for m in by_model.values(): m["active_h"] = round(m["active_h"], 1)
    snap["usage"] = {"total": agg, "by_model": by_model, "window_days": 14,
                     "source": "Claude Code session transcripts on this machine",
                     "not_covered": "sessions on other machines, and any harness that is not Claude Code"}

    # ---- local workspaces --------------------------------------------------------
    def sh(c):
        try: return subprocess.run(c, shell=True, capture_output=True, text=True, timeout=30).stdout.strip()
        except Exception: return ""

    roots = ["/home/kiat/repo/opus-max", "/home/kiat/repo/sbg", "/home/kiat/repo/laravel"]
    for root in roots:
        if not os.path.isdir(root): continue
        for name in sorted(os.listdir(root)):
            p = os.path.join(root, name)
            if not os.path.isdir(os.path.join(p, ".git")) and not os.path.isfile(os.path.join(p, ".git")):
                continue
            branch = sh(f"git -C {p} rev-parse --abbrev-ref HEAD 2>/dev/null")
            dirty = sh(f"git -C {p} status --porcelain 2>/dev/null")
            head = sh(f"git -C {p} rev-parse --short HEAD 2>/dev/null")
            unpushed = sh(f"git -C {p} log --oneline @{{u}}..HEAD 2>/dev/null")
            size = sh(f"du -sm {p} 2>/dev/null | cut -f1")
            snap["workspaces"].append({
                "path": p.replace("/home/kiat/", "~/"), "branch": branch, "head": head,
                "dirty_files": len([x for x in dirty.splitlines() if x.strip()]),
                "unpushed": len([x for x in unpushed.splitlines() if x.strip()]),
                "size_mb": int(size) if size.isdigit() else None,
            })

    # ---- quota -------------------------------------------------------------------
    rl = gh("rate_limit")
    if rl:
        core = rl["resources"]["core"]
        snap["quota"]["github_core"] = {
            "account": sh("gh api user --jq .login"),
            "remaining": core["remaining"], "limit": core["limit"],
            "resets_at": datetime.datetime.fromtimestamp(core["reset"], datetime.timezone.utc).isoformat(timespec="seconds"),
            "scope": "shared by every agent using this account",
        }
    snap["quota"]["model_tokens"] = {"state": "unknown", "why": "no harness on this machine reports model quota"}

    # ---- coverage declarations (what each number does NOT check) -----------------
    snap["coverage"] = [
      {"signal": "waiting_on", "checks": "draft flag, latest check-run per name, mergeable_state, and — when the "
                 "review gate is red — whether any non-author verdict marker is bound to the exact head",
       "not_checked": "whether a human intends to act; whether a reviewer is reachable or rate-limited. "
                 "The head-verdict scan is NOT review_gate.sh: it ignores carry-forward across a clean base "
                 "merge, finding-test clearance, and unbound approvals, so the gate can and does overrule it"},
      {"signal": "landable", "checks": "mergeable_state == clean AND no red checks",
       "not_checked": "the AI Team review gate — a PR can be clean here and refused by review_gate.sh"},
      {"signal": "merged counts", "checks": "merged_at on the most recent 100 closed PRs per repo",
       "not_checked": "anything older than that window; unlabelled PRs are grouped, not attributed"},
      {"signal": "verdicts", "checks": "**From:**/**Verdict:** markers on reviews of the 25 most recent merges per repo",
       "not_checked": "verdicts written in other formats — two were missed this way on 2026-09-11"},
      {"signal": "agent last_seen", "checks": "newest merge or verdict timestamp",
       "not_checked": "liveness — an agent can be alive and idle, or dead and recently merged"},
      {"signal": "workspaces", "checks": "git status, unpushed commits, disk size on this machine",
       "not_checked": "other machines; whether a worktree's composed dependencies are stale"},
      {"signal": "model quota", "checks": "nothing",
       "not_checked": "everything — reported as unknown rather than zero"},
    ]

    # ---- the-hill's own state: the part GitHub cannot answer ----------------
    # Who holds which lane, who has ticked, what is unread. This is the
    # "waiting on whom" that a delivery record has no way to know.
    try:
        con = _db.connect()
        held = _db.claims(con)
        unread = {r[0]: r[1] for r in con.execute(
            """SELECT m.recipient, COUNT(*) FROM messages m
               WHERE NOT EXISTS (SELECT 1 FROM message_reads r
                                 WHERE r.message_id=m.id AND r.agent=m.recipient)
               GROUP BY m.recipient""")}
        snap["hill"] = {
            "claims": held,
            "live": _live.read_all(),
            "unread_by_agent": unread,
            "events": _db.events(con, 30),
            "source": "the-hill state on this machine",
        }
    except Exception as e:
        # A collector failure must never look like an empty board.
        snap["hill"] = {"error": f"{type(e).__name__}: {e}", "claims": [], "live": [],
                        "unread_by_agent": {}, "events": []}


    return snap


if __name__ == "__main__":
    print(json.dumps(collect(), indent=1))
