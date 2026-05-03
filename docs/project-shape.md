# Project Shape

This repository is intentionally staying Python-first. The current shape keeps
the working pipeline obvious while preserving compatibility wrappers for older
local workflows.

## Active Entry Points

- `./run.sh` is the main user-facing runner.
- `./install.sh` prepares a local `.venv` and checks common dependencies.
- `python -m reporting` is the canonical direct report-generation command for
  existing backup data.
- `python -m reporting --source-dir ... --output-dir ...` renders one explicit
  source directory, which powers the fixture demo mode.
- `python -m reporting.health` runs read-only environment checks.
- `report_generator.py` is a compatibility entrypoint that calls
  `python -m reporting`.
- `scripts/generate_report.py` is also retained as a compatibility wrapper.

## Active Pipeline Stages

- `meraki_query.py` checks Meraki API access and organization discovery.
- `meraki_backup.py` collects API data into `backups/<org>/`.
- `merge_recommendations.py` combines per-org recommendations.
- `ollama_review.py` optionally enriches recommendations with a local Ollama
  model and unloads the model after each generation pass.
- `reporting/` contains report rendering, HTML/PDF shell helpers, section
  builders, and topology helpers.

## Reference Material

- `legacy/` contains older baseline/reporting scripts. They are not part of the
  production workflow and should not be called by `run.sh`.
- `docs/cis-meraki-reference.md` preserves the upstream CIS mapping reference.
- `docs/reporting/draft-reporting-guide.md` and
  `docs/reporting/network-assessment-report-template.docx` are retained as
  report-writing references for now.
- `docs/repository-audit.md` is the historical cleanup audit snapshot. It is
  useful context, but its issue list is not fully current.
- Older one-off helper scripts that are not part of the pipeline live in
  `legacy/`.

## Test And Sample Data

- `tests/fixtures/` is the primary sanitized fixture set used by automated
  tests.
- `sample_data/` is for small development examples that can be committed safely.
- `backups/` is generated runtime output and must remain untracked.
- See `sample_data/README.md` and `tests/fixtures/README.md` before adding new
  sample inputs.

## Cleanup Rules

- Keep `.env`, `backups/`, generated reports, caches, and local virtual
  environments out of git.
- Do not move active top-level pipeline scripts until the corresponding
  `run.sh` behavior and tests are updated in the same change.
- Remove or archive `legacy/` only after current report output is reproducible
  from the modern pipeline without consulting those files.
- Port useful upstream ideas manually from
  `iramku/Meraki-Security-Benchmark:Meraki-Security-Audit`; do not merge that
  branch wholesale.
