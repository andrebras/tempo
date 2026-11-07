# Changelog

All notable changes to Tempo are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- Two answers sent to one question at the same moment (a double click, two tabs, or the page
  and the `tempo` command) no longer both save; the second is refused as already answered.
  An answer, follow-up or reopen also checks the question again inside its write, so it
  cannot land on a question archived or reworded a moment before, or pick an option the
  reword removed.
- Two rewords of one question at the same moment both save, as versions one after the
  other, instead of the second failing with a database error.
- A blank option letter with a written answer saves the written answer, instead of failing
  with "Option () does not exist".
- Joining a project into another one keeps one "start here" mark per goal. When both had a
  marked question in the same goal, the joined project's mark is dropped.

## [0.5.4] - 2026-11-07

### Added

- A dot on a question in the list marks one Claude changed since you last opened it: reworded,
  given more context, or new since your first visit to the project. Questions keep their
  place. Opening the question clears the dot. See
  [What changed since you opened it](docs/reference.md#what-changed-since-you-opened-it).

## [0.5.3] - 2026-11-07

### Changed

- The header's project dropdown is replaced by project chips: one per project with open
  questions or something new since you last looked, each with its open count, plus the
  project you are on. A dot marks a project where something arrived since you last viewed
  it (a new question, a reworded one, or Claude's reply to a follow-up). An "All projects"
  menu lists every project. See [Projects in the header](docs/reference.md#projects-in-the-header).

## [0.5.2] - 2026-11-07

### Added

- `TEMPO_IN_THREAD=1` keeps Tempo's hooks quiet: they do nothing and print nothing. Another
  tool that runs Claude sessions on your behalf can set it in those sessions.

### Changed

- Settings keeps every control in the right-hand column: the Width choices no longer drop
  below their label in monospace, and every project's name field and Rename button line up.
- Settings lists each project's folders read-only: the menu to file a folder under another
  project is gone. Folders that no longer exist on disk, and old ones whose path isn't known,
  are hidden (they stay in the database).
- When several known folders are missing, the rename banner shows a "Link to <project>"
  button for each one, with its path, instead of sending you to Settings.

### Removed

- Threads, the early feature behind `TEMPO_THREADS=1`: the `/threads` page, its API, the
  Settings link, and the `TEMPO_THREADS` and `TEMPO_THREADS_PERMISSION_MODE` settings. Threads
  move to their own project. Tempo goes back to capturing the questions Claude asks while you
  work in the terminal. Databases that already have the threads tables keep them, unused.

## [0.5.1] - 2026-11-07

### Added

- Each reply in a thread has a "Details" toggle, closed at first, that lists the steps
  Claude took on the way: each tool it used, a short summary of what it gave the tool, and
  the start of the result.
- When a thread's Claude needs approval for a tool call (for example a network command
  outside the `acceptEdits` permission mode), the request shows in the thread with Allow
  and Deny buttons, and the thread list says "Needs your OK". Claude waits for the answer;
  with no answer in 10 minutes, the call is denied.

### Changed

- A session start replaces the page only when it runs an older version of Tempo, or is
  broken. A page of the same or a newer version keeps running, whatever folder it runs
  from, so the installed plugin and a checkout no longer keep replacing each other's page.
- An open page reloads itself when the server behind it restarts, for example after an
  update, so it never runs old code against new data (Settings showed folders as
  "[object Object]").

## [0.5.0] - 2026-11-07

### Added

- Threads, an early feature that is off by default. With `TEMPO_THREADS=1` set where the
  Tempo server starts, `/threads` opens a page where each conversation with Claude is its
  own thread: Claude works on it in a background session in the chosen project folder and
  replies there with the result only. Settings links to the page while it is on. With the
  setting off, the page and its API answer "Not found".
- Answers always reach Claude. When a reply ends, the Stop hook hands Claude the project's
  new answers and follow-ups, so it carries on with them instead of stopping. If the project
  has open questions and no `tempo wait` is running, it sends Claude back once to start one.
  `tempo wait` leaves a marker next to the database while it runs, so the hook can tell.
- An answer whose note asks something ("Do we still need the skill?") is delivered with a
  line asking Claude to reply in the question with `tempo context`.
- `GET /health` on the page's server answers with JSON saying which Tempo is running: its
  version, git commit (when run from a checkout), folder, process id and start time.

### Changed

- Tempo code running from a git worktree uses its own database, `.tempo-dev/questions.db`
  inside the worktree, so code still being built never touches the real questions. The
  installed plugin and the main checkout keep the real database; `TEMPO_DB` still wins.
- On wide screens the question list and the question scroll separately, so scrolling a
  long question no longer moves the list, and the list keeps its place.
- Follow-ups from the page now tell Claude to answer them in the question itself with
  `tempo reword`, as the skill says, so the answer shows in the box the user reads.
- When a session starts, Tempo replaces a Tempo page that is still running from another
  version or folder, or that no longer loads. Before, any program on port 2204 was left as
  it was, so a page started from a renamed folder kept failing. Programs that are not Tempo
  are never stopped.
- The empty screen says "Nothing is waiting on you" (or "No answered questions"), without the
  project name, which the header already shows.
- The empty screen's width follows the font (70 characters) instead of a fixed 460 pixels,
  so its short sentences no longer break in the middle.
- The Stop hook catches every question left only in chat: yes-or-no questions and requests
  for permission ("May I…?", "Should I…?", "Reply yes or no") as well as lettered options.
  A question counts as recorded only when the reply ran `tempo add`, `tempo reword` or
  `tempo context`, or links it outside the closing list of open questions. Code and quoted
  text don't count as asking.
- The skill puts yes/no confirmations and requests for permission in Tempo too. Merging,
  publishing and installing still also need a one-line OK in chat.
- Joining two projects in Settings works even when both have questions. The renamed
  project's questions move after the other's, in their order, with new numbers (for
  example tempo Q1–Q5 become Tempo Q28–Q32). Each gets a note in its thread saying its old
  number, and Settings says how many moved and where. Before, Tempo refused the join.
- Tempo now stores each folder's full path (the top folder of the git repository's main
  checkout, or the folder itself outside git), not only its name. Two folders with the same
  name, such as two `api` folders, are now separate folders. Folders remembered before keep
  their project: the next session in a folder of that name fills in its path. Settings shows
  the path under each project's folders. An older Tempo still running on the same database
  keeps working.
- Tempo spots a folder that was renamed or moved. When a session runs in a folder Tempo has
  never seen while the path of a known folder no longer exists, Tempo no longer quietly
  starts a new project and leaves it at that. A banner on every screen of the page offers to
  link the new folder to the missing folder's project in one click, or to keep it as its own
  project. When several known folders are missing, the banner asks you to pick in Settings,
  where each folder now has a menu to file it under any project. The session is told too,
  so Claude mentions it in chat. Each missing folder is offered once only.
- Tests can no longer open the real database: a test that does not pick its own fails.

## [0.4.0] - 2026-11-07

### Added

- Goals: each question names the piece of work it moves forward (`tempo add --goal`, and
  `tempo goal <n>` to set it later). The page groups open questions by goal, oldest group
  first, with questions without a goal last under "Other".
- Start here: within a goal, one question can be marked as the one to answer first, with a
  one-line reason (`--start-here "unlocks …"`). It shows a badge and sorts first in its goal.
- Two facts on each open question: **quick** (a recommended option, at most three options,
  never reworded) and how long it has been open.

### Changed

- The skill says only the user answers questions; Claude never answers through the page.
- The SessionStart note and the skill ask Claude to give every question a goal, and to mark
  "start here" only for a real dependency.
- "Waiting on Claude" is easier to notice: the question header says "Waiting on Claude" with
  three moving dots, and its row in the list shows the dots (still when Reduce motion is on).

### Removed

- The Now / Soon / Later priorities, from the page, the command and the skill. The database
  keeps the column for old rows but no longer uses it. `--priority` and `tempo priority`
  are still accepted for one release and only print a note.

### Fixed

- A reworded question's `created_at` (in `tempo show --json` and the page's data) was its
  latest version's time, not the time it was first asked.

## [0.3.3] - 2026-11-07

### Added

- A pull request template with the usual sections and a short checklist.
- A Width setting under Appearance: Narrow (68 characters per line), Medium (80) or Wide (100).

### Changed

- The question column defaults to 80 characters per line, the WCAG limit, instead of 68.

## [0.3.2] - 2026-11-07

### Added

- This changelog, with entries for every earlier release.
- CI fails when `plugin.json` and `pyproject.toml` carry different versions, or when this
  changelog has no section for the current version.
- A Release workflow that tags each new version and publishes its changelog section as a
  GitHub release.
- Weekly Dependabot pull requests for GitHub Actions and the development tools.
- Type hints on every function in `tempo/`, checked by mypy in CI.
- A schema version in the database, with numbered upgrade steps that each run once.
- The page's script in its own file, `tempo/page.js`, linted by Biome, with tests for the
  text formatting.

### Changed

- The question column is as wide as about 68 characters of text, so lines stay easy to read
  whatever the font and size.
- Ruff 0.17.3 and Bandit 1.8.6 for development; newer GitHub Actions versions in CI.

## [0.3.1] - 2026-11-06

### Changed

- List answered and archived questions newest answer first.

## [0.3.0] - 2026-11-06

### Added

- Tell every session to use Tempo, through a note at the start of the session.
- Catch questions asked only in chat, with a check at the end of every reply.

## [0.2.9] - 2026-11-06

### Fixed

- Render code inside bold text.

## [0.2.8] - 2026-11-06

### Added

- Render basic Markdown in question context and the thread.
- Tell Claude to lay out context and answer follow-ups in the question.

## [0.2.7] - 2026-11-06

### Changed

- Show only the three busiest projects under Still open elsewhere.

## [0.2.6] - 2026-11-06

### Added

- Archive answered questions a week after their answer.

## [0.2.5] - 2026-11-06

### Changed

- Restyle the links on the empty screen.

## [0.2.4] - 2026-11-06

### Added

- Link an empty Open tab to the answered questions.

## [0.2.3] - 2026-11-06

### Changed

- Center the question in its panel.

## [0.2.2] - 2026-11-06

### Fixed

- Hide the repeated empty line next to the empty screen.

## [0.2.1] - 2026-11-06

### Fixed

- Center the empty list message and remove a stray 00.

## [0.2.0] - 2026-11-06

### Added

- A Settings screen for theme, font, text size, project names and each folder's project.
- A screenshot and a short demo GIF in the README.
- Run CI on GitHub for pull requests and main.

### Changed

- Give the page a calmer, more polished design.
- Make a project just its name.
- Slim the README and move reference and contributor notes out.

### Fixed

- Show projects that have no questions yet.
- Stop folders from inventing empty projects in Settings.
- Work with any Git version, not only 2.31 and newer.

## [0.1.0] - 2026-11-06

### Added

- First version: questions in SQLite, the `tempo` command, hooks, a skill, and a local page
  to read, follow up on and answer them.
