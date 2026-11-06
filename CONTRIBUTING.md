# Contributing to Tempo

Issues and pull requests are welcome. Please run `bin/ci` before opening a pull request, and
add a test for any change in behaviour. GitHub runs the same checks on every pull request, on
Python 3.9 and 3.13.

## Running the checks

```bash
bin/ci                                      # every check below, then all tests
TEMPO_DB=/tmp/questions.db bin/tempo serve  # run the page on a scratch database
```

`bin/ci` installs the pinned tools from `requirements-dev.txt` into `.venv` on its first run.
It needs [ShellCheck](https://www.shellcheck.net) (`brew install shellcheck`).

| Check | Tool |
| --- | --- |
| Lint: errors, bugs, import order, simpler forms, security rules | [Ruff](https://docs.astral.sh/ruff/) |
| Formatting | `ruff format` |
| Security scan | [Bandit](https://bandit.readthedocs.io) |
| Shell scripts | [ShellCheck](https://www.shellcheck.net) |
| Tests | `unittest`, from the standard library |

The tools are configured in `pyproject.toml`. When a rule has to be silenced on one line, the
line says why.

Use a scratch database (`TEMPO_DB`) and another port (`TEMPO_PORT`) when you try changes by
hand, so your real questions stay untouched.

## Layout

```
.claude-plugin/   plugin and marketplace manifests
bin/              tempo (the command), oq (its old name), ci and check-version
docs/             reference, screenshots and the demo GIF
hooks/            hook configuration
tempo/            store (SQLite), cli (tempo), server and page
skills/           the skill that teaches Claude the workflow
tests/
```

## Releasing

Installed copies only update when the version changes. To release changes users should get,
do all of this in one pull request:

1. Raise `version` in both `.claude-plugin/plugin.json` and `pyproject.toml`, following
   [Semantic Versioning](https://semver.org).
2. In `CHANGELOG.md`, rename the `Unreleased` section to the new version and today's date,
   with one short line per change.

`bin/check-version`, run by `bin/ci`, fails when the two versions differ or the changelog has
no section for the version. When the pull request merges, the Release workflow creates the tag
`vX.Y.Z` and a GitHub release whose notes are that changelog section. Users then run
`claude plugin marketplace update tempo` and `claude plugin update tempo@tempo`.

Dependabot opens grouped pull requests once a week for newer GitHub Actions and the tools in
`requirements-dev.txt`.
