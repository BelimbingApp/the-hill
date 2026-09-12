"""Tests for the parts of the-hill that can destroy or lose work.

Deliberately narrow. These cover the behaviour that would cause real damage if
wrong — a lane claimed twice, a message read by nobody, a takeover that leaves
no trace — and nothing else. ai-team#128 rejected framework-wide test ceremony;
this is the floor, not a suite to grow for its own sake.
"""
import os
import tempfile
import threading
import unittest
from pathlib import Path

TMP = tempfile.mkdtemp(prefix="hill-test-")
os.environ["HILL_HOME"] = TMP

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hill_lib import db, live  # noqa: E402


class ClaimTests(unittest.TestCase):
    def setUp(self):
        self.con = db.connect()
        self.con.execute("DELETE FROM claims")
        self.con.execute("DELETE FROM messages")
        self.con.execute("DELETE FROM events")

    def test_one_winner_when_two_agents_race(self):
        """The whole reason this is SQLite and not a JSON file."""
        errors, wins = [], []

        def grab(name):
            try:
                con = db.connect()
                wins.append(db.claim(con, "repo#1", name)["agent"])
            except db.Held:
                errors.append(name)
            except Exception as e:  # pragma: no cover
                errors.append(f"{name}:{e!r}")

        ts = [threading.Thread(target=grab, args=(f"agent-{i}",)) for i in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(len(wins), 1, f"expected exactly one winner, got {wins}")
        self.assertEqual(len(errors), 7)

    def test_reclaiming_your_own_lane_is_idempotent(self):
        a = db.claim(self.con, "repo#2", "me")
        b = db.claim(self.con, "repo#2", "me")
        self.assertEqual(a["claimed_at"], b["claimed_at"])

    def test_another_agent_is_refused_without_take(self):
        db.claim(self.con, "repo#3", "first")
        with self.assertRaises(db.Held):
            db.claim(self.con, "repo#3", "second")

    def test_takeover_succeeds_and_records_who_was_displaced(self):
        db.claim(self.con, "repo#4", "first")
        rec = db.claim(self.con, "repo#4", "second", take=True)
        self.assertEqual(rec["agent"], "second")
        kinds = [e["kind"] for e in db.events(self.con)]
        self.assertIn("claim.taken", kinds,
                      "a takeover that leaves no trace is how a collision becomes invisible")
        taken = next(e for e in db.events(self.con) if e["kind"] == "claim.taken")
        self.assertIn("first", taken["detail"])

    def test_release_by_another_agent_is_refused(self):
        db.claim(self.con, "repo#5", "owner")
        with self.assertRaises(db.Held):
            db.release(self.con, "repo#5", "someone-else")
        self.assertTrue(db.release(self.con, "repo#5", "owner"))


class MessageTests(unittest.TestCase):
    def setUp(self):
        self.con = db.connect()
        self.con.execute("DELETE FROM messages")

    def test_message_is_delivered_once(self):
        db.send(self.con, "a", "b", "hello", ref="http://pr/1")
        first = db.inbox(self.con, "b")
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["ref"], "http://pr/1")
        self.assertEqual(db.inbox(self.con, "b"), [], "a read message must not come back")

    def test_peek_does_not_consume(self):
        db.send(self.con, "a", "b", "keep me")
        db.inbox(self.con, "b", mark=False)
        self.assertEqual(len(db.inbox(self.con, "b")), 1)

    def test_broadcast_reaches_everyone_separately(self):
        db.send(self.con, "a", "*", "all hands")
        self.assertEqual(len(db.inbox(self.con, "b")), 1)
        self.assertEqual(len(db.inbox(self.con, "c")), 1,
                         "a broadcast read by one agent must still reach the others")

    def test_message_for_someone_else_is_not_visible(self):
        db.send(self.con, "a", "b", "private")
        self.assertEqual(db.inbox(self.con, "z"), [])


class LivenessTests(unittest.TestCase):
    def test_tick_is_readable_and_ages(self):
        live.tick("agent-x", session="s1", lane="repo#9")
        rows = {r["agent"]: r for r in live.read_all()}
        self.assertIn("agent-x", rows)
        self.assertEqual(rows["agent-x"]["lane"], "repo#9")
        self.assertLess(rows["agent-x"]["age_s"], 5)
        self.assertEqual(rows["agent-x"]["freshness"], "fresh")

    def test_agents_do_not_overwrite_each_other(self):
        live.tick("one"); live.tick("two")
        names = {r["agent"] for r in live.read_all()}
        self.assertTrue({"one", "two"} <= names)

    def test_no_record_ever_reports_dead(self):
        """Unknown is a state. Nothing here may conclude an agent has stopped."""
        live.tick("quiet")
        for r in live.read_all():
            self.assertIn(r["freshness"], {"fresh", "unknown", "check", "stale"})
            self.assertNotIn("dead", str(r).lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
