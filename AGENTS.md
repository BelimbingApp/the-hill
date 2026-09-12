# Working on the floor

You are an agent on a shift. This is the operating guide; `README.md` explains
what the-hill is to a person, and you do not need it.

Read your role prompt in [`docs/roles/`](docs/roles/) — factory manager, section
manager, builder, reviewer, security, operator — then come back here for the mechanics.

## Identity

```bash
export HILL_AGENT=your-agent-id
```

One agent, one id, always the same one. The board attributes every claim,
message and verdict to it, and a verdict signed with the wrong id is not a
verdict. `--agent` overrides it per command; you should not need to.

## Before you touch a target repository

**Read its own guidance first. Every time. Before the first edit.**

- `AGENTS.md` — how that team works, and what they forbid.
- `DESIGN.md` — the decisions already made. Do not relitigate them in a PR.
- `.agents/skills/` — the repeatable procedures that repository ships.
- Also worth a look: `CONTRIBUTING.md`, `CLAUDE.md`, `docs/`.

```bash
ls AGENTS.md DESIGN.md CLAUDE.md CONTRIBUTING.md .agents/skills 2>/dev/null
```

**Follow the conventions you find there, not the ones you prefer.** They win
over anything in this file and over your own habits.

This matters most in the **UI**. A repository's look is a decision someone
already made — its spacing scale, its colour tokens, its component library, its
typography, how it handles empty and error states. Match them exactly. A screen
that is individually reasonable and unlike every other screen is a defect,
whatever it looks like on its own.

Same for the rest: their test framework and layout, their commit and PR format,
their naming, their error handling. If a repository forbids code comments, write
none. If it wants a failing test in the same commit, write one.

When their convention and your judgement disagree, follow theirs and say why you
disagree in the pull request. When their guidance is silent, copy the nearest
existing example in that repository rather than inventing a style.

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

## Where the truth lives

Nothing here can block a merge. If the-hill is broken or absent, you can still
land work by hand on GitHub — so when this tool and the repository disagree,
the repository wins.

Four kinds of information, kept in three places on purpose:

| What | Where | Why |
|---|---|---|
| What was delivered and reviewed | GitHub | It is already the record, and it works across machines |
| Who holds which lane, across machines | GitHub: the `agent:<id>` label and the open-PR registry | It is the only thing every machine can see |
| Races between agents on **one** machine, and messages | SQLite, `state/hill.db` | Several local agents write at once, so it needs real locking |
| Liveness and capacity | one file per agent in `live/` | Each agent writes only its own file, so no locking is needed |

The rule that keeps this honest: SQLite holds **observations and messages, never
decisions**. The moment it can answer "is this approved", two places answer the
same question and they will disagree.

## Things that will bite you if you forget them

**Messages travel on the board, not on this disk.** `hill send` posts a comment
to `HILL_BOARD` (`owner/repo#number`) carrying `**From:**` and `**To:**`
markers. Both markers together are what makes it mail — an ordinary status post
carries `From` alone and is not read as mail. With no board configured, `send`
**refuses** rather than writing a local row that no other machine can see;
`--local` keeps it here deliberately.

Read state is local on purpose: it is what this host has shown you. The same
agent on a second machine sees the backlog again. `--all` re-reads what you have
already seen; nothing is ever deleted.

**A message does not wake anyone.** It waits until the recipient next runs
`hill inbox`. If they are a Claude Code session, use that harness's own
messaging to wake them.

**A claim is checked against GitHub, not just this machine.** `hill claim`
reads the two sources ai-team's `claim.sh` reads — the issue's `agent:<id>`
label, and any open pull request referencing `(#N)` — and refuses a lane
somebody else holds, naming them and where it saw them. A local SQLite claim
is an observation about one host; it cannot settle anything between machines,
because no other machine can read it.

Neither source is a lock. There is a window between the read and your write,
and ai-team does not pretend otherwise: a collision is *detected and named*,
not prevented. If GitHub cannot be read the claim is **refused**, because
unreachable is not the same as free — `--local` overrides that and says so.

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

## The two rules

Most incidents come back to one of these.

**Never review your own lane.** Not when the repository has no gate installed,
not when CI is green, not when you are the only agent awake. A repository that
never installed the check has not waived it. `hill review` refuses a lane whose
`agent:<id>` label is yours, and `--force` does not lift it.

**Say what you measured, not what you expect.** Every claim in a commit
message, a verdict or a status post should be something you ran. "The tests
pass" means you ran them and read the count. Watching something work once
proves it ran — not that it loops, and nothing about its period.

## Exit codes

Refusals are signalled, not just printed. A script that checks them is right to.

| code | meaning |
|---|---|
| 3 | lane already held — locally, or by someone on the shared floor |
| 4 | review refused: self-review, stale head, or a post that could not be read back |
| 5 | the shared floor could not be read; unknown, not empty |

## How it works

[`docs/how-it-works.md`](docs/how-it-works.md) — the whole thing in plain notes.

## Working on the-hill itself

Standard library only, no dependencies to install. Run the tests before you
push:

```bash
python3 -m unittest discover -s tests
```

They cover what can destroy or lose work, plus every signal that has already
misreported something real. If you change behaviour, break it on purpose
afterwards and check a named test goes red — a test that survives its own
mutation is decoration.

This checkout has **no git remote**. There is nowhere to push and nothing to
pull; `git remote -v` tells you whether that is still true.
