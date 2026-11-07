# Tempo reference

## Commands

Claude uses these through the bundled skill. You can use them too, because `tempo` is on Claude
Code's `PATH` while the plugin is enabled. Run `tempo <command> --help` for every option.

| Command | What it does |
| --- | --- |
| `tempo add --title … --context … --option … --option …` | Add a question (`--recommended b`, `--goal "Release 0.4"`, `--start-here "unlocks Q5"`) |
| `tempo list`, `tempo show <n>` | List questions, or show one with its thread (`<n>` may be a short code such as `12b`) |
| `tempo context <n> "…"` | Add context to a question |
| `tempo reword <n> --note "…"` | Save a new version of a question; reopens it if it was answered |
| `tempo goal <n> "…"` | Set a question's goal without rewording it (`--start-here "…"`, `--no-start-here`; `""` removes the goal) |
| `tempo archive` | Archive the project's answered questions now (they also move to Archived by themselves 7 days after their latest answer) |
| `tempo set-next <n>` | Continue numbering from an older list |
| `tempo inbox` | Print answers and follow-ups Claude hasn't seen yet (`--all-projects` shows every project's without marking them seen) |
| `tempo wait --timeout 1800` | Wait until you answer or follow up, then print it |
| `tempo serve [--background]` | Run the page |

`oq`, the command's old name, still works as an alias.

## Hooks

Hooks are small commands that Claude Code runs at fixed moments of a session. Tempo uses
three, each a call to `tempo hook …`:

- **SessionStart**, when a session starts: starts the page (see
  [The page server](#the-page-server)) and tells Claude how to use Tempo: every decision
  goes in Tempo, each question gets a goal, and every update ends with the project's open
  questions. It then lists those open questions. When the session's folder looks like a
  known folder renamed or moved, it asks Claude to tell you about the banner on the page.
- **UserPromptSubmit**, when you send a message: adds the answers and follow-ups Claude
  hasn't seen yet, so Claude reads them before your message.
- **Stop**, when Claude finishes a reply: sends Claude back to work, at most once per
  reply, in the first of these cases that applies:
  1. You answered or followed up and Claude hasn't seen it yet. The hook hands it over, so
     an answer sent from the page while Claude worked doesn't wait for your next message.
  2. The reply asks you something (lettered options, a yes-or-no question or a request for
     permission) that isn't in Tempo. Claude is asked to add it with `tempo add`. Code and
     quoted text don't count as asking.
  3. The project has open questions but no `tempo wait` is running. Claude is asked to
     start one in the background, so your answers from the page reach it without you
     writing in chat.

Each answer is delivered once, whichever way it arrives: through a hook or `tempo wait`.

A Tempo problem never blocks your prompt or the session. When something goes wrong, such as
a locked database or a folder Tempo can't name, the hooks exit cleanly and say nothing to the
session. The error goes to standard error, which Claude Code doesn't pass to Claude, and to
the page log. With `TEMPO_IN_THREAD=1` (see [Environment variables](#environment-variables)),
the hooks do nothing but write their line in the log.

A Claude session keeps the plugin version it started with, hooks included. After you update
Tempo, restart open sessions, for example with `claude --continue`, so they use the new
hooks. When a session's hooks are older than the installed plugin (as listed in
`~/.claude/plugins/installed_plugins.json`), the SessionStart and UserPromptSubmit hooks say
so once in that session, so Claude can ask you to restart it. Code run from a git checkout
or worktree never says this.

## The page server

The page is served by a small web server on `127.0.0.1`, port 2204 by default. Tempo code
that uses a branch database (see [Environment variables](#environment-variables)) serves its
own page on another port, between 22100 and 22999, worked out from the database's path, so
it never takes the real page's place. Every new session checks what is running on its port:

- Nothing: Tempo starts its server in the background.
- A Tempo server that shows another database: it is left alone, whatever its version.
- A Tempo server of the same version or a newer one: it is kept, whatever folder it runs
  from. So when sessions run different versions of Tempo, the newest one wins.
- A Tempo server that reports an older version: Tempo stops it and starts its own.
- A server that no longer answers: Tempo stops it and starts its own, but only if the
  process on the port is a Tempo server (`python -m tempo serve`).
- Anything else that answers, such as a Tempo too old to have `/health` or another
  program: it is left alone.

To tell, Tempo asks the server for `/health`. That address answers without opening the
database, so a broken database can't hide the server. It returns JSON with `app`
(`"tempo"`), `version`, `commit` (the git commit, when Tempo runs from a git checkout),
`root` (the folder the server's code runs from), `db` (the database it shows), `pid` (its
process number) and `started` (when it started, in UTC). All of it is worked out when the
server starts.

The page asks for `/health` too, every few seconds. When the server it first talked to has
been replaced (its `pid` or `started` changed), usually after an update, the page reloads
itself, so its code always matches the server's. A note you were typing and the option you
picked are kept through the reload.

## Projects in the header

The page shows one project at a time. The header has a chip for each project that has
something for you: at least one open question, or something new since you last looked at
that project. Each chip shows the project's name and its open count, such as `tempo · 2`.
The project you are on always has a chip, highlighted, even with nothing open. Click a chip
to switch to that project.

A dot on a chip means something arrived since you last viewed that project: a new question,
a reworded question, or Claude's reply to a follow-up. Viewing the project clears the dot.
When you last viewed each project is kept in your browser, not in the database; on a
browser's first visit nothing counts as new yet.

**All projects ▾**, after the chips, lists every project with its open count, so a project
with nothing waiting is one click away. It works with the keyboard: the arrow keys, Home
and End move through the list, and Escape closes it.

A project gets its chip by itself, within a few seconds of its first new question.

## Goals

Each question names its **goal**: the piece of work its answer moves forward, such as
"Release 0.4". The page groups a project's open questions under their goal. Groups come in
the order of their oldest question, and questions without a goal come last, under "Other".
Inside a group, the oldest question comes first.

Within a goal, Claude may mark one question **Start here**, with a one-line reason such as
"unlocks Q5 and Q6". It does so only when that answer unlocks or sets the pattern for the
others. The question then comes first in its group. Marking another question in the same
goal moves the mark.

Each open question also shows two facts that Tempo works out itself:

- **quick**: it has a recommended option, at most three options, and was never reworded.
- **open 3 days** (or hours, or minutes): how long ago it was asked.

### What changed since you opened it

Questions keep their place in the list; nothing moves when Claude changes one. Instead, a
dot after the question's number, the same dot as on the project chips, means Claude changed
it since you last opened it: it reworded the question, or added context (such as its reply
to your follow-up). A question that arrives after your first visit to the project gets the
dot too, until you open it. Opening the question clears the dot.

While a question waits on Claude after you ask for more, its row shows the moving "Waiting
on Claude" dots instead. When Claude replies or rewords, the wait ends and the dot appears.

When you last opened each question is kept in your browser, not in the database, and only
for open questions. On a browser's first visit to a project, every question counts as
opened, so nothing is marked yet.

Goals replace the earlier Now / Soon / Later priorities. Old questions keep their priority in
the database, but nothing uses it. `--priority` and `tempo priority` still run for one
release, but only print a note.

## Settings

The gear button at the top right of the page opens Settings.

- **Appearance**: theme (System, Light or Dark), font (System sans, Serif or Monospace), text
  size, and width: how many characters fit on a line of a question (Narrow 68, Medium 80 or
  Wide 100). These are kept in your browser, not in the database.
- **Projects**: a project's name is what the page shows everywhere. A new git folder starts
  as a project with the folder's name. Renaming it, for example `onzehq` → "ONZE HQ", takes
  its questions, their numbers and its folders along. To join two projects, rename one to
  the other's name. If both have questions, the renamed one's questions move after the
  other's, in their order, and get new numbers: joining `tempo` (Q1–Q5) into "Tempo"
  (Q1–Q27) makes them Q28–Q32. Each moved question gets a note in its thread with its old
  number, such as "Moved from tempo Q2 when tempo joined Tempo.", so old references can
  still be traced.
  Under each project, Settings lists the folders it covers, with their paths, read-only.
  Only folders still on disk are listed: a folder that no longer exists, or that was
  remembered before Tempo stored paths, is hidden but not deleted.
- **Renamed or moved folders**: when a session runs in a new folder while a known folder no
  longer exists, a banner on every screen asks whether the new folder is that one renamed or
  moved. It has a "Link to <project>" button for each missing folder, named with that
  folder's path, and a "Keep as its own project" button.

Projects are saved in the database, so every session, the hooks and the `tempo` command use
them.

## Environment variables

| Setting | Default | Variable |
| --- | --- | --- |
| Database | `~/.claude/tempo/questions.db` | `TEMPO_DB` |
| Port | `2204`, or a port of its own for code on a branch database | `TEMPO_PORT` |
| Project | The project the current git repository's folder is filed under, named after the folder until renamed (worktrees count as the same folder) | `TEMPO_PROJECT`, or `--project` on a command; both win over Settings |

The page log is written next to the database. The database sits outside the plugin folder, so
it survives plugin updates and reinstalls. The variables' old names (`OQ_DB`, `OQ_PORT`,
`OQ_PROJECT`) still work; when both are set, the `TEMPO_` one wins. If only the old database
at `~/.claude/open-questions/questions.db` exists, Tempo keeps using it where it is.

Tempo code that runs from a git checkout which isn't on `main` uses a branch database
instead, `.tempo-dev/questions.db` inside that checkout, unless `TEMPO_DB` is set, so code
still being built never touches (or migrates) your real questions. That covers every
worktree, whatever its branch, and the main checkout on another branch or on no branch. Only
the installed plugin and the main checkout on `main` use the real database. Code on a branch
database serves its page on a port of its own too, unless `TEMPO_PORT` is set.

A database last changed by a newer Tempo, with changes this code doesn't know, is refused:
every command stops with an error that says so, before it reads or changes anything. Update
Tempo, or restart the session so it uses the installed version.

The page log, `server.log`, also gets one line for each change made through the page (the
time in UTC, the method, the path and the status code, such as
`2026-11-07T10:00:00Z POST /api/questions/demo/1/answer 200`) and one for each hook run (the
time, the event, the project and what happened, such as `delivered 2`,
`blocked: unrecorded question`, `blocked: no wait` or `error: …`). It never holds what a request
sent or the text of a question or answer. Past about 1 MB it moves to `server.log.1`, replacing
the older one, and a new `server.log` starts.

With `TEMPO_IN_THREAD=1`, Tempo's hooks do nothing and print nothing, apart from their line in
the log: another tool that runs Claude sessions on your behalf can set it so Tempo stays quiet in
those sessions.
