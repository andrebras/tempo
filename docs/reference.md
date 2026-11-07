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
  the other's name. That works while only one of them has questions; otherwise both would
  have a Q1.

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
