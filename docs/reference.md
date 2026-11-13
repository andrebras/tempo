# Tempo reference

## Commands

Claude uses these through the bundled skill. You can use them too: `tempo` is on Claude Code's
`PATH` while the plugin is enabled, and `/tempo:install` puts it in your own shell. Run
`tempo <command> --help` for every option. `oq`, the command's old name, still works.

Commands for Claude:

| Command | What it does |
| --- | --- |
| `tempo add --title … --context … --option … --option …` | Add a question. `--recommended b`, `--goal "Release 0.4"`, `--start-here "unlocks Q5"`; `--number 40` uses that number (the next free one or higher) |
| `tempo list` | List the open questions. `--status answered`, `archived` or `all` lists others; `--all-projects` lists every project's |
| `tempo show <n>` | Show one question with its thread (`<n>` may be a code such as `12b`) |
| `tempo context <n> "…"` | Add context to a question |
| `tempo reword <n> --note "…"` | Save a new version of a question; reopens it if it was answered |
| `tempo goal <n> "…"` | Set a question's goal (`""` removes it). `--start-here "…"`, `--no-start-here` |
| `tempo archive` | Archive the answered questions now (they also archive by themselves 7 days after their latest answer) |
| `tempo set-next <n>` | Continue numbering from an older list |
| `tempo inbox` | Print answers and follow-ups Claude hasn't seen yet. `--all-projects` shows every project's without marking them seen |
| `tempo wait` | Wait until you answer or follow up, then print it. `--timeout 1800` stops after that many seconds (`0` or none: wait forever); `--interval 2` sets how often it looks |

Commands for you:

| Command | What it does |
| --- | --- |
| `tempo answer <n> <letter>` | Answer a question (`--note "…"`). The option may also be named by its text, such as `tempo answer 61 yes`; a valid letter wins |
| `tempo reply <n> "…"` | Ask for more context (a follow-up) |
| `tempo tui` | The full-screen terminal view (see [`tempo tui`](#tempo-tui)) |
| `tempo sidebar [on\|off\|toggle]` | The tmux sidebar (see [A sidebar beside Claude](#a-sidebar-beside-claude)) |
| `tempo status` | Print `? 3` when the folder's project has 3 open questions, nothing when none (see [A badge in the tmux status bar](#a-badge-in-the-tmux-status-bar)) |
| `tempo config` | Show your settings. `tempo config page on\|off` (see [Starting the page with each session](#starting-the-page-with-each-session)); `tempo config install-hint on\|off` turns off the session's `/tempo:install` reminder |
| `tempo install` | Put `tempo` in your own shell (see [`tempo` in your own shell](#tempo-in-your-own-shell)). `--tmux` also turns the sidebar on |
| `tempo uninstall` | Remove the `~/.local/bin/tempo` link, and print any Tempo lines left in your tmux config for you to remove |
| `tempo serve` | Run the page. `--background` runs it in the background; `--port` picks another port |

`add`, `show` and `list` take `--json` for scripts. Most commands take `--project NAME` to act
on another project than the current folder's. `tempo hook <event>` and `tempo slash <name>`
are run by the hooks and slash commands; they are not for direct use.

## Slash commands

These let you read and answer questions inside a Claude Code session. Only you can run them:
Claude Code refuses when Claude tries.

| Command | What it does |
| --- | --- |
| `/tempo:list` | This project's open questions |
| `/tempo:show 57` | One question with its options and thread (`57b` works too) |
| `/tempo:answer 57a [note]` | Answer with option (a), with an optional note. `57 a`, `Q57a` and `Q57 a)` work too, and so does the start of the option's text, such as `61 yes` |
| `/tempo:dive 57 [follow-up]` | Ask for more; without text it asks "Tell me more.", and `57b` asks about option (b) |
| `/tempo:install` | Runs `tempo install --tmux`: puts `tempo` in your shell, turns the sidebar on and shows the tmux line to add yourself |

The answer or follow-up is saved before Claude reads anything, so it reaches Claude once,
right away. An unknown question or option saves nothing and shows Tempo's error. With
`disableSkillShellExecution` on in your Claude Code settings, these commands can't run. One
known limit: a line of typed text that is exactly `TEMPO_END_OF_TYPED_TEXT_9C4F2A7E` ends the
text early, and the lines after it run as shell commands.

## Hooks

Hooks are small commands that Claude Code runs at fixed moments of a session. Tempo uses three:

| Hook | When | What it does |
| --- | --- | --- |
| SessionStart | A session starts | Tells Claude how to use Tempo and lists the project's open questions. Starts the page if you turned that on. Until `tempo` is in your shell, asks once to run `/tempo:install` |
| UserPromptSubmit | You send a message | Adds the answers and follow-ups Claude hasn't seen yet |
| Stop | Claude finishes a reply | Sends Claude back to work, at most once per reply: to read a new answer, to record a question it asked you in chat, to answer a follow-up waiting more than five minutes, or to start a `tempo wait` |

Each answer is delivered once, and only to sessions of its own project: the folder Claude Code
started the session in. Long lists are cut to fit what Claude Code shows of a hook; `tempo
inbox` and `tempo list` print the rest. A Tempo problem never blocks your prompt or the
session: the error goes to the page log. After you update Tempo, restart open sessions (for
example with `claude --continue`); a session with older hooks says so once.

## The page server

The page runs on <http://127.0.0.1:2204/> by default (`TEMPO_PORT` or `--port` changes it). A
session that starts the page keeps a Tempo page already running on that port, unless it is an
older version or no longer answers; then it replaces it. `/health` returns the running
server's version and database as JSON.

### Starting the page with each session

By default, sessions don't start the page. To have each one start it, use the switch under
Sessions in the page's Settings, or:

```bash
tempo config page on    # each session starts the page
tempo config page off   # back to the default
tempo config            # show the current value
```

With it off, `tempo serve --background` still starts the page when you want it, and Claude
points you to `tempo show N` and `tempo answer N <letter>` instead of page links.

## Projects in the header

The page shows one project at a time. The header has a chip for each project with an open
question or something new, showing its open count, such as `tempo · 2`. A dot on a chip means
something new arrived there since you last viewed it. **All projects ▾** lists every project.

## Goals

Each question names its **goal**, the piece of work its answer moves forward, such as
"Release 0.4". Open questions are grouped by goal, oldest first, with "Other" last. Claude may
mark one question per goal **Start here** when its answer unlocks the others. **quick** marks
a question with a recommended option, at most three options and no rewording. Goals replace
the old priorities: `--priority` and `tempo priority` only print a note.

### What changed since you opened it

A dot after a question's number means it is new, or Claude reworded it or added context, since
you last opened it. While a question waits on Claude after a follow-up, it shows "Waiting on
Claude" instead. Opening the question clears the dot. This is kept in your browser.

## When a question changes while you answer

An answer is tied to the wording you saw. If Claude rewords the question meanwhile, the page
and `tempo tui` refuse the answer ("Q1 changed since you looked at it"), show the new wording
and keep your note, so a letter never lands on an option you didn't see. `tempo answer` and
`/tempo:answer` answer the current wording.

## Settings

The gear button on the page opens Settings. `s` in `tempo tui` opens its own Settings, with
the projects only.

| Section | What it holds |
| --- | --- |
| Appearance | Theme, font, text size and line width. Kept in your browser |
| Sessions | "Start the page with each session", the same as `tempo config page on\|off` |
| Projects | Each project's name, number of questions and folders, with Rename and "Merge into…" under Edit |

A new git folder starts as a project named after the folder. Tempo knows a folder by its path,
so a renamed or moved folder starts a new, empty project. To bring the old questions along,
merge the old project into the new one:

- **On the page:** press Edit on the old project, pick the other in "Merge into…", click
  "Merge…" and confirm. Renaming a project to another's name does the same.
- **In `tempo tui`:** press `s`, select the old project, press `m`, pick the other with `j`
  and `k`, press `Enter`, then `y` (`n` or `Esc` cancels).

The moved questions get new numbers after the other project's, and each gets a note with its
old number, such as "Moved from tempo Q2 when tempo joined Tempo." A project with no questions
leaves the list once its folders are gone; folders in the system's temporary folders, such as
`/tmp`, are never remembered.

## `tempo tui`

The full-screen terminal view; its panels and keys are in the
[README](../README.md#without-tmux-tempo-tui). `--project NAME` starts on another project,
`--sidebar` is the compact view for a narrow pane, and `--question N` shows only that question
and closes once it is answered.

Pasted text goes into the note or follow-up you are writing, line breaks included (shown as
`↵`). In a terminal with bracketed paste, which most have, a paste never acts as keys. Alt
(Option on a Mac) with a key is ignored. On a screen narrower than 60 characters or shorter
than 10 lines it shows one panel at a time. `NO_COLOR` turns colours off. To merge projects with
`s`, see [Settings](#settings); to open it in a tmux popup, see
[`tempo tui` in a tmux popup](#tempo-tui-in-a-tmux-popup).

## `tempo` in your own shell

`tempo` is on Claude Code's `PATH` only inside a session. `/tempo:install`, or this command,
makes `~/.local/bin/tempo`, a link to Tempo's marketplace copy, which stays put across updates:

```bash
~/.claude/plugins/marketplaces/tempo/bin/tempo install
```

Run from a clone, it links to the clone's `bin/tempo`. It never replaces anything already at
that path, and prints the line to add to your shell's config when `~/.local/bin` is not on
your `PATH`. `tempo uninstall` removes the link. An alias works too
(`alias tempo=~/.claude/plugins/marketplaces/tempo/bin/tempo`), but then use that full path in
the tmux lines below.

## tmux and the status line

tmux and Claude Code run these commands without your shell's settings, so they use the full
path `~/.local/bin/tempo`. After changing `~/.tmux.conf`, reload it with
`tmux source-file ~/.tmux.conf`.

### `tempo tui` in a tmux popup

To open `tempo tui` in a popup with `prefix` then `T` (tmux 3.2 or later), or with `Alt-t`
alone:

```tmux
bind T display-popup -E -w 90% -h 85% -d "#{pane_current_path}" "~/.local/bin/tempo tui"
bind -n M-t display-popup -E -w 90% -h 85% -d "#{pane_current_path}" "~/.local/bin/tempo tui"
```

### A sidebar beside Claude

The sidebar is a 40-column pane in every tmux window, listing the open questions of that
window's project by goal. A dot marks a new or changed question, `…` one waiting on Claude
and `»` the one to start with.

| Key | What it does |
| --- | --- |
| `j` `k` | Move between questions |
| `h` `l` | Show another project's questions |
| `Enter` | Open the question in a popup: a letter answers, `r` asks for more, `Esc` closes |
| `g` | Go to the project's Claude pane |
| `s`, `?` | Settings, and the keys |
| `q` | Close this sidebar |

In the popup, dragging over the question's text copies it to the clipboard (with `pbcopy`
on a Mac, else through tmux), and the wheel scrolls.

`tempo sidebar on|off|toggle` (toggle is the default) turns it on or off in every window.
`--side left|right` picks the side, which is remembered. To bring it back when tmux restarts,
add this line to the end of `~/.tmux.conf` yourself; Tempo never edits that file:

```tmux
run-shell -b "~/.local/bin/tempo sidebar --restore"
```

To show or hide it with `prefix` then `S`:

```tmux
bind S run-shell -c "#{pane_current_path}" "~/.local/bin/tempo sidebar"
```

### A badge in the tmux status bar

`tempo status` prints `? 3` when the current folder's project has three open questions, and
nothing when it has none. `--project NAME` counts one project, `--all` every project. It only
reads the database, so it is safe to run every few seconds. In the status bar:

```tmux
set -g status-interval 5
set -g status-right '#(cd "#{pane_current_path}" && ~/.local/bin/tempo status) %H:%M'
```

Or on every pane's border, each for its own folder:

```tmux
set -g pane-border-status top
set -g pane-border-format ' #(cd "#{pane_current_path}" && ~/.local/bin/tempo status) #{pane_current_command} '
```

### A badge in the Claude Code status line

Claude Code's status line runs a command you choose and passes it the session's details as
JSON. This script, saved as `~/.claude/statusline.sh` and made runnable with `chmod +x`,
shows the folder's name and the badge (it needs [jq](https://jqlang.org)):

```sh
#!/bin/sh
input=$(cat)
dir=$(printf '%s' "$input" | jq -r '.workspace.current_dir')
tempo=$(cd "$dir" && ~/.local/bin/tempo status 2>/dev/null)
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
| Database | `~/.claude/tempo/questions.db` (or `~/.claude/open-questions/questions.db` on older installs) | `TEMPO_DB` |
| Port | `2204` | `TEMPO_PORT` |
| Project | The project of the current git repository's folder (worktrees count as the same folder) | `TEMPO_PROJECT`, or `--project` on a command |
| Hooks stay quiet | Off | `TEMPO_IN_THREAD=1`: the hooks do nothing, for tools that run Claude sessions on your behalf |
| Colours in `tempo tui` | On | `NO_COLOR`, set to anything, turns them off |
| Google Chrome, for the page tests and `bin/media` | Found in the usual places | `CHROME`, the browser's path |

The old names `OQ_DB`, `OQ_PORT` and `OQ_PROJECT` still work; the `TEMPO_` ones win. Tempo
run from a git checkout that isn't on `main` uses its own database, `.tempo-dev/questions.db`
in that checkout, and its own port, unless `TEMPO_DB` and `TEMPO_PORT` are set. A database
changed by a newer Tempo is refused until you update.

The page log, `server.log`, sits next to the database. It gets one line per change made
through the page and per hook run, never the text of a question or answer, and rolls over to
`server.log.1` past about 1 MB.
