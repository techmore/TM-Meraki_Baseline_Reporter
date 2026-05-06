#!/usr/bin/env bash
# UniFi Network Report Suite — runner

set -uo pipefail
cd "$(dirname "$0")/.."

usage() {
  echo "Usage: ./unifi/run.sh [options]"
  echo ""
  echo "      --mode <auto|site-manager|network|both>"
  echo "                       API collection mode. Default: auto"
  echo "      --site-id <id>   Limit local Network Application collection to one site ID"
  echo "      --console-id <id>"
  echo "                       Use api.ui.com remote connector for this console ID"
  echo "      --report-only    Skip API collection; build report from unifi/backups/latest"
  echo "      --backups-dir <dir>"
  echo "                       Backup JSON directory. Default: unifi/backups/latest"
  echo "      --reports-dir <dir>"
  echo "                       Report output directory. Default: unifi/reports/latest"
  echo "      --keep-html      Keep report.html alongside report.pdf"
  echo "      --health-check   Validate local environment and exit"
  echo "      --no-open        Do not open generated report after a successful run"
  echo "      --help           Show this help"
  echo ""
  echo "  Env examples:"
  echo "    UNIFI_SITE_MANAGER_API_KEY=... ./unifi/run.sh"
  echo "    UNIFI_NETWORK_BASE_URL=https://192.168.1.1 UNIFI_NETWORK_API_KEY=... ./unifi/run.sh"
  echo "    UNIFI_NETWORK_CONSOLE_ID=58D...:123 UNIFI_NETWORK_API_KEY=... ./unifi/run.sh"
  echo "    UNIFI_VERIFY_SSL=0 ./unifi/run.sh --mode network"
}

MODE="${UNIFI_COLLECTION_MODE:-auto}"
REPORT_ONLY=0
NO_OPEN=0
HEALTH_CHECK=0
KEEP_HTML=0
SITE_ID="${UNIFI_SITE_ID:-}"
CONSOLE_ID="${UNIFI_NETWORK_CONSOLE_ID:-}"
BACKUPS_DIR="unifi/backups/latest"
REPORTS_DIR="unifi/reports/latest"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode)
      MODE="${2:-}"
      if [[ -z "$MODE" || "$MODE" == --* ]]; then
        echo "Missing value for $1" >&2
        exit 2
      fi
      shift 2
      ;;
    --site-id)
      SITE_ID="${2:-}"
      if [[ -z "$SITE_ID" || "$SITE_ID" == --* ]]; then
        echo "Missing value for $1" >&2
        exit 2
      fi
      shift 2
      ;;
    --console-id)
      CONSOLE_ID="${2:-}"
      if [[ -z "$CONSOLE_ID" || "$CONSOLE_ID" == --* ]]; then
        echo "Missing value for $1" >&2
        exit 2
      fi
      shift 2
      ;;
    --report-only)
      REPORT_ONLY=1
      shift
      ;;
    --backups-dir)
      BACKUPS_DIR="${2:-}"
      if [[ -z "$BACKUPS_DIR" || "$BACKUPS_DIR" == --* ]]; then
        echo "Missing value for $1" >&2
        exit 2
      fi
      shift 2
      ;;
    --reports-dir)
      REPORTS_DIR="${2:-}"
      if [[ -z "$REPORTS_DIR" || "$REPORTS_DIR" == --* ]]; then
        echo "Missing value for $1" >&2
        exit 2
      fi
      shift 2
      ;;
    --keep-html)
      KEEP_HTML=1
      shift
      ;;
    --health-check)
      HEALTH_CHECK=1
      shift
      ;;
    --no-open)
      NO_OPEN=1
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "${PYTHON_BIN:-}" ]]; then
  if [[ -x ".venv/bin/python" ]]; then
    PYTHON_BIN=".venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
  else
    PYTHON_BIN="python3"
  fi
fi

run_stage() {
  local label="$1"
  shift
  echo ""
  echo "==> $label"
  "$@"
}

echo ""
echo "UniFi Network Report Suite"
echo "Mode: $MODE"
echo "Backups: $BACKUPS_DIR"
echo "Reports: $REPORTS_DIR"

if (( HEALTH_CHECK == 1 )); then
  health_args=(--backups-dir "$BACKUPS_DIR")
  if (( REPORT_ONLY == 1 )); then
    health_args+=(--report-only)
  fi
  "$PYTHON_BIN" -m unifi.health "${health_args[@]}"
  exit $?
fi

failures=0
health_args=(--backups-dir "$BACKUPS_DIR")
if (( REPORT_ONLY == 1 )); then
  health_args+=(--report-only)
fi
run_stage "Environment Validation" "$PYTHON_BIN" -m unifi.health "${health_args[@]}" || failures=$((failures + 1))

if (( failures == 0 )); then
  if (( REPORT_ONLY == 0 )); then
    collect_args=(--mode "$MODE" --output-dir "$BACKUPS_DIR")
    if [[ -n "$SITE_ID" ]]; then
      collect_args+=(--site-id "$SITE_ID")
    fi
    if [[ -n "$CONSOLE_ID" ]]; then
      collect_args+=(--console-id "$CONSOLE_ID")
    fi
    run_stage "Query UniFi API" "$PYTHON_BIN" -m unifi.collect "${collect_args[@]}" || failures=$((failures + 1))
  else
    echo ""
    echo "==> Query UniFi API"
    echo "Skipped by --report-only"
  fi
fi

if (( failures == 0 )); then
  report_args=(--source-dir "$BACKUPS_DIR" --output-dir "$REPORTS_DIR")
  if (( KEEP_HTML == 0 )); then
    report_args+=(--pdf-only)
  fi
  run_stage "Generate UniFi Report" "$PYTHON_BIN" -m unifi.report "${report_args[@]}" || failures=$((failures + 1))
fi

if (( failures == 0 )); then
  run_stage "Report Inventory" "$PYTHON_BIN" -m unifi.inventory --backups-dir "$BACKUPS_DIR" --reports-dir "$REPORTS_DIR" || failures=$((failures + 1))
fi

if (( failures == 0 )); then
  echo ""
  echo "All UniFi stages passed."
  if (( NO_OPEN == 0 )) && [[ -f "$REPORTS_DIR/report.pdf" ]]; then
    if command -v open >/dev/null 2>&1; then
      open "$REPORTS_DIR/report.pdf"
    elif command -v xdg-open >/dev/null 2>&1; then
      xdg-open "$REPORTS_DIR/report.pdf"
    fi
  fi
else
  echo ""
  echo "$failures UniFi stage(s) failed."
fi

exit "$failures"
