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

    def test_clean_and_quiet_is_landable(self):
        self.assertEqual(waiting_on(False, [], [], "clean", None), "nothing — landable")


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
