# Repository Audit Issues

## Repository Overview
Purpose: Generate Meraki network health reports (HTML/PDF) from Meraki API backups, with optional Ollama review and a shell pipeline.

Architecture: Single-script pipeline (bash) orchestrates Python scripts for backup, summarization, LLM review, and report rendering.

Primary modules:
- `meraki_backup.py` (data acquisition + recommendations)
- `report_generator.py` (HTML/PDF rendering)
- `merge_recommendations.py`
- `ollama_review.py`
- `run.sh`

Entrypoints:
- `run.sh` (pipeline)
- `meraki_backup.py` (backup)
- `report_generator.py` (report)

Critical path: Meraki API → `backups/` JSON → `recommendations.md` → `report_generator.py` → `report.html`/`report.pdf`

---

## Identified Issues (GitHub issue format)

1. **Title:** [SECURITY] API key committed in .env  
   **Type:** security  
   **Severity:** critical  
   **Area:** repo root  
   **Description:** A real Meraki API key is present in version control. This is a direct credential leak and allows unauthorized access to Meraki organizations.  
   **Evidence:** `.env:1`  
   **Proposed Fix:** Rotate the key immediately, remove `.env` from the repo history, and store secrets outside VCS.  
   **Implementation Notes:** Add a pre-commit secret scanner (e.g., gitleaks) and ensure `.env` is always ignored.

2. **Title:** [SECURITY] Backups and org data are stored in-repo  
   **Type:** security  
   **Severity:** high  
   **Area:** `backups/` and sample dumps  
   **Description:** Backups contain sensitive org names, inventory, client data, and reports. The repository currently includes these directories.  
   **Evidence:** `backups/` and `meraki_backup_sample_20260311_204000/`  
   **Proposed Fix:** Remove sensitive data from VCS and add ignores for `backups/` and sample dumps or replace with sanitized fixtures.  
   **Implementation Notes:** Provide a `sample_data/` set with anonymized JSON for demos/tests.

3. **Title:** [SECURITY] HTML/JS injection in report output  
   **Type:** security  
   **Severity:** high  
   **Area:** `report_generator.py`  
   **Description:** Data from Meraki API and `recommendations.md` is inserted into HTML without escaping. If any field contains HTML, it will execute when the report is opened.  
   **Evidence:** `report_generator.py` uses raw f-strings in many sections and `md_to_html()` does not escape content.  
   **Proposed Fix:** Escape all dynamic content by default and only allow a small safe HTML subset for markdown.  
   **Implementation Notes:** Use an HTML sanitizer (e.g., `bleach`) or a markdown renderer with safe mode.

4. **Title:** Hardcoded absolute paths break portability  
   **Type:** refactor  
   **Severity:** medium  
   **Area:** `report_generator.py`, `merge_recommendations.py`, `ollama_review.py`  
   **Description:** Multiple scripts hardcode `/Users/seandolbec/Projects/Meraki-2026_planning` which breaks execution on any other machine or when the repo moves.  
   **Evidence:** `report_generator.py:8`, `merge_recommendations.py:5`, `ollama_review.py:15`  
   **Proposed Fix:** Derive base paths from `__file__` or accept a CLI `--base-dir`/`--backups-dir` override.  
   **Implementation Notes:** Centralize path resolution in a shared module.

5. **Title:** wkhtmltopdf invocation uses `os.system` with unescaped paths  
   **Type:** bug  
   **Severity:** medium  
   **Area:** `report_generator.py`  
   **Description:** `os.system` with string interpolation fails on paths with spaces and is a shell injection risk if paths are attacker-controlled.  
   **Evidence:** `report_generator.py` (`write_pdf`) uses `os.system` for wkhtmltopdf.  
   **Proposed Fix:** Use `subprocess.run([wk, html_path, pdf_path], check=True)` and capture errors.  
   **Implementation Notes:** Return error details to the caller for clearer diagnostics.

6. **Title:** Pipeline is macOS-only when auto-opening reports  
   **Type:** enhancement  
   **Severity:** medium  
   **Area:** `run.sh`  
   **Description:** `run.sh` uses the macOS `open` command to launch reports, which fails on Linux/Windows and limits CI usage.  
   **Evidence:** `run.sh` uses `open` in the report auto-open block.  
   **Proposed Fix:** Detect OS and use `xdg-open` (Linux) or `start` (Windows), or add a `--no-open` flag.  
   **Implementation Notes:** Provide a headless mode for CI.

7. **Title:** Chart rendering functions are unused and one emits invalid HTML  
   **Type:** bug  
   **Severity:** medium  
   **Area:** `report_generator.py`  
   **Description:** `render_bar_chart`/`render_pie_chart`/`render_line_chart` are never used, and `render_bar_chart` emits `class=\"bar-row\"` (literal backslashes). README claims multiple chart types.  
   **Evidence:** `report_generator.py` `render_*` functions, no call sites.  
   **Proposed Fix:** Either integrate charts into report sections or remove dead code. Fix HTML escaping in `render_bar_chart`.  
   **Implementation Notes:** Add chart sections for device distribution/PoE/channel utilization if keeping them.

8. **Title:** Report generator is a monolith (hard to maintain and test)  
   **Type:** refactor  
   **Severity:** medium  
   **Area:** `report_generator.py`  
   **Description:** Rendering logic, data ingestion, and CSS are all in one large file (~2800 lines), making maintenance and testing difficult.  
   **Evidence:** `report_generator.py`  
   **Proposed Fix:** Split into modules: data loading, data modeling, HTML templates, and renderers.  
   **Implementation Notes:** Consider Jinja2 templates and a dedicated `styles.css`.

9. **Title:** No dependency manifest or pinned versions  
   **Type:** documentation  
   **Severity:** medium  
   **Area:** repo root  
   **Description:** The project references WeasyPrint and wkhtmltopdf but has no `requirements.txt` or `pyproject.toml`. Reproducibility and onboarding are weak.  
   **Evidence:** `README.md` Requirements section.  
   **Proposed Fix:** Add `requirements.txt` or `pyproject.toml` with pinned versions and external dependency notes.  
   **Implementation Notes:** Document platform-specific deps (wkhtmltopdf) and PDF fallback behavior.

10. **Title:** Rate-limit handling can loop indefinitely and ignores `Retry-After`  
    **Type:** bug  
    **Severity:** medium  
    **Area:** `meraki_backup.py`  
    **Description:** On HTTP 429, the code sleeps a fixed 2 seconds and retries without max attempts or `Retry-After`, risking infinite loops and API bans.  
    **Evidence:** `meraki_backup.py` `paged_get` retry block.  
    **Proposed Fix:** Respect `Retry-After`, add exponential backoff and max retries per request.  
    **Implementation Notes:** Log and continue on non-critical endpoints after max retries.

11. **Title:** Silent JSON parse failures hide data loss  
    **Type:** maintainability  
    **Severity:** low  
    **Area:** `report_generator.py`  
    **Description:** `load_json()` swallows all exceptions and returns `None`, making data corruption invisible and producing misleading reports.  
    **Evidence:** `report_generator.py` `load_json`.  
    **Proposed Fix:** Log parsing errors and display a warning section in the report when inputs are missing or invalid.  
    **Implementation Notes:** Track a warnings list and render it in the report.

12. **Title:** No automated tests or CI coverage  
    **Type:** enhancement  
    **Severity:** low  
    **Area:** repo root  
    **Description:** There are no tests or CI workflows. Regression risk is high for report layout, parsing, and API data handling.  
    **Evidence:** No `tests/` directory or CI config present.  
    **Proposed Fix:** Add unit tests for parsers/renderers and a minimal CI pipeline (lint + tests).  
    **Implementation Notes:** Use sample fixtures in `sample_data/` for deterministic tests.

13. **Title:** Remote Google Fonts in HTML can fail in offline PDF generation  
    **Type:** optimization  
    **Severity:** low  
    **Area:** `report_generator.py`  
    **Description:** PDF generation relies on external font URLs, which may fail or slow builds in offline or locked-down environments.  
    **Evidence:** `report_generator.py` Google Fonts link in `build_html`.  
    **Proposed Fix:** Bundle fonts locally and reference local paths.  
    **Implementation Notes:** Add a `fonts/` folder and configure WeasyPrint to load local assets.

---

## Initiative Groups

**Security Hardening**
- [SECURITY] API key committed in .env
- [SECURITY] Backups and org data are stored in-repo
- [SECURITY] HTML/JS injection in report output

**Reliability & API Resilience**
- Rate-limit handling can loop indefinitely and ignores Retry-After
- Silent JSON parse failures hide data loss

**Portability & Packaging**
- Hardcoded absolute paths break portability
- Pipeline is macOS-only when auto-opening reports
- No dependency manifest or pinned versions
- Remote Google Fonts in HTML can fail in offline PDF generation

**Report Engine Refactor**
- wkhtmltopdf invocation uses os.system with unescaped paths
- Chart rendering functions are unused and one emits invalid HTML
- Report generator is a monolith (hard to maintain and test)

**Developer Experience**
- No automated tests or CI coverage

---

## Implementation Roadmap (Priority Order)

1. Fix credential exposure and remove sensitive backups.
2. Add output sanitization for HTML generation.
3. Harden API retry logic.
4. Remove hardcoded paths; make base dirs configurable.
5. Replace os.system with subprocess.run for wkhtmltopdf.
6. Provide dependency manifest and install docs.
7. Add CI and tests for parsers/renderers.
8. Refactor report generator into modules/templates.
9. Either integrate or remove chart renderers and fix HTML bug.
10. Add cross-platform report opening and offline font bundling.

---

## High Impact Refactors

1. Modularize `report_generator.py` into `data/`, `render/`, and `templates/` with Jinja2.
2. Centralize configuration (base dir, backups dir, output dir, model) into a config module or CLI flags.
3. Introduce a proper dependency manifest and reproducible setup script.
4. Add a safe HTML rendering layer (escaping + markdown sanitizer).
5. Build a small test suite around sample data to lock down report correctness.
6. Replace shell calls with `subprocess` and structured error handling.
7. Normalize API fetch logic with backoff and maximum retries.

---

## V1 Release Plan

### Release Goal

Ship a unified Meraki assessment tool that can:

- collect Meraki org data into structured backups
- generate recommendations and PDF/HTML reports from those backups
- include baseline MX security findings from the upstream baseline project
- run in either live API mode or report-only mode

### Scope For V1

- upstream baseline branch merged into `enhanced-reporting`
- baseline appliance checks collected in `meraki_backup.py`
- baseline section rendered in `report_generator.py`
- `run.sh --report-only` for offline rendering from existing backups
- `.gitignore` hardened for secrets, backups, and generated artifacts

### Must-Fix Before Tagging `v1.0.0`

1. Rotate the leaked Meraki API key and purge any exposed secrets from git history.
2. Remove committed live backup data and replace it with sanitized fixtures.
3. Add dependency management (`requirements.txt` or `pyproject.toml`).
4. Add at least smoke-test CI for the report-only pipeline.
5. Fix HTML escaping and markdown sanitization in report generation.
6. Improve API retry handling with bounded retries and `Retry-After` support.

### Should-Fix Shortly After V1

1. Break `report_generator.py` into modules/templates.
2. Replace remote Google Fonts with local assets.
3. Improve `merge_recommendations.py` to emit a warning summary for orgs missing `recommendations.md`.
4. Add labels/milestones to the created GitHub issues and track them against a `v1.0.0` milestone.

### Proposed Milestones

1. `v1-security-hardening`
2. `v1-runtime-stability`
3. `v1-release-packaging`
4. `v1-post-release-refactor`
