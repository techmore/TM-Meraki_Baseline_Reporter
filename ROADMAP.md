# TM Meraki Reporter Roadmap

This project is currently functional as a Python reporting pipeline. The immediate focus is Phase 4: reporting improvements and clearer run modes.

## Current State

- `./run.sh` is the main pipeline runner.
- Python dependencies install cleanly into `.venv`.
- Tests pass locally: `115 passed`.
- Report-only generation works from existing `backups/`.
- `run.sh` now separates generated report deliverables into `reports/` while leaving raw backup data in `backups/`.
- `.env` is gitignored and should remain local because it may contain `MERAKI_API_KEY`.
- Clean-history repository is published at `https://github.com/techmore/TM-Meraki_Baseline_Reporter.git`.
- `legacy/` contains historical scripts that should not be run in production.
- `docs/cis-meraki-reference.md` preserves the useful upstream CIS mapping as reference material.
- Generated reports now include named aliases like `SITE_NAME_Complete_Report_YYYY-MM-DD.pdf`.
- Ollama review unloads the active model after each generation pass to reduce idle RAM usage.
- Deterministic report generation is available with `./run.sh --fixed-now ...`,
  `python -m reporting --fixed-now ...`, or `MERAKI_REPORT_FIXED_NOW`.
- `./run.sh` remains the full default pipeline and now validates the generated latest
  report deliverables after report generation, including a latest report manifest
  and static HTML index.

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
- ~~Improve AI review controls:~~
  - ~~default low-RAM model~~
  - ~~explicit model override~~
  - ~~no-AI mode for deterministic runs~~
- ~~Keep report rendering deterministic enough that tests can catch regressions.~~
- ~~Increase table-of-contents density and make TOC titles link to report sections.~~
- ~~Add report page furniture:~~
  - ~~header with `TM Meraki Baseline`~~
  - ~~page `current / total` footer~~
  - ~~release number based on the report release date~~
  - ~~end-of-report page~~
- ~~Fix switch port issue classification so disconnected/unused ports are not reported as issues.~~
- ~~Improve switch identification in issue tables by showing switch labels alongside serial numbers.~~
- ~~Investigate why Client Analysis is blank for current backups and add fallback rendering from `clients_overview.json`.~~
- ~~Investigate blank Switch Deep Dive sections and improve fallback messaging when port telemetry is missing.~~
- ~~Increase switch deep-dive table density so the wide port table fits PDF pages.~~
- ~~Add firmware status/current-vs-available rendering from Meraki firmware upgrade data.~~
- ~~Highlight EOL/EOS inventory: red when end of support is within 2 years, yellow when announced farther out.~~
- ~~Further compress switch deep-dive table font, padding, and badge density for PDF fit.~~
- ~~Replace heuristic UniFi comparison pricing with maintained JSON-backed pricing/equivalent references for Meraki and UniFi.~~
- ~~Add Meraki hardware capability data, including PoE budgets, from a maintained JSON reference instead of estimates.~~
- ~~Review the proposed K-12 VLAN structure and add it as a supplemental/reference section if it fits the report audience.~~
- ~~Clean up completed-report quality issues: suppress benign mesh 404s, collapse disabled default SSIDs, remove empty AP model cells, fix 100 Gbps speed labeling, filter disconnected deep-dive port badges, and avoid false "no significant issues" messages.~~
- ~~Replace unreliable wireless-only client collection with network-wide client collection and report wired/wireless client detail coverage.~~
- ~~Separate generated report deliverables into `reports/` and keep `backups/` focused on raw collection data.~~
- ~~Add PDF-only output mode so routine runs do not retain generated HTML unless requested.~~
- ~~Add a final report inventory check so missing generated deliverables fail the run visibly.~~
- ~~Write `reports/latest/report_inventory.json` so the generated report set can be audited without browsing folders.~~
- ~~Write `reports/latest/index.html` as a static report index with links to each latest deliverable.~~

## Phase 5: Optional Interfaces

- Do not rewrite to npm unless there is a concrete need for a web UI or Node deployment.
- If desired later, add a minimal `package.json` as a command wrapper only.
- Keep Python as the source of truth for Meraki collection, report generation, and tests.

## Phase 6: UniFi / Ubiquiti Reporting - Started

- ~~Add a separate `./unifi/run.sh` runner so UniFi work does not regress the
  Meraki pipeline.~~
- ~~Support both official Site Manager API collection and local UniFi Network
  Application Integration API collection.~~
- ~~Save raw UniFi JSON backups separately under `unifi/backups/`.~~
- ~~Generate a first-pass UniFi baseline report under `unifi/reports/`.~~
- ~~Treat local Network Application endpoint gaps as reportable coverage
  findings while we learn the exact controller version and API surface.~~
- ~~Add saved site profiles in `unifi/.env` and `./unifi/run.sh --all-sites`
  for multi-site runs.~~
- ~~Write a top-level UniFi multi-site report index for saved profile runs.~~
- ~~Add per-profile network size and coverage metrics to the UniFi multi-site
  manifest/index.~~
- ~~Write UniFi report inventory data and a static `index.html` for generated
  outputs.~~
- ~~Improve UniFi executive summary language once more live sites are captured.~~
- ~~Document UniFi interface telemetry coverage so reports distinguish advertised
  port/radio capability flags from detailed per-port/per-radio metrics.~~
- ~~Probe likely UniFi port/radio telemetry endpoints during collection and save
  structured coverage evidence in the backup/report.~~
- ~~Add a UniFi configuration backup completeness matrix showing captured,
  captured-empty, and unsupported endpoint coverage.~~
- ~~Split UniFi per-device telemetry probes by sampled AP, switch, and gateway
  roles so future exposed endpoints can be attributed to the right hardware.~~
- ~~Clarify UniFi hardware planning so retained active gear is not counted as
  unpriced refresh scope, and summarize refresh/retain/excluded actions.~~
- ~~Promote high client concentration on one AP/switch into UniFi executive
  risks, priorities, and implementation planning.~~
- ~~Promote flat DEFAULT client access policy usage into UniFi executive,
  security baseline, and implementation planning sections.~~
- ~~Promote missing UniFi subnet/gateway/DHCP fields into executive,
  confidence, security baseline, and implementation planning sections.~~
- Add deeper UniFi switch/AP port and radio telemetry when the controller API
  exposes it.

## Release Checklist

- Run `./install.sh`.
- Run `.venv/bin/python -m pytest -q`.
- Run `./run.sh --report-only --no-ai-review --no-open`.
- Check `git status --short`.
- Confirm `.env` and `backups/` are not staged.
- Confirm `reports/` is not staged unless a sanitized sample is intentionally added.
- Confirm generated or customer-specific report files are not staged unless sanitized.
- Commit the surgical changes.
- Push to `https://github.com/techmore/TM-Meraki_Baseline_Reporter.git` after verification.
