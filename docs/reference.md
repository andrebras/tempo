# Tempo reference

## Commands

Claude uses these through the bundled skill. You can use them too, because `tempo` is on Claude
Code's `PATH` while the plugin is enabled. Run `tempo <command> --help` for every option.

| Command | What it does |
| --- | --- |
| `tempo add --title … --context … --option … --option …` | Add a question (`--recommended b`, `--goal "Release 0.4"`, `--start-here "unlocks Q5"`; `--number 40` uses that number, which must be the next free one or higher) |
| `tempo list`, `tempo show <n>` | List the open questions, or show one with its thread (`<n>` may be a short code such as `12b`). `list --status answered` (or `archived`, `all`) lists others, and `list --all-projects` lists every project's |
| `tempo context <n> "…"` | Add context to a question |
| `tempo reword <n> --note "…"` | Save a new version of a question; reopens it if it was answered |
| `tempo goal <n> "…"` | Set a question's goal without rewording it (`--start-here "…"`, `--no-start-here`; `""` removes the goal) |
| `tempo archive` | Archive the project's answered questions now (they also move to Archived by themselves 7 days after their latest answer) |
| `tempo set-next <n>` | Continue numbering from an older list |
| `tempo inbox` | Print answers and follow-ups Claude hasn't seen yet (`--all-projects` shows every project's without marking them seen) |
| `tempo wait --timeout 1800` | Wait until you answer or follow up, then print it. Without `--timeout` (or with `0`) it waits forever; `--interval 2` is how many seconds pass between looks at the database |
| `tempo answer <n> <letter>` | For you, not Claude: answer a question from the terminal, as the page does (`--note "…"`). The option may also be named by its whole text, in any case and without trailing punctuation, such as `tempo answer 61 yes`; a valid letter wins. It answers the current wording; once the question was reworded, the confirmation names it: `Answered Q61 (version 2): (a) Yes` |
| `tempo reply <n> "…"` | For you, not Claude: ask for more context from the terminal, as the page's follow-up does |
| `tempo status` | For you, not Claude: print `? 3` when the current folder's project has 3 open questions, nothing when it has none, for the tmux status bar (`--project NAME` counts one project, `--all` every project). It only reads the database: it never creates or changes it, and never starts the page |
| `tempo tui` | For you, not Claude: answer in a full-screen terminal view with Projects, Questions and Detail panels, keyboard only (`?` lists the keys; `--project NAME` starts on another project; `s` opens Settings, which lists the projects with their folders and merges one into another). It saves answers and follow-ups as the page does, follows changes by itself, and never starts the page |
| `tempo config`, `tempo config page on\|off` | For you, not Claude: show your settings, or turn on or off whether each session starts the page (see [Turning the page off](#turning-the-page-off)) |
| `tempo serve [--background]` | Run the page; works whether or not sessions start it (`--port` picks another port) |
| `tempo hook <event>` | Run by Claude Code's hooks (`session-start`, `user-prompt-submit`, `stop`); not for direct use (see [Hooks](#hooks)) |
| `tempo slash <name>` | Run by the slash commands below, with the typed text on stdin; not for direct use |

`add`, `show` and `list` take `--json` to print JSON instead of text, for scripts. `serve` and
`hook` take `--port` to use another port than the default (see
[The page server](#the-page-server)).

`oq`, the command's old name, still works as an alias.

## Slash commands

Four slash commands let you read and answer questions inside a Claude Code session. Each is a
skill in `skills/<name>/SKILL.md` with `disable-model-invocation: true`, so only you can run
it: Claude Code refuses when Claude tries. Before Claude reads the command, Claude Code runs
`bin/tempo slash <name>` from the plugin's folder, passing what you typed after the command on
stdin through a quoted heredoc: the shell doesn't read your text, unless one of its lines is
exactly the heredoc's end marker, `TEMPO_END_OF_TYPED_TEXT_9C4F2A7E`. Claude Code puts your
text into the command as it is, and offers no way to pass it outside the shell, so a line
like that ends the text early and the lines after it run as shell commands. The marker is
one nobody types or pastes by chance. The output becomes the text Claude gets.

| Command | What it does |
| --- | --- |
| `/tempo:list` | This project's open questions |
| `/tempo:show 57` | One question with its options and thread (`57b` works too) |
| `/tempo:answer 57a [note]` | Answer with option (a), with an optional note. The code may be `57a`, `57 a`, `Q57a` or `Q57 a)`, and the option may be named by the start of its text, such as `61 yes, but later` (a valid letter wins). Claude then acts on it |
| `/tempo:dive 57 [follow-up]` | Ask for more, like the page's follow-up; without text it records "Tell me more.", and `57b` (no space) asks about option (b). Claude then answers it in the question with `tempo reword` |

An answer or follow-up made this way is saved already marked delivered, in the same write:
Claude has it from the command's output, so the hooks and `tempo wait` don't hand it over a
second time. The page shows it as seen. When the question or option doesn't exist, nothing is
saved: the output says "Nothing was recorded:" with Tempo's error, and Claude passes it on.
The project is the one of the session's project folder (`TEMPO_PROJECT` still wins). With
`disableSkillShellExecution` on in your Claude Code settings, the commands can't run.

## Hooks

Hooks are small commands that Claude Code runs at fixed moments of a session. Tempo uses
three, each a call to `tempo hook …`:

- **SessionStart**, when a session starts: starts the page (see
  [The page server](#the-page-server)), unless you turned that off (see
  [Turning the page off](#turning-the-page-off)), and tells Claude how to use Tempo: every decision
  goes in Tempo, each question gets a goal, and every update ends with the project's open
  questions. It then lists those open questions.
- **UserPromptSubmit**, when you send a message: adds the answers and follow-ups Claude
  hasn't seen yet, so Claude reads them before your message.
- **Stop**, when Claude finishes a reply: sends Claude back to work, at most once per
  reply, in the first of these cases that applies:
  1. You answered or followed up and Claude hasn't seen it yet. The hook hands it over, so
     an answer sent from the page while Claude worked doesn't wait for your next message.
  2. The reply asks you something (lettered options, a yes-or-no question, a request for
     permission or a request to pick, as in "tell me A, B or C") that isn't in Tempo. Claude
     is asked to add it with `tempo add`. Asking you to do something, such as run a command,
     doesn't count, and neither do code and quoted text. Only a `tempo add`, `reword` or `context` that
     worked, or a Tempo link in the same paragraph, counts as recording it.
  3. A follow-up of yours reached Claude more than five minutes ago and Claude hasn't
     answered it (no context or reword after it). Claude is asked to answer each in the
     question with `tempo reword N --context` (`tempo context N` on an answered question).
  4. The project has open questions but no `tempo wait` is running. Claude is asked to
     start one in the background, so your answers from the page reach it without you
     writing in chat.

Each answer is delivered once, whichever way it arrives: through a hook or `tempo wait`.

The hooks file a session under the project of the folder Claude Code started it in
(`CLAUDE_PROJECT_DIR`), so a session that `cd`s into another project's folder doesn't take
that project's answers or nag about its questions. Only when Claude Code doesn't name that
folder do they use the folder the hook runs in. `tempo wait` does the same when
`CLAUDE_PROJECT_DIR` is set; in a Bash command it usually isn't, so the wait uses the folder
it runs in.

Claude Code shows Claude at most 10,000 characters of a hook's text: past that, only a file
path and a short preview. So a hook hands over only the answers and follow-ups that fit in
9,000 characters, oldest first, and ends with how many more are waiting. Those stay
undelivered: the next hook or `tempo wait` brings them, or `tempo inbox` prints them all at
once. One answer too long on its own still goes, cut short, with `tempo show` for the rest.
The SessionStart hook's list of open questions is cut the same way, ending with how many
more there are and `tempo list` to see them.

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
  process on the port is a Tempo server (`tempo serve`, run either as `python -m tempo serve`
  or as `…/tempo/__main__.py serve`).
- Anything else that answers, such as a Tempo too old to have `/health` or another
  program: it is left alone.

After starting its server, the session checks, for up to two seconds, what answers on the
port. An older session starting at the same moment may have found the port free too and
started its older server, which then holds the port; when what answers is an older Tempo on
the same database, it is replaced once.

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

### Turning the page off

If you answer from the terminal, you can stop sessions from starting the page, with the
switch under Sessions in the page's Settings or from the terminal:

```bash
tempo config page off   # sessions no longer start the page
tempo config page on    # back to the default
tempo config            # show the current value
```

The setting is kept in the database, so every session and project sees it. With it off:

- The SessionStart hook doesn't start the page. A page that is already running is left alone.
- `tempo serve --background` still starts the page whenever you want it.
- Claude points you to the terminal instead of the page. The SessionStart note, `tempo add`
  and the hooks name `tempo show N` and `tempo answer N <letter>` and give no page links.
  The Stop hook counts a question's number (such as Q5) in a sentence as pointing at it,
  where it would otherwise want the question's link.

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

## When a question changes while you answer

An answer is tied to the wording you saw. The page and the terminal screen (`tempo tui`)
send the version of the question they showed with your answer. If Claude reworded the
question in the meantime, Tempo refuses the answer with "Q1 changed since you looked at it
(now version 2); check it again", because the same letter may now name another option. The
page then shows the new wording at once and says what changed: your pick is cleared, and
your note is kept. On the terminal screen, the message shows on the bottom line and the new
wording in Detail; pick again. `tempo answer` and `/tempo:answer` answer the current
wording, and their confirmation names the version once the question was reworded.

If the question on the page is answered or archived elsewhere (the terminal screen, the
`tempo` command or another tab) while you are typing a note, the page stays on it and says
so, with your note still there to copy. **Move on** opens the next question. Without a
note or a pick, the page moves on by itself.

## Settings

The gear button at the top right of the page opens Settings.

- **Appearance**: theme (System, Light or Dark), font (System sans, Serif or Monospace), text
  size, and width: how many characters fit on a line of a question (Narrow 68, Medium 80 or
  Wide 100). These are kept in your browser, not in the database.
- **Sessions**: "Start the page with each session", On or Off. It is the same setting as
  `tempo config page on|off` (see [Turning the page off](#turning-the-page-off)) and is kept
  in the database.
- **Projects**: a project's name is what the page shows everywhere. A new git folder starts
  as a project with the folder's name. Renaming it, for example `onzehq` → "ONZE HQ", takes
  its questions, their numbers and its folders along.
  Each project shows its name, its number of questions and its folders, with an Edit
  button. Edit opens Rename and "Merge into…" under that project only; `Esc` closes it and
  drops what was typed there. To join two projects, for example a folder's old project and
  the new one Tempo started after the folder was renamed or moved, press Edit on the one to
  merge, pick the other project in "Merge into…" and click "Merge…". The page says what will move and asks again, inline,
  with "Merge into <project>" and "Cancel" (`Esc` cancels too); nothing moves until then.
  The merged project's questions and folders move to the other one, and the merged project
  goes away. Renaming a project to another's name does the same. If the other has
  questions, or numbering set with `tempo set-next`, the merged one's questions move after
  its numbers, in their order, and get new numbers:
  joining `tempo` (Q1–Q5) into "Tempo" (Q1–Q27) makes them Q28–Q32, and into a "Tempo" with
  no questions but `set-next 30`, Q30–Q34. Each moved question gets a note in its thread with its old
  number, such as "Moved from tempo Q2 when tempo joined Tempo.", so old references can
  still be traced.
  Under each project, Settings lists the folders it covers, with their paths, read-only.
  A long path wraps after a `/`, and inside a folder's name only when that name alone is
  wider than the line. A folder that no longer exists is marked "folder
  gone"; one remembered before Tempo stored paths is hidden but not deleted.
  A project is listed from the first session in its folder, before its first question. A
  project with no questions, and no numbering set with `tempo set-next`, is listed only
  while at least one of its folders is still on disk: once they are all gone, as with a
  throwaway folder in `/tmp`, Tempo forgets them the next time it lists the projects, and
  the project leaves Settings, the page's project list and `tempo tui`. Projects with
  questions are always listed, and their folders are kept.
- **Renamed or moved folders**: Tempo knows a folder by its path, so a folder that was
  renamed or moved is a new one to it and starts its own empty project. Tempo never guesses
  that it is an old one. To bring the old project's questions along, merge the old project
  into the new one with "Merge into…" (above), or with Settings (`s`) in `tempo tui`.

Projects are saved in the database, so every session, the hooks and the `tempo` command use
them.

## tmux and the status line

These recipes use the path of Tempo's marketplace copy; if you cloned Tempo yourself, use your
clone's `bin/tempo` instead.

### `tempo tui` in a tmux popup

tmux can open `tempo tui` in a popup window over your panes, and close it when you quit.
Add this to `~/.tmux.conf` to open it with `prefix` then `T` (`display-popup` needs tmux 3.2
or later):

```tmux
bind T display-popup -E -w 90% -h 85% -d "#{pane_current_path}" "~/.claude/plugins/marketplaces/tempo/bin/tempo tui"
```

`-E` closes the popup when `tempo tui` ends, `-w` and `-h` set its size, and `-d` starts it in
the current pane's folder, so it opens on that folder's project. To open it with `Alt-t`
alone, without the prefix, add this line too (`-n` means no prefix):

```tmux
bind -n M-t display-popup -E -w 90% -h 85% -d "#{pane_current_path}" "~/.claude/plugins/marketplaces/tempo/bin/tempo tui"
```

Then reload the file with `tmux source-file ~/.tmux.conf`.

### A badge in the tmux status bar

`tempo status` prints `? 3` when the current folder's project has three open questions, and
nothing when it has none, just like the page shows one project at a time. In a
pane in `~/Lab/shop` it counts shop's questions, not another project's. `--project NAME`
counts one project by name and `--all` counts every project. It only reads the database, so it
is quick and safe to run every few seconds, and it never starts the page. To show it in tmux,
add this to `~/.tmux.conf` (use the full path of `bin/tempo`, because tmux runs it outside
your shell and its aliases):

```tmux
set -g status-interval 5
set -g status-right '#(cd "#{pane_current_path}" && ~/.claude/plugins/marketplaces/tempo/bin/tempo status) %H:%M'
```

tmux fills in `#{pane_current_path}`, the folder of the pane you are in, so the badge follows
you from project to project.

To see a badge on every pane instead, each for its own folder, show it in the pane borders:

```tmux
set -g pane-border-status top
set -g pane-border-format ' #(cd "#{pane_current_path}" && ~/.claude/plugins/marketplaces/tempo/bin/tempo status) #{pane_current_command} '
```

Then reload it with `tmux source-file ~/.tmux.conf`.

### A badge in the Claude Code status line

Claude Code can show a status line under the prompt: it runs a command you choose and shows
what it prints. Claude Code passes the command details about the session as JSON on its
standard input, including the session's folder in `workspace.current_dir`. Run
`tempo status` in that folder to add the badge for the session's project.

If you already have a status line script, add these two lines to it and print `$tempo` at
the end of its output (they use [jq](https://jqlang.org) to read the JSON):

```sh
dir=$(printf '%s' "$input" | jq -r '.workspace.current_dir')
tempo=$(cd "$dir" && ~/.claude/plugins/marketplaces/tempo/bin/tempo status 2>/dev/null)
```

Here `$input` holds the JSON the script read with `input=$(cat)`. A whole script that shows
only the folder's name and the badge looks like this, saved as `~/.claude/statusline.sh`
and made runnable with `chmod +x ~/.claude/statusline.sh`:

```sh
#!/bin/sh
input=$(cat)
dir=$(printf '%s' "$input" | jq -r '.workspace.current_dir')
tempo=$(cd "$dir" && ~/.claude/plugins/marketplaces/tempo/bin/tempo status 2>/dev/null)
printf '%s %s\n' "$(basename "$dir")" "$tempo"
```

Then point Claude Code at it in `~/.claude/settings.json`:

```json
{
  "statusLine": { "type": "command", "command": "~/.claude/statusline.sh" }
}
```

## Environment variables

| Setting | Default | Variable |
| --- | --- | --- |
| Database | `~/.claude/tempo/questions.db` | `TEMPO_DB` |
| Port | `2204`, or a port of its own for code on a branch database | `TEMPO_PORT` |
| Project | The project the current git repository's folder is filed under, named after the folder until renamed (worktrees count as the same folder) | `TEMPO_PROJECT`, or `--project` on a command; both win over Settings |
| Hooks stay quiet | Off | `TEMPO_IN_THREAD=1` (see below) |
| Colours in `tempo tui` | On | `NO_COLOR`, set to anything, turns them off |
| Google Chrome, for the page tests and `bin/media` | Found in the usual places | `CHROME`, the browser's path |

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
every command stops with an error that says so, before it reads or changes anything, and
the page shows that error. Update Tempo, or restart the session so it uses the installed
version.

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
