# Contributing to Tempo

Issues and pull requests are welcome. New pull requests start from a short template. Please
run `bin/ci` before opening one, and add a test for any change in behaviour. GitHub runs the
same checks on every pull request, on Python 3.9 and 3.13.

## Running the checks

```bash
bin/ci                                                        # every check below, then all tests
TEMPO_DB=/tmp/questions.db TEMPO_PORT=22417 bin/tempo serve  # the page on a scratch database
```

Always set `TEMPO_PORT` together with `TEMPO_DB`. With only `TEMPO_DB` set, the page uses the
real port, 2204, so it would take the place of your real page, or fail to start while that
one runs. Any port from 22100 to 22999 is safe.

`bin/ci` installs the pinned tools from `requirements-dev.txt` into `.venv` on its first run.
It rebuilds `.venv` when its Python no longer runs (after the folder was moved, for example),
and Biome and Ruff skip `.claude/`, so git worktrees kept there do not break the checks.
It needs [ShellCheck](https://www.shellcheck.net) (`brew install shellcheck`) and
[Node.js](https://nodejs.org) 18 or newer (`brew install node`), which runs the checks for
the page's script, `tempo/page.js`. `npx` fetches the pinned Biome version on first use.

| Check | Tool |
| --- | --- |
| Lint: errors, bugs, import order, simpler forms, security rules | [Ruff](https://docs.astral.sh/ruff/) |
| Formatting | `ruff format` |
| Security scan | [Bandit](https://bandit.readthedocs.io) |
| Types: every function in `tempo/`, `bin/release` and `bin/check-version` has type hints, checked against Python 3.9 | [mypy](https://mypy.readthedocs.io) |
| Shell scripts: every `bin/` script that starts with `#!/bin/sh` | [ShellCheck](https://www.shellcheck.net) |
| Tests | `unittest`, from the standard library |
| Test coverage of `tempo/`, report only: a per-file summary at the end, never a failure | [coverage.py](https://coverage.readthedocs.io) |
| Lint the page's script (`tempo/page.js`) and its tests | [Biome](https://biomejs.dev), recommended rules |
| Tests for the page's text renderer | `node --test`, built into Node.js |
| The page's main flows in a real browser | `unittest`, driving headless Google Chrome |

The Python tools are configured in `pyproject.toml`, Biome in `biome.json`. When a rule has to be silenced on one line, the
line says why.

Use a scratch database (`TEMPO_DB`) and another port (`TEMPO_PORT`) when you try changes by
hand, so your real questions stay untouched. Code run from a worktree, or from the main
checkout on any branch but `main`, does this by itself: it uses `.tempo-dev/questions.db`
inside its folder and a port of its own.

## Changing the database

A Claude session keeps the Tempo it started with, so an older Tempo may still be reading
and writing the database after a newer one has changed it. The database's `user_version`
counts the numbered steps that changed it, and Tempo refuses a database whose number is
higher than it knows. So a change raises that number only when it has to.

- **Compatible change**: older code can ignore it. Examples are a new table, a new index
  and a new column that may be empty. Add it to `SCHEMA` in `tempo/store/schema.py`; for a
  new column, also add a step to `ADDITIONS` that calls `_add_column`. Both run on every open
  and add only what is missing, and `user_version` stays the same.
- **Incompatible change**: older code would get it wrong. Examples are a renamed or dropped
  column, a new required value, or a value whose meaning changed. Add a step at the end of
  `MIGRATIONS`. It runs once and raises `user_version`, and older sessions then stop with a
  message asking to restart them.

Prefer a compatible change when one can do the job. `tests/test_previous_release.py`
checks that the last release still works on a database the current code has opened.

## Page tests in a browser

`tests/test_page.py` runs the page in headless Google Chrome against the real server code
and a throwaway database: answering, asking for more, a reword under a typed note,
reopening, the timed refresh keeping focus, a server restart keeping the draft, the "new"
dots, a link to a missing question, long words wrapping, labels for screen readers, and
Settings keeping the keyboard focus. It also checks that the page's colours meet the
contrast minimum of WCAG (the Web Content Accessibility Guidelines) in both themes. `bin/ci` runs them with the other tests; to run only
them, use `python3 -m unittest tests.test_page`. They take about two seconds. They skip,
saying why, when Chrome isn't installed; set `CHROME` to the browser's path if it isn't
found on its own. GitHub's Ubuntu runners have Chrome, so they always run there.

## Screenshots and demo GIF

`bin/media` makes the README's pictures again, `docs/screenshot-light.png`,
`docs/screenshot-dark.png` and `docs/demo.gif`, from made-up questions in a throwaway
database, so run it after a change to the page. It needs Google Chrome and
[ffmpeg](https://ffmpeg.org) (`brew install ffmpeg`), and never touches your own questions.

## Layout

```
.claude-plugin/   plugin and marketplace manifests
bin/              tempo (the command), oq (its old name), ci, check-version, media and
                  release
docs/             reference, screenshots and the demo GIF
hooks/            hook configuration
tempo/
  cli.py          the tempo command and the hooks
  server.py       the page's web server
  page.html       the page, with its styles
  page.js         the page's script
  slash.py        what the slash commands run
  log.py          the page log, server.log
  store/          the SQLite database: questions, projects, settings and delivery
  tui/            tempo tui, the full-screen terminal view
skills/           five skills: tempo, which teaches Claude the workflow, and one for each
                  slash command (list, show, answer, dive)
tests/            Python tests (test_page.py drives the page in Chrome), and page.test.js
                  for the page's script
```

## Releasing

Installed copies only update when the version changes. A release is one pull request that
raises `version` in both `.claude-plugin/plugin.json` and `pyproject.toml`, following
[Semantic Versioning](https://semver.org), and renames the `Unreleased` section of
`CHANGELOG.md` to the new version and today's date.

`bin/release` makes that pull request. Run it in the main checkout, on `main`:

```sh
bin/release 0.6.0
```

It first refuses, saying why, when the version isn't `x.y.z` or isn't higher than the current
one, when `main` has uncommitted changes or isn't the same as `origin/main`, or when
`Unreleased` has nothing in it. Then it makes the branch `release/0.6.0`, edits the three
files, commits "Release 0.6.0", runs `bin/ci` in a clean copy (a temporary git worktree outside
this folder, so the worktrees under `.claude/` can't interfere), pushes, opens the pull request
and turns on auto-merge. It prints each step and stops at the first that fails; if `bin/ci`
fails, you are left on the release branch. Fix the problem on `main` as usual, then run
`git switch main`, `git branch -D release/0.6.0` and `bin/release 0.6.0` again.

It does not merge, update the installed plugin or restart the page. GitHub merges the pull
request once its CI passes; the commands for afterwards are printed at the end.

`bin/check-version`, run by `bin/ci`, fails when the two versions differ or the changelog has
no section for the version. When the pull request merges, the Release workflow creates the tag
`vX.Y.Z` and a GitHub release whose notes are that changelog section. Users then run
`claude plugin marketplace update tempo` and `claude plugin update tempo@tempo`.

Dependabot opens grouped pull requests once a week for newer GitHub Actions and the tools in
`requirements-dev.txt`.
