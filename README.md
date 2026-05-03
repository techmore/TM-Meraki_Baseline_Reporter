# Meraki Security Baseline and Report Suite

A reporting pipeline that collects Meraki org data, generates network health and security recommendations, optionally enriches them with a local LLM review, and renders HTML/PDF reports.

## Components

| Script | Role |
|--------|------|
| `meraki_backup.py` | Pulls Meraki API data into per-org backup directories |
| `merge_recommendations.py` | Combines per-org recommendation files |
| `ollama_review.py` | Optional local LLM review stage |
| `report_generator.py` | Renders HTML/PDF reports |
| `run.sh` | Full pipeline orchestrator |
| `legacy/` | Original MX baseline scripts (reference only) |
| `docs/cis-meraki-reference.md` | CIS Controls to Meraki reference mapping |

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

Optional — specify a local Ollama model for AI-enhanced recommendations:

```bash
./run.sh --model gemma4:e4b
```

The default Ollama model is `gemma4:e2b`, chosen for lower RAM usage. Pull it
before enabling AI review:

```bash
ollama pull gemma4:e2b
```

## Output

All output is written to `backups/<org>/` (gitignored):

- `recommendations.md` — per-org findings and recommendations
- `SITE_NAME_Complete_Report_YYYY-MM-DD.html` / `.pdf` — named full report for sharing
- `SITE_NAME_Executive_Summary_Report_YYYY-MM-DD.html` / `.pdf` — named executive summary
- `SITE_NAME_Backup_Settings_Report_YYYY-MM-DD.html` / `.pdf` — named backup settings report
- `report.html` / `report.pdf` — compatibility aliases for older scripts
- `backups/master_recommendations.md` — combined across all orgs
- `backups/recommendations_ai_enhanced.md` — LLM-reviewed version

## Optional Pricing Input

To enable the Hardware Cost & Refresh Plan section, create a `pricing.json` at the repo root
or within a specific org backup directory. See `pricing.json.example` for the expected shape.
Set `unit_cost` and optional `replacement_cycle_years` per model.

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
python3 scripts/generate_report.py
./run.sh --report-only --no-ai-review --no-open
```

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
