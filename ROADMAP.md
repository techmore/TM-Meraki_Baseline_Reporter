# TM Meraki Reporter Roadmap

This project is currently functional as a Python reporting pipeline. The immediate focus is Phase 4: reporting improvements and clearer run modes.

## Current State

- `./run.sh` is the main pipeline runner.
- Python dependencies install cleanly into `.venv`.
- Tests pass locally: `80 passed`.
- Report-only generation works from existing `backups/`.
- `.env` is gitignored and should remain local because it may contain `MERAKI_API_KEY`.
- Clean-history repository is published at `https://github.com/techmore/TM-Meraki_Baseline_Reporter.git`.
- `legacy/` contains historical scripts that should not be run in production.
- `docs/cis-meraki-reference.md` preserves the useful upstream CIS mapping as reference material.
- Generated reports now include named aliases like `SITE_NAME_Complete_Report_YYYY-MM-DD.pdf`.
- Ollama review unloads the active model after each generation pass to reduce idle RAM usage.

## Phase 1: Stabilize The Existing Python App - Complete

- ~~Keep the core implementation in Python.~~
- ~~Use `install.sh` for first-time setup.~~
- ~~Keep `.env` local and untracked.~~
- ~~Prefer `./run.sh --report-only --no-ai-review --no-open` for safe local report regeneration.~~
- ~~Use `./run.sh --model gemma4:e2b` or the default `gemma4:e2b` for lower-RAM Ollama review.~~
- ~~Continue using `pytest` as the main regression check.~~

## Phase 2: Repository Hygiene Before Publishing - Complete

- ~~Review untracked local files before any commit:~~
  - `.claude/`
  - `docs/reporting/draft-reporting-guide.md`
  - `docs/reporting/network-assessment-report-template.docx`
- ~~Confirm no live credentials are tracked:~~
  - `.env` must stay ignored.
  - generated `backups/` should stay ignored unless a sanitized sample is intentionally added.
- ~~Add the GitHub remote only after confirming the commit scope.~~
- ~~Prefer a clean-history import into `https://github.com/techmore/TM-Meraki_Baseline_Reporter.git` rather than pushing the old history.~~
- ~~Push only code, docs, tests, and sanitized examples.~~

## Phase 3: Clean Report Project Shape - Complete

- ~~Keep `legacy/` temporarily for reference.~~
- ~~Move historical scripts out of the default workflow.~~
- ~~Document the current project shape and cleanup rules in `docs/project-shape.md`.~~
- ~~Move report-writing reference material under `docs/reporting/`.~~
- ~~Move historical audit notes under `docs/repository-audit.md`.~~
- ~~Decide whether to remove `legacy/` after the current report output is reproducible from the modern pipeline.~~
- ~~Keep `sample_data/` and `tests/fixtures/` as sanitized examples for development.~~
- ~~Check `iramku/Meraki-Security-Benchmark:Meraki-Security-Audit` periodically for conceptual updates, but port useful ideas manually rather than merging the branch.~~
- ~~Add `python -m reporting` as the clearer package entrypoint while retaining compatibility wrappers.~~

## Phase 4: Reporting Improvements - Active

- ~~Add a small health-check command for validating `.env`, dependencies, Ollama availability, and backup directories.~~
- ~~Add clearer report modes:~~
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
- Push to `https://github.com/techmore/TM-Meraki_Baseline_Reporter.git` after verification.
