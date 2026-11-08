# Changelog

All notable changes to Tempo are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Four slash commands answer from inside the Claude Code session: `/tempo:list`,
  `/tempo:show 57`, `/tempo:answer 57a` (also `57 a`, `Q57a` or `Q57 a)`, with an optional
  note after the code) and `/tempo:dive 57` (ask for more, with an optional question; "Tell
  me more." without one). Only you can run them. They save the answer or follow-up before
  Claude reads anything, already marked delivered, so it reaches Claude once. Claude then acts
  on the answer, or answers the follow-up in the question.
- `tempo answer` and `/tempo:answer` take an option by its text as well as its letter, in any
  case and without trailing punctuation: `tempo answer 61 yes` on a Yes/No question. A valid
  letter still wins.

## [0.7.0] - 2026-11-07

### Added

- `tempo tui` shows the page's questions in the terminal, in panels side by side as in
  lazygit, keyboard only. Projects lists every project with open questions plus the current
  folder's, with their counts; Questions lists the selected project's open questions grouped by
  goal; Detail shows the selected question with its context, options and thread. `a`, `b`,
  `c`… answer after a y/n confirm that can add a note, `r` asks for more, and `?` lists every
  key. Answers and follow-ups are saved as the page saves them, so Claude gets them the same
  way. The screen follows changes by itself within a second or two, fits a tmux popup (one
  panel at a time below 60 columns), honors `NO_COLOR`, and never starts the page. It is drawn
  with curses, which comes with Python, so there is nothing to install. The README has the tmux
  line to open it in a popup.
- Claude now links the question number instead of printing the page address: the closing
  list reads `- [Q3](…) Pick a colour`, and a question in a reply is headed
  `**[Q3](…): Pick a colour?**`, so you click Q3 in the terminal. With the page off, the
  number is plain text.
- `tempo config page off` stops sessions from starting the page, for when you answer from the
  terminal. Claude then points you to `tempo show N` and `tempo answer N <letter>` instead of
  page links. `tempo serve --background` still starts the page on demand, and
  `tempo config page on` brings back the default. `tempo config` shows the current value. The
  setting is kept in the database; existing databases get it without a version change. The
  page's Settings has the same switch, under Sessions.

### Changed

- `tempo status` counts the questions of the current folder's project, as the page shows one
  project at a time, instead of every project. `--all` counts every project. The README's tmux
  line now runs it in the folder of the pane you are in, so a pane in one project never shows
  another project's questions.

### Fixed

- Running the tests (`bin/ci`, which `bin/release` also runs) no longer starts a real page
  server. One test did, on port 2204 and on the test's throwaway database: during the 0.6.0
  release it replaced your page for a few minutes, so answers given then were lost, and it
  kept running after the tests ended. The tests now use a port of their own, and any test
  that would start a real server fails instead.
- A Tempo page from 0.5 or older, whose `/health` does not say which database it uses, is no
  longer replaced by code on a test or branch database. It is taken to be on the real
  database, and only code on the real database replaces it.
- `bin/tempo` always runs its own copy of Tempo. Before, run from a folder that holds a
  `tempo/` package, such as a Tempo checkout, it ran that folder's code instead, because
  `python -m tempo` looks in the current folder first. That hit hooks in sessions working on
  Tempo itself and the tmux badge in a pane there. It now runs `tempo/__main__.py` as a file,
  which leaves the current folder off Python's search path; the command still runs in the
  current folder. Running `tempo wait` and `tempo serve` processes are still recognized by
  their new command line, and by the old one.

## [0.6.0] - 2026-11-07

### Added

- `tempo answer N b` (with `--note "…"` if you like) and `tempo reply N "…"` let you answer a
  question or ask for more context from the terminal, without opening the page. They save
  exactly what the page saves, so Claude gets them the same way: through the hooks,
  `tempo wait` and `tempo inbox`. An unknown question or option letter is refused with an
  error. The skill tells Claude these commands are yours and never to run them.
- `tempo status` prints a short badge for the tmux status bar: `? 3` when three questions in
  all projects wait for you (`--project NAME` for one project), and nothing when none do. It
  only reads the database, never creates or changes it and never starts the page, so it is
  safe to run every few seconds. The README shows the tmux lines to add.
- `bin/ci` reports test coverage: a per-file summary of how much of `tempo/` the tests run,
  including the `bin/tempo` processes some tests start. It is a report only and never fails the
  run. On GitHub the same table appears on the job's summary page.
- `server.log` now has one line for each change made through the page (method, path and status
  code) and for each hook run (event, project and what happened, such as "delivered 2" or
  "blocked: no wait"), so you can see afterwards what Tempo did. It never holds request bodies or
  answer text, and it moves to `server.log.1` once it passes about 1 MB.
- A session whose Tempo is older than the installed plugin says so once: its SessionStart
  or UserPromptSubmit hook asks Claude to tell you to restart the session
  (`claude --continue`). Sessions keep the hooks they started with, so before this an old
  session kept running old code without saying so. Code run from a git checkout stays quiet.
- A test runs the previous release's `tempo add`, `list` and prompt hook against a database
  migrated by the current code, so a release that breaks older sessions fails CI.
- A test runs the prompt and Stop hooks the way Claude Code does (`bin/tempo hook …` in its
  own process, with Claude Code's JSON on stdin) and checks that an answer from the page
  reaches Claude exactly once.
- When a reply ends, the Stop hook also looks for follow-ups Claude was given more than five
  minutes ago and never answered (no context or reword from Claude after them, on a question
  of this project that is not archived). It sends Claude back once, listing them, to answer
  each with `tempo reword N --context` (`tempo context N` on an answered question). Before, a
  session could pass over a follow-up without a word.

### Changed

- Database changes that older code can ignore (a new table, index or column that may be
  empty) are now made on every open and no longer raise the database's `user_version`.
  Only a change older code would get wrong raises it, so sessions still running an older
  Tempo keep working after most upgrades instead of being asked to restart.
  `CONTRIBUTING.md` says which kind of change to use when.
- A missing folder is offered as renamed or moved only to a new git repository that is a
  copy of it: one with the same first commit or, failing that, the same `origin` remote.
  Before, a deleted repository was offered to every new repository until one was linked to
  it. Tempo notes the first commit and remote when it first sees a folder, so normal session
  starts cost no more than before. Folders it remembered earlier are offered as before.
- A git folder Tempo remembered before it noted first commits and remotes gets them the next
  time a session starts or a prompt arrives there, once, so it too is matched only with a
  copy of itself when it goes missing. Folders that already have them cost no git call.

### Fixed

- The skill no longer mentions answers "given with `tempo`", which Claude could read as
  answers it may give itself. Only the user answers, on the page, in chat, or with the new
  `tempo answer` (see Added).
- A project can't be named `.` or `..` any more, with `--project`, `TEMPO_PROJECT` or a
  rename. The page could not open such a project's questions, because the browser reads
  those names in a link as steps between folders.
- `tempo show 12b` shows Q12, so the short code the user names an answer with works there
  too. It failed with "invalid _number value".
- After Claude rewords a question you have picked an option on, the page clears the pick.
  The same letter may now name another option, so a pick kept from the old wording could
  answer something you didn't choose. The note you were typing is kept.
- While an answer, follow-up or reopen is being sent, the page's buttons stay disabled even
  when the timed refresh redraws the question. Before, a redraw enabled them again, so a
  second click could send the answer twice.
- Several sessions, hooks or commands opening a brand-new database at the same moment no
  longer fail now and then with "database is locked". When a write fails in a way that makes
  SQLite undo it by itself, the error you see is that one, not "no transaction is active".
- Tempo refuses a database that a newer Tempo has changed in ways it doesn't know. Every
  command stops with a clear error before reading or writing anything. Before, it went on
  silently and could fail halfway through, or misread the newer data.
- Tempo code run from the main checkout on a branch other than `main`, or on no branch, uses
  its own database, `.tempo-dev/questions.db`, like a worktree does. Before, it used and
  migrated the real database, so older sessions then ran on a database changed by code that
  was never released. The main checkout on `main` and the installed plugin keep the real
  database.
- Tempo works in a repository that pins a Python version that isn't installed, through asdf,
  pyenv or mise. Its hooks and commands failed there with exit code 126, because the version
  manager picked Python by the session's folder. The `tempo` command now looks up Python from
  the plugin's folder, then tries the system's and Homebrew's, uses the first that is 3.9 or
  newer, and says so plainly when none is.
- Tempo code running from a git worktree serves its page on a port of its own, worked out
  from its database's path, instead of taking port 2204. Before, a worktree's page could
  replace the real one, so the page showed the worktree's throwaway questions. `/health`
  now names the database a server shows, and a session never replaces a server on another
  database, whatever its version.
- The page's server reads its version and its page files once, when it starts. Before, a
  server started from a git checkout that was then updated reported the new version, so a
  newer session never replaced it, and it served the new page script against its old code.
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
- An answer or follow-up reaches Claude once, even when two sessions in the same project,
  or a session and `tempo wait`, pick up the user's news at the same moment.
- A session started in `/` no longer takes every project's answers and follow-ups. The folder
  has no name, so its project name was empty, which was read as "all projects": its Stop hook
  handed every project's news to that session, and the sessions they were meant for never saw
  them. A folder with no name now belongs to no project, and its hooks deliver nothing.
- An answer or follow-up is no longer lost when its text cannot be built. The news was marked
  delivered first and described after, so an error while describing it (for example an older
  answer naming an option its question no longer had) dropped it without a word. It is now
  marked delivered only once its text is ready, and an option that is missing shows as its
  letter alone.
- A `tempo wait` that was killed no longer counts as running when the computer gives its process
  number to another program. The Stop hook now checks that the process really runs `tempo wait`,
  so it asks Claude to start a new wait instead of trusting a dead one.
- A `tempo wait` started by an older install, which leaves no marker file, now counts as
  running. Before, the Stop hook only looked for markers, so it sent Claude back after every
  reply to start a wait that was already running. Such a wait counts for the project it names
  with `--project` or `TEMPO_PROJECT`, else for its folder's project.
- A running `tempo wait` follows its folder's project when the project is renamed or joined
  into another in Settings. Before, it kept waiting under the old name, so it missed every
  answer and the Stop hook no longer saw it as running.
- One session no longer takes other sessions' news. `tempo inbox --all-projects` now only shows
  every project's answers and follow-ups, without marking them seen, and `tempo wait` no longer
  waits for every project: `--all-projects` is still accepted for one release, with a note, and
  waits for the folder's project only.
- A `tempo wait` whose session has ended stops at its next check. Before, it kept running for up
  to its timeout and could take an answer meant for a later session in the same project.
- The Stop hook now catches more questions asked only in chat: questions with a dot inside
  (`v0.5.4`, `README.md`, "e.g."), "Is it OK if…", "Would you prefer…", "Which do you prefer…",
  "go ahead?", "Let me know if you want me to…", "Say whether I should…", and options written
  "- a)" or "**A.**". It no longer counts a question Claude answers at once ("…? No, …") or a
  sentence listing facts as "(a) … and (b) …".
- Unrelated things no longer excuse a question asked only in chat. Before, any mention of
  `tempo add` in a tool call (even a `grep`, or an add that failed) or any Tempo link anywhere
  in the reply (even a numbered or table closing list) let it through. Now only a Bash command
  that runs `tempo add`, `reword` or `context` and works counts, or a Tempo link in the same
  paragraph as the question (for options, also the paragraph before).
- A Tempo error in a hook no longer blocks the prompt or the session. For example, every prompt in
  a session started in `/` was discarded, because the folder has no name. The hooks now always
  exit cleanly and say nothing to the session when something goes wrong, and the page still
  starts.
- `TEMPO_DB` may be a relative path or start with `~`. A relative path such as `questions.db` made
  every command fail, and a `~` left unexpanded (as in Claude Code's `env` settings) gave each
  folder its own database in a folder literally named `~`.
- The page's server answers every request. A body that is not a JSON object, a number where
  text is expected, or an unexpected error such as a locked database used to close the
  connection, so the page showed a network error. Bad input now gets a 400 with a clear message,
  and an unexpected error a 500, with the details in the server log.
- A request with a negative `Content-Length` no longer holds a server thread until the client
  hangs up. It gets a 400, a body over 1 MB gets a 413, and a client that stops sending is
  dropped after 30 seconds.
- "Reopen to answer again" works on the page again. It sent its request the wrong way, so the
  server answered "Not found" and the question stayed answered.
- After you reopen a question, the page switches to the Open tab and stays on it. Before, the
  next refresh jumped to another question and the note you were typing was lost.
- When the page reloads itself after a server restart (for example after an update), the note
  you were typing and the option you picked are kept.
- The timed refresh, every 4 seconds, no longer moves keyboard focus off the question list or
  the buttons on the empty screen. The list is redrawn only when it changes.
- A slow reply for a question you have already left no longer replaces the question you opened
  next, or clears the note you started there.
- A link to a question that doesn't exist (for example one renumbered when two projects were
  joined) says "Question not found". Before, it opened another question without saying so.
- When Claude changes the question you are typing a note on, the note box keeps focus and the
  cursor stays where it was.
- Clicking "Link" or "Keep" in the rename banner while Settings is open no longer clears a
  project name you are typing.
- A send that fails always shows its error, even when the question was redrawn while the
  request ran.
- The "renamed or moved" banner reaches the folder that was really renamed. Before, a missing
  folder was offered once only, to the first new folder any session opened, even a scratch
  folder outside git, and "Keep as its own project" on that wrong banner used it up. Now only
  a new git repo is offered missing folders, a missing folder stays on offer until a folder
  is linked to it, and banners left with nothing to offer go away.
- "Link to <project>" in the rename banner brings along the questions asked in the folder
  before you clicked it. They stayed in the folder's interim project, which no session used any
  more, so their answers were never delivered. They now move into the linked project after its
  own questions, each noting its old number, unless another folder still uses that project.
- Two repos with the same folder name, such as `work/api` and `personal/api`, get a project
  each. The second one used to land in the first one's project, with no way to split them; it
  now starts in a project named after its parent folder too, such as `api (personal)`, which
  you can rename in Settings. A folder moved to a new place under the same name also starts
  its own project: link it from the banner, or rename its project in Settings to join them.
- Worktrees of a bare repo, such as `shop.git` with `shop-main` checked out from it, share the
  project `shop`. They used to be filed under the folder the bare repo sits in. A submodule is
  now a project of its own, named after its folder, instead of a project named after a folder
  inside `.git/modules`.

### Security

- The database and the server log are now readable by your account only (mode 0600), and a new
  data folder is made with mode 0700. Before, they used the default permissions, so other
  accounts on a shared machine could read every question and answer. An existing database is
  tightened the next time Tempo opens it; an existing folder is left as it is.

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
