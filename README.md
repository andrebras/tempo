# Open questions

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)
![No dependencies](https://img.shields.io/badge/dependencies-none-brightgreen.svg)

A [Claude Code](https://code.claude.com) plugin that keeps the questions Claude asks you in one
place, with the context you need to answer them, and brings your answers back to Claude by
themselves.

Questions asked in chat scroll away, lose their context and are hard to answer later. With this
plugin, Claude saves every decision it needs from you as a self-contained question. You answer
on a local web page whenever it suits you, and Claude picks the answer up without you having
to go back to the chat.

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Usage](#usage)
- [How it works](#how-it-works)
- [Configuration](#configuration)
- [Security](#security)
- [Development](#development)
- [Contributing](#contributing)
- [License](#license)

## Features

- **Self-contained questions.** Each question has a title, the full context, options written
  out and lettered (a), (b)… and, if Claude has one, a recommendation. The `oq` command refuses
  a question without context or with fewer than two options.
- **Stable numbers.** Questions are numbered per project (Q1, Q2…) and numbers are never
  reused, so a short answer like "12b" always means one thing.
- **A thread per question.** Context Claude added, rewordings (old versions are kept), your
  follow-ups and your answer stay together, in order.
- **Answer in the browser.** Pick an option, write your own answer, or press "Ask for more
  context". The page works at phone width too.
- **Answers reach Claude by themselves**, on your next message or straight away if Claude is
  waiting in the background.
- **No dependencies.** It only uses Python's standard library, with SQLite for storage.

## Requirements

- [Claude Code](https://code.claude.com)
- Python 3.9 or newer (macOS ships with one as `/usr/bin/python3`)
- Git 2.31 or newer

## Installation

```bash
claude plugin marketplace add andrebras/open-questions
claude plugin install open-questions@open-questions
```

To try it from a local checkout for one session without installing it:

```bash
git clone https://github.com/andrebras/open-questions.git
claude --plugin-dir ./open-questions
```

## Usage

You don't need to run anything. When a Claude Code session starts, the plugin starts the page
at <http://127.0.0.1:2204/>. When Claude needs a decision, it adds a question and gives you its
link in chat.

On the page you can:

- **Answer**: pick an option, add a note if you like, and press "Send answer". You can also
  answer in your own words without picking an option.
- **Ask for more context**: write what's unclear and press "Ask for more context". Claude adds
  an explanation or rewords the question, sending a subagent to research it if needed. The
  thread shows what changed.
- **Change an answer** later, as long as the question isn't archived.

Answering in chat ("12b") still works as before.

### Commands

Claude uses these through the bundled skill. You can use them too, because `oq` is on Claude
Code's `PATH` while the plugin is enabled.

| Command | What it does |
| --- | --- |
| `oq add --title … --context … --option … --option …` | Add a question (`--recommended b`, `--urgent`) |
| `oq list`, `oq show <n>` | List questions, or show one with its thread |
| `oq context <n> "…"` | Add context to a question |
| `oq reword <n> --note "…"` | Save a new version of a question |
| `oq urgent <n>` | Mark a question urgent (`--off` to undo) |
| `oq archive` | Archive the project's answered questions |
| `oq set-next <n>` | Continue numbering from an older list |
| `oq inbox` | Print answers and follow-ups Claude hasn't seen yet |
| `oq wait --timeout 1800` | Wait until you answer or follow up, then print it |
| `oq serve [--background]` | Run the page |

Run `oq <command> --help` for every option.

## How it works

```
 Claude ──oq add / context / reword──▶  SQLite  ◀──answer / follow-up── Page (127.0.0.1:2204)
   ▲                                       │
   └──── hook on your next message ────────┤
   └──── oq wait (running in background) ──┘
```

- **Storage**: one SQLite file holds the questions, every version of their wording, and a
  thread of entries for each.
- **Skill**: tells Claude how to write a self-contained question and how to handle answers
  and follow-ups.
- **Hooks**: scripts Claude Code runs at set moments. `SessionStart` starts the page if it
  isn't already running. `UserPromptSubmit` adds anything new from the page to Claude's
  context before it reads your message.
- **`oq wait`**: Claude runs it in the background while it works. It exits as soon as you send
  something, which wakes Claude up.

Each answer or follow-up is delivered to Claude once. Questions belong to a project, so a
session only receives answers for its own project.

## Configuration

| Setting | Default | Change it with |
| --- | --- | --- |
| Database | `~/.claude/open-questions/questions.db` | `OQ_DB` |
| Port | `2204` | `OQ_PORT` |
| Project | The folder name of the current git repository (worktrees count as the same project) | `OQ_PROJECT` or `--project` |
| Page log | `~/.claude/open-questions/server.log` | Lives next to the database |

The database sits outside the plugin folder, so it survives plugin updates and reinstalls.

## Security

- The page only listens on `127.0.0.1`, so other machines can't reach it.
- Any website open in your browser can still send requests to a local port. The server
  refuses requests that aren't addressed to `127.0.0.1` or `localhost`, which blocks DNS
  rebinding (a trick that makes another site's address point at your machine). It only accepts
  writes sent as JSON from the page itself.
- There is no login: anyone with access to your user account on this machine can read and
  answer your questions.

Please report security issues privately through
[GitHub security advisories](https://github.com/andrebras/open-questions/security/advisories/new),
not in public issues.

## Development

```bash
bin/ci                                # every check below, then all tests
OQ_DB=/tmp/questions.db bin/oq serve  # run the page on a scratch database
```

`bin/ci` installs the pinned tools from `requirements-dev.txt` into `.venv` on its first run.
It needs [ShellCheck](https://www.shellcheck.net) (`brew install shellcheck`).

| Check | Tool |
| --- | --- |
| Lint: errors, bugs, import order, simpler forms, security rules | [Ruff](https://docs.astral.sh/ruff/) |
| Formatting | `ruff format` |
| Security scan | [Bandit](https://bandit.readthedocs.io) |
| Shell scripts | [ShellCheck](https://www.shellcheck.net) |
| Tests | `unittest`, from the standard library |

The tools are configured in `pyproject.toml`. When a rule has to be silenced on one line, the
line says why.

Layout:

```
.claude-plugin/   plugin and marketplace manifests
bin/              oq (the command) and ci
hooks/            hook configuration
open_questions/   store (SQLite), cli (oq), server and page
skills/           the skill that teaches Claude the workflow
tests/
```

## Contributing

Issues and pull requests are welcome. Please run `bin/ci` before opening a pull request, and
add a test for any change in behaviour.

## License

[MIT](LICENSE)
