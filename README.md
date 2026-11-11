# Tempo

[![CI](https://github.com/andrebras/tempo/actions/workflows/ci.yml/badge.svg)](https://github.com/andrebras/tempo/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)
![No dependencies](https://img.shields.io/badge/dependencies-none-brightgreen.svg)

You set the tempo; Claude follows. Like a conductor, you decide, and Claude picks up your
decisions and keeps playing.

Tempo is a [Claude Code](https://code.claude.com) plugin. When Claude needs a decision from
you, it saves the question with all the context you need, instead of letting it scroll away
in chat. You answer in the terminal whenever it suits you, in a sidebar beside Claude in
tmux or in a full-screen view, and Claude picks the answer up by itself. A local web page
is there too if you want it; it is off unless you turn it on.

- 🚀 [Quick start](#quick-start)
- 🎬 [See it in action](#see-it-in-action)
- 📦 [Install](#install)
- 🔄 [Update and uninstall](#update-and-uninstall)
- 🎛️ [Usage](#usage)
  - 🗂️ [A sidebar in tmux](#a-sidebar-in-tmux): `tempo sidebar on`
  - 🖥️ [Without tmux: `tempo tui`](#without-tmux-tempo-tui), the full-screen view
  - ⌨️ [Commands](#commands) and [slash commands](#in-the-claude-code-session)
  - 🌐 [The page (optional)](#the-page-optional)
- ⚙️ [How it works](#how-it-works)
- 🔒 [Security](#security)
- 🤝 [Contributing](#contributing)
- 📜 [Changelog](CHANGELOG.md)
- ⚖️ [License](#license)

## Quick start

```bash
claude plugin marketplace add andrebras/tempo
claude plugin install tempo@tempo
```

The first command adds Tempo's marketplace, the list of plugins Claude Code installs from.
The second installs the plugin. Then start a new Claude Code session and type:

```text
/tempo:install
```

It puts the `tempo` command in your own terminal and turns the tmux sidebar on. Claude
tells you what it did, and shows you the one line to add to your tmux config yourself so the
sidebar comes back when tmux starts. New sessions remind you of it until it is done.

## See it in action

All three recordings show the same steps: in the tmux sidebar, in `tempo tui` and on the
page. You answer a question with a note, and Tempo moves on to the next one. There you ask
for more context; Claude rewords the question, and its thread shows what you asked and what
changed.

**In tmux, with the sidebar**

![A tmux session with two windows, one per project. In the first, a stand-in for Claude's pane says it asked two questions in Tempo, and the sidebar is turned on: a column on the right lists the project's open questions by goal. Enter opens Q1 in a popup over the panes; option (b) is picked with a typed note, the popup closes and Claude's pane shows it got the answer and goes on. Q2 opens, a follow-up is typed, and Claude's pane shows the question; Claude rewords Q2, the list shows the new title with a dot, and the popup shows the thread. Switching to the second window shows that project's sidebar.](docs/sidebar.gif)

**Without tmux, with `tempo tui`**

![The same steps in tempo tui, the full-screen terminal view: option (b) is picked with a typed note, the screen moves on to the next question, a follow-up is typed and the question shows "Waiting on Claude"; Claude rewords it and the thread shows both steps.](docs/tui.gif)

**On the page**

![Answering on the Tempo page in dark colours: option (b) is picked, a note is typed and the answer is sent, and the page moves on to the next question. There, a follow-up is typed and "Ask for more context" is pressed; Claude rewords the question and the thread shows both steps.](docs/demo.gif)

The recordings use made-up questions; `bin/media` makes them again (see
[CONTRIBUTING.md](CONTRIBUTING.md#screenshots-and-demo-gifs)).

## Install

```bash
claude plugin marketplace add andrebras/tempo
claude plugin install tempo@tempo
```

You need Python 3.9 or newer; on a Mac, Apple's Command Line Tools include one
(`xcode-select --install`). Installing from the marketplace also needs Git. Tempo has no other
dependencies.
Start a new Claude Code session afterwards: sessions that were already open don't load it.

To try it for one session without installing it, clone the repo and run
`claude --plugin-dir .` inside the clone.

## Update and uninstall

To update Tempo:

```bash
claude plugin marketplace update tempo
claude plugin update tempo@tempo
```

A Claude session keeps the plugin version it started with. After you update Tempo, restart
open sessions (for example with `claude --continue`) so they use the new hooks. A session
whose hooks are older than the installed Tempo says so once, so you know to restart it.

To remove it, run `tempo uninstall` first if you ran `/tempo:install`: it removes the
`~/.local/bin/tempo` link, and prints any of Tempo's lines left in your tmux config for you
to remove. Then:

```bash
claude plugin uninstall tempo@tempo
claude plugin marketplace remove tempo
```

Your questions stay in `~/.claude/tempo/`. Delete that folder if you want them gone too.

## Usage

There's nothing to run. When Claude needs a decision, it adds a question and tells you how
to answer it. You answer in the terminal: in the tmux sidebar, in `tempo tui`, with
`tempo answer`, or with a slash command in the session. The page is optional, and off
unless you turn it on.

Every question has a number per project that is never reused, so answering "12b" in chat
works too. Merging two projects renumbers the questions that move.

### Use `tempo` in your own shell

`tempo` is on Claude Code's `PATH` (the list of folders your shell looks in for commands)
only inside a session. `/tempo:install` (see [Quick start](#quick-start)) puts it in your
own shell. To do it by hand instead, run this once:

```bash
~/.claude/plugins/marketplaces/tempo/bin/tempo install          # just the command
~/.claude/plugins/marketplaces/tempo/bin/tempo install --tmux   # also turns the sidebar on and prints the tmux line
```

It makes `~/.local/bin/tempo`, a link to Tempo's copy that stays put across updates, and
never replaces anything already there. `tempo uninstall` undoes it. More, including an
alias instead of the link, in [`tempo` in your own shell](docs/reference.md#tempo-in-your-own-shell).

### A sidebar in tmux

`/tempo:install` turns it on for you. To do it by hand in [tmux](https://github.com/tmux/tmux),
run this once in any pane:

```bash
tempo sidebar on                  # a sidebar in every window, on the left
tempo sidebar on --side right     # or on the right
```

Every tmux window gets a narrow column, the full height, with the open questions of the
project its folder belongs to, grouped by goal. New windows and sessions get one too, and a
sidebar closes with the last other pane of its window. `tempo sidebar off` closes them all,
and a plain `tempo sidebar` toggles.

| Key | What it does |
| --- | --- |
| `j` `k` | Move between questions |
| `h` `l` | Show another project's questions (it only browses) |
| `Enter` | Open the question in a popup over your panes: a letter answers, `r` asks Claude for more, `h` or `Esc` closes it |
| `g` | Go to the project's Claude pane, in whichever tmux session it is |
| `s`, `?` | Settings, and the keys |
| `q` | Close this sidebar |

The list itself never answers, so a stray key can't. A column on the left marks
each question: a dot when a question is new or changed since you opened it, `…` while it waits on
Claude, and `»` for the question to start with.

tmux forgets its sidebars when it stops, after a reboot say. To bring them back, add this
line to the end of `~/.tmux.conf` (or `~/.config/tmux/tmux.conf`, if that is the one you
use):

```tmux
run-shell -b "~/.local/bin/tempo sidebar --restore"
```

Tempo never changes your tmux config: `/tempo:install` and `tempo install --tmux` print this
line and the file to add it to. More, and a key to show or hide the sidebar, in
[A sidebar beside Claude](docs/reference.md#a-sidebar-beside-claude).

### Without tmux: `tempo tui`

`tempo tui` shows the open questions full screen, with panels side by side as in lazygit,
and you use it only with the keyboard. It needs nothing installed: it is drawn with
curses, which comes with Python. Projects (top left) lists every project with open questions,
plus the current folder's, with how many are open; a dot marks the ones with questions waiting
for you. Questions (bottom left) lists the selected project's open questions, grouped by goal
as on the page: `»` marks the goal's start-here question, `…` one waiting on Claude, and
`quick` one that is quick to answer. Detail (right) shows the selected question: its context,
the lettered options with the recommended one marked, and its thread. The bottom line shows the
keys for the panel you are in.

[The second recording above](#see-it-in-action) shows it at work.

| Key | What it does |
| --- | --- |
| `j` `k`, `↓` `↑` | Move to the next or previous project or question; in Detail, scroll |
| `h` `l`, `←` `→`, `Tab` `Shift-Tab` | Go to the previous or next panel |
| `Enter` | Go from Projects to Questions, and from Questions to Detail |
| `a`, `b`, `c`… | Answer the selected question with that option. The bottom line opens for an optional note: `Enter` sends, with or without one, and `Esc` cancels |
| `A`, `B`, `C`… | The same. Use Shift for options `h`, `j`, `k`, `l`, `q`, `r` and `s`, since those letters also move, quit or open something |
| `r` | Ask Claude for more (a follow-up): type it on the bottom line, `Enter` sends, `Esc` cancels |
| `s` | Open Settings (see below); `Esc` goes back to the questions |
| `PgDn` `PgUp`, `Ctrl-d` `Ctrl-u` | Scroll the detail by a page |
| `?` | Show the keys |
| `q`, `Esc` | Quit |

Answers and follow-ups are saved exactly as the page and `tempo answer` save them, so Claude
gets them the same way. The bottom line then says what was saved, or why it was refused (for
example, when the question was answered on the page meanwhile). The screen follows changes by
itself within a second or two: a question Claude adds or rewords, or an answer from the page.
On a screen narrower than 60 characters or shorter than 10 lines it shows one panel at a time, and `h` and `l` go to
the others. Set `NO_COLOR` to turn colors off. It never starts the page. For answered and
archived questions, search, renaming a project and the other settings, use
[the page](#the-page-optional).

Settings (`s`) lists every project with its number of questions and the folders it covers.
To merge one project into another, for example after a folder was renamed or moved and its
new questions went to a new project, select the old project, press `m`, pick the project to
merge it into with `j` and `k`, press `Enter`, then `y` to confirm (`n` or `Esc` cancels).
Its questions and folders move over, as when you rename a project to another's name on the
page: if both have questions, the moved ones get new numbers after the other's, and the bottom
line says which, such as "Moved 5 questions from tempo to Tempo: now Q28–Q32."

Text you paste goes into the note or follow-up you are writing, line breaks included (shown
as `↵`). In a terminal with bracketed paste, which marks pasted text and which most have, a
paste never acts as keys, so a newline in it doesn't send and a letter doesn't pick an option.
Pasting anywhere else only shows a hint on the bottom line. Alt (Option on a Mac) with a key, such as
Option+Left, is ignored too, rather than read as `Esc` and then a letter.

To open it in a tmux popup, see [tmux and the status line](docs/reference.md#tmux-and-the-status-line).

### Commands

These commands are for you, not for Claude, and save exactly what the sidebar and the page
save: Claude gets it through the same hooks and `tempo wait`.

```bash
tempo list                                # the project's open questions
tempo show 12                             # one question, with its options and thread
tempo answer 12 b                         # answer Q12 with option (b)
tempo answer 61 yes                       # or name the option by its text
tempo answer 12 b --note "Keep it small"  # with a note
tempo reply 12 "What would (c) cost?"     # ask for more context (a follow-up)
```

They act on the project of the folder you run them in; add `--project NAME` for another one
(`tempo list --all-projects` shows every project's questions).

### In the Claude Code session

The same, without leaving the session: type one of these slash commands in Claude Code. They
are for you, not for Claude (Claude Code does not let Claude invoke them). They record your
answer or follow-up before Claude reads anything, so it comes from you and reaches Claude
once, right away.

```text
/tempo:list                          # the project's open questions
/tempo:show 57                       # one question, with its options and thread
/tempo:answer 57a                    # answer Q57 with option (a); also 57 a, Q57a, Q57 a)
/tempo:answer 57a keep the old flag  # with a note
/tempo:answer 61 yes                 # name the option by its text, in any case
/tempo:dive 57                       # ask for more ("Tell me more.")
/tempo:dive 57 why not b?            # ask something specific
```

After `/tempo:answer` Claude acts on the answer. After `/tempo:dive` Claude digs in and
answers in the question itself, so the sidebar, `tempo tui` and the page show the new
explanation. An unknown question or option records nothing and shows Tempo's error instead.

### The page (optional)

Tempo also has a local web page, at <http://127.0.0.1:2204/>, with the same questions and a
few things the terminal doesn't do: answered and archived questions, search, and
appearance settings. It is off by default. Turn it on with:

```bash
tempo config page on      # each Claude Code session starts the page, and Claude links to it
tempo serve --background  # or start it just this once
```

`tempo config page off` turns it off again, and `tempo config` shows the current value.
With the page on, Claude links each question on it. If a Tempo page is already running
there, it is kept, unless it comes from an older version of Tempo or no longer works; then
Tempo replaces it (details in [The page server](docs/reference.md#the-page-server)). On
the page you can:

- **Answer**: pick an option, add a note if you like, and send it. The page moves on to the
  next open question. If Claude rewords the question while you answer, the page refuses the
  answer, shows the new wording and keeps your note, so a letter never lands on an option
  you didn't see.
- **Ask for more context**: say what's unclear. Claude adds an explanation or rewords the
  question, and the question's thread shows what changed.
- **Reopen** an answered question to change your answer.
- **Find a question**: the search box filters the list, and the **Open**, **Answered** and
  **Archived** tabs switch between open questions, answered ones and older ones.
- **Switch projects** with the chips in the header: one per project with something for you,
  showing its open count. A dot on a chip means something new arrived there since you last
  looked. **All projects ▾** lists every project.
- **Spot what changed**: a dot after a question's number in the list means the question is new,
  or Claude reworded it or added context, since you last opened it.
- **Adjust Settings** with the gear button: theme, font, text size and line width, whether
  each session starts the page, and project names. Settings also lists the folders each
  project covers, and merges one project into another. A renamed or moved folder starts its
  own project: press Edit on the old project and use "Merge into…" to join it into the new one.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/settings-dark.png">
  <source media="(prefers-color-scheme: light)" srcset="docs/settings-light.png">
  <img alt="Settings on the Tempo page: the Appearance card with theme, font, text size and width, and the Sessions card with the On and Off switch for starting the page with each session." src="docs/settings-light.png" width="1280">
</picture>

## How it works

```
 Claude ──tempo add / context / reword──▶  SQLite  ◀──answer / follow-up── sidebar / tui / page / tempo answer
   ▲                                          │
   ├──── hook on your next message ───────────┤
   ├──── tempo wait (running in background) ──┤
   └──── hook when a reply ends ──────────────┘
```

Claude writes questions with the bundled `tempo` command, following the bundled skill. Tempo
uses three hooks: small commands that Claude Code runs at fixed moments of a session. At the
start of every session, one tells Claude that every decision goes through Tempo and lists the
project's open questions.

Answers from the sidebar, `tempo tui`, the page, `tempo answer` or `tempo reply` reach
Claude in one of three ways:

- a hook adds them before Claude reads your next message;
- a `tempo wait` that Claude keeps running in the background wakes it as soon as you answer;
- when Claude finishes a reply, a hook hands over any answers it hasn't seen yet, so Claude
  carries on instead of stopping.

Each answer is delivered once, and only to sessions of its own project. The slash commands
are a fourth way: they save the answer already delivered, and Claude reads it straight from
the command's output.

The hook that runs when a reply ends also keeps the loop going. If the reply asks you something
(lettered options, a yes-or-no question or a request for permission) without a Tempo question,
it sends Claude back to add one. If the project has open questions and no `tempo wait` is
running, it sends Claude back to start one, so your answers from the sidebar reach it without you
writing in chat. More in [Hooks](docs/reference.md#hooks).

Commands, settings and environment variables are listed in [docs/reference.md](docs/reference.md).

## Security

The page only listens on `127.0.0.1`. It refuses requests addressed to any other host name, and
only accepts answers sent as JSON from the page itself, so other websites open in your browser
can't answer for you. There is no login: anyone using your account on this machine can read
and answer your questions.

The database and the page's log are private to your account: only you can read or write them
(file permissions 0600), and Tempo makes their folder readable by you only (0700). So other
accounts on a shared machine can't read your questions. A database from an older Tempo is
made private the next time Tempo opens it; a folder that already existed is left as it is.

Another tool that runs Claude sessions for you can set `TEMPO_IN_THREAD=1` in
those sessions. Tempo's hooks then do nothing and print nothing there, so Tempo stays out of
sessions you don't drive yourself.

Please report security issues privately through
[GitHub security advisories](https://github.com/andrebras/tempo/security/advisories/new).

## Contributing

Issues and pull requests are welcome. [CONTRIBUTING.md](CONTRIBUTING.md) explains how to run the
checks and the tests.

## License

[MIT](LICENSE)
