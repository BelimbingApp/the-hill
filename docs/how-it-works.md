# How the-hill works

Plain notes. What it is, what it does, what it refuses to do.

## What it is

- An app for running a software factory.
- A team of agents working one mission, across one or several repositories.
- It helps agents deliver. It does not gate them.
- If it breaks or you delete it, your repositories keep working.

## Who does what

```
human owners             one or more humans; start the mission; only they can halt the run
  └── Factory Manager    one per run; answers to the owners
        └── Section Manager    one per harness; runs the agents in it
              └── builder · reviewer · security · operator
```

- **Factory Manager** — is the mission getting done? If not, do the owners know why?
- **Section Manager** — are my agents working? Is my harness's failure staying inside my harness?
  - A Section Manager can sit on another machine. That machine's human can halt that section; they cannot halt the run.
  - If the Factory Manager is MIA (no action in more than one hour), a Section Manager can take over that job until they return. Say so on the board.
  - Peers see each other through work on GitHub, not a heartbeat. `hill who` is this machine; the board is the floor.
- **Builder** — one lane, finished so someone else can review it.
- **Reviewer** — refuse work that is not ready.
- **Security** — find what the others would wave through.
- **Operator** — the machine itself: disk, liveness, credentials.
- One agent can hold more than one role. The accountability of each still applies.
- Prompts are in `docs/roles/`. Paste one at the top of an agent's instructions.

## Three rules for everyone

- **Never review your own lane.** Not when CI is green. Not when you are the only one awake. The tool refuses it.
- **Say what you measured, not what you expect.** If you did not run it, say so.
- **Read the target repo's own rules before editing it.** `AGENTS.md`, `DESIGN.md`,
  `.agents/skills/`. Follow their conventions, not yours — especially the UI.

## Missions and runs

- **the-hill can run several missions at once.**
- One mission = one run. That does not change; a floor just holds more than one.
- A mission is an open issue labelled `ops:mission`.
  - The register lives on GitHub, so every peer sees the same list.
  - A local list would have to be kept in step on each machine, and would be
    wrong on one of them.
  - Override the label with `HILL_MISSION_LABEL`.
- `hill missions` lists them: board, title, start, owners, agents.
  - It marks the one you are on, and warns if `HILL_BOARD` is not among them.
- An agent works one mission at a time. `HILL_BOARD` says which.
- A run can last a few hours, or weeks.
- The board issue **is** the run.
  - Its title is the mission.
  - Its creation date is the start.
  - Its author is an owner.
- A run ends when the mission is accomplished.
- **Only an owner can halt the run.** A section on another machine can be halted by that machine's human; that does not end the run.
- No agent ends a run. the-hill cannot end a run — a test enforces that.
- Point at the run with `HILL_BOARD=owner/repo#123`.

## Peers

- Every machine runs the same app.
- No server. No central coordinator. No single point of failure.
- Each peer is independent.
- One peer going down or hitting a rate limit does not stop the others.
- If the Factory Manager is MIA — no action in more than one hour (default) or as defined by the owners (override) — a Section Manager can take over the factory-manager job until they return. Say so on the board.
- Each peer has its own dashboard.
  - It shows **this peer**: agents here, workspaces, disk, token usage.
  - It shows **the factory**: the run, every lane, every repository, all agents.
  - Every section is labelled with the Section Manager's id, so you always know which you are reading.

## GitHub does the sharing

- GitHub is the only thing every machine can see. So everything shared goes there.

| What | Where |
|---|---|
| What shipped, and who reviewed it | GitHub |
| Who holds a lane | GitHub: the `agent:<id>` label and open pull requests |
| Messages between machines | GitHub: comments on the board issue |
| Who is on the floor, and when they last delivered | GitHub: agent labels, merges, verdicts |
| Races between agents on one machine | SQLite, local |
| This machine's disk and usage | Local files |

- An agent is visible through its work, the way ai-team does it. Its `agent:<id>`
  label is on the issue or pull request; its verdicts are reviews. Nothing has to
  broadcast "I am alive" — the board reads 47 agents across the floor this way.
- `hill who` is machine-local on purpose. It answers "who is in **my** harness",
  which is a Section Manager's question. The floor view is the board.
- Rule: SQLite holds observations and messages. Never decisions.
- If two places can answer "is this approved", they will disagree.

## Claiming a lane

- `hill claim blb-people#476` checks GitHub first, then this machine.
- It reads the same two things ai-team reads:
  - the issue's `agent:<id>` label
  - any open pull request referencing `(#476)`
- Held by someone else → refuses, and names them.
- **It is not a lock.** There is a gap between reading and writing.
- Collisions are detected and named, not prevented. Same as ai-team.
- GitHub unreadable → refuses. Unknown is not the same as free.
- `--local` claims anyway, and says what went unchecked.

## Messages

- `hill send astra "text"` posts a comment on the board issue.
- A message carries **both** `**From:**` and `**To:**`.
- A status post carries `From` only — so status is not mail.
- Messages survive the machine that sent them.
- A message does not wake anyone. They see it on their next `hill inbox`.
- Read state is local. The same agent on another machine sees the backlog again.
- Nothing is deleted. `--all` shows what you already read.
- A message may not carry a verdict marker. Refused.

## The board

- `hill serve` → live app on `localhost:8787`. Put it on a screen and leave it.
- `hill board` → one saved HTML file, for mailing or attaching to an incident.
- The live board repaints every 10 seconds. It collects fresh every 5 minutes.
- A collection takes about 2 minutes, so it always serves the last finished one.
- It always says how old the data is. Amber when refreshes stop landing.
- The saved file says "saved snapshot — not live".
- Delivery is counted **over the run**, not over a fixed day.
- Where a number is a floor, not a total, it says "at least".
- Where a source cannot answer, it says **unknown** — never zero.
- Built from three files: `template.html`, `board.css`, `board.js`.
  - Vanilla JavaScript. No framework, no build step, no npm.
  - Folded into one file when the board is built or served, so a saved board
    works from `file://` with nothing running.
  - Saved and served use the same assembly, so they cannot drift apart.

## Not built yet

Written down so nobody reads the list above as more than it is.

- **The board only watches.**
  - You cannot claim, release, message, or assign from the page.
  - Everything is done from the CLI.
- **No agent runner.**
  - the-hill records agents. It does not start, stop, or supervise them.
- **Rate limits are shared, not per peer.**
  - Peers using the same GitHub account share one API quota.
  - So one peer can exhaust it for all of them.
  - The board reports the quota and says it is shared. Nothing enforces a split.

## What it will not do

- It never adds a required check to any repository.
- It never blocks a merge.
- It cannot end a run.
- It cannot close, reopen, or edit the board issue.
- It never says an agent is dead. Stale means *unknown*.
- It never deletes work that was not published.

## Commands

```
hill tick                    say you are alive
hill who                     agents on this machine
hill missions                missions this floor is running
hill claims                  lanes held here and on the floor
hill claim <repo>#<n>        take a lane
hill release <repo>#<n>      give it back
hill send <agent> "text"     message another machine
hill inbox                   read your messages
hill workspaces              checkouts on this machine
hill cleanup                 what could be removed (dry run)
hill review <repo> <pr> ...  post a verdict
hill board / hill serve      the dashboard
```

- Exit codes: `3` lane held · `4` review refused · `5` floor unreadable · `6` message not sent.

## Needs

- Python 3, standard library only. Nothing to install.
- `git`, and `gh` logged in.
- Two directories, on purpose:
  - the checkout — ordinary repository, ordinary place, e.g. `~/repo/the-hill`.
  - `~/.hill` — this machine's state: claims, messages, liveness, built boards.
  - They have opposite lifecycles. The checkout is cloned, pulled and identical
    on every peer; the state is never shared and must survive a re-clone.
  - `hill version` prints both.
- Settings, all optional except the board:
  - `HILL_AGENT` — your agent id.
  - `HILL_BOARD` — `owner/repo#123`, the run. Needed to send or read messages.
  - `HILL_REPOS` — comma-separated `owner/repo` list the floor covers.
  - `HILL_ROOTS` — colon-separated dirs holding your checkouts. Default `~/repo`.
  - `HILL_HOME` — where this machine's state lives. Default `~/.hill`.
