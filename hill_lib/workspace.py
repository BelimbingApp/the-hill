"""Worktree inventory and cleanup that cannot destroy unpublished work.

Agents on this machine create worktrees faster than anyone retires them: 31
checkouts and 8.4 GB when this was written, 11 of them holding changes that
exist nowhere else. The obvious fix is the one that already went wrong — a
blanket sweep deleted vendor/ and storage/ out of workspaces that were still in
use and broke local testing for hours. So this module removes a whole worktree
or nothing at all; it never reaches inside one to reclaim space.

BelimbingApp/ai-team#128 settled the rule: automatic cleanup must never destroy
unpublished work, and must handle squash merges. Those two pull in opposite
directions, and both failures are real. Keeping too much is how the disk filled
up and how someone ends up running rm by hand again; deleting too much costs
work that cannot be recovered. The resolution here is asymmetric: every check
answers "is this provably finished", and every failure — git erroring, gh
missing, a timeout — resolves to keep. Unknown means keep.

Squash merges are why `git branch --merged` is not enough on its own. A squash
merge rewrites the branch's commits into one new commit on main, so the branch
is not an ancestor of main and an ancestry test reads the delivered branch as
undelivered forever. That is a safe error in isolation but a dangerous one in
aggregate: a cleanup that never reclaims anything is a cleanup nobody trusts.
`delivered` therefore asks GitHub for a merged PR on the branch as a second
question, and only the pair of "no" answers means not delivered.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from . import db

# ~/.the-hill/worktrees is where new agent worktrees are meant to go; ~/repo is
# the layout that already exists and holds most of the 31.
WORKTREE_ROOT = db.HOME / "worktrees"
REPO_ROOT = Path.home() / "repo"

DEFAULT_BRANCHES = {"main", "master"}

# The directories the earlier blanket cleanup destroyed. Nothing here deletes a
# path inside a workspace, so these names should never reach a delete call; the
# set exists so that the one rmtree in this file refuses them outright and a
# later edit cannot quietly reintroduce that incident.
PROTECTED_NAMES = {"vendor", "node_modules", "storage"}

_ENV = {
    **os.environ,
    # Everything here is local. Never let a git or gh call sit waiting for
    # credentials on a terminal that nobody is watching.
    "GIT_TERMINAL_PROMPT": "0",
    # Read-only inspection of worktrees other agents are actively using: do not
    # take the index lock to refresh it. The cost is that a stat-dirty file can
    # be counted as modified, which errs towards keeping the checkout.
    "GIT_OPTIONAL_LOCKS": "0",
    "LC_ALL": "C",
}

_SLUG_RE = re.compile(r"github\.com[:/]+([^/]+/[^/]+?)(?:\.git)?/?$")


# --- plumbing -------------------------------------------------------------

def _run(args: list[str], cwd: Path | None = None, timeout: int = 20) -> tuple[bool, str]:
    """Run a command and never raise.

    Returns (succeeded, text) where text is stdout on success and stderr on
    failure, so a caller can put a real git message in an error string. Callers
    must check the flag before using the text — on failure it is a diagnostic,
    not a value.
    """
    try:
        p = subprocess.run(args, cwd=str(cwd) if cwd is not None else None, env=_ENV,
                           capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return False, ""
    if p.returncode == 0:
        return True, p.stdout.strip()
    return False, (p.stderr.strip() or p.stdout.strip())


def _git(path: Path, *args: str, timeout: int = 20) -> tuple[bool, str]:
    return _run(["git", "-C", str(path), *args], timeout=timeout)


def _display(path: Path) -> str:
    """Path with the home prefix shortened, for output only."""
    home = str(Path.home())
    s = str(path)
    return "~/" + s[len(home) + 1:] if s.startswith(home + "/") else s


def _kind(path: Path) -> str | None:
    """"worktree", "repo", or None for a directory that is neither."""
    dot = path / ".git"
    if dot.is_file():
        # A linked worktree records its admin dir in a .git *file*. A submodule
        # checkout looks identical from here, which is why the removal path
        # re-checks against `git worktree list` before deleting anything.
        return "worktree"
    if dot.is_dir():
        return "repo"
    return None


def _roots(roots: list[Path] | None) -> list[Path]:
    if roots is not None:
        return [Path(r) for r in roots]
    found = [WORKTREE_ROOT]
    if REPO_ROOT.is_dir():
        found += sorted(p for p in REPO_ROOT.iterdir() if p.is_dir() and not p.is_symlink())
    return found


def _linked_worktrees(path: Path) -> list[Path]:
    """Worktrees git says belong to this checkout, wherever they are on disk.

    Walking the roots only finds worktrees parked beside a clone, but the ones
    agents actually create sit inside a checkout under a dot directory —
    <repo>/.claude/worktrees/<name> and <root>/.ai-team-lanes/<lane>. On this
    machine that is 19 of the 20 linked worktrees, so a scan that only walks
    directories has nothing to offer. Git already knows where they are; asking
    it is cheaper and more accurate than guessing the naming conventions.
    """
    ok, out = _git(path, "worktree", "list", "--porcelain")
    if not ok:
        return []
    return [Path(ln[len("worktree "):]) for ln in out.splitlines()
            if ln.startswith("worktree ")]


def _checkouts(roots: list[Path] | None) -> list[Path]:
    """Every checkout under the given roots, in a stable order.

    A root is either a checkout itself (~/repo/bilimbi) or a directory holding
    checkouts (~/.the-hill/worktrees, and the per-agent clone roots such as
    ~/repo/opus-max, which is where most of them live). Only one level down:
    deeper recursion would walk into vendor/ and node_modules/ of every
    workspace for no gain — the worktrees further down are found by asking git
    instead. A bare repository has no .git entry and is skipped.
    """
    seen: set[Path] = set()
    out: list[Path] = []

    def add(path: Path) -> bool:
        if not _kind(path):
            return False
        key = path.resolve()
        if key in seen:
            return False
        seen.add(key)
        out.append(path)
        return True

    for root in _roots(roots):
        if not root.is_dir():
            continue
        if _kind(root):
            group = [root]
        else:
            group = sorted(p for p in root.iterdir() if p.is_dir() and not p.is_symlink())
        for path in group:
            add(path)

    # Second pass over what the roots found: a repo's own worktree list reaches
    # the ones nested inside it. Iterating over a copy keeps this to one hop, so
    # a worktree can never pull in an unrelated tree that git happens to share.
    for path in list(out):
        for linked in _linked_worktrees(path):
            add(linked)
    return out


def _size_mb(path: Path) -> int | None:
    """Disk used by the whole checkout, or None when it could not be measured."""
    ok, out = _run(["du", "-sm", "--", str(path)], timeout=30)
    if not ok:
        return None
    head = out.split(maxsplit=1)[0] if out else ""
    return int(head) if head.isdigit() else None


def _repo_name(path: Path) -> str:
    ok, url = _git(path, "remote", "get-url", "origin")
    if ok and url:
        name = url.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1]
        if name.endswith(".git"):
            name = name[:-4]
        if name:
            return name
    return path.name


def _slug(path: Path) -> str | None:
    """owner/name for a GitHub origin, else None (which disables the gh check)."""
    ok, url = _git(path, "remote", "get-url", "origin")
    if not ok or not url:
        return None
    m = _SLUG_RE.search(url.strip())
    return m.group(1) if m else None


# --- delivery -------------------------------------------------------------

def delivered(path: Path, branch: str, check_remote: bool = True) -> tuple[bool, str]:
    """Did this branch's work actually land? Returns (delivered, reason).

    Two questions, because one is not enough. Ancestry of origin/main settles an
    ordinary merge. A squash merge rewrites history, so the branch is not an
    ancestor of anything and only GitHub knows it landed — that is the second
    question. Never raises: any failure to answer is reported as not delivered
    with "could not verify delivery", because the caller deletes on True.
    """
    path = Path(path)
    base_seen = False
    for ref in ("origin/main", "origin/master"):
        ok, _ = _git(path, "rev-parse", "--verify", "--quiet", f"refs/remotes/{ref}")
        if not ok:
            continue
        base_seen = True
        merged, _ = _git(path, "merge-base", "--is-ancestor", branch, ref)
        if merged:
            return True, f"merged into {ref}"

    remote_answered = False
    if check_remote:
        slug = _slug(path)
        if slug and shutil.which("gh"):
            ok, out = _run(["gh", "pr", "list", "--repo", slug, "--head", branch,
                            "--state", "merged", "--json", "number"],
                           cwd=path, timeout=30)
            if ok:
                try:
                    prs = json.loads(out or "[]")
                except ValueError:
                    prs = None
                if isinstance(prs, list):
                    if prs:
                        return True, f"squash-merged in PR #{prs[0].get('number')}"
                    remote_answered = True

    if not base_seen:
        return False, "could not verify delivery"
    if check_remote and not remote_answered:
        # Ancestry says no, and the squash-merge question went unanswered: gh is
        # missing, not a GitHub remote, or it errored. That is unknown, not no.
        return False, "could not verify delivery"
    return False, "not merged and no merged PR"


# --- inventory ------------------------------------------------------------

def _inspect(path: Path) -> dict:
    kind = _kind(path)

    ok, branch = _git(path, "symbolic-ref", "--quiet", "--short", "HEAD")
    branch = branch if ok and branch else None  # None means detached HEAD

    ok_head, head = _git(path, "rev-parse", "--short", "HEAD")
    head = head if ok_head else ""

    ok_status, status = _git(path, "status", "--porcelain", timeout=60)
    dirty = len([ln for ln in status.splitlines() if ln.strip()]) if ok_status else 0

    has_upstream, _ = _git(path, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    unpushed = 0
    ok_count = True
    if has_upstream:
        ok_count, count = _git(path, "rev-list", "--count", "@{u}..HEAD")
        unpushed = int(count) if ok_count and count.isdigit() else 0

    # One flag for "git would not tell us". It stays a local: callers get a
    # reason they can read rather than a field they have to remember to check.
    readable = ok_status and ok_count and bool(head)

    row = {
        "path": _display(path),
        "repo": _repo_name(path),
        "branch": branch,
        "head": head,
        "dirty": dirty,
        "unpushed": unpushed,
        "has_upstream": bool(has_upstream),
        "size_mb": _size_mb(path),
        "is_worktree": kind == "worktree",
        "safe_to_remove": False,
        "reason": "",
    }

    # Ordered most-blocking first, and the delivery question is asked last
    # because it is the only one that can touch the network.
    if kind != "worktree":
        row["reason"] = "main checkout"
    elif not readable:
        row["reason"] = "could not read git state"
    elif dirty:
        row["reason"] = f"uncommitted changes ({dirty} file{'s' if dirty != 1 else ''})"
    elif branch is None:
        row["reason"] = "detached HEAD"
    elif branch in DEFAULT_BRANCHES:
        row["reason"] = "default branch"
    elif not row["has_upstream"]:
        row["reason"] = "no upstream branch"
    elif unpushed:
        row["reason"] = f"{unpushed} commit{'s' if unpushed != 1 else ''} not pushed"
    else:
        ok_delivered, why = delivered(path, branch)
        row["safe_to_remove"] = ok_delivered
        row["reason"] = f"clean and delivered ({why})" if ok_delivered else why
    return row


def _scan(roots: list[Path] | None) -> list[tuple[Path, dict]]:
    """scan() plus the real path, which cleanup needs and the contract omits."""
    pairs = [(p, _inspect(p)) for p in _checkouts(roots)]
    # Dirty first because that is what a human needs to deal with, largest first
    # within each group because that is what a human is reading the list for.
    pairs.sort(key=lambda pair: (0 if pair[1]["dirty"] else 1, -(pair[1]["size_mb"] or 0)))
    return pairs


def scan(roots: list[Path] | None = None) -> list[dict]:
    """Every checkout under `roots`, dirty first then largest first. Local only."""
    return [row for _, row in _scan(roots)]


# --- cleanup --------------------------------------------------------------

def _main_checkout(path: Path) -> Path | None:
    """The clone a linked worktree belongs to — `git worktree remove` runs there."""
    ok, common = _git(path, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if not ok or not common:
        return None
    d = Path(common)
    return d.parent if d.name == ".git" else d


def _is_registered(main: Path, path: Path) -> bool:
    """Is `path` a worktree git itself knows about? Guards the rmtree fallback."""
    ok, out = _git(main, "worktree", "list", "--porcelain")
    if not ok:
        return False
    target = str(path.resolve())
    for line in out.splitlines():
        if line.startswith("worktree ") and str(Path(line[9:]).resolve()) == target:
            return True
    return False


def _removable_dir(path: Path) -> bool:
    if not path.is_dir() or path.is_symlink():
        return False
    if path.name in PROTECTED_NAMES:
        return False
    if path.resolve() in (Path.home().resolve(), Path("/")):
        return False
    return (path / ".git").is_file()


def _remove(path: Path, row: dict) -> tuple[bool, str | None]:
    """Remove one worktree and its branch. Returns (removed, error message)."""
    main = _main_checkout(path)
    if main is None:
        return False, f"{row['path']}: cannot locate the main checkout"

    ok, out = _git(main, "worktree", "remove", str(path), timeout=180)
    if not ok:
        # No --force: `worktree remove` refuses a worktree with local changes,
        # which is a second opinion on scan()'s. Fall back only for a directory
        # git still lists as a worktree of this repo — a submodule has a .git
        # file too, and rmtree must never be pointed at one.
        if not _is_registered(main, path) or not _removable_dir(path):
            return False, f"{row['path']}: worktree remove failed ({out or 'no output'})"
        try:
            shutil.rmtree(path)
        except OSError as e:
            return False, f"{row['path']}: {e}"
        # prune only clears admin records whose directory is already gone, so it
        # follows the rmtree rather than preceding it.
        _git(main, "worktree", "prune")

    branch = row["branch"]
    if branch:
        # -d, never -D: git refuses a branch it cannot prove is merged into HEAD
        # or into its own upstream, which is a third guard. A squash-merged
        # branch passes it via the upstream that safe_to_remove already required.
        ok_branch, out_branch = _git(main, "branch", "-d", branch)
        if not ok_branch:
            return True, (f"{row['path']}: worktree removed, branch {branch} kept "
                          f"({out_branch or 'not fully merged'})")
    return True, None


def cleanup(apply: bool = False, roots: list[Path] | None = None) -> dict:
    """Remove finished worktrees. Dry run unless `apply`; deletes nothing else.

    With apply=False the "removed" list is what would go — the CLI prints it and
    then says nothing was deleted. Anything whose safe_to_remove is False is
    kept with the reason, and a removal that fails is reported as kept plus an
    error rather than retried harder.
    """
    result: dict = {"removed": [], "kept": [], "errors": []}
    for path, row in _scan(roots):
        if not row["safe_to_remove"]:
            result["kept"].append({"path": row["path"], "reason": row["reason"]})
            continue
        if not apply:
            result["removed"].append(row["path"])
            continue
        removed, error = _remove(path, row)
        if error:
            result["errors"].append(error)
        if removed:
            result["removed"].append(row["path"])
        else:
            result["kept"].append({"path": row["path"], "reason": "removal failed"})
    return result
