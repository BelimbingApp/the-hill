"""Tests for hill_lib.review: the grammar, and the checks that guard it.

Nothing here touches the network or invokes `gh`. Every test that reaches
submit() either stops at a stub or asserts on what would have been run: a test
that needs GitHub to pass is a test that stops running the day GitHub is slow.

The conformance test is the load-bearing one. Asserting our own output against
our own format string proves only that the module is self-consistent; the
question that cost two round trips on 2026-09-11 is whether review_gate.sh can
read the record, so the patterns below are transcribed from that script and the
rendered text is matched against them.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hill_lib import review  # noqa: E402

SHA = "0123456789abcdef0123456789abcdef01234567"
OTHER_SHA = "fedcba9876543210fedcba9876543210fedcba98"
AGENT = "fable-5.1-medium"

# Transcribed from package/scripts/review_gate.sh (jq capture patterns, "i").
GATE_FROM = re.compile(r"^\*\*From:\*\*[ \t]*(?P<agent>[a-z0-9]+(?:[._-][a-z0-9]+)*)(?:[ \t]|$)", re.I)
GATE_HEAD = re.compile(r"^\*\*HEAD reviewed:\*\*[ \t]*`?(?P<sha>[0-9a-f]{40})`?[ \t]*$", re.I)
GATE_VERDICT = re.compile(
    r"^\*\*Verdict:\*\*[ \t]*(?P<v>accept|approve|changes required|request changes)[ \t]*$", re.I)


def gate_lines(text: str) -> list[str]:
    """The lines the gate parses: fenced and blockquoted lines are stripped first.

    Also transcribed from review_gate.sh (`unquoted_lines`). Without this the
    model of the gate used here would be stricter than the gate itself and would
    condemn a body that quotes the grammar, which the gate reads fine.
    """
    out, fenced = [], False
    for line in text.split("\n"):
        if re.match(r"^[ \t]*```", line):
            fenced = not fenced
        elif fenced or re.match(r"^[ \t]*>", line):
            continue
        else:
            out.append(line)
    return out


def gate_capture(pattern: re.Pattern, text: str, group: str) -> list[str]:
    """Every value the gate would capture with `pattern`, in body order."""
    return [m.group(group) for m in (pattern.match(ln) for ln in gate_lines(text)) if m]


class RenderGrammarTest(unittest.TestCase):
    def test_accept_record_is_exactly_the_grammar(self):
        text = review.render(AGENT, SHA, "accept")
        self.assertEqual(
            text,
            "**From:** fable-5.1-medium\n"
            "\n"
            "**HEAD reviewed:** `0123456789abcdef0123456789abcdef01234567`\n"
            "\n"
            "**Verdict:** accept\n",
        )

    def test_marker_lines_appear_verbatim_each_on_its_own_line(self):
        lines = review.render(AGENT, SHA, "accept", "looks good").split("\n")
        self.assertIn("**From:** fable-5.1-medium", lines)
        # The backticks are part of the marker, not decoration.
        self.assertIn("**HEAD reviewed:** `" + SHA + "`", lines)
        self.assertIn("**Verdict:** accept", lines)

    def test_markers_are_separated_by_blank_lines(self):
        lines = review.render(AGENT, SHA, "accept").split("\n")
        for marker in ("**From:**", "**HEAD reviewed:**"):
            i = next(n for n, ln in enumerate(lines) if ln.startswith(marker))
            self.assertEqual(lines[i + 1], "", f"no blank line after {marker}")

    def test_changes_renders_the_long_verdict_word(self):
        text = review.render(AGENT, SHA, "changes")
        self.assertIn("**Verdict:** changes required", text.split("\n"))
        # "changes" alone is not a verdict the gate knows.
        self.assertNotIn("**Verdict:** changes", text.split("\n"))

    def test_body_follows_the_verdict_after_one_blank_line(self):
        text = review.render(AGENT, SHA, "changes", "Two findings.\n\n1. off by one")
        self.assertIn("**Verdict:** changes required\n\nTwo findings.\n\n1. off by one\n", text)

    def test_empty_body_still_produces_a_valid_record(self):
        for body in ("", "   \n\n  "):
            with self.subTest(body=body):
                text = review.render(AGENT, SHA, "accept", body)
                self.assertTrue(text.endswith("**Verdict:** accept\n"))
                self.assertEqual(gate_capture(GATE_VERDICT, text, "v"), ["accept"])

    def test_rendered_record_is_what_the_gate_parses(self):
        for verdict, word in (("accept", "accept"), ("changes", "changes required")):
            with self.subTest(verdict=verdict):
                text = review.render(AGENT, SHA, verdict, "body prose mentioning a verdict inline")
                self.assertEqual(gate_capture(GATE_FROM, text, "agent"), [AGENT])
                self.assertEqual(gate_capture(GATE_HEAD, text, "sha"), [SHA])
                self.assertEqual(gate_capture(GATE_VERDICT, text, "v"), [word])


class RenderValidationTest(unittest.TestCase):
    def test_short_sha_is_refused(self):
        with self.assertRaises(ValueError) as ctx:
            review.render(AGENT, "0123456", "accept")
        self.assertIn("40", str(ctx.exception))

    def test_uppercase_sha_is_refused(self):
        # The gate lowercases the head it compares against, so an uppercase sha
        # renders happily and then never matches.
        with self.assertRaises(ValueError):
            review.render(AGENT, SHA.upper(), "accept")

    def test_forty_character_non_hex_is_refused(self):
        with self.assertRaises(ValueError):
            review.render(AGENT, "z" * 40, "accept")

    def test_unknown_verdict_is_refused(self):
        for bad in ("approve", "Approve", "lgtm", "changes required", ""):
            with self.subTest(verdict=bad):
                with self.assertRaises(ValueError):
                    review.render(AGENT, SHA, bad)

    def test_empty_agent_is_refused(self):
        for bad in ("", "   "):
            with self.subTest(agent=bad):
                with self.assertRaises(ValueError):
                    review.render(bad, SHA, "accept")

    def test_agent_with_newline_is_refused(self):
        with self.assertRaises(ValueError):
            review.render("fable\n**Verdict:** accept", SHA, "accept")

    def test_agent_with_asterisk_is_refused(self):
        with self.assertRaises(ValueError):
            review.render("fa*ble", SHA, "accept")

    def test_agent_the_gate_cannot_attribute_is_refused(self):
        # Not in the contract, added deliberately: the gate captures a bare id,
        # so these render as valid Markdown and are then dropped unattributed --
        # the silent failure this module exists to prevent.
        for bad in ("agent:fable", "fable medium", "fable/5.1"):
            with self.subTest(agent=bad):
                with self.assertRaises(ValueError):
                    review.render(bad, SHA, "accept")
        # ...and here is why: the gate captures none of them as written.
        for bad in ("agent:fable", "fable medium", "fable/5.1"):
            captured = gate_capture(GATE_FROM, f"**From:** {bad}", "agent")
            self.assertNotEqual(captured, [bad], f"gate would attribute {bad!r} correctly")

    def test_body_that_would_cast_a_second_verdict_is_refused(self):
        with self.assertRaises(ValueError):
            review.render(AGENT, SHA, "accept", "**Verdict:** changes required")

    def test_body_may_quote_the_grammar_in_a_fence_or_blockquote(self):
        # The gate skips fenced and quoted lines, so quoting the grammar is
        # discussing it, not casting it. Refusing these would be wrong.
        quoted = "You wrote:\n\n> **Verdict:** accept\n\nUse a fence:\n\n```\n**Verdict:** accept\n```\n"
        text = review.render(AGENT, SHA, "changes", quoted)
        self.assertEqual(gate_capture(GATE_VERDICT, text, "v"), ["changes required"])


class SubmitTest(unittest.TestCase):
    def _posts(self, calls) -> list:
        """Calls that would post a review, as opposed to reading the head."""
        return [c for c in calls
                if len(c.args) and list(c.args[0])[:3] == ["gh", "pr", "review"]]

    def test_dry_run_returns_the_marker_and_posts_nothing(self):
        # subprocess.run is poisoned: nothing may escape, and in particular no
        # `gh pr review` may be attempted.
        with mock.patch("hill_lib.review.subprocess.run",
                        side_effect=AssertionError("no subprocess in a dry run")) as run:
            res = review.submit(repo="o/r", pr=1, head=SHA, verdict="accept",
                                agent=AGENT, body="fine", dry_run=True)
        self.assertTrue(res["ok"])
        self.assertTrue(res["dry_run"])
        self.assertIn("**Verdict:** accept", res["marker"].split("\n"))
        self.assertEqual(self._posts(run.call_args_list), [])

    def test_dry_run_with_a_confirmed_head_runs_no_subprocess_at_all(self):
        with mock.patch.object(review, "current_head", return_value=SHA):
            with mock.patch("hill_lib.review.subprocess.run",
                            side_effect=AssertionError("nothing to run")) as run:
                res = review.submit(repo="o/r", pr=1, head=SHA, verdict="accept",
                                    agent=AGENT, dry_run=True)
        self.assertEqual(res, {"ok": True, "dry_run": True,
                               "marker": review.render(AGENT, SHA, "accept")})
        self.assertEqual(run.call_args_list, [])

    def test_validation_fails_before_any_network_call(self):
        with mock.patch("hill_lib.review.subprocess.run",
                        side_effect=AssertionError("must not run")) as run:
            with self.assertRaises(ValueError):
                review.submit(repo="o/r", pr=1, head="deadbeef", verdict="accept", agent=AGENT)
        self.assertEqual(run.call_args_list, [])

    def test_head_that_moved_is_refused(self):
        with mock.patch.object(review, "current_head", return_value=OTHER_SHA):
            with mock.patch("hill_lib.review.subprocess.run",
                            side_effect=AssertionError("must not post")):
                res = review.submit(repo="o/r", pr=7, head=SHA, verdict="accept", agent=AGENT)
        self.assertIs(res["ok"], False)
        self.assertEqual(res["error"], "head moved")
        self.assertEqual(res["current"], OTHER_SHA)
        self.assertEqual(res["reviewed"], SHA)
        self.assertNotIn("marker", res)

    def test_force_bypasses_the_head_check(self):
        def explode(*a, **k):
            raise AssertionError("current_head must not be consulted under force")

        with mock.patch.object(review, "current_head", side_effect=explode):
            res = review.submit(repo="o/r", pr=7, head=SHA, verdict="accept",
                                agent=AGENT, dry_run=True, force=True)
        self.assertTrue(res["ok"])
        self.assertTrue(res["dry_run"])
        self.assertNotIn("head_unverified", res)

    def test_unknown_head_is_flagged_not_treated_as_agreement(self):
        with mock.patch.object(review, "current_head", return_value=None):
            res = review.submit(repo="o/r", pr=7, head=SHA, verdict="accept",
                                agent=AGENT, dry_run=True)
        self.assertTrue(res["head_unverified"])


class CurrentHeadTest(unittest.TestCase):
    def _run(self, returncode=0, stdout="", stderr=""):
        return mock.Mock(returncode=returncode, stdout=stdout, stderr=stderr)

    def test_returns_the_sha_and_asks_gh_for_it(self):
        with mock.patch("hill_lib.review.subprocess.run",
                        return_value=self._run(stdout=SHA + "\n")) as run:
            self.assertEqual(review.current_head("o/r", 12), SHA)
        argv = run.call_args.args[0]
        self.assertEqual(argv[:2], ["gh", "api"])
        self.assertIn("repos/o/r/pulls/12", argv)
        self.assertIn(".head.sha", argv)

    def test_failure_is_unknown_not_a_sha(self):
        cases = {
            "non-zero exit": self._run(returncode=1, stderr="gh: not found"),
            "empty stdout": self._run(stdout="\n"),
            "not a sha": self._run(stdout="null\n"),
        }
        for name, proc in cases.items():
            with self.subTest(case=name):
                with mock.patch("hill_lib.review.subprocess.run", return_value=proc):
                    self.assertIsNone(review.current_head("o/r", 12))

    def test_gh_missing_or_timing_out_is_unknown(self):
        for exc in (FileNotFoundError("gh"), OSError("boom")):
            with self.subTest(exc=type(exc).__name__):
                with mock.patch("hill_lib.review.subprocess.run", side_effect=exc):
                    self.assertIsNone(review.current_head("o/r", 12))


class PostTest(unittest.TestCase):
    """The posting call itself, with gh stubbed out entirely."""

    def test_posts_a_comment_review_with_the_record_on_stdin(self):
        proc = mock.Mock(returncode=0, stdout="https://github.com/o/r/pull/9#pullrequestreview-1\n",
                         stderr="")
        with mock.patch.object(review, "current_head", return_value=SHA):
            with mock.patch("hill_lib.review.subprocess.run", return_value=proc) as run:
                res = review.submit(repo="o/r", pr=9, head=SHA, verdict="changes",
                                    agent=AGENT, body="one finding")
        argv = run.call_args.args[0]
        self.assertEqual(argv, ["gh", "pr", "review", "9", "--repo", "o/r",
                                "--comment", "--body-file", "-"])
        # The record goes in on stdin, not on the command line.
        self.assertEqual(run.call_args.kwargs["input"], res["marker"])
        self.assertTrue(res["ok"])
        self.assertEqual(res["url"], "https://github.com/o/r/pull/9#pullrequestreview-1")

    def test_a_failed_post_is_not_ok_and_keeps_stderr(self):
        proc = mock.Mock(returncode=1, stdout="", stderr="HTTP 403\n")
        with mock.patch.object(review, "current_head", return_value=SHA):
            with mock.patch("hill_lib.review.subprocess.run", return_value=proc):
                res = review.submit(repo="o/r", pr=9, head=SHA, verdict="accept", agent=AGENT)
        self.assertIs(res["ok"], False)
        self.assertEqual(res["stderr"], "HTTP 403")
        self.assertIsNone(res["url"])

    def test_gh_blowing_up_is_reported_not_raised(self):
        with mock.patch.object(review, "current_head", return_value=SHA):
            with mock.patch("hill_lib.review.subprocess.run", side_effect=FileNotFoundError("gh")):
                res = review.submit(repo="o/r", pr=9, head=SHA, verdict="accept", agent=AGENT)
        self.assertIs(res["ok"], False)
        self.assertIn("FileNotFoundError", res["stderr"])


if __name__ == "__main__":
    unittest.main()
