"""Reading who holds a lane from the only place every machine can see.

SQLite settles a race on one host and says nothing about any other, so these
tests are about the cross-machine answer: the two sources ai-team's claim.sh
reads, and the third state that matters more than either -- unreachable, which
must never render as free.
"""
import json
import re
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("HILL_HOME", tempfile.mkdtemp(prefix="hill-test-"))
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from hill_lib import floor  # noqa: E402

REPOS = ["Owner/blb-people", "Owner/ai-team"]


class ParseKeyTest(unittest.TestCase):
    def test_a_short_name_resolves_against_the_configured_repositories(self):
        # The same key typed on two machines has to mean the same lane.
        self.assertEqual(floor.parse_key("blb-people#476", REPOS), ("Owner/blb-people", 476))

    def test_a_fully_qualified_key_needs_no_configuration(self):
        self.assertEqual(floor.parse_key("SB-Tape/sbg-belim#70", REPOS),
                         ("SB-Tape/sbg-belim", 70))

    def test_an_unknown_short_name_is_none_rather_than_a_guess(self):
        self.assertIsNone(floor.parse_key("not-a-repo#1", REPOS))

    def test_things_that_are_not_lanes(self):
        for key in ("blb-people", "#476", "blb-people#", "", "blb-people#abc"):
            with self.subTest(key=key):
                self.assertIsNone(floor.parse_key(key, REPOS))


class HoldersTest(unittest.TestCase):
    def gh(self, issue_labels, prs):
        def fake(args):
            if args[0] == "api":
                return json.dumps({"labels": issue_labels, "state": "open"})
            return json.dumps(prs)
        return fake

    def holders(self, issue_labels=(), prs=()):
        with mock.patch.object(floor, "_gh", side_effect=self.gh(list(issue_labels), list(prs))):
            return floor.holders("Owner/repo", 476)["holders"]

    def test_an_unheld_lane_is_empty(self):
        self.assertEqual(self.holders(), {})

    def test_the_issue_label_is_one_source(self):
        got = self.holders(issue_labels=["task:active", "agent:astra"])
        self.assertIn("astra", got)
        self.assertIn("Owner/repo#476 label", got["astra"])

    def test_an_open_pull_request_referencing_the_issue_is_the_other(self):
        got = self.holders(prs=[{"number": 9, "title": "work (#476)", "body": "",
                                 "headRefName": "x", "labels": [{"name": "agent:composer"}],
                                 "url": "https://github.com/Owner/repo/pull/9"}])
        self.assertEqual(list(got), ["composer"])

    def test_an_unrelated_pull_request_does_not_block_the_lane(self):
        got = self.holders(prs=[{"number": 9, "title": "something else (#999)", "body": "",
                                 "headRefName": "feature", "labels": [{"name": "agent:composer"}],
                                 "url": "u"}])
        self.assertEqual(got, {})

    def test_the_branch_convention_counts_only_with_an_owner_label(self):
        # claim.sh's rule exactly: an unlabelled branch that merely looks like a
        # claim must not be able to hold the queue shut.
        unlabelled = [{"number": 9, "title": "t", "body": "", "labels": [],
                       "headRefName": "agent/x-issue-476", "url": "u"}]
        self.assertEqual(self.holders(prs=unlabelled).get("(unlabelled)"), None)

        labelled = [{"number": 9, "title": "t", "body": "", "url": "u",
                     "labels": [{"name": "agent:fable"}],
                     "headRefName": "agent/fable-issue-476"}]
        self.assertEqual(list(self.holders(prs=labelled)), ["fable"])

    def test_a_half_claim_is_reported_under_its_body_marker(self):
        # claim.sh created the PR and stopped before the labels landed. The
        # From marker is the only identity left, and reporting it as free is
        # how the next agent collides.
        got = self.holders(prs=[{"number": 9, "title": "claim (#476)",
                                 "body": "**From:** desktop-luna\n", "labels": [],
                                 "headRefName": "x", "url": "u"}])
        self.assertIn("desktop-luna", got)
        self.assertIn("half-claim", got["desktop-luna"][0])

    def test_a_half_claim_with_no_marker_is_still_not_free(self):
        got = self.holders(prs=[{"number": 9, "title": "claim (#476)", "body": "",
                                 "labels": [], "headRefName": "x", "url": "u"}])
        self.assertIn("(unlabelled)", got)

    def test_two_holders_are_both_named(self):
        got = self.holders(issue_labels=["agent:astra"],
                           prs=[{"number": 9, "title": "w (#476)", "body": "",
                                 "labels": [{"name": "agent:composer"}],
                                 "headRefName": "x", "url": "u"}])
        self.assertEqual(sorted(got), ["astra", "composer"])


class UnreachableTest(unittest.TestCase):
    """The state that must never render as an empty floor."""

    def test_a_failed_read_raises_rather_than_returning_nobody(self):
        with mock.patch.object(floor, "_gh", side_effect=floor.Unreachable("HTTP 404")):
            with self.assertRaises(floor.Unreachable):
                floor.holders("Owner/repo", 1)

    def test_gh_missing_or_failing_becomes_unreachable_not_an_empty_list(self):
        proc = mock.Mock(returncode=1, stdout="", stderr="gh: Not Found")
        with mock.patch("hill_lib.floor.subprocess.run", return_value=proc):
            with self.assertRaises(floor.Unreachable):
                floor.holders("Owner/repo", 1)
        with mock.patch("hill_lib.floor.subprocess.run", side_effect=FileNotFoundError("gh")):
            with self.assertRaises(floor.Unreachable):
                floor.holders("Owner/repo", 1)


if __name__ == "__main__":
    unittest.main()


class LanesTest(unittest.TestCase):
    """The whole floor, for `hill claims`.

    It answered from SQLite alone, which is this machine's opinion presented as
    the floor's: on a second host, an empty board that is not empty.
    """

    def lanes(self, per_repo):
        calls = {"n": 0}

        def fake(args):
            repo = args[args.index("--repo") + 1]
            rows = per_repo.get(repo)
            if rows is None:
                raise floor.Unreachable("HTTP 404")
            calls["n"] += 1
            # pr list first, then issue list
            return json.dumps(rows["pr"] if args[0] == "pr" else rows["issue"])

        with mock.patch.object(floor, "_gh", side_effect=fake):
            return floor.lanes(list(per_repo))

    def row(self, number, labels, title="t"):
        return {"number": number, "title": title,
                "labels": [{"name": n} for n in labels], "url": f"u/{number}"}

    def test_only_lanes_carrying_an_agent_label_are_held(self):
        got = self.lanes({"Owner/a": {"pr": [self.row(1, ["agent:astra", "task:review"])],
                                      "issue": [self.row(2, ["task:ready"])]}})
        self.assertEqual([(l["number"], l["agents"]) for l in got["held"]], [(1, ["astra"])])

    def test_task_labels_travel_with_the_lane(self):
        got = self.lanes({"Owner/a": {"pr": [self.row(1, ["agent:astra", "task:review"])],
                                      "issue": []}})
        self.assertEqual(got["held"][0]["task"], ["task:review"])

    def test_a_repository_that_cannot_be_read_is_named_not_dropped(self):
        # Silently contributing nothing would make an unreadable repository
        # look like a quiet one, which is the failure this whole module exists
        # to avoid.
        got = self.lanes({"Owner/a": {"pr": [self.row(1, ["agent:astra"])], "issue": []},
                          "Owner/gone": None})
        self.assertEqual([u["repo"] for u in got["unreachable"]], ["Owner/gone"])
        self.assertEqual(len(got["held"]), 1, "the readable repository still reports")

    def test_lanes_are_ordered_so_two_runs_read_the_same(self):
        got = self.lanes({"Owner/a": {"pr": [self.row(9, ["agent:x"]), self.row(2, ["agent:y"])],
                                      "issue": []}})
        self.assertEqual([l["number"] for l in got["held"]], [2, 9])


class NoRunControlTest(unittest.TestCase):
    """the-hill cannot end a run, and that has to stay true by construction.

    A run completes when the mission is accomplished, and only the person who
    started it may halt it. "Accomplished" is a human judgement: an agent can
    report progress toward it and must never declare it reached. The cheapest
    durable guarantee is that no code path here can end a run at all, so this
    asserts the absence rather than trusting the habit.

    Two earlier spellings of this guard were wrong in opposite directions, which
    is why it now has tests of its own. The first also matched `--state open` --
    the read filter on `gh pr list` -- and failed on honest code. The second
    expected `--method PATCH` adjacent and so missed `"--method", "PATCH"`,
    passing while real PATCH code was added.
    """

    FORBIDDEN = re.compile(
        r"""["']issue["']\s*,\s*["'](?:close|reopen)["']"""        # gh issue close
        r"""|["']pr["']\s*,\s*["'](?:close|reopen|merge)["']"""    # gh pr close/merge
        r"""|issues/\{number\}["'][^\n]*PATCH"""                    # PATCH the run
        r"""|issues/\{number\}[^\n]*--method""", re.S)

    def sources(self):
        root = Path(__file__).resolve().parent.parent
        files = list((root / "hill_lib").glob("*.py")) + [root / "hill"]
        return {f: f.read_text(encoding="utf-8") for f in files}

    def test_the_guard_stops_a_patch_aimed_at_the_run(self):
        self.assertIsNotNone(self.FORBIDDEN.search(
            '_gh(["api", f"repos/{repo}/issues/{number}", "--method", "PATCH"])'))

    def test_the_guard_allows_editing_our_own_comment(self):
        # Publishing a peer's liveness edits one comment in place. That is our
        # status line, not the run.
        self.assertIsNone(self.FORBIDDEN.search(
            '_gh(["api", f"repos/{repo}/issues/comments/{cid}", "--method", "PATCH"])'))

    def test_the_guard_allows_a_state_read_filter(self):
        self.assertIsNone(self.FORBIDDEN.search(
            '_gh(["pr", "list", "--repo", repo, "--state", "open"])'))

    def test_the_guard_stops_closing_a_run(self):
        self.assertIsNotNone(self.FORBIDDEN.search('_gh(["issue", "close", "40"])'))
        self.assertIsNotNone(self.FORBIDDEN.search('_gh(["pr", "merge", "9"])'))

    def test_no_module_ends_a_run(self):
        for path, text in self.sources().items():
            with self.subTest(path=path.name):
                self.assertIsNone(self.FORBIDDEN.search(text),
                                  f"{path.name} appears to end a run or land work")

    def test_nothing_edits_the_board_issue_body_or_title(self):
        for path, text in self.sources().items():
            with self.subTest(path=path.name):
                self.assertNotIn("issue edit", text,
                                 f"{path.name} edits an issue; the run is not ours to rewrite")
