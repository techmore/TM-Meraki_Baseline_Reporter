# TM Meraki Reporter Roadmap

This project is currently functional as a Python reporting pipeline. The near-term goal is to keep it stable, protect local credentials, and make cleanup incremental instead of rewriting working pieces.

## Current State

- `./run.sh` is the main pipeline runner.
- Python dependencies install cleanly into `.venv`.
- Tests pass locally: `75 passed`.
- Report-only generation works from existing `backups/`.
- `.env` is gitignored and should remain local because it may contain `MERAKI_API_KEY`.
- The current repo history may contain old secrets based on `ISSUES.md`; do not push this history to a new public repository without cleaning or rebuilding history.
- `legacy/` contains historical scripts that should not be run in production.
- `docs/cis-meraki-reference.md` preserves the useful upstream CIS mapping as reference material.

## Phase 1: Stabilize The Existing Python App

- Keep the core implementation in Python.
- Use `install.sh` for first-time setup.
- Keep `.env` local and untracked.
- Prefer `./run.sh --report-only --no-ai-review --no-open` for safe local report regeneration.
- Use `./run.sh --model gemma4:e2b` or the default `gemma4:e2b` for lower-RAM Ollama review.
- Continue using `pytest` as the main regression check.

## Phase 2: Repository Hygiene Before Publishing

- Review untracked local files before any commit:
  - `.claude/`
  - `draft-reporting-guide.md`
  - `network-assessment-report-template.docx`
- Confirm no live credentials are tracked:
  - `.env` must stay ignored.
  - generated `backups/` should stay ignored unless a sanitized sample is intentionally added.
- Add the GitHub remote only after confirming the commit scope.
- Prefer a clean-history import into `https://github.com/techmore/TM-Meraki_Reporter.git` rather than pushing the current history.
- Push only code, docs, tests, and sanitized examples.

## Phase 3: Clean Report Project Shape

- Keep `legacy/` temporarily for reference.
- Move historical scripts out of the default workflow.
- Decide whether to remove `legacy/` after the current report output is reproducible from the modern pipeline.
- Keep `sample_data/` and `tests/fixtures/` as sanitized examples for development.
- Check `iramku/Meraki-Security-Benchmark:Meraki-Security-Audit` periodically for conceptual updates, but port useful ideas manually rather than merging the branch.
- Consider replacing ad hoc top-level scripts with a clearer Python package entrypoint after the next stable report.

## Phase 4: Reporting Improvements

- Add a small health-check command for validating `.env`, dependencies, Ollama availability, and backup directories.
- Add clearer report modes:
  - full API collection
  - report-only from existing backups
  - fixture/demo report generation
- Improve AI review controls:
  - default low-RAM model
  - explicit model override
  - no-AI mode for deterministic runs
- Keep report rendering deterministic enough that tests can catch regressions.

## Phase 5: Optional Interfaces

- Do not rewrite to npm unless there is a concrete need for a web UI or Node deployment.
- If desired later, add a minimal `package.json` as a command wrapper only.
- Keep Python as the source of truth for Meraki collection, report generation, and tests.

## Release Checklist

- Run `./install.sh`.
- Run `.venv/bin/python -m pytest -q`.
- Run `./run.sh --report-only --no-ai-review --no-open`.
- Check `git status --short`.
- Confirm `.env` and `backups/` are not staged.
- Confirm generated or customer-specific report files are not staged unless sanitized.
- Commit the surgical changes.
- For the new repository, create a clean branch or fresh clone that excludes old history.
- Add remote `https://github.com/techmore/TM-Meraki_Reporter.git` only in that clean working copy.
- Push to a branch first, then merge intentionally.
