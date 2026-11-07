# Tempo reference

## Commands

Claude uses these through the bundled skill. You can use them too, because `tempo` is on Claude
Code's `PATH` while the plugin is enabled. Run `tempo <command> --help` for every option.

| Command | What it does |
| --- | --- |
| `tempo add --title … --context … --option … --option …` | Add a question (`--recommended b`, `--goal "Release 0.4"`, `--start-here "unlocks Q5"`) |
| `tempo list`, `tempo show <n>` | List questions, or show one with its thread |
| `tempo context <n> "…"` | Add context to a question |
| `tempo reword <n> --note "…"` | Save a new version of a question; reopens it if it was answered |
| `tempo goal <n> "…"` | Set a question's goal without rewording it (`--start-here "…"`, `--no-start-here`; `""` removes the goal) |
| `tempo archive` | Archive the project's answered questions now (they also move to Archived by themselves 7 days after their latest answer) |
| `tempo set-next <n>` | Continue numbering from an older list |
| `tempo inbox` | Print answers and follow-ups Claude hasn't seen yet |
| `tempo wait --timeout 1800` | Wait until you answer or follow up, then print it |
| `tempo serve [--background]` | Run the page |

`oq`, the command's old name, still works as an alias.

## Projects in the header

The page shows one project at a time. The header has a chip for each project that has
something for you: at least one open question, or something new since you last looked at
that project. Each chip shows the project's name and its open count, such as `tempo · 2`.
The project you are on always has a chip, highlighted, even with nothing open. Click a chip
to switch to that project.

A dot on a chip means something arrived since you last viewed that project: a new question,
a reworded question, or Claude's reply to a follow-up. Viewing the project clears the dot.
When you last viewed each project is kept in your browser, not in the database; on a
browser's first visit nothing counts as new yet.

**All projects ▾**, after the chips, lists every project with its open count, so a project
with nothing waiting is one click away. It works with the keyboard: the arrow keys, Home
and End move through the list, and Escape closes it.

A project gets its chip by itself, within a few seconds of its first new question.

## Goals

Each question names its **goal**: the piece of work its answer moves forward, such as
"Release 0.4". The page groups a project's open questions under their goal. Groups come in
the order of their oldest question, and questions without a goal come last, under "Other".
Inside a group, the oldest question comes first.

Within a goal, Claude may mark one question **Start here**, with a one-line reason such as
"unlocks Q5 and Q6". It does so only when that answer unlocks or sets the pattern for the
others. The question then comes first in its group. Marking another question in the same
goal moves the mark.

Each open question also shows two facts that Tempo works out itself:

- **quick**: it has a recommended option, at most three options, and was never reworded.
- **open 3 days** (or hours, or minutes): how long ago it was asked.

Goals replace the earlier Now / Soon / Later priorities. Old questions keep their priority in
the database, but nothing uses it. `--priority` and `tempo priority` still run for one
release, but only print a note.

## Settings

The gear button at the top right of the page opens Settings.

- **Appearance**: theme (System, Light or Dark), font (System sans, Serif or Monospace), text
  size, and width: how many characters fit on a line of a question (Narrow 68, Medium 80 or
  Wide 100). These are kept in your browser, not in the database.
- **Projects**: a project's name is what the page shows everywhere. A new git folder starts
  as a project with the folder's name. Renaming it, for example `onzehq` → "ONZE HQ", takes
  its questions, their numbers and its folders along. To join two projects, rename one to
  the other's name. If both have questions, the renamed one's questions move after the
  other's, in their order, and get new numbers: joining `tempo` (Q1–Q5) into "Tempo"
  (Q1–Q27) makes them Q28–Q32. Each moved question gets a note in its thread with its old
  number, such as "Moved from tempo Q2 when tempo joined Tempo.", so old references can
  still be traced.
  Under each project, Settings lists the folders it covers, with their paths, read-only.
  Only folders still on disk are listed: a folder that no longer exists, or that was
  remembered before Tempo stored paths, is hidden but not deleted.
- **Renamed or moved folders**: when a session runs in a new folder while a known folder no
  longer exists, a banner on every screen asks whether the new folder is that one renamed or
  moved. It has a "Link to <project>" button for each missing folder, named with that
  folder's path, and a "Keep as its own project" button.

Projects are saved in the database, so every session, the hooks and the `tempo` command use
them.

## Environment variables

| Setting | Default | Variable |
| --- | --- | --- |
| Database | `~/.claude/tempo/questions.db` | `TEMPO_DB` |
| Port | `2204` | `TEMPO_PORT` |
| Project | The project the current git repository's folder is filed under, named after the folder until renamed (worktrees count as the same folder) | `TEMPO_PROJECT`, or `--project` on a command; both win over Settings |

The page log is written next to the database. The database sits outside the plugin folder, so
it survives plugin updates and reinstalls. The variables' old names (`OQ_DB`, `OQ_PORT`,
`OQ_PROJECT`) still work; when both are set, the `TEMPO_` one wins. If only the old database
at `~/.claude/open-questions/questions.db` exists, Tempo keeps using it where it is.

With `TEMPO_IN_THREAD=1`, Tempo's hooks do nothing and print nothing: another tool that runs
Claude sessions on your behalf can set it so Tempo stays quiet in those sessions.
