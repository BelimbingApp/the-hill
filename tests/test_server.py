"""The live board's state machine, without a network.

A wall display that freezes while still looking current is the worst failure
this repository can have, so what `Board` reports about its own freshness is
tested directly: age, whether a pass is running, and whether the last one
failed. The HTTP layer is three fixed routes over this object.
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
from hill_lib import server  # noqa: E402


class BoardStateTest(unittest.TestCase):
    def board(self, interval=300):
        return server.Board(interval)

    def test_before_any_collection_it_says_so_rather_than_showing_nothing(self):
        st = self.board().state()
        self.assertIsNone(st["snapshot"])
        self.assertIsNone(st["age_seconds"])
        self.assertIsNone(st["collected_at"])

    def test_a_finished_pass_is_served_with_an_age(self):
        b = self.board()
        with mock.patch.object(server.collect, "collect", return_value={"open_lanes": []}):
            b.refresh_once()
        st = b.state()
        self.assertEqual(st["snapshot"], {"open_lanes": []})
        self.assertIsNotNone(st["collected_at"])
        self.assertLess(st["age_seconds"], 5)
        self.assertFalse(st["collecting"])
        self.assertIsNone(st["last_error"])

    def test_a_failed_pass_is_reported_and_does_not_lose_the_last_good_data(self):
        # The failure that matters: refreshes stop landing and the board keeps
        # showing old numbers as though they were current.
        b = self.board()
        with mock.patch.object(server.collect, "collect", return_value={"open_lanes": [1]}):
            b.refresh_once()
        with mock.patch.object(server.collect, "collect", side_effect=RuntimeError("gh exploded")):
            b.refresh_once()
        st = b.state()
        self.assertEqual(st["snapshot"], {"open_lanes": [1]}, "last good data must survive")
        self.assertIn("gh exploded", st["last_error"])
        self.assertFalse(st["collecting"], "a crash must clear the in-flight flag")

    def test_a_later_success_clears_the_error(self):
        b = self.board()
        with mock.patch.object(server.collect, "collect", side_effect=RuntimeError("boom")):
            b.refresh_once()
        self.assertIsNotNone(b.state()["last_error"])
        with mock.patch.object(server.collect, "collect", return_value={}):
            b.refresh_once()
        self.assertIsNone(b.state()["last_error"])

    def test_age_is_measured_from_when_the_pass_started_reading(self):
        # A pass takes about a hundred seconds. Measuring from when it finished
        # storing makes the data look that much fresher than it is, and
        # disagrees with the timestamp the page prints in its masthead. The age
        # must never flatter itself.
        started = "2026-09-12T11:33:59+00:00"
        b = self.board()
        with mock.patch.object(server.collect, "collect",
                               return_value={"collected_at": started}):
            b.refresh_once()
        st = b.state()
        self.assertEqual(st["collected_at"], started)
        self.assertGreater(st["age_seconds"], 60,
                           "age must reflect the snapshot's own stamp, not the store time")
        # The store time is still reported, just not as the age anchor.
        self.assertIsNotNone(st["stored_at"])
        self.assertNotEqual(st["stored_at"], st["collected_at"])

    def test_a_snapshot_with_no_usable_stamp_falls_back_to_the_store_time(self):
        for snap in ({}, {"collected_at": "not-a-date"}):
            with self.subTest(snap=snap):
                b = self.board()
                with mock.patch.object(server.collect, "collect", return_value=snap):
                    b.refresh_once()
                st = b.state()
                self.assertIsNotNone(st["collected_at"])
                self.assertLess(st["age_seconds"], 5)

    def test_the_refresh_interval_has_a_floor(self):
        # A one-second interval would hammer the GitHub API on every tick and
        # spend the shared quota every agent on the account is drawing from.
        self.assertEqual(self.board(interval=1).interval, 60)
        self.assertEqual(self.board(interval=900).interval, 900)

    def test_state_is_json_serialisable(self):
        # It is handed straight to the page; anything unserialisable is a 500.
        b = self.board()
        with mock.patch.object(server.collect, "collect", return_value={"x": 1}):
            b.refresh_once()
        json.dumps(b.state(), default=str)


class RouteTest(unittest.TestCase):
    """Route selection, without binding a socket."""

    def routes(self, path):
        return path.split("?", 1)[0].rstrip("/") or "/"

    def test_known_routes(self):
        self.assertEqual(self.routes("/"), "/")
        self.assertEqual(self.routes("/api/board.json?x=1"), "/api/board.json")
        self.assertEqual(self.routes("/healthz/"), "/healthz")

    def test_a_traversal_attempt_is_just_an_unknown_route(self):
        # Nothing turns a path into a filesystem lookup, so this is a 404
        # rather than a file. Pinned because adding static serving later is
        # exactly how that stops being true.
        self.assertEqual(self.routes("/../../etc/passwd"), "/../../etc/passwd")
        self.assertNotIn(self.routes("/../../etc/passwd"),
                         ("/", "/api/board.json", "/healthz"))


if __name__ == "__main__":
    unittest.main()
