"""Tests for the one board signal that told a reader the wrong thing.

blb-people#483 sat with the review gate red and nothing else wrong, and the
board reported waiting_on='CI'. A reader following that goes looking for a
broken test; the lane was waiting on a person. Same floor as test_db.py — this
covers the misreport and its neighbours, not the whole collector.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("HILL_HOME", tempfile.mkdtemp(prefix="hill-test-"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hill_lib.collect import GATE_CHECK, head_verdict, waiting_on  # noqa: E402

HEAD = "1c11938b2ef3fc6dfb57f49ea1b50d14f4f19a74"
OTHER = "0" * 40


class WaitingOnTests(unittest.TestCase):
    def test_gate_red_with_no_verdict_names_a_reviewer_not_ci(self):
        # The exact #483 shape: gate red, CI still running, mergeable blocked.
        self.assertEqual(
            waiting_on(False, [GATE_CHECK], ["ci / sqlite"], "blocked", None),
            "a reviewer (none at this head)")

    def test_a_real_red_test_still_reads_as_ci(self):
        self.assertEqual(waiting_on(False, [GATE_CHECK, "ci / sqlite"], [], "blocked", None), "CI")

    def test_a_broken_test_outranks_the_gate(self):
        # Both red: the test is the thing a human can act on first.
        self.assertEqual(waiting_on(False, ["ci / sqlite"], [], "blocked", None), "CI")

    def test_changes_requested_points_at_the_author(self):
        self.assertEqual(waiting_on(False, [GATE_CHECK], [], "blocked", "changes"),
                         "author (changes requested)")

    def test_accept_at_head_with_a_red_gate_is_flagged_not_hidden(self):
        # The scan is weaker than review_gate.sh, so it can be wrong. When it
        # disagrees with the gate, say so rather than pick a winner.
        self.assertIn("gate disagrees", waiting_on(False, [GATE_CHECK], [], "blocked", "accept"))

    def test_draft_outranks_everything(self):
        self.assertEqual(waiting_on(True, [GATE_CHECK, "ci / sqlite"], [], "blocked", None),
                         "author (draft)")

    def test_clean_with_an_accepted_head_is_landable(self):
        self.assertEqual(waiting_on(False, [], [], "clean", "accept"), "landable")

    def test_clean_but_unread_says_so(self):
        # blb-people-connector installs no review gate and requires 0 approvals,
        # so a PR reaches clean with nobody having read it. Calling that plain
        # "landable" invites the author to merge their own work.
        self.assertEqual(waiting_on(False, [], [], "clean", None), "landable — no verdict")


class HeadVerdictTests(unittest.TestCase):
    def scan(self, reviews, author="opus-max", sha=HEAD):
        return head_verdict(lambda _p: reviews, "o/r", 1, sha, author)

    def review(self, agent="astra", verdict="accept", marker=HEAD, commit=HEAD, state="COMMENTED"):
        return {"state": state, "commit_id": commit,
                "body": f"**From:** {agent}\n**HEAD reviewed:** `{marker}`\n**Verdict:** {verdict}"}

    def test_reads_a_bound_verdict(self):
        self.assertEqual(self.scan([self.review()]), "accept")
        self.assertEqual(self.scan([self.review(verdict="changes required")]), "changes")

    def test_github_verdict_words_count(self):
        # The gate accepts Approve/Request changes as verdict words (#70).
        self.assertEqual(self.scan([self.review(verdict="approve")]), "accept")
        self.assertEqual(self.scan([self.review(verdict="request changes")]), "changes")

    def test_a_verdict_for_an_older_head_does_not_carry(self):
        self.assertIsNone(self.scan([self.review(marker=OTHER, commit=OTHER)]))

    def test_a_rebase_rewriting_commit_id_does_not_forge_a_binding(self):
        # commit_id now points at the new head but the marker still names the
        # old one. This is exactly why the gate requires both.
        self.assertIsNone(self.scan([self.review(marker=OTHER, commit=HEAD)]))

    def test_the_author_cannot_review_themselves(self):
        self.assertIsNone(self.scan([self.review(agent="opus-max")]))

    def test_a_dismissed_review_does_not_count(self):
        self.assertIsNone(self.scan([self.review(state="DISMISSED")]))

    def test_an_unmarked_review_does_not_count(self):
        self.assertIsNone(self.scan([{"state": "APPROVED", "commit_id": HEAD, "body": "lgtm"}]))

    def test_no_head_means_no_claim(self):
        self.assertIsNone(self.scan([self.review()], sha=""))


if __name__ == "__main__":
    unittest.main()


class AssemblyTest(unittest.TestCase):
    """The page is three files folded into one, and both modes must fold the same.

    The stylesheet and script live outside the HTML so they can be edited as
    CSS and JavaScript. They are inlined at build time because a saved board
    has to work from file:// with nothing running, and a browser will not let
    a local page read its neighbours. If the saved and served pages ever
    assemble differently, the file stops being a faithful copy of the board.
    """

    def test_every_marker_is_filled(self):
        from hill_lib import build
        page = build.assemble()
        for marker in ("/*__CSS__*/", "/*__JS__*/"):
            self.assertNotIn(marker, page, f"{marker} was left unfilled")
        self.assertIn(build.PLACEHOLDER, page, "the data placeholder must survive")

    def test_the_stylesheet_and_script_actually_arrive(self):
        from hill_lib import build
        page = build.assemble()
        self.assertIn("function paint(D)", page)
        self.assertIn(".chip{", page)

    def test_a_missing_marker_is_an_error_not_a_silent_half_page(self):
        from hill_lib import build
        from unittest import mock
        # The fixture carries the DATA placeholder but not the CSS/JS markers,
        # so only the marker check can catch it. An earlier version used a
        # template with nothing in it at all, and the placeholder check fired
        # instead -- the test passed with the marker check deleted.
        class Fake:
            @staticmethod
            def read_text(*a, **k):
                return "<html>/*__DATA__*/null</html>"

        with mock.patch.object(build, "TEMPLATE", Fake):
            with self.assertRaises(RuntimeError):
                build.assemble()

    def test_saved_and_served_share_one_assembly(self):
        # Not two code paths that happen to agree today.
        import inspect
        from hill_lib import server
        self.assertIn("build.assemble()", inspect.getsource(server.serve))


class ReadErrorTest(unittest.TestCase):
    """A failed read must be visible, not absorbed.

    collect.gh() returned None on any failure and said nothing. The board's
    whole dataset comes through it, so an unreachable repository produced a
    smaller board that looked exactly like a quiet one -- the mistake this
    tool exists to avoid, in the collector itself.
    """

    def setUp(self):
        from hill_lib import collect
        self.collect = collect
        collect.READ_ERRORS.clear()
        self.addCleanup(collect.READ_ERRORS.clear)

    def test_a_failing_call_is_recorded_and_still_returns_none(self):
        from unittest import mock
        proc = mock.Mock(returncode=1, stdout="", stderr="gh: Not Found (HTTP 404)")
        with mock.patch("hill_lib.collect.subprocess.run", return_value=proc):
            self.assertIsNone(self.collect.gh("repos/o/r/pulls"))
        self.assertEqual(len(self.collect.READ_ERRORS), 1)
        self.assertIn("Not Found", self.collect.READ_ERRORS[0]["why"])
        self.assertEqual(self.collect.READ_ERRORS[0]["path"], "repos/o/r/pulls")

    def test_an_exception_is_recorded_too(self):
        from unittest import mock
        with mock.patch("hill_lib.collect.subprocess.run", side_effect=OSError("boom")):
            self.assertIsNone(self.collect.gh("repos/o/r/pulls"))
        self.assertEqual(len(self.collect.READ_ERRORS), 1)

    def test_a_successful_call_records_nothing(self):
        from unittest import mock
        proc = mock.Mock(returncode=0, stdout='{"ok":true}', stderr="")
        with mock.patch("hill_lib.collect.subprocess.run", return_value=proc):
            self.assertEqual(self.collect.gh("repos/o/r"), {"ok": True})
        self.assertEqual(self.collect.READ_ERRORS, [])


class AgentLabelTest(unittest.TestCase):
    """One label means one agent, wherever it is read.

    collect parsed `agent:<id>` without normalising and floor lowercased, so
    `agent:Opus-Max` was one agent in the board's table and a different one in
    the claim check.
    """

    def test_every_reader_normalises_the_same_way(self):
        from hill_lib import gh, collect, floor
        labels = [{"name": "agent:Opus-Max"}, {"name": "task:review"}]
        self.assertEqual(gh.agent_of(labels), "opus-max")
        self.assertEqual(collect.agent_of(labels), "opus-max")
        self.assertEqual(floor._agent_labels(labels), ["opus-max"])

    def test_surrounding_space_does_not_make_a_second_agent(self):
        from hill_lib import gh
        self.assertEqual(gh.agent_of([{"name": "agent: astra "}]), "astra")

    def test_no_agent_label_is_none(self):
        from hill_lib import gh
        self.assertIsNone(gh.agent_of([{"name": "task:ready"}]))
        self.assertIsNone(gh.agent_of([]))
