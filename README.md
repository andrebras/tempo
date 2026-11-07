# Tempo

[![CI](https://github.com/andrebras/tempo/actions/workflows/ci.yml/badge.svg)](https://github.com/andrebras/tempo/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)
![No dependencies](https://img.shields.io/badge/dependencies-none-brightgreen.svg)

You set the tempo; Claude follows. Like a conductor, you decide, and Claude picks up your
decisions and keeps playing.

Tempo is a [Claude Code](https://code.claude.com) plugin. When Claude needs a decision from
you, it saves the question with all the context you need, instead of letting it scroll away
in chat. You answer on a local page whenever it suits you, and Claude picks the answer up by
itself.

- 🎬 [See it in action](#see-it-in-action)
- 📦 [Install](#install)
- 🎛️ [Usage](#usage)
- ⚙️ [How it works](#how-it-works)
- 🔒 [Security](#security)

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/screenshot-dark.png">
  <source media="(prefers-color-scheme: light)" srcset="docs/screenshot-light.png">
  <img alt="The Tempo page: project chips across the top, one with a dot for something new; on the left the open questions grouped by goal, one with a dot because Claude added to it and one waiting on Claude; on the right the question &quot;Where should the new Export orders button go?&quot; with its context and three lettered options, the recommended one picked." src="docs/screenshot-light.png" width="1280">
</picture>

## See it in action

![Answering on the Tempo page: an option is picked and sent, and the page moves on to the next question. There, a note is typed and "Ask for more context" is pressed; Claude rewords the question and the thread shows both steps.](docs/demo.gif)

You pick an option and send it, and the page moves on to the next question. On that one, you
ask for more context; Claude rewords the question, and the thread shows what you asked and what
changed. The pictures use made-up questions; `bin/media` makes them again (see
[CONTRIBUTING.md](CONTRIBUTING.md#screenshots-and-demo-gif)).

## Install

```bash
claude plugin marketplace add andrebras/tempo
claude plugin install tempo@tempo
```

You need Python 3.9 or newer (macOS ships with one) and Git. Tempo has no other dependencies.

To try it for one session without installing it, clone the repo and run
`claude --plugin-dir ./tempo`.

## Usage

There's nothing to run. When a Claude Code session starts, Tempo starts its page at
<http://127.0.0.1:2204/>. If a Tempo page is already running there, it is kept, unless it comes
from an older version of Tempo or no longer works; then Tempo replaces it (details in
[The page server](docs/reference.md#the-page-server)). When Claude needs a
decision, it adds a question and links to it in chat. On the page you can:

- **Answer**: pick an option, add a note if you like, and send it. The page moves on to the
  next open question.
- **Ask for more context**: say what's unclear. Claude adds an explanation or rewords the
  question, and the question's thread shows what changed.
- **Reopen** an answered question to change your answer.
- **Switch projects** with the chips in the header: one per project with something for you,
  showing its open count. A dot on a chip means something new arrived there since you last
  looked. **All projects ▾** lists every project.
- **Spot what changed**: a dot after a question's number in the list means Claude reworded
  it or added context since you last opened it.
- **Adjust Settings** with the gear button: theme, font, text size and line width, and project
  names. Settings also lists the folders each project covers. When a folder looks renamed or
  moved, a banner asks whether to link it to its old project.

Every question has a number per project that is never reused, so answering "12b" in chat
works too.

## How it works

```
 Claude ──tempo add / context / reword──▶  SQLite  ◀──answer / follow-up── Page (127.0.0.1:2204)
   ▲                                          │
   ├──── hook on your next message ───────────┤
   ├──── tempo wait (running in background) ──┤
   └──── hook when a reply ends ──────────────┘
```

Claude writes questions with the bundled `tempo` command, following the bundled skill. Tempo
uses three hooks: small commands that Claude Code runs at fixed moments of a session. At the
start of every session, one tells Claude that every decision goes through Tempo and lists the
project's open questions.

Your answers reach Claude in one of three ways:

- a hook adds them before Claude reads your next message;
- a `tempo wait` that Claude keeps running in the background wakes it as soon as you answer;
- when Claude finishes a reply, a hook hands over any answers it hasn't seen yet, so Claude
  carries on instead of stopping.

Each answer is delivered once, and only to sessions of its own project.

The hook that runs when a reply ends also keeps the loop going. If the reply asks you something
(lettered options, a yes-or-no question or a request for permission) without a Tempo question,
it sends Claude back to add one. If the project has open questions and no `tempo wait` is
running, it sends Claude back to start one, so your answers from the page reach it without you
writing in chat. More in [Hooks](docs/reference.md#hooks).

A Claude session keeps the plugin version it started with. After you update Tempo, restart
open sessions (for example with `claude --continue`) so they use the new hooks.

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

Another tool that runs Claude sessions for you, such as Harp, can set `TEMPO_IN_THREAD=1` in
those sessions. Tempo's hooks then do nothing and print nothing there, so Tempo stays out of
sessions you don't drive yourself.

Please report security issues privately through
[GitHub security advisories](https://github.com/andrebras/tempo/security/advisories/new).

## Contributing

Issues and pull requests are welcome. [CONTRIBUTING.md](CONTRIBUTING.md) explains how to run the
checks and the tests.

## License

[MIT](LICENSE)
