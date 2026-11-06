# Tempo

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)
![No dependencies](https://img.shields.io/badge/dependencies-none-brightgreen.svg)

You set the tempo; Claude follows. Like a conductor, you decide, and Claude picks up your
decisions and keeps playing.

A [Claude Code](https://code.claude.com) plugin that keeps the questions Claude asks you in one
place, with the context you need to answer them, and brings your answers back to Claude by
themselves.

Questions asked in chat scroll away, lose their context and are hard to answer later. With this
plugin, Claude saves every decision it needs from you as a self-contained question. You answer
on a local web page whenever it suits you, and Claude picks the answer up without you having
to go back to the chat.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/screenshot-dark.png">
  <source media="(prefers-color-scheme: light)" srcset="docs/screenshot-light.png">
  <img alt="The Tempo page: a list of open questions on the left, and on the right the question &quot;Where should the new Export orders button go?&quot; with its context and three lettered options, the recommended one picked." src="docs/screenshot-light.png" width="1280">
</picture>

## Contents

- [See it in action](#see-it-in-action)
- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Usage](#usage)
- [How it works](#how-it-works)
- [Configuration](#configuration)
- [Upgrading from open-questions](#upgrading-from-open-questions)
- [Security](#security)
- [Development](#development)
- [Contributing](#contributing)
- [License](#license)

## See it in action

![Answering on the Tempo page: an option is picked and sent, and the page moves on to the next question. There, a note is typed and "Ask for more context" is pressed; Claude rewords the question and the thread shows both steps.](docs/demo.gif)

You pick an option and send it, and the page moves on to the next question. On that one, you
ask for more context; Claude rewords the question, and the thread shows what you asked and what
changed.

## Features

- **Self-contained questions.** Each question has a title, the full context, options written
  out and lettered (a), (b)… and, if Claude has one, a recommendation. The `tempo` command refuses
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
- Git

## Installation

```bash
claude plugin marketplace add andrebras/tempo
claude plugin install tempo@tempo
```

To try it from a local checkout for one session without installing it:

```bash
git clone https://github.com/andrebras/tempo.git
claude --plugin-dir ./tempo
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
- **Change settings** with the gear button: the page's look, project names, and which project
  each folder belongs to. See [Settings](#settings).

Answering in chat ("12b") still works as before.

### Commands

Claude uses these through the bundled skill. You can use them too, because `tempo` is on Claude
Code's `PATH` while the plugin is enabled.

| Command | What it does |
| --- | --- |
| `tempo add --title … --context … --option … --option …` | Add a question (`--recommended b`, `--priority now`) |
| `tempo list`, `tempo show <n>` | List questions, or show one with its thread |
| `tempo context <n> "…"` | Add context to a question |
| `tempo reword <n> --note "…"` | Save a new version of a question |
| `tempo priority <n> now\|soon\|later` | Change how soon an answer is needed |
| `tempo archive` | Archive the project's answered questions |
| `tempo set-next <n>` | Continue numbering from an older list |
| `tempo inbox` | Print answers and follow-ups Claude hasn't seen yet |
| `tempo wait --timeout 1800` | Wait until you answer or follow up, then print it |
| `tempo serve [--background]` | Run the page |

Run `tempo <command> --help` for every option.

## How it works

```
 Claude ──tempo add / context / reword──▶  SQLite  ◀──answer / follow-up── Page (127.0.0.1:2204)
   ▲                                          │
   └──── hook on your next message ───────────┤
   └──── tempo wait (running in background) ──┘
```

- **Storage**: one SQLite file holds the questions, every version of their wording, and a
  thread of entries for each.
- **Skill**: tells Claude how to write a self-contained question and how to handle answers
  and follow-ups.
- **Hooks**: scripts Claude Code runs at set moments. `SessionStart` starts the page if it
  isn't already running. `UserPromptSubmit` adds anything new from the page to Claude's
  context before it reads your message.
- **`tempo wait`**: Claude runs it in the background while it works. It exits as soon as you send
  something, which wakes Claude up.

Each answer or follow-up is delivered to Claude once. Questions belong to a project, so a
session only receives answers for its own project.

## Configuration

| Setting | Default | Change it with |
| --- | --- | --- |
| Database | `~/.claude/tempo/questions.db` | `TEMPO_DB` |
| Port | `2204` | `TEMPO_PORT` |
| Project | The project chosen for the current git repository's folder in Settings, else the folder name (worktrees count as the same folder) | `TEMPO_PROJECT` or `--project`, which win over Settings |
| Page log | `~/.claude/tempo/server.log` | Lives next to the database |

The database sits outside the plugin folder, so it survives plugin updates and reinstalls.

### Settings

The gear button at the top right of the page opens Settings:

- **Appearance**: theme (System, Light or Dark), font (System sans, Serif or Monospace) and
  text size. These are kept in your browser, not in the database.
- **Projects**: give a project a readable name, for example `onzehq` → "ONZE HQ". Its
  questions, their numbers and its folders move to the new name. A project can't take the
  name of another project that already has questions, because both would have a Q1.
- **Folders**: every git folder a Claude session ran in, and the project its questions go
  to. Put several folders in one project to share its questions and numbering.

Projects and folders are saved in the database, so every session, the hooks and the `tempo`
command use them.

## Upgrading from open-questions

Tempo used to be called open-questions. Nothing breaks when you upgrade:

- `oq`, the old command, still works as an alias for `tempo`.
- The old `OQ_DB`, `OQ_PORT` and `OQ_PROJECT` variables still work. If both are set, the
  `TEMPO_` one wins.
- If `~/.claude/tempo/questions.db` doesn't exist but `~/.claude/open-questions/questions.db`
  does, Tempo keeps using the old file where it is, and writes the page log next to it. To
  move to the new folder, stop the page and move the folder:
  `mv ~/.claude/open-questions ~/.claude/tempo`.
- Reinstall the plugin under its new name: run
  `claude plugin uninstall open-questions@open-questions`, then the
  [installation](#installation) commands above.

## Security

- The page only listens on `127.0.0.1`, so other machines can't reach it.
- Any website open in your browser can still send requests to a local port. The server
  refuses requests that aren't addressed to `127.0.0.1` or `localhost`, which blocks DNS
  rebinding (a trick that makes another site's address point at your machine). It only accepts
  writes sent as JSON from the page itself.
- There is no login: anyone with access to your user account on this machine can read and
  answer your questions.

Please report security issues privately through
[GitHub security advisories](https://github.com/andrebras/tempo/security/advisories/new),
not in public issues.

## Development

```bash
bin/ci                                      # every check below, then all tests
TEMPO_DB=/tmp/questions.db bin/tempo serve  # run the page on a scratch database
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
bin/              tempo (the command), oq (its old name) and ci
hooks/            hook configuration
tempo/            store (SQLite), cli (tempo), server and page
skills/           the skill that teaches Claude the workflow
tests/
```

## Contributing

Issues and pull requests are welcome. Please run `bin/ci` before opening a pull request, and
add a test for any change in behaviour.

## License

[MIT](LICENSE)
