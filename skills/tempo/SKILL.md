---
name: tempo
description: Records every question Claude needs the user to decide on in the Tempo database, with full context and lettered options, and picks up the user's answers and follow-ups from its local page. Use whenever you are about to ask the user to choose or decide something that blocks or shapes the work, when the user asks about an open question or a Q number (like "Q12" or "12b"), or when hook output says the user answered or asked for more on the Tempo page. Do not use for quick yes/no confirmations inside a single reply.
---

# Tempo

Questions for the user live in a SQLite database, not only in chat. The user reads and answers
them on a local page at http://127.0.0.1:2204/ (started by this plugin's SessionStart hook).
Each question has a running number per project (Q1, Q2…) that is never reused, so the user can
answer anywhere with a short code like "12b".

The `tempo` command is on your PATH (`oq`, its old name, still works as an alias). The project
defaults to the one the user chose for the current git repo's folder in the page's Settings,
else the folder name. Use the name `tempo add` prints, which may differ from the folder name
(for example "ONZE HQ" for `onzehq`). Don't pass `--project` unless asked: the user can rename
projects at any time.

## Asking a question

Write it so someone with no context can answer it cold:

- `--title`: the question itself, one sentence.
- `--context`: what this is about, why it matters now, and what each option leads to. Explain
  every acronym and technical term. Short sentences.
- `--option`: one per option, written out in full; they are lettered (a), (b)… in order.
- `--recommended`: the letter you recommend, if any. Say why in the context.
- `--priority`: how soon you need the answer. `now`: work is waiting on it. `soon` (the
  default): needed this week. `later`: no rush. The page sorts by it, then by number.

```bash
tempo add --title "Where should the export button go?" \
  --context "The export feature is built and needs a place in the UI. ..." \
  --option "In the toolbar, next to Share" \
  --option "In the ⋯ menu" \
  --recommended a
```

The command prints the number and a link. In chat, give the question in full with its options
and the link, so the user can answer in either place. `tempo add` refuses incomplete questions
and reused numbers: fix the question, don't work around the check.

Every choice you leave to the user goes into Tempo, however small, including the ones that
sneak into a list of next steps ("say whether you want X", "tell me if Y"). A question that
lives only in chat is easy to miss and never reaches the page.

To continue numbering from an older list, run `tempo set-next <number>` once before the first add.

## Getting answers back

Answers and follow-ups from the page reach you two ways:

1. **On the user's next message**: the UserPromptSubmit hook adds them to your context.
2. **While you keep working**: after asking, start `tempo wait --timeout 1800` with the Bash
   tool's `run_in_background` option. It exits as soon as the user answers or follows up,
   which wakes you with what they wrote, or after 30 minutes with "Nothing new from the
   user." Start it again after either, while questions are still open, so no wait outlives
   the session by long. Run only one wait per session.

Each item is delivered once. `tempo inbox` prints anything not yet delivered.

## Handling an answer

Act on it, and tell the user in chat what you did with it. On the page an answer always
picks an option (a note may come with it); a note on its own arrives as a follow-up. Answers
given with `tempo` or in chat can still be free text: follow them, and ask again only if
truly unclear. If the answer asks
for more instead of deciding ("give me more options"), reword the question: that opens it
again for a new answer.

## Handling a follow-up ("ask for more context")

The user wants to understand the question better before answering. Either:

- add context: `tempo context <number> "…"`, for an extra explanation or a fact you looked up, or
- reword it: `tempo reword <number> --note "Why it changed" [--title …] [--context …] [--option …]`,
  when the question itself was unclear or the options need to change. Passing `--option`
  replaces all options. Old versions stay in the thread.

When answering needs research (reading code, docs, past decisions), hand it to a subagent and
have it run `tempo context` or `tempo reword` itself. Then tell the user in chat that the question
was updated, with its link.

## Other commands

- `tempo list` (`--status answered|archived|all`, `--all-projects`) and `tempo show <number>`.
- `tempo priority <number> now|soon|later` when the need changes.
- Answered questions move to Archived by themselves 7 days after their latest answer.
  `tempo archive` archives all of the project's answered questions at once.
