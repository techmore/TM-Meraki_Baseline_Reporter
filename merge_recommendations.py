#!/usr/bin/env python3
import logging
import os
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BACKUPS_DIR = os.path.join(BASE_DIR, "backups")
_NON_ORG = {"backup.log", "organizations.json", "master_recommendations.md",
             "recommendations_ai_enhanced.md"}


def find_org_dirs(backups: str):
    if not os.path.isdir(backups):
        return []
    return sorted(
        os.path.join(backups, name)
        for name in os.listdir(backups)
        if (
            os.path.isdir(os.path.join(backups, name))
            and not name.startswith(".")
            and name not in _NON_ORG
        )
    )


def main() -> int:
    org_dirs = find_org_dirs(BACKUPS_DIR)
    if not org_dirs:
        log.error("No org directories found in %s/", BACKUPS_DIR)
        log.error("Run meraki_backup.py first.")
        return 1

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # master_recommendations.md lives at the backups/ root so Ollama gets all orgs at once
    out_path = os.path.join(BACKUPS_DIR, "master_recommendations.md")

    # Collect per-org recommendation files first so we know what we're working with
    rec_files = []
    for org_dir in org_dirs:
        rec_path = os.path.join(org_dir, "recommendations.md")
        if os.path.exists(rec_path):
            # Read display name
            name_file = os.path.join(org_dir, "org_name.txt")
            display = (
                open(name_file, encoding="utf-8").read().strip()
                if os.path.exists(name_file)
                else os.path.basename(org_dir)
            )
            rec_files.append((display, rec_path))

    if not rec_files:
        log.warning(
            "No per-org recommendations.md files found in backups/. "
            "Run meraki_backup.py first. Skipping merge."
        )
        return 0

    os.makedirs(BACKUPS_DIR, exist_ok=True)
    org_count = 0
    with open(out_path, "w", encoding="utf-8") as out:
        out.write("# Meraki Master Recommendations\n\n")
        out.write(f"Generated: {ts}\n\n")
        for display_name, rec_path in rec_files:
            out.write("---\n\n")
            with open(rec_path, "r", encoding="utf-8") as f:
                out.write(f.read().rstrip())
                out.write("\n\n")
            org_count += 1
            log.info("Merged: %s", display_name)

    log.info("Merged %d org recommendation(s) → %s", org_count, out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
