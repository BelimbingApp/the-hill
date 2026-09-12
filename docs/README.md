# the-hill

Tools for running a team of AI agents across several repositories. One checkout
per machine at `~/.the-hill`. Update it with `git pull`. There is no version
number and no release process — `hill version` tells you when the checkout was
last updated.

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
    hill board --open               build and open the dashboard

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

**Cleanup refuses to delete work that was never published.** Uncommitted files or
unpushed commits mean the worktree is kept, and it handles squash merges — a
squash-merged branch is not an ancestor of main, so a naive check would call
delivered work undelivered and a naive cleanup would then delete it.

## Dashboard

`hill board` writes a single self-contained HTML file to `board/latest.html`.
Open it by double-clicking; there is no server to start. The data is inside the
file, and each build is kept under its own timestamp so you can open an old one.

Figures name their source. Where a source cannot answer, the board says
"unknown" rather than showing zero.

## Tests

    python3 -m unittest discover -s tests

They cover the things that can destroy or lose work — a lane claimed twice, a
message read by nobody, a cleanup that deletes unpublished work — and the one
board signal that has already misreported a real lane, which is who a lane is
waiting on. This is the floor, not a suite to grow for its own sake.
