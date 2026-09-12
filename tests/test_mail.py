"""Messages that have to survive the sender's machine going away.

`hill send` wrote SQLite, so a message to another host went nowhere while
reporting success. These cover the grammar that carries it on GitHub instead,
and the three refusals that keep it honest.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("HILL_HOME", tempfile.mkdtemp(prefix="hill-test-"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hill_lib import mail  # noqa: E402


class BoardTest(unittest.TestCase):
    def board(self, value):
        with mock.patch.dict(os.environ, {"HILL_BOARD": value}, clear=False):
            return mail.board()

    def test_a_configured_board_parses(self):
        self.assertEqual(self.board("Owner/repo#40"), ("Owner/repo", 40))

    def test_anything_malformed_is_none_rather_than_a_guess(self):
        for bad in ("", "Owner/repo", "#40", "repo#40", "Owner/repo#x", "  "):
            with self.subTest(bad=bad):
                self.assertIsNone(self.board(bad))


class RenderTest(unittest.TestCase):
    def test_both_markers_are_present_because_both_are_the_discriminator(self):
        text = mail.render("opus-max", "astra", "hello")
        self.assertIn("**From:** opus-max", text)
        self.assertIn("**To:** astra", text)

    def test_a_ref_is_carried_when_given_and_absent_when_not(self):
        self.assertIn("**Ref:** https://x/1", mail.render("a", "b", "hi", ref="https://x/1"))
        self.assertNotIn("**Ref:**", mail.render("a", "b", "hi"))

    def test_a_verdict_marker_is_refused(self):
        # The gate reports verdict markers found in issue comments. A message
        # that trips that warning is worse than no message.
        for body in ("**Verdict:** accept",
                     "notes\n\n**HEAD reviewed:** `" + "a" * 40 + "`\n"):
            with self.subTest(body=body[:20]):
                with self.assertRaises(ValueError):
                    mail.render("a", "b", body)


class FetchTest(unittest.TestCase):
    def comments(self, bodies):
        rows = [{"id": i + 1, "body": b, "url": f"u/{i+1}",
                 "at": f"2026-01-0{i+1}T00:00:00Z"} for i, b in enumerate(bodies)]
        return json.dumps(rows)

    def fetch(self, bodies):
        with mock.patch.object(mail, "_gh", return_value=self.comments(bodies)):
            return mail.fetch("Owner/repo", 40)

    def test_a_comment_with_both_markers_is_mail(self):
        got = self.fetch(["**From:** opus-max\n\n**To:** astra\n\nbody here"])
        self.assertEqual(len(got), 1)
        self.assertEqual((got[0]["sender"], got[0]["to"]), ("opus-max", "astra"))
        self.assertEqual(got[0]["body"], "body here")

    def test_a_status_post_carrying_only_From_is_not_mail(self):
        # This is the whole discriminator. Every status post on the board has a
        # From marker; reading those as mail would make the inbox useless.
        self.assertEqual(self.fetch(["**From:** opus-max\n\nnothing landed this tick"]), [])

    def test_an_ordinary_comment_is_not_mail(self):
        self.assertEqual(self.fetch(["looks good to me"]), [])

    def test_the_ref_is_extracted_and_kept_out_of_the_body(self):
        got = self.fetch(["**From:** a\n\n**To:** b\n\n**Ref:** https://x/1\n\nthe text"])
        self.assertEqual(got[0]["ref"], "https://x/1")
        self.assertEqual(got[0]["body"], "the text")

    def test_messages_come_back_in_a_stable_order(self):
        got = self.fetch(["**From:** a\n\n**To:** b\n\nfirst",
                          "**From:** c\n\n**To:** b\n\nsecond"])
        self.assertEqual([m["body"] for m in got], ["first", "second"])


class UnreachableTest(unittest.TestCase):
    def test_a_failed_read_raises_rather_than_returning_an_empty_inbox(self):
        proc = mock.Mock(returncode=1, stdout="", stderr="gh: Not Found")
        with mock.patch("hill_lib.mail.subprocess.run", return_value=proc):
            with self.assertRaises(mail.Unreachable):
                mail.fetch("Owner/repo", 40)

    def test_a_post_that_cannot_be_read_back_is_not_reported_as_delivered(self):
        # gh exits 0 but the comment is not there. Reporting that as delivered
        # is how a message is lost silently.
        with mock.patch.object(mail, "_gh", return_value=""):
            with mock.patch.object(mail, "fetch", return_value=[]):
                with self.assertRaises(mail.Unreachable):
                    mail.post("Owner/repo", 40, "a", "b", "hi")


if __name__ == "__main__":
    unittest.main()
