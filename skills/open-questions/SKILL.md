---
name: open-questions
description: Records every question Claude needs the user to decide on in the open questions database, with full context and lettered options, and picks up the user's answers and follow-ups from its local page. Use whenever you are about to ask the user to choose or decide something that blocks or shapes the work, when the user asks about an open question or a Q number (like "Q12" or "12b"), or when hook output says the user answered or asked for more on the open questions page. Do not use for quick yes/no confirmations inside a single reply.
---

# Open questions

Questions for the user live in a SQLite database, not only in chat. The user reads and answers
them on a local page at http://127.0.0.1:2204/ (started by this plugin's SessionStart hook).
Each question has a running number per project (Q1, Q2…) that is never reused, so the user can
answer anywhere with a short code like "12b".

The `oq` command is on your PATH. The project defaults to the current git repo's name.

## Asking a question

Write it so someone with no context can answer it cold:

- `--title`: the question itself, one sentence.
- `--context`: what this is about, why it matters now, and what each option leads to. Explain
  every acronym and technical term. Short sentences.
- `--option`: one per option, written out in full; they are lettered (a), (b)… in order.
- `--recommended`: the letter you recommend, if any. Say why in the context.
- `--urgent`: only when it blocks work right now.

```bash
oq add --title "Where should the export button go?" \
  --context "The export feature is built and needs a place in the UI. ..." \
  --option "In the toolbar, next to Share" \
  --option "In the ⋯ menu" \
  --recommended a
```

The command prints the number and a link. In chat, give the question in full with its options
and the link, so the user can answer in either place. `oq add` refuses incomplete questions
and reused numbers: fix the question, don't work around the check.

To continue numbering from an older list, run `oq set-next <number>` once before the first add.

## Getting answers back

Answers and follow-ups from the page reach you two ways:

1. **On the user's next message**: the UserPromptSubmit hook adds them to your context.
2. **While you keep working**: after asking, start `oq wait` with the Bash tool's
   `run_in_background` option. It exits as soon as the user answers or follows up, which
   wakes you with what they wrote. Start it again after handling the result while questions
   are still open. Run only one wait per session.

Each item is delivered once. `oq inbox` prints anything not yet delivered.

## Handling an answer

Act on it, and tell the user in chat what you did with it. If the answer is free text that
doesn't match an option, follow it; ask again only if it is truly unclear.

## Handling a follow-up ("ask for more context")

The user wants to understand the question better before answering. Either:

- add context: `oq context <number> "…"`, for an extra explanation or a fact you looked up, or
- reword it: `oq reword <number> --note "Why it changed" [--title …] [--context …] [--option …]`,
  when the question itself was unclear or the options need to change. Passing `--option`
  replaces all options. Old versions stay in the thread.

When answering needs research (reading code, docs, past decisions), hand it to a subagent and
have it run `oq context` or `oq reword` itself. Then tell the user in chat that the question
was updated, with its link.

## Other commands

- `oq list` (`--status answered|archived|all`, `--all-projects`) and `oq show <number>`.
- `oq urgent <number>` / `oq urgent <number> --off`.
- `oq archive`: archive the project's answered questions once nothing is open.
