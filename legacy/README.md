# Legacy Scripts

These scripts are superseded by the current pipeline (`meraki_backup.py` → `merge_recommendations.py` → `ollama_review.py` → `report_generator.py`). They are retained for historical reference only and should not be run in production.

| File | Description |
|------|-------------|
| `Meraki-Baseline-Security.py` | Original baseline security check (v1) |
| `mbsv2.py` | Baseline security check v2 |
| `v6_baseline.py` | Baseline script v6 |
| `v6-mdm.py` | MDM variant of v6 baseline |
| `v9.py` | v9 iteration before pipeline refactor |
| `networking-script-no-topography.py` | Pre-topology version of the network report |

Use `report_generator.py` for all current reporting needs.
