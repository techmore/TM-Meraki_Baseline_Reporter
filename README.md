# Meraki Security Baseline and Report Suite

A reporting pipeline that collects Meraki org data, generates network health and security recommendations, optionally enriches them with a local LLM review, and renders HTML/PDF reports.

## Components

| Script | Role |
|--------|------|
| `meraki_backup.py` | Pulls Meraki API data into per-org backup directories |
| `merge_recommendations.py` | Combines per-org recommendation files |
| `ollama_review.py` | Optional local LLM review stage |
| `python -m reporting` | Direct report generation from existing backup data |
| `report_generator.py` | Compatibility wrapper for report generation |
| `report_inventory.py` | Validates the expected latest report deliverables after generation |
| `run.sh` | Full pipeline orchestrator |
| `legacy/` | Original MX baseline scripts (reference only) |
| `docs/cis-meraki-reference.md` | CIS Controls to Meraki reference mapping |
| `docs/reporting/` | Report-writing guide and `.docx` template references |
| `docs/repository-audit.md` | Historical cleanup audit snapshot |

## Lineage

This project started from Meraki security baseline work and is now a broader
Meraki reporting pipeline. The historical upstream branch
[`iramku/Meraki-Security-Benchmark:Meraki-Security-Audit`](https://github.com/iramku/Meraki-Security-Benchmark/tree/Meraki-Security-Audit)
is kept as a reference for MX baseline and CIS mapping ideas, but this repository
has diverged substantially and should not merge that branch wholesale.

## Quick Start

1. Run the installer:

```bash
./install.sh
```

2. Set `MERAKI_API_KEY` in `.env`.
3. Run the full pipeline:

```bash
./run.sh
```

Check the local environment without running the pipeline:

```bash
./run.sh --health-check
./run.sh --report-only --health-check
```

Generate a demo report from sanitized fixtures without Meraki API access:

```bash
./run.sh --demo-report --no-open
./run.sh --demo-report --fixed-now 2026-05-02T21:30:00 --no-open
```

Optional — specify a local Ollama model for AI-enhanced recommendations:

```bash
./run.sh --model gemma4:e2b
```

The default Ollama model is `gemma4:e2b`, chosen for lower RAM usage. Pull it
before enabling AI review:

```bash
ollama pull gemma4:e2b
```

## Output

`./run.sh` keeps raw Meraki backup data in `backups/<org>/` and writes generated
shareable reports to `reports/` (both gitignored). By default, `./run.sh` runs
the full pipeline: Meraki query, backup, recommendation merge, optional AI review,
report generation, and a final deliverable inventory check.

- `recommendations.md` — per-org findings and recommendations
- `backups/master_recommendations.md` — combined across all orgs
- `backups/recommendations_ai_enhanced.md` — LLM-reviewed version
- `reports/<org>/<timestamp>/SITE_NAME_Complete_Report_YYYY-MM-DD.pdf` — run-specific full report
- `reports/<org>/<timestamp>/SITE_NAME_Executive_Summary_Report_YYYY-MM-DD.pdf` — run-specific executive summary
- `reports/<org>/<timestamp>/SITE_NAME_Backup_Settings_Report_YYYY-MM-DD.pdf` — run-specific backup settings report
- `reports/<org>/<timestamp>/SITE_NAME_Battery_Backup_Pricing_Calculation_Report_YYYY-MM-DD.pdf` — run-specific UPS runtime and pricing report
- `reports/<org>/<timestamp>/SITE_NAME_AP_Spectrum_Report_YYYY-MM-DD.pdf` — run-specific AP spectrum and interference report
- `reports/<org>/<timestamp>/SITE_NAME_UPS_Switch_Power_Plan_Report_YYYY-MM-DD.json` — run-specific UPS sizing data
- `reports/latest/<org>/report.pdf` — compatibility alias for the latest full report

By default `run.sh` passes `--pdf-only`, so generated HTML is removed after PDFs
are rendered. Use `./run.sh --keep-html` when HTML inspection is useful.
Direct `python3 -m reporting` remains backward-compatible and writes reports into
each `backups/<org>/` directory unless `--reports-dir` or `--output-dir` is used.

## Optional Pricing Input

To enable the Hardware Cost & Refresh Plan section, create a `pricing.json` at the repo root
or within a specific org backup directory. See `pricing.json.example` for the expected shape.
Set `unit_cost` and optional `replacement_cycle_years` per model.

The UniFi migration section also reads `reporting/reference/pricing_reference.json`, which
contains maintained public UniFi planning prices, product source URLs, UI Care add-ons, and
Meraki-to-UniFi model-family mappings. Use an org-local `pricing.json` whenever reseller,
E-rate, Meraki, support, optics, or professional-services pricing needs to override the
public planning reference.

## Requirements

Install dependencies:

```bash
./install.sh
```

- Python 3.10+
- WeasyPrint (PDF rendering)
- `wkhtmltopdf` (optional PDF fallback)
- Ollama with `gemma4:e2b` pulled locally (optional LLM review)

## Development

For local iteration without live Meraki access, use the committed fixture set in
`tests/fixtures/`.

Generate a report directly from fixture data:

```bash
python3 - <<'PY'
from reporting.app import build_org_report
from reporting.html_shell import build_html

body = build_org_report("tests/fixtures", "Fixture Org")
print(build_html("Fixture Org — Network Health Report", body)[:400])
PY
```

Run the script entrypoint against existing backups:

```bash
python3 -m reporting
python3 -m reporting --reports-dir reports --pdf-only
python3 -m reporting --source-dir tests/fixtures --org-name "Fixture Demo Org" --output-dir backups/.demo/Fixture_Demo_Org
./run.sh --report-only --no-ai-review --no-open
```

Generate deterministic fixture output for regression checks:

```bash
./run.sh --demo-report --fixed-now 2026-05-02T21:30:00 --no-open
python3 -m reporting --source-dir tests/fixtures --org-name "Fixture Demo Org" --output-dir backups/.demo/Fixture_Demo_Org --fixed-now 2026-05-02T21:30:00
```

The same fixed clock can be set for compatible report-generation paths with
`MERAKI_REPORT_FIXED_NOW=2026-05-02T21:30:00`.

Run tests:

```bash
pytest -q
```

## Project Shape

See `docs/project-shape.md` for the current file layout and cleanup rules. In
short, `run.sh` remains the main user entrypoint, active reporting code lives in
`reporting/`, root Python scripts are compatibility or pipeline stage
entrypoints, and `legacy/` is retained only as historical reference.

## License

The upstream baseline project includes GPL-3.0 licensed components. Review licensing obligations before redistributing a packaged release.
