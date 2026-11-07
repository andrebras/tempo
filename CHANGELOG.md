# Changelog

All notable changes to Tempo are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- A pull request template with the usual sections and a short checklist.

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
