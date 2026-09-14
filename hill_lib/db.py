"""Per-machine SQLite: the two things that genuinely need contended writes.

Claims and messages are shared state that several agent processes on one machine
write at the same time. A JSON file cannot arbitrate that — two agents claiming
the same lane would silently clobber each other, which is the collision that had
no answer in the ai-team#128 discussion.

Everything else stays out. Liveness and quota are per-agent writes with no
contention, so they live in files under live/ where an agent can only overwrite
its own record. Delivery and review decisions live in GitHub. This database
holds observations and messages, never decisions: the moment it can answer
"is this approved" there are two sources of truth for one question, which is
the defect shape behind ai-team#125, #127 and blb-people#480.
"""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import datetime
from pathlib import Path

# Machine-local state, deliberately NOT inside the checkout. Source and state
# have opposite lifecycles: the checkout is cloned, pulled and identical on
# every peer, while state/ live/ board/ are never shared and must survive a
# re-clone. Keeping them in one directory meant `rm -rf` and clone again
# silently destroyed this machine's claims, messages and liveness.
HOME = Path(os.environ.get("HILL_HOME", Path.home() / ".hill"))
STATE = HOME / "state"
DB_PATH = STATE / "hill.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS claims (
  key         TEXT PRIMARY KEY,   -- "<repo>#<lane>", e.g. blb-people#476
  agent       TEXT NOT NULL,
  session     TEXT,
  host        TEXT NOT NULL,
  claimed_at  TEXT NOT NULL,
  note        TEXT
);
CREATE TABLE IF NOT EXISTS messages (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  sender      TEXT NOT NULL,
  recipient   TEXT NOT NULL,      -- agent id, or "*" for everyone
  body        TEXT NOT NULL,
  ref         TEXT,               -- durable handoff this refers to (PR/issue URL)
  sent_at     TEXT NOT NULL,
  read_at     TEXT,
  read_by     TEXT,
  host        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  at          TEXT NOT NULL,
  agent       TEXT,
  kind        TEXT NOT NULL,
  detail      TEXT
);
-- Read state belongs to the (message, reader) pair, not to the message. A
-- broadcast has many readers, and a single read_at column on the message would
-- let the first reader mark it read for everyone — so the second agent never
-- sees it. Caught by tests/test_db.py before it could lose a message.
CREATE TABLE IF NOT EXISTS message_reads (
  message_id  INTEGER NOT NULL,
  agent       TEXT NOT NULL,
  read_at     TEXT NOT NULL,
  PRIMARY KEY (message_id, agent)
);
CREATE INDEX IF NOT EXISTS ix_msg_recipient ON messages(recipient);
CREATE INDEX IF NOT EXISTS ix_events_at ON events(at);
"""


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def host() -> str:
    return socket.gethostname()


def connect() -> sqlite3.Connection:
    STATE.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=15, isolation_level=None)
    con.row_factory = sqlite3.Row
    # WAL lets readers run while another process writes; without it a dashboard
    # collector would block an agent trying to claim a lane.
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=15000")
    con.executescript(SCHEMA)
    return con


def log(con: sqlite3.Connection, kind: str, agent: str | None = None, **detail) -> None:
    con.execute(
        "INSERT INTO events (at, agent, kind, detail) VALUES (?,?,?,?)",
        (now(), agent, kind, json.dumps(detail, separators=(",", ":")) if detail else None),
    )


# --- claims ---------------------------------------------------------------

class Held(Exception):
    """Raised when a lane is already claimed by someone else."""

    def __init__(self, row: sqlite3.Row):
        self.row = row
        super().__init__(f"{row['key']} is held by {row['agent']} since {row['claimed_at']}")


def claim(con, key: str, agent: str, session: str | None = None,
          note: str | None = None, take: bool = False) -> dict:
    """Take a lane. Atomic: two agents racing cannot both win.

    `take` performs the owner-approved takeover (ai-team#128): it succeeds even
    when held, but records who was displaced so the takeover is visible rather
    than silent. It never touches the other agent's files or branch — fencing
    the previous writer is the caller's problem, and unchanged HEAD is not proof
    that they stopped.
    """
    with con:
        con.execute("BEGIN IMMEDIATE")
        cur = con.execute("SELECT * FROM claims WHERE key=?", (key,)).fetchone()
        if cur and cur["agent"] != agent:
            if not take:
                raise Held(cur)
            log(con, "claim.taken", agent, key=key, displaced=cur["agent"],
                held_since=cur["claimed_at"])
            con.execute("DELETE FROM claims WHERE key=?", (key,))
        elif cur:
            return dict(cur)  # already ours; idempotent
        con.execute(
            "INSERT INTO claims (key, agent, session, host, claimed_at, note) VALUES (?,?,?,?,?,?)",
            (key, agent, session, host(), now(), note),
        )
        log(con, "claim", agent, key=key, note=note)
        return dict(con.execute("SELECT * FROM claims WHERE key=?", (key,)).fetchone())


def release(con, key: str, agent: str | None = None) -> bool:
    with con:
        con.execute("BEGIN IMMEDIATE")
        cur = con.execute("SELECT * FROM claims WHERE key=?", (key,)).fetchone()
        if not cur:
            return False
        if agent and cur["agent"] != agent:
            raise Held(cur)
        con.execute("DELETE FROM claims WHERE key=?", (key,))
        log(con, "release", cur["agent"], key=key)
        return True


def claims(con) -> list[dict]:
    return [dict(r) for r in con.execute("SELECT * FROM claims ORDER BY claimed_at")]


# --- messages -------------------------------------------------------------

def send(con, sender: str, recipient: str, body: str, ref: str | None = None) -> int:
    with con:
        cur = con.execute(
            "INSERT INTO messages (sender, recipient, body, ref, sent_at, host) VALUES (?,?,?,?,?,?)",
            (sender, recipient, body, ref, now(), host()),
        )
        log(con, "message.sent", sender, to=recipient, ref=ref)
        return int(cur.lastrowid)


def inbox(con, agent: str, unread_only: bool = True, mark: bool = True) -> list[dict]:
    """Messages addressed to `agent` (and broadcasts), oldest first.

    Unread is per reader, so a broadcast is delivered once to each agent rather
    than consumed by whoever happens to read first.
    """
    sql = """SELECT m.* FROM messages m
             WHERE (m.recipient=? OR m.recipient='*')"""
    if unread_only:
        sql += """ AND NOT EXISTS (SELECT 1 FROM message_reads r
                                   WHERE r.message_id=m.id AND r.agent=?)"""
        params = (agent, agent)
    else:
        params = (agent,)
    sql += " ORDER BY m.id"
    rows = [dict(r) for r in con.execute(sql, params)]
    if mark and rows:
        with con:
            con.executemany(
                "INSERT OR IGNORE INTO message_reads (message_id, agent, read_at) VALUES (?,?,?)",
                [(r["id"], agent, now()) for r in rows])
            # Informational only: when the message was first picked up by anyone.
            con.executemany(
                "UPDATE messages SET read_at=?, read_by=? WHERE id=? AND read_at IS NULL",
                [(now(), agent, r["id"]) for r in rows])
    return rows


def outbox(con, limit: int = 50) -> list[dict]:
    return [dict(r) for r in con.execute(
        "SELECT * FROM messages ORDER BY id DESC LIMIT ?", (limit,))]


def message_seen(con, external_id: str, agent: str) -> bool:
    """Has this agent already been shown a message carried by GitHub?

    Keyed by the comment id rather than a local row, so the same board message
    read on this machine stays read here across restarts. Deliberately local:
    read state is what THIS host has shown you, and the same agent on another
    machine sees the backlog again.
    """
    row = con.execute(
        "SELECT 1 FROM message_reads WHERE message_id = ? AND agent = ?",
        (_external_key(external_id), agent)).fetchone()
    return row is not None


def mark_seen(con, external_id: str, agent: str) -> None:
    with con:
        con.execute(
            "INSERT OR IGNORE INTO message_reads (message_id, agent, read_at) "
            "VALUES (?,?,?)", (_external_key(external_id), agent, now()))


def _external_key(external_id: str) -> int:
    """A stable negative row id for a message that lives on GitHub.

    message_reads.message_id is an integer keyed to local messages. Negative
    keys cannot collide with a local rowid, so both kinds of read state share
    one table without a migration or a second one to keep in step.
    """
    digits = "".join(ch for ch in external_id if ch.isdigit())
    return -int(digits) if digits else -abs(hash(external_id))


def events(con, limit: int = 200) -> list[dict]:
    return [dict(r) for r in con.execute(
        "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))]
