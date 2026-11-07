# Changelog

All notable changes to Tempo are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Follow-ups from the page now tell Claude to answer them in the question itself with
  `tempo reword`, as the skill says, so the answer shows in the box the user reads.
- The empty screen says "Nothing is waiting on you" (or "No answered questions"), without the
  project name, which the header already shows.
- The empty screen's width follows the font (70 characters) instead of a fixed 460 pixels,
  so its short sentences no longer break in the middle.

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
