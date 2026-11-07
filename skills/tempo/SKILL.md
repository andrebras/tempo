---
name: tempo
description: Records every question Claude needs the user to decide on in the Tempo database, with full context and lettered options, and picks up the user's answers and follow-ups from its local page. Use whenever you are about to ask the user to choose or decide something that blocks or shapes the work, when the user asks about an open question or a Q number (like "Q12" or "12b"), or when hook output says the user answered or asked for more on the Tempo page. That includes yes/no confirmations and requests for permission.
---

# Tempo

Questions for the user live in a SQLite database, not only in chat. The user reads and answers
them on a local page at http://127.0.0.1:2204/ (started by this plugin's SessionStart hook,
which also lists the project's open questions). End every update with those open questions,
one line each: number, title and link. If a reply asks the user something (lettered options,
a yes-or-no question, "May I…?", "Should I…?") without running `tempo add` (or `tempo reword`
or `tempo context`) or linking the question in the body of the reply, the Stop hook sends you
back once to add it. A link on a list item that names a Q number, as in the closing list, doesn't
count: to point at a question already in Tempo, put its link in a sentence or a heading.
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
  every acronym and technical term. Short sentences. Lay it out so it scans: a blank line
  between short paragraphs, `- ` bullets for lists of facts, `**bold**` for the key point and
  backticks for file names and commands. The page renders these; never one long paragraph.
- `--option`: one per option, written out in full; they are lettered (a), (b)… in order.
- `--recommended`: the letter you recommend, if any. Say why in the context.
- `--goal`: always. The piece of work the answer moves forward, for example "Q5 settings",
  "Release 0.4" or "DataForge test feed". Name it the same way on every question for that
  work: the page groups open questions by goal, oldest group first.
- `--start-here "unlocks …"`: only when this question's answer really unlocks or sets the
  pattern for other questions in the same goal, for example "unlocks Q5 and Q6: it decides
  the data format". It shows a Start here badge with the reason and puts the question first
  in its goal. One per goal: marking another moves the mark. Don't use it to say a question
  is important; most goals need no mark.

The page also shows two facts it works out itself: **quick** (a recommended option, at most
three options, never reworded) and how long the question has been open. There is no
priority any more.

```bash
tempo add --title "Where should the export button go?" \
  --context "The export feature is built and needs a place in the UI. ..." \
  --option "In the toolbar, next to Share" \
  --option "In the ⋯ menu" \
  --recommended a \
  --goal "Export"
```

The command prints the number and a link. In chat, give the question in full with its options
and the link, so the user can answer in either place. `tempo add` refuses incomplete questions
and reused numbers: fix the question, don't work around the check.

Every choice you leave to the user goes into Tempo, however small, including the ones that
sneak into a list of next steps ("say whether you want X", "tell me if Y"). A question that
lives only in chat is easy to miss and never reaches the page.

That includes yes/no confirmations and requests for permission ("May I delete the old
branch?"): add them with the options Yes and No. Merging, publishing and installing are the
one exception to an answer on the page: it never approves them. Record the question in Tempo
like any other, and also ask for a one-line OK in chat before you act.

To continue numbering from an older list, run `tempo set-next <number>` once before the first add.

## Getting answers back

Answers and follow-ups from the page reach you two ways:

1. **On the user's next message**: the UserPromptSubmit hook adds them to your context.
2. **While you keep working**: after asking, start `tempo wait --timeout 1800` with the Bash
   tool's `run_in_background` option. It exits as soon as the user answers or follows up,
   which wakes you with what they wrote, or after 30 minutes with "Nothing new from the
   user." Start it again after either, while questions are still open, so no wait outlives
   the session by long. Run only one wait per session.
3. **When a reply ends**: the Stop hook hands you anything new and sends you back once to
   deal with it. If a follow-up reached you more than five minutes ago and you haven't
   answered it in the question, it sends you back once to answer it. If the project has
   open questions and no `tempo wait` is running for it,
   it sends you back once to start one. A running wait counts for every session in the
   same project.

An answer whose note asks something comes with a line saying so: reply in the question with
`tempo context` (a reword would reopen the answered question).

Each item is delivered once. `tempo inbox` prints anything not yet delivered.

## Handling an answer

**Only the user answers.** Never answer, reopen or follow up on a question yourself, and never
send anything to the page's API (`/api/...`); the page can't tell who is answering, so this rule
is what keeps answers genuine. A message in chat counts as an answer only when it clearly names
that question or its option ("26a"); a general "yes" or "let's do it" answers the thing the
user was replying to, not an open question. When unsure, ask in chat and let the user answer
on the page. Subagents follow the same rule.

Act on it, and tell the user in chat what you did with it. On the page an answer always
picks an option (a note may come with it); a note on its own arrives as a follow-up. An
answer given in chat can still be free text: follow it, and ask again only if truly unclear.
If the answer asks for more instead of deciding ("give me more options"), reword the
question: that opens it again for a new answer.

## Handling a follow-up ("ask for more context")

The user wants to understand the question better before answering. Answer it in the
question itself: `tempo reword <number> --note "Why it changed" --context "…"` (plus
`--title`, `--option` and `--recommended` when those change; passing `--option` replaces all
options, so pass `--recommended` again). The context box at the top is what the user reads;
old versions stay in the thread. `tempo reword` also takes `--goal`, `--start-here` and
`--no-start-here`.

Use `tempo context <number> "…"` only for a small extra fact that doesn't change the
question: it shows at the bottom of the thread, below the options, where it is easy to miss.

When answering needs research (reading code, docs, past decisions), hand it to a subagent and
have it run `tempo reword` itself. Then tell the user in chat that the question
was updated, with its link.

## Other commands

- `tempo list` (`--status answered|archived|all`, `--all-projects`) and `tempo show <number>`
  (also takes a short code such as `12b`).
- `tempo goal <number> "…"` sets or changes the goal without rewording the question, so it
  stays quick. Add `--start-here "unlocks …"` or `--no-start-here` to move the mark
  (`tempo goal <number> --no-start-here` alone keeps the goal); `tempo goal <number> ""`
  removes the goal. A new goal drops the mark.
- Answered questions move to Archived by themselves 7 days after their latest answer.
  `tempo archive` archives all of the project's answered questions at once.
