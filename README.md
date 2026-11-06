# Open questions

A Claude Code plugin that keeps the questions Claude asks you in one place, with the
context you need to answer them, and brings your answers back to Claude by themselves.

When Claude needs a decision from you, it saves the question in a local SQLite database
(a database kept in a single file on your machine). Each question has:

- a running number per project (Q1, Q2…) that is never reused, so "12b" always means one thing;
- a title, the full context, lettered options and, if Claude has one, a recommendation;
- a thread: context Claude added, rewordings (old versions are kept), your follow-ups and your answer.

You read and answer the questions on a local page at http://127.0.0.1:2204/. Pick an option,
write your own answer, or press "Ask for more context". Claude gets what you sent in two ways:

- on your next message in any Claude Code session for that project, through a hook (a script
  Claude Code runs before each message);
- right away, if Claude is waiting in the background with `oq wait` while it keeps working.

When you ask for more context, Claude adds an explanation or rewords the question, sending a
subagent to research it when needed, and the page shows the new version.

## Install

```bash
claude plugin marketplace add andrebras/open-questions
claude plugin install open-questions@open-questions
```

The plugin needs `python3` (3.9 or newer, as shipped with macOS) and `git`. It has no other
dependencies.

The page starts by itself when a Claude Code session starts. To start it by hand, run
`oq serve --background`.

## Where things live

- Questions: `~/.claude/open-questions/questions.db`. Set `OQ_DB` to use another file.
- Page log: `~/.claude/open-questions/server.log`.
- Port: 2204. Set `OQ_PORT` to change it.
- Project: the name of the current git repository's folder (worktrees count as the same
  project). Set `OQ_PROJECT` or pass `--project` to override it.

## Security

The page only listens on 127.0.0.1, so other machines cannot reach it. Any website open in
your browser could still send requests to a local port, so the server refuses requests that
are not addressed to 127.0.0.1 or localhost, and only accepts answers sent as JSON from the
page itself.

## Commands

Claude uses these; you can too.

| Command | What it does |
| --- | --- |
| `oq add` | Add a question (refuses one without context or with fewer than two options) |
| `oq list`, `oq show <n>` | List questions, or show one with its thread |
| `oq context <n> "…"` | Add context to a question |
| `oq reword <n> --note "…"` | Save a new version of a question |
| `oq urgent <n>` | Mark a question urgent (`--off` to undo) |
| `oq archive` | Archive the project's answered questions |
| `oq set-next <n>` | Continue numbering from an older list |
| `oq inbox` | Print answers and follow-ups Claude has not seen yet |
| `oq wait` | Wait until you answer or follow up, then print it |
| `oq serve` | Run the page |

## Development

```bash
bin/ci                       # compile check and all tests
OQ_DB=/tmp/q.db bin/oq serve # run the page on a scratch database
```
