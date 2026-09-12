# the-hill

**An app for running a software factory.**

A team of agents is not a pool of assistants answering questions. It is a shift
on a floor, working a mission: a body of software that has to get built,
reviewed and shipped, by agents that come and go, across several repositories,
without a human holding every thread.

the-hill is what that shift runs on. It answers the questions a floor manager
asks and nothing else:

- What is every lane waiting on, and is that thing going to happen?
- Who is working, who is stuck, who has gone quiet?
- What shipped, what was refused, and by whom?
- What on this machine is holding work nobody has saved?

**The board is the point.** `hill serve` puts a live display on a screen — the
real thing on a production floor, refreshing itself, stating how old it is,
going amber when a refresh stops landing. Not a report someone remembers to
run.

One checkout per machine at `~/.the-hill`. There is no version number and no
release process — `hill version` tells you when the checkout was last updated.

> **This checkout has no git remote yet.** It is a local repository on one
> machine, so there is nowhere to clone it from and `git pull` has nothing to
> fetch. The per-machine model below is how it is meant to work once a remote
> exists; until someone pushes it somewhere, copy the directory to get it onto
> a second machine. `git remote -v` tells you whether this still applies.

## Requirements

- **Python 3**, standard library only. Nothing to install, no virtualenv, no
  `requirements.txt`. Exercised on 3.12 and 3.14; the modules that use `X | None`
  all carry `from __future__ import annotations`, so older 3.x should work, but
  those two are the versions it has actually been run on.
- **git**, on `PATH`.
- **[`gh`](https://cli.github.com/), authenticated.** `hill board` and
  `hill review` read and write GitHub through it. Check with `gh auth status`.
  The local commands — `claim`, `send`, `who`, `workspaces` — do not need it.
- `du` for workspace sizes, and `xdg-open` (or `open` on macOS) for
  `hill board --open`. Both are optional; without them you get a size of
  `unknown` and a path to open yourself.

## Setup

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

## A normal session

```bash
hill tick                        # say you are alive, at the top of each cycle
hill who                         # who else is on this machine
hill claims                      # what is already held
hill claim blb-people#476        # take a lane; a race has exactly one winner
# ... do the work ...
hill inbox                       # anything queued for you
hill release blb-people#476      # give it back
```

`hill tick` is worth running every cycle rather than only when you touch the
board. Liveness is a snapshot, not a history — if you stop ticking, the record
goes stale and nobody can tell whether you are working or gone.

## The roles

A factory needs people who are accountable for different things, and an agent
does whichever job its prompt tells it to. Those prompts live in
[`docs/roles/`](docs/roles/):

| Role | Accountable for |
|---|---|
| [steward](docs/roles/steward.md) | the board moving at all |
| [builder](docs/roles/builder.md) | one lane, delivered and reviewable |
| [reviewer](docs/roles/reviewer.md) | refusing work that is not ready |
| [security](docs/roles/security.md) | what the other three would wave through |
| [operator](docs/roles/operator.md) | the machine the factory runs on |

Paste one at the top of an agent's instructions. They say what the role owns
and where it must stop; they do not explain how to write code.

Two rules bind every role, and most incidents come from breaking one of them:
**never review your own lane**, and **say what you measured, not what you
expect**.

## What it does and does not do

the-hill **helps agents deliver**. It does not stand between you and a merge.

- It never adds a required check to a product repository.
- It never blocks a merge. If the-hill is broken, your repositories keep working.
- You can always merge by hand on GitHub. Nothing here will stop you.

Three kinds of information, kept in three places on purpose:

| What | Where | Why |
|---|---|---|
| What was delivered and reviewed | GitHub | It is already the record, and it works across machines |
| Who holds which lane, and messages | SQLite, `state/hill.db` | Several agents write at once, so it needs real locking |
| Liveness and capacity | one file per agent in `live/` | Each agent writes only its own file, so no locking is needed |

The rule that keeps this honest: SQLite holds **observations and messages, never
decisions**. The moment it can answer "is this approved", two places answer the
same question and they will disagree.

## Commands

    hill tick --agent me            record that you are running
    hill who                        who is on this machine, and how fresh
    hill claim blb-people#476       take a lane (atomic — a race has one winner)
    hill claim blb-people#476 --take   take over a lane someone else holds
    hill release blb-people#476     give it up
    hill claims                     what is held right now
    hill send astra "text" --ref URL   queue a message
    hill inbox --agent me           read your messages
    hill workspaces                 worktrees on this machine
    hill cleanup                    show what could be removed (dry run)
    hill cleanup --apply            actually remove them
    hill review <repo> <pr> --head <sha> --verdict accept    post a verdict
    hill board --open               build one snapshot to a file
    hill serve                      run the live board on localhost:8787

Set `HILL_AGENT` once and you can drop `--agent`.

## Things that will bite you if you forget them

**A message does not wake anyone.** `hill send` puts it in a mailbox. If the
recipient is a Claude Code session, use that harness's own messaging to wake it.
Otherwise the message waits until the agent next runs `hill inbox`.

**Taking a lane does not stop the other agent.** `--take` records the takeover so
it is visible, but it cannot fence a writer. Two agents can still be writing. Keep
their unpushed work, re-check the branch immediately before you push, and never
force-push.

**A stale liveness record means unknown, not stopped.** An agent can be alive and
idle, or gone and recently ticked. Nothing here reports an agent as dead.

**A red review gate is not a failing test.** `Independent review` goes red the
moment a pull request opens, because nobody has reviewed it yet. The board
therefore reports it as *a reviewer*, never as CI. Where it says *gate
disagrees*, it found an acceptance bound to the head and the gate refused the
pull request anyway — trust the gate and go read its log, because the board's
scan is the weaker of the two and knows it.

**A workspace git cannot read is shown, not skipped.** An orphaned worktree —
one whose parent clone was deleted, so its `.git` file points at an admin
directory that no longer exists — is listed as `unreadable` and never offered
for removal. It is reported rather than omitted because the tool's whole job is
saying what is on the disk, and the largest reclaimable thing on this machine
was invisible to it for four days. Note that a fresh repository whose branch
has no commits yet also has no resolvable head; that is not unreadable, and it
can hold uncommitted work.

**Cleanup refuses to delete work that was never published.** Uncommitted files or
unpushed commits mean the worktree is kept, and it handles squash merges — a
squash-merged branch is not an ancestor of main, so a naive check would call
delivered work undelivered and a naive cleanup would then delete it.

## The board

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

## Tests

    python3 -m unittest discover -s tests

Add `-v` to see the names. They cover the things that can destroy or lose work — a lane claimed twice, a
message read by nobody, a cleanup that deletes unpublished work — and the one
board signal that has already misreported a real lane, which is who a lane is
waiting on. This is the floor, not a suite to grow for its own sake.

## Licence

MIT — see [LICENSE](LICENSE).
