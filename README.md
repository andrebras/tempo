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

It puts the `tempo` command in your own terminal (see
[`tempo` in your own shell](docs/reference.md#tempo-in-your-own-shell)) and turns the tmux
sidebar on. For tmux, it
shows one line to add to your tmux config yourself, so the sidebar comes back when tmux starts:

```tmux
run-shell -b "~/.local/bin/tempo sidebar --restore"
```

## See it in action

All three recordings show the same steps: you answer a question with a note, then ask for
more context on the next one, and Claude rewords it.

**In tmux, with the sidebar**

![A tmux session with two windows, one per project. In the first, a stand-in for Claude's pane says it asked two questions in Tempo, and the sidebar is turned on: a column on the right lists the project's open questions by goal. Enter opens Q1 in a popup over the panes; option (b) is picked with a typed note, the popup closes and Claude's pane shows it got the answer and goes on. Q2 opens, a follow-up is typed, and Claude's pane shows the question; Claude rewords Q2, the list shows the new title with a dot, and the popup shows the thread. Switching to the second window shows that project's sidebar.](docs/sidebar.gif)

**Without tmux, with `tempo tui`**

![The same steps in tempo tui, the full-screen terminal view: option (b) is picked with a typed note, the screen moves on to the next question, a follow-up is typed and the question shows "Waiting on Claude"; Claude rewords it and the thread shows both steps.](docs/tui.gif)

**On the page**

![Answering on the Tempo page in dark colours: option (b) is picked, a note is typed and the answer is sent, and the page moves on to the next question. There, a follow-up is typed and "Ask for more context" is pressed; Claude rewords the question and the thread shows both steps.](docs/demo.gif)

The recordings use made-up questions; `bin/media` makes them again (see
[CONTRIBUTING.md](CONTRIBUTING.md#screenshots-and-demo-gifs)).

## Install

The commands are in [Quick start](#quick-start). You need Python 3.9 or newer; on a Mac,
Apple's Command Line Tools include one (`xcode-select --install`). Installing from the
marketplace also needs Git. Tempo has no other dependencies. Start a new Claude Code session
afterwards: sessions that were already open don't load it.

To try it for one session without installing it, clone the repo and run
`claude --plugin-dir .` inside the clone.

## Update and uninstall

To update Tempo:

```bash
claude plugin marketplace update tempo
claude plugin update tempo@tempo
```

Then restart open sessions (for example with `claude --continue`): a session keeps the
plugin version it started with, and says so once when it is out of date.

To remove it, run `tempo uninstall` first if you ran `/tempo:install`: it removes the
`~/.local/bin/tempo` link, and prints any of Tempo's lines left in your tmux config for you
to remove. Then:

```bash
claude plugin uninstall tempo@tempo
claude plugin marketplace remove tempo
```

Your questions stay in `~/.claude/tempo/` (older installs keep them in
`~/.claude/open-questions/`). Delete that folder if you want them gone too.

## Usage

Once `/tempo:install` has run, there's nothing else to start. When Claude needs a decision,
it adds a question and tells you how to answer it. You answer in the terminal: in the tmux
sidebar, in `tempo tui`, with `tempo answer`, or with a slash command in the session. The
page is optional, and off unless you turn it on.

Every question has a number per project that is never reused, so answering "12b" in chat
works too. Merging two projects renumbers the questions that move.

### A sidebar in tmux

`/tempo:install` turns it on: every [tmux](https://github.com/tmux/tmux) window gets a
narrow column with the open questions of its project, grouped by goal. `tempo sidebar on`,
`off` or a plain `tempo sidebar` (toggle) does it by hand; `--side right` moves it.

| Key | What it does |
| --- | --- |
| `j` `k` | Move between questions |
| `h` `l` | Show another project's questions (it only browses) |
| `Enter` | Open the question in a popup: a letter answers (the list itself never does), `r` asks Claude for more, `h` or `Esc` closes it |
| `g` | Go to the project's Claude pane, in whichever tmux session it is |
| `s`, `?` | Settings, and the keys |
| `q` | Close this sidebar |

To bring it back when tmux restarts, add the line from [Quick start](#quick-start) to the end
of `~/.tmux.conf` yourself; Tempo never changes it. A key to show or hide the sidebar is in
[A sidebar beside Claude](docs/reference.md#a-sidebar-beside-claude).

### Without tmux: `tempo tui`

`tempo tui` shows the open questions full screen, with panels side by side as in lazygit,
and you use it only with the keyboard. Projects (top left) lists the projects with open
questions. Questions (bottom left) lists the selected project's questions by goal. Detail
(right) shows the selected question: its context, its options and its thread.

| Key | What it does |
| --- | --- |
| `j` `k`, `↓` `↑` | Move to the next or previous project or question; in Detail, scroll |
| `h` `l`, `←` `→`, `Tab` `Shift-Tab` | Go to the previous or next panel |
| `Enter` | Go from Projects to Questions, and from Questions to Detail |
| `a`, `b`, `c`… | Answer with that option, with an optional note on the bottom line: `Enter` sends, `Esc` cancels. Use Shift (`A`, `B`…) for options `h`, `j`, `k`, `l`, `q`, `r` and `s` |
| `r` | Ask Claude for more (a follow-up): type it on the bottom line, `Enter` sends, `Esc` cancels |
| `←` `→`, `Home` `End` (or `Ctrl-a` `Ctrl-e`) | While typing a note or follow-up, move the cursor; `Backspace` and `Delete` remove the character before or under it |
| `s` | Open Settings, to see and merge projects; `Esc` goes back |
| `PgDn` `PgUp`, `Ctrl-d` `Ctrl-u` | Scroll the detail by a page |
| `?` | Show the keys |
| `q`, `Esc` | Quit |

Answers are saved exactly as the page and `tempo answer` save them, and the screen follows
changes by itself. For answered and archived questions and search, use
[the page](#the-page-optional). Paste handling, merging projects with `s`, and a tmux popup
for `tempo tui` are in [the reference](docs/reference.md#tempo-tui).

### Commands

These commands are for you, not for Claude, and save exactly what the sidebar and the page
save.

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

The same, without leaving the session. Only you can run these slash commands, and your
answer reaches Claude once, right away.

```text
/tempo:list                          # the project's open questions
/tempo:show 57                       # one question, with its options and thread
/tempo:answer 57a                    # answer Q57 with option (a); also 57 a, Q57a, Q57 a)
/tempo:answer 57a keep the old flag  # with a note
/tempo:dive 57                       # ask for more ("Tell me more.")
/tempo:dive 57 why not b?            # ask something specific
```

After `/tempo:answer` Claude acts on the answer. After `/tempo:dive` Claude answers in the
question itself, so the sidebar, `tempo tui` and the page show the new explanation.

### The page (optional)

Tempo also has a local web page, at <http://127.0.0.1:2204/>, with the same questions and a
few things the terminal doesn't do: answered and archived questions, search, and
appearance settings. It is off by default. Turn it on with:

```bash
tempo config page on      # each Claude Code session starts the page, and Claude links to it
tempo serve --background  # or start it just this once
```

`tempo config page off` turns it off again. On the page you pick an option, add a note and
send it; ask for more context; reopen an answered question; search, and switch between open,
answered and archived questions; switch projects with the chips in the header; and change
the theme, font and project names in Settings, where you can also merge two projects. More in
[the reference](docs/reference.md#settings).

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
uses three hooks, small commands that Claude Code runs at fixed moments of a session.

Your answers reach Claude in one of three ways: a hook adds them before Claude reads your
next message; a `tempo wait` that Claude keeps running in the background wakes it as soon as
you answer; or, when Claude finishes a reply, a hook hands them over so Claude carries on.

Each answer is delivered once, and only to sessions of its own project. The slash commands
save the answer already delivered, and Claude reads it straight from their output. The hook
at the end of a reply also sends Claude back when it asked you something in chat without
recording it, or when no `tempo wait` is running. More in [Hooks](docs/reference.md#hooks);
commands, settings and environment variables are in [docs/reference.md](docs/reference.md).

## Security

The page only listens on `127.0.0.1`. It refuses requests addressed to any other host name, and
only accepts answers sent as JSON from the page itself, so other websites open in your browser
can't answer for you. There is no login: anyone using your account on this machine can read
and answer your questions.

The database and the page's log are private to your account: only you can read or write them
(file permissions 0600), and Tempo makes their folder readable by you only (0700). So other
accounts on a shared machine can't read your questions. A database from an older Tempo is
made private the next time Tempo opens it; a folder that already existed is left as it is.

Another tool that runs Claude sessions for you can set `TEMPO_IN_THREAD=1` in them, so
Tempo's hooks stay quiet in sessions you don't drive yourself.

Please report security issues privately through
[GitHub security advisories](https://github.com/andrebras/tempo/security/advisories/new).

## Contributing

Issues and pull requests are welcome. [CONTRIBUTING.md](CONTRIBUTING.md) explains how to run the
checks and the tests.

## License

[MIT](LICENSE)
