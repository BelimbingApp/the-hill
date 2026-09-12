# the-hill

**An app for running a software factory.**

A team of agents is not a pool of assistants answering questions. It is a shift
on a floor, working a mission: software that has to get built, reviewed and
shipped, by agents that come and go, across several repositories, without a
person holding every thread.

the-hill is what that shift runs on. It answers the questions a floor manager
asks, and nothing else:

- What is every lane waiting on, and is that thing going to happen?
- Who is working, who is stuck, who has gone quiet?
- What shipped, what was refused, and by whom?
- What on this machine is holding work nobody has saved?

It **assists delivery and never gates it**. It adds no required check to any
repository, and it cannot block a merge. If it breaks, or you throw it away,
your repositories keep working exactly as before.

> **Agents: read [AGENTS.md](AGENTS.md), not this file.** This one is for the
> person running the floor.

## The board

The reason to install it. `hill serve` puts a live display on a screen — the
real thing on a production floor, refreshing itself, stating how old it is, and
turning amber the moment a refresh stops landing.

### Running it (what you want)

```bash
hill serve                                  # http://localhost:8787
hill serve --port 9000 --interval 120       # pick a port, refresh more often
```

A live app. Open it on a spare screen and leave it. The page repaints every
ten seconds; a fresh collection starts every five minutes by default.

`--interval` is the time between the **starts** of two collections, not the
gap after one finishes. A pass takes about 108 seconds, so the two readings
differ by more than they sound: measured on the running board, waiting the
interval after each pass gave a real period of 408 seconds for a 300 second
setting. If a pass ever runs longer than the interval, the next one starts
immediately rather than queueing up behind it.

Three things it does deliberately:

**A request never waits on a collection.** One pass over every repository takes
about two minutes. A background thread does that on its own schedule and the
page is served instantly from the last finished pass. A board that hangs is
not a board.

**The page always says how old it is.** A strip under the masthead reads `live
· 12s old`, turns amber when refreshes fall behind, and red with the reason
when they stop landing. A wall display that quietly freezes is the worst thing
in this repository, so it cannot happen silently.

**It binds to loopback.** This reports on private repositories. `--host
0.0.0.0` puts that on your network and says so when it starts.

It serves exactly three routes — the page, `/api/board.json`, and `/healthz`.
No path is ever turned into a file lookup.

### Building one to a file

```bash
hill board --open          # one snapshot, written to board/latest.html
hill board --archive       # also keep it under its own timestamp
```

For mailing, attaching to an incident, or looking at the floor without a
process running. The data is inlined, so the file works on its own with no
server — and it labels itself **saved snapshot — not live**, because a
photograph that looks like a live feed is the same lie in a different format.

Archiving is opt-in: a directory filling with near-identical snapshots is not
a record anyone reads.

### What it shows

Every open lane and who it is waiting on; deliveries per day; per-agent accept
and changes-required counts; workspaces on this machine; GitHub API quota. It
follows your browser's light or dark theme.

Figures name their source. Where a source cannot answer, the board says
**unknown** rather than showing zero — the distinction between *measured zero*
and *not measured* is the whole reason to trust anything else on the page.

## Getting it running

- **Python 3**, standard library only. Nothing to install, no virtualenv, no
  `requirements.txt`. Exercised on 3.12 and 3.14; the modules that use `X | None`
  all carry `from __future__ import annotations`, so older 3.x should work, but
  those two are the versions it has actually been run on.
- **git**, on `PATH`.
- **[`gh`](https://cli.github.com/), authenticated.** `hill board`, `hill
  review` and `hill claim` all read GitHub through it — claim included, because
  who holds a lane is a question only GitHub can answer across machines. Check
  with `gh auth status`. Purely local commands — `send`, `inbox`, `who`,
  `workspaces`, `cleanup` — do not need it, and `hill claim --local` skips the
  check when you accept that the shared floor goes unread.
- `du` for workspace sizes, and `xdg-open` (or `open` on macOS) for
  `hill board --open`. Both are optional; without them you get a size of
  `unknown` and a path to open yourself.

**This checkout has no git remote yet.** It is a local repository on one
machine, so there is nowhere to clone from and `git pull` has nothing to fetch.
Copy the directory to get it onto a second machine until someone pushes it
somewhere; `git remote -v` tells you whether that is still true.

```bash
# once a remote exists:
git clone <remote> ~/.the-hill
# today, from the machine that has it:
cp -a /path/to/the-hill ~/.the-hill

cd ~/.the-hill
./hill version          # confirms where the checkout is and when it was updated
```

`hill version` reports `local_edits` so you can tell a checkout you have
changed from one you have not.

Put it on your `PATH` and name yourself once, in your shell profile:

```bash
export PATH="$HOME/.the-hill:$PATH"
export HILL_AGENT=your-agent-id
```

`HILL_AGENT` is the id every command attributes work to; with it set you can
drop `--agent` everywhere. Then check it works:

```bash
hill tick               # record that you are running
hill who                # you should see yourself, fresh
```

Nothing is created until first use. `state/`, `live/` and `board/` are written
on demand and are all git-ignored: they are local to this machine and are never
committed.

## Who does what

An agent does whichever job its prompt gives it. The prompts are in
[`docs/roles/`](docs/roles/):

| Role | Accountable for |
|---|---|
| [factory-manager](docs/roles/factory-manager.md) | the mission getting accomplished |
| [section-manager](docs/roles/section-manager.md) | the agents in one harness |
| [builder](docs/roles/builder.md) | one lane, delivered and reviewable |
| [reviewer](docs/roles/reviewer.md) | refusing work that is not ready |
| [security](docs/roles/security.md) | what the other three would wave through |
| [operator](docs/roles/operator.md) | the machine the factory runs on |

Paste one at the top of an agent's instructions. They say what the role owns
and where it must stop; they do not explain how to write code.

Two rules bind every role, and most incidents come from breaking one:
**never review your own lane**, and **say what you measured, not what you
expect**.

## How it keeps its facts straight

Four kinds of information, in three places, chosen deliberately:

| What | Where | Why |
|---|---|---|
| What was delivered and reviewed | GitHub | It is already the record, and it works across machines |
| Who holds which lane, across machines | GitHub: the `agent:<id>` label and the open-PR registry | It is the only thing every machine can see |
| Races between agents on **one** machine, and messages | SQLite, `state/hill.db` | Several local agents write at once, so it needs real locking |
| Messages between machines | GitHub: comments on the board issue (`HILL_BOARD`) | They must outlive the machine that sent them |
| Liveness and capacity | one file per agent in `live/` | Each agent writes only its own file, so no locking is needed |

The rule that keeps it honest: SQLite holds **observations and messages, never
decisions**. The moment it can answer "is this approved", two places answer the
same question and they will disagree.

A consequence worth knowing before you trust the board: where a source cannot
answer, it says **unknown** rather than showing zero. Distinguishing *measured
zero* from *not measured* is the reason to believe anything else on the page.

## Tests

    python3 -m unittest discover -s tests

Add `-v` to see the names. They cover the things that can destroy or lose work
— a lane claimed twice, a message read by nobody, a cleanup that deletes
unpublished work — and every signal that has already misreported something
real. This is the floor, not a suite to grow for its own sake.

## How it works

[`docs/how-it-works.md`](docs/how-it-works.md) — the whole thing in plain notes.

## Licence

MIT — see [LICENSE](LICENSE).
