"""Tests for hill_lib.workspace against real git repositories.

Nothing here is mocked. The module's entire job is to read git correctly — what
a squash merge does to ancestry, when `status --porcelain` prints a line, when
@{u} resolves — and a fake git would only test the test author's idea of git.
Each case builds a bare "origin", a clone, and worktrees in a temp directory, so
the assertions are about git's real answers and no test touches the network.

Commits pass identity with -c so the suite runs on a machine with no global git
identity configured.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hill_lib import workspace  # noqa: E402

GIT_ID = ["-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false"]


def git(cwd: Path, *args: str) -> str:
    p = subprocess.run(["git", *GIT_ID, "-C", str(cwd), *args],
                       capture_output=True, text=True)
    if p.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} in {cwd} failed:\n{p.stderr}")
    return p.stdout.strip()


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


class WorkspaceFixture(unittest.TestCase):
    """One temp machine: a bare origin, a main clone, four worktrees.

    The worktrees differ in exactly one respect each, so a failing assertion
    names the condition that broke rather than a combination of them.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="hill-ws-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

        self.origin = self.tmp / "origin.git"
        git(self.tmp, "init", "--bare", "-b", "main", str(self.origin))

        self.main = self.tmp / "product"
        subprocess.run(["git", *GIT_ID, "clone", str(self.origin), str(self.main)],
                       capture_output=True, text=True, check=True)
        # An empty clone leaves HEAD unborn; name it so the fixture does not
        # depend on the machine's init.defaultBranch.
        git(self.main, "symbolic-ref", "HEAD", "refs/heads/main")
        write(self.main / "README.md", "seed\n")
        # Real workspaces ignore these; the fixture does too, so that a clean
        # worktree can still contain the directories the old sweep destroyed.
        write(self.main / ".gitignore", "vendor/\nstorage/\n")
        git(self.main, "add", "-A")
        git(self.main, "commit", "-m", "seed")
        git(self.main, "push", "-u", "origin", "main")

        self.done = self._worktree("done", "wt-done", push=True, land=True)
        self.dirty = self._worktree("busy", "wt-dirty", push=True, land=True)
        write(self.dirty / "scratch.txt", "work in progress\n")
        self.ahead = self._worktree("ahead", "wt-ahead", push=True, land=False)
        write(self.ahead / "more.txt", "later\n")
        git(self.ahead, "add", "-A")
        git(self.ahead, "commit", "-m", "not pushed")
        self.noup = self._worktree("solo", "wt-noup", push=False, land=False)
        # Landed by squash merge: origin/main has the work, but as a new commit,
        # so the branch tip is an ancestor of nothing. This is the case that
        # makes `git branch --merged` the wrong question on its own.
        self.squashed = self._worktree("squashed", "wt-squash", push=True, land=False)
        git(self.main, "merge", "--squash", "squashed")
        git(self.main, "commit", "-m", "land squashed")
        git(self.main, "push")

    def _worktree(self, branch: str, dirname: str, push: bool, land: bool) -> Path:
        path = self.tmp / dirname
        git(self.main, "branch", branch, "main")
        git(self.main, "worktree", "add", str(path), branch)
        write(path / f"{branch}.txt", f"{branch}\n")
        git(path, "add", "-A")
        git(path, "commit", "-m", f"work on {branch}")
        # Every workspace carries the directories the earlier blanket cleanup
        # destroyed, ignored by git, so "clean" here means what it means in the
        # real ones.
        write(path / "vendor" / "lib.txt", "third party\n")
        write(path / "storage" / "app.log", "logs\n")
        if push:
            git(path, "push", "-u", "origin", branch)
        if land:
            git(self.main, "merge", "--no-ff", "-m", f"land {branch}", branch)
            git(self.main, "push")
        return path

    def rows(self) -> dict[str, dict]:
        """scan() keyed by directory name."""
        return {Path(r["path"]).name: r for r in workspace.scan(roots=[self.tmp])}


class ScanTest(WorkspaceFixture):

    def test_clean_delivered_worktree_is_safe_to_remove(self):
        row = self.rows()["wt-done"]
        self.assertTrue(row["is_worktree"])
        self.assertEqual(0, row["dirty"])
        self.assertEqual(0, row["unpushed"])
        self.assertTrue(row["has_upstream"])
        self.assertEqual("done", row["branch"])
        self.assertTrue(row["safe_to_remove"], row["reason"])
        self.assertIn("clean and delivered", row["reason"])

    def test_uncommitted_changes_are_never_safe(self):
        row = self.rows()["wt-dirty"]
        self.assertFalse(row["safe_to_remove"])
        self.assertGreaterEqual(row["dirty"], 1)
        self.assertIn("uncommitted changes", row["reason"])

    def test_unpushed_commit_is_never_safe(self):
        row = self.rows()["wt-ahead"]
        self.assertFalse(row["safe_to_remove"])
        self.assertEqual(0, row["dirty"])
        self.assertEqual(1, row["unpushed"])
        self.assertIn("not pushed", row["reason"])

    def test_missing_upstream_is_never_safe(self):
        row = self.rows()["wt-noup"]
        self.assertFalse(row["safe_to_remove"])
        self.assertFalse(row["has_upstream"])
        self.assertEqual("no upstream branch", row["reason"])

    def test_main_checkout_is_never_safe(self):
        row = self.rows()["product"]
        self.assertFalse(row["is_worktree"])
        self.assertFalse(row["safe_to_remove"])
        self.assertEqual("main checkout", row["reason"])

    def test_squash_merge_defeats_ancestry_and_is_not_guessed(self):
        # Ancestry alone reports the delivered branch as undelivered...
        ok, reason = workspace.delivered(self.squashed, "squashed", check_remote=False)
        self.assertFalse(ok)
        self.assertIn("not merged", reason)

        # ...and with no GitHub remote to ask, scan() says so rather than
        # guessing either way. Unknown means keep.
        row = self.rows()["wt-squash"]
        self.assertEqual(0, row["dirty"])
        self.assertEqual(0, row["unpushed"])
        self.assertTrue(row["has_upstream"])
        self.assertFalse(row["safe_to_remove"])
        self.assertEqual("could not verify delivery", row["reason"])

    def test_rows_are_sorted_dirty_first(self):
        rows = workspace.scan(roots=[self.tmp])
        self.assertTrue(rows[0]["dirty"], rows[0]["path"])
        flags = [bool(r["dirty"]) for r in rows]
        self.assertEqual(sorted(flags, reverse=True), flags)


class CleanupTest(WorkspaceFixture):

    def test_dry_run_deletes_nothing(self):
        res = workspace.cleanup(apply=False, roots=[self.tmp])
        for path in (self.done, self.dirty, self.ahead, self.noup, self.squashed, self.main):
            self.assertTrue(path.is_dir(), f"{path} was deleted by a dry run")
        self.assertEqual(["wt-done"], [Path(p).name for p in res["removed"]])
        self.assertIn("wt-dirty", [Path(k["path"]).name for k in res["kept"]])

    def test_apply_removes_only_the_safe_worktree(self):
        res = workspace.cleanup(apply=True, roots=[self.tmp])

        self.assertEqual(["wt-done"], [Path(p).name for p in res["removed"]])
        self.assertEqual([], res["errors"])
        self.assertFalse(self.done.exists())

        for path in (self.dirty, self.ahead, self.noup, self.squashed, self.main):
            self.assertTrue(path.is_dir(), f"{path} should have been kept")
        kept = {Path(k["path"]).name for k in res["kept"]}
        self.assertEqual({"wt-dirty", "wt-ahead", "wt-noup", "wt-squash", "product"}, kept)

        # The incident this module exists to prevent: nothing reaches inside a
        # workspace that is being kept.
        self.assertTrue((self.dirty / "vendor" / "lib.txt").is_file())
        self.assertTrue((self.dirty / "storage" / "app.log").is_file())
        self.assertTrue((self.dirty / "scratch.txt").is_file())

        # The delivered branch goes with its worktree; the others stay.
        branches = git(self.main, "branch", "--format=%(refname:short)").split()
        self.assertNotIn("done", branches)
        self.assertIn("busy", branches)
        self.assertIn("ahead", branches)
        self.assertIn("solo", branches)
        self.assertIn("squashed", branches)


class DeliveredTest(unittest.TestCase):

    def test_no_origin_main_is_not_delivered_and_does_not_raise(self):
        tmp = Path(tempfile.mkdtemp(prefix="hill-ws-solo-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        repo = tmp / "lonely"
        repo.mkdir()
        git(repo, "init", "-b", "main")
        write(repo / "a.txt", "a\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-m", "only commit")

        ok, reason = workspace.delivered(repo, "main", check_remote=False)
        self.assertFalse(ok)
        self.assertTrue(reason.strip())


if __name__ == "__main__":
    unittest.main()


class OrphanedContainerTest(WorkspaceFixture):
    """A directory of worktrees whose parent clone is gone must still be seen.

    Found by walking into it: 220 MB of quarantined lane worktrees on this
    machine were invisible to `hill workspaces`. Their container has no `.git`,
    so the root scan dropped it, and the second pass could not reach them
    either because it walks a repo's own `git worktree list` and the clone that
    owned them had been deleted. Nothing was wrong with the safety rules --
    handed the container directly the tool refuses every one of them with
    "could not read git state". It simply never looked, and a workspace tool
    that silently omits the largest reclaimable thing on the disk is reporting
    what it could parse, not what is there.
    """

    def quarantine(self) -> Path:
        """Move a worktree into a plain directory and orphan it."""
        box = self.tmp / ".lanes-quarantine"
        box.mkdir()
        moved = box / "orphan"
        shutil.move(str(self.done), str(moved))
        # Break the link the way a deleted parent clone does: the .git file
        # still points at an admin dir that is no longer there.
        (moved / ".git").write_text("gitdir: /nonexistent/.git/worktrees/orphan\n",
                                    encoding="utf-8")
        return moved

    def test_an_orphaned_worktree_in_a_container_is_found(self):
        moved = self.quarantine()
        found = {Path(r["path"]).name for r in workspace.scan(roots=[self.tmp])}
        self.assertIn(moved.name, found)

    def test_it_is_never_offered_for_removal(self):
        # The safety property that already held, pinned so discovery cannot
        # quietly start deleting what it cannot verify.
        self.quarantine()
        rows = {Path(r["path"]).name: r for r in workspace.scan(roots=[self.tmp])}
        row = rows["orphan"]
        self.assertFalse(row["safe_to_remove"])
        self.assertIn("git", row["reason"])

    def test_a_container_of_ordinary_directories_adds_nothing(self):
        # One level down, and only things that are actually checkouts, so a
        # directory of notes does not become a workspace listing.
        box = self.tmp / "notes"
        (box / "a").mkdir(parents=True)
        (box / "b").mkdir(parents=True)
        write(box / "a" / "note.txt", "hello\n")
        found = {Path(r["path"]).name for r in workspace.scan(roots=[self.tmp])}
        self.assertNotIn("a", found)
        self.assertNotIn("b", found)


class DisplayFlagTest(unittest.TestCase):
    """The word printed beside a workspace, which is all most readers see.

    Kept as a pure function of a row so the two cases that look alike -- an
    orphan git cannot read, and a fresh repo whose branch has no commits yet --
    stay distinguishable. Keying on the missing head alone labelled
    a real checkout "unreadable" while it held 27 uncommitted files, which is the opposite of what a cleanup tool exists to surface.
    """

    @staticmethod
    def flag(row):
        if not row.get("head") and not row.get("branch"):
            return "unreadable"
        if row["dirty"]:
            return "dirty"
        if row["unpushed"]:
            return "unpushed"
        return "clean"

    def test_orphan_with_no_head_and_no_branch_is_unreadable(self):
        self.assertEqual(self.flag(
            {"head": "", "branch": None, "dirty": 0, "unpushed": 0}), "unreadable")

    def test_unborn_branch_with_uncommitted_work_is_dirty_not_unreadable(self):
        self.assertEqual(self.flag(
            {"head": "", "branch": "main", "dirty": 27, "unpushed": 0}), "dirty")

    def test_an_ordinary_clean_checkout_is_clean(self):
        self.assertEqual(self.flag(
            {"head": "e397ec15", "branch": "main", "dirty": 0, "unpushed": 0}), "clean")

    def test_unpushed_outranks_clean(self):
        self.assertEqual(self.flag(
            {"head": "e397ec15", "branch": "x", "dirty": 0, "unpushed": 2}), "unpushed")
