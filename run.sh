#!/usr/bin/env bash
# Meraki Network Report Suite — runner

set -uo pipefail
cd "$(dirname "$0")"

usage() {
  echo "Usage: ./run.sh [options]"
  echo ""
  echo "  -m, --model <model>  Override the Ollama model used for AI review"
  echo "                       Default: gemma4:e2b"
  echo "      --report-only    Skip all data collection; build reports from existing backups/"
  echo "      --no-query       Skip API query + backup stages; use data already in backups/"
  echo "      --no-ai-review   Skip the Ollama review stage"
  echo "      --no-open        Do not open generated reports after a successful run"
  echo "      --help           Show this help"
  echo ""
  echo "  Examples:"
  echo "    ./run.sh                           # full run — always fetches fresh data"
  echo "    ./run.sh --model gemma4:e4b"
  echo "    ./run.sh --no-query                # re-generate reports from last backup"
  echo "    ./run.sh --report-only --no-ai-review"
}

validate_environment() {
  local failures=0

  printf "  ${DIM2}│${R}  Checking Python runtime... "
  if [[ -x "$PYTHON_BIN" ]]; then
    echo -e "${GRN}$("$PYTHON_BIN" --version 2>&1)${R}"
  else
    echo -e "${RED}missing${R}"
    (( failures++ )) || true
  fi

  printf "  ${DIM2}│${R}  Checking pipeline imports... "
  if (( REPORT_ONLY == 1 || NO_QUERY == 1 )); then
    if "$PYTHON_BIN" - <<'PY' >/dev/null 2>&1
import report_generator
import merge_recommendations
PY
    then
      echo -e "${GRN}ok${R}"
    else
      echo -e "${RED}import failure${R}"
      (( failures++ )) || true
    fi
  else
    if "$PYTHON_BIN" - <<'PY' >/dev/null 2>&1
import meraki_backup
import meraki_query
import report_generator
import merge_recommendations
PY
    then
      echo -e "${GRN}ok${R}"
    else
      echo -e "${RED}import failure${R}"
      (( failures++ )) || true
    fi
  fi

  printf "  ${DIM2}│${R}  Checking backup directory... "
  mkdir -p backups && echo -e "${GRN}ok${R}"

  if (( REPORT_ONLY == 1 || NO_QUERY == 1 )); then
    printf "  ${DIM2}│${R}  Checking existing org backups... "
    if find backups -mindepth 1 -maxdepth 1 -type d ! -name '.*' | read -r _; then
      echo -e "${GRN}ok${R}"
    else
      echo -e "${RED}no org backups found${R}"
      (( failures++ )) || true
    fi
  else
    printf "  ${DIM2}│${R}  Checking MERAKI_API_KEY... "
    if has_meraki_api_key; then
      echo -e "${GRN}ok${R}"
    else
      echo -e "${RED}missing${R}"
      (( failures++ )) || true
    fi
  fi

  printf "  ${DIM2}│${R}  Checking Ollama availability... "
  if (( NO_AI_REVIEW == 1 )); then
    echo -e "${MGT}skipped${R}"
  elif command -v ollama >/dev/null 2>&1; then
    echo -e "${GRN}installed${R}"
  else
    echo -e "${YLW}optional${R}"
  fi

  printf "  ${DIM2}│${R}  Checking PDF fallback tool... "
  if "$PYTHON_BIN" - <<'PY' >/dev/null 2>&1
import importlib
importlib.import_module("weasyprint")
PY
  then
    echo -e "${GRN}weasyprint${R}"
  elif command -v wkhtmltopdf >/dev/null 2>&1; then
    echo -e "${GRN}wkhtmltopdf${R}"
  else
    echo -e "${RED}no PDF generator${R}"
    (( failures++ )) || true
  fi

  return "$failures"
}

has_meraki_api_key() {
  if [[ -n "${MERAKI_API_KEY:-}" && "${MERAKI_API_KEY}" != "your_key_here" ]]; then
    return 0
  fi

  [[ -f .env ]] || return 1

  local line key value
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line#"${line%%[![:space:]]*}"}"
    [[ -z "$line" || "$line" == \#* || "$line" != *=* ]] && continue
    key="${line%%=*}"
    value="${line#*=}"
    key="${key%"${key##*[![:space:]]}"}"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    value="${value%\"}"
    value="${value#\"}"
    value="${value%\'}"
    value="${value#\'}"
    if [[ "$key" == "MERAKI_API_KEY" && -n "$value" && "$value" != "your_key_here" ]]; then
      return 0
    fi
  done < .env

  return 1
}

# ── Flag parsing ─────────────────────────────────────────────────────────────
CUSTOM_MODEL=""
REPORT_ONLY=0
NO_AI_REVIEW=0
NO_QUERY=0
NO_OPEN=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --model|-m)
      CUSTOM_MODEL="${2:-}"
      shift 2
      ;;
    --report-only)
      REPORT_ONLY=1
      shift
      ;;
    --no-ai-review)
      NO_AI_REVIEW=1
      shift
      ;;
    --no-open)
      NO_OPEN=1
      shift
      ;;
    --no-query)
      NO_QUERY=1
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      echo "" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -n "$CUSTOM_MODEL" ]]; then
  export OLLAMA_MODEL="$CUSTOM_MODEL"
fi

if [[ -z "${PYTHON_BIN:-}" ]]; then
  if [[ -x ".venv/bin/python" ]]; then
    PYTHON_BIN=".venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
  else
    PYTHON_BIN="python3"
  fi
fi

# ── Color palette (256-color) ───────────────────────────────────────────────
R='\033[0m'         # reset
BOLD='\033[1m'
DIM='\033[2m'
BLU='\033[38;5;67m'    # steel blue
CYN='\033[38;5;73m'    # cyan
OLV='\033[38;5;143m'   # olive
GRN='\033[38;5;71m'    # green
YLW='\033[38;5;179m'   # amber
RED='\033[38;5;167m'   # red
MGT='\033[38;5;138m'   # muted rose (skip)
DIM2='\033[38;5;242m'  # dim gray

# ── Stage definitions: "label|script" ───────────────────────────────────────
STAGES=(
  "Environment Validation|__internal__validate_environment__"
  "Query Meraki API|meraki_query.py"
  "Backup Data|meraki_backup.py"
  "Merge Recommendations|merge_recommendations.py"
  "AI Review (Ollama)|ollama_review.py"
  "Generate Reports|report_generator.py"
)
TOTAL=${#STAGES[@]}
TIMING_HISTORY_FILE="$(pwd)/backups/.stage_timings.json"

# ── Helpers ─────────────────────────────────────────────────────────────────
_hr() {
  # Print a horizontal rule of width $1 using char $2
  local width="${1:-60}" char="${2:--}"
  printf '%0.s'"$char" $(seq 1 "$width")
  echo
}

print_header() {
  local now model_line mode_line
  now=$(date '+%A, %-d %B %Y  %H:%M')
  model_line="AI model: ${OLLAMA_MODEL:-gemma4:e2b (default)}"
  if   (( REPORT_ONLY == 1 )); then mode_line="Mode: report-only (skipping data collection)"
  elif (( NO_QUERY    == 1 )); then mode_line="Mode: no-query (using existing backup data)"
  else                              mode_line="Mode: full run (fetching fresh API data)"
  fi
  echo ""
  echo -e "${BLU}╭$(_hr 62 ─ | tr -d '\n')╮${R}"
  printf "${BLU}│${R}  ${BOLD}${OLV}%-58s${R}  ${BLU}│${R}\n" "MERAKI NETWORK REPORT SUITE  ·  v1.0"
  printf "${BLU}│${R}  ${DIM2}%-58s${R}  ${BLU}│${R}\n" "$now"
  printf "${BLU}│${R}  ${DIM2}%-58s${R}  ${BLU}│${R}\n" "$model_line"
  printf "${BLU}│${R}  ${DIM2}%-58s${R}  ${BLU}│${R}\n" "$mode_line"
  echo -e "${BLU}╰$(_hr 62 ─ | tr -d '\n')╯${R}"
  echo ""
}

_spinner() {
  # Background spinner with live elapsed timing and previous-run comparison
  local msg="$1"
  local previous_duration="${2:-0}"
  local frames=('⠋' '⠙' '⠸' '⠴' '⠦' '⠇')
  local i=0
  local start_ts now elapsed hint previous_display
  start_ts=$(date +%s)
  while true; do
    now=$(date +%s)
    elapsed=$(( now - start_ts ))
    if (( previous_duration > 0 )); then
      previous_display=$(_fmt_duration "$previous_duration")
      hint=""
      if (( elapsed > (previous_duration + (previous_duration / 4)) )); then
        hint=" ${RED}(longer than usual)${R}"
      fi
      printf "\r  ${YLW}%s${R}  ${DIM2}%s${R}  ${DIM2}%s / %s last run${R}%b  " \
        "${frames[$i]}" "$msg" "$(_fmt_duration "$elapsed")" "$previous_display" "$hint"
    else
      printf "\r  ${YLW}%s${R}  ${DIM2}%s${R}  ${DIM2}%s elapsed${R}  " \
        "${frames[$i]}" "$msg" "$(_fmt_duration "$elapsed")"
    fi
    i=$(( (i+1) % 6 ))
    sleep 0.08
  done
}

_clear_line() {
  printf '\r%80s\r' ''
}

_fmt_duration() {
  local total="${1:-0}"
  local mins secs
  mins=$(( total / 60 ))
  secs=$(( total % 60 ))
  printf "%02d:%02d" "$mins" "$secs"
}

read_previous_duration() {
  local stage_label="$1"
  "$PYTHON_BIN" - "$TIMING_HISTORY_FILE" "$stage_label" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
label = sys.argv[2]
if not path.exists():
    print(0)
    raise SystemExit
try:
    data = json.loads(path.read_text())
except Exception:
    print(0)
    raise SystemExit
print(int((data.get("stages") or {}).get(label, 0) or 0))
PY
}

write_timing_history() {
  local tmp
  tmp=$(mktemp)
  "$PYTHON_BIN" - "$TIMING_HISTORY_FILE" "$tmp" "${STAGES[@]}" "${RESULTS[@]}" "${DURATIONS[@]}" <<'PY'
import json, pathlib, sys

history_path = pathlib.Path(sys.argv[1])
tmp_path = pathlib.Path(sys.argv[2])
argv = sys.argv[3:]
stage_count = len(argv) // 3
stage_defs = argv[:stage_count]
results = argv[stage_count:stage_count * 2]
durations = argv[stage_count * 2:]

stages = {}
for stage_def, result, duration in zip(stage_defs, results, durations):
    label = stage_def.split("|", 1)[0]
    if result == "ok":
        try:
            stages[label] = int(duration)
        except ValueError:
            pass

payload = {"updatedAt": __import__("datetime").datetime.now().isoformat(), "stages": stages}
tmp_path.write_text(json.dumps(payload, indent=2))
history_path.parent.mkdir(parents=True, exist_ok=True)
tmp_path.replace(history_path)
PY
  rm -f "$tmp"
}

# ── Stage runner ─────────────────────────────────────────────────────────────
# Returns 0 on success, 1 on failure
# Globals written: STAGE_STATUS (ok|fail|skip), STAGE_DURATION
run_stage() {
  local label="$1"
  local script="$2"
  local step="$3"
  local previous_duration="${4:-0}"

  # Stage header
  echo ""
  printf "  ${BLU}${BOLD}[%d/%d]${R}  ${BOLD}%s${R}\n" "$step" "$TOTAL" "$label"
  echo -e "  ${DIM2}$(printf '─%.0s' $(seq 1 58))${R}"

  if [[ "$script" == "__internal__validate_environment__" ]]; then
    local t_start t_end
    t_start=$(date +%s)
    if validate_environment; then
      t_end=$(date +%s)
      STAGE_DURATION=$(( t_end - t_start ))
      printf "\n  ${GRN}✓${R}  ${BOLD}%s${R}${DIM2}  completed in %ds${R}\n" \
        "$label" "$STAGE_DURATION"
      STAGE_STATUS="ok"
      return 0
    else
      t_end=$(date +%s)
      STAGE_DURATION=$(( t_end - t_start ))
      printf "\n  ${RED}✗${R}  ${BOLD}%s${R}${RED}  failed${R}${DIM2}  after %ds${R}\n" \
        "$label" "$STAGE_DURATION"
      STAGE_STATUS="fail"
      return 1
    fi
  fi

  if [[ ! -f "$script" ]]; then
    printf "  ${MGT}⚡  Script not found: %s — skipping${R}\n" "$script"
    STAGE_STATUS="skip"
    STAGE_DURATION=0
    return 0
  fi

  # Capture output to temp file; show spinner while running
  local tmp
  tmp=$(mktemp)
  local t_start
  t_start=$(date +%s)

  _spinner "$script" "$previous_duration" &
  local spin_pid=$!

  # Build extra args for stages that support them
  local extra_args=()
  if [[ "$script" == "meraki_backup.py" ]]; then
    extra_args+=("--force-refresh")   # always fetch fresh — use --no-query to skip entirely
  fi

  set +e
  "$PYTHON_BIN" "$script" "${extra_args[@]+"${extra_args[@]}"}" > "$tmp" 2>&1
  local exit_code=$?
  set -e

  kill "$spin_pid" 2>/dev/null
  wait "$spin_pid" 2>/dev/null
  _clear_line

  local t_end
  t_end=$(date +%s)
  STAGE_DURATION=$(( t_end - t_start ))

  # Print captured output, indented
  if [[ -s "$tmp" ]]; then
    while IFS= read -r line; do
      printf "  ${DIM2}│${R}  %s\n" "$line"
    done < "$tmp"
  fi
  rm -f "$tmp"

  if (( exit_code == 0 )); then
    printf "\n  ${GRN}✓${R}  ${BOLD}%s${R}${DIM2}  completed in %ds${R}\n" \
      "$label" "$STAGE_DURATION"
    STAGE_STATUS="ok"
    return 0
  else
    printf "\n  ${RED}✗${R}  ${BOLD}%s${R}${RED}  failed${R}${DIM2}  after %ds${R}\n" \
      "$label" "$STAGE_DURATION"
    STAGE_STATUS="fail"
    return 1
  fi
}

# ── Main ─────────────────────────────────────────────────────────────────────
print_header

SUITE_START=$(date +%s)
declare -a RESULTS   # "ok|fail|skip" per stage
declare -a DURATIONS # seconds per stage
FAIL_COUNT=0
SKIP_COUNT=0

for i in "${!STAGES[@]}"; do
  IFS='|' read -r label script <<< "${STAGES[$i]}"
  step=$(( i + 1 ))

  STAGE_STATUS="ok"
  STAGE_DURATION=0
  PREVIOUS_DURATION=$(read_previous_duration "$label")

  if (( REPORT_ONLY == 1 && i > 0 && i < 3 )); then
    echo ""
    printf "  ${BLU}${BOLD}[%d/%d]${R}  ${BOLD}%s${R}\n" "$step" "$TOTAL" "$label"
    echo -e "  ${DIM2}$(printf '─%.0s' $(seq 1 58))${R}"
    printf "  ${MGT}⚡${R}  ${BOLD}%s${R}${DIM2}  skipped in report-only mode${R}\n" "$label"
    RESULTS[$i]="skip"
    DURATIONS[$i]=0
    (( SKIP_COUNT++ )) || true
    continue
  fi

  # --no-query: skip the Query and Backup stages (indices 1 and 2) only
  if (( NO_QUERY == 1 && (i == 1 || i == 2) )); then
    echo ""
    printf "  ${BLU}${BOLD}[%d/%d]${R}  ${BOLD}%s${R}\n" "$step" "$TOTAL" "$label"
    echo -e "  ${DIM2}$(printf '─%.0s' $(seq 1 58))${R}"
    printf "  ${MGT}⚡${R}  ${BOLD}%s${R}${DIM2}  skipped by --no-query${R}\n" "$label"
    RESULTS[$i]="skip"
    DURATIONS[$i]=0
    (( SKIP_COUNT++ )) || true
    continue
  fi

  if (( NO_AI_REVIEW == 1 && i == 4 )); then
    echo ""
    printf "  ${BLU}${BOLD}[%d/%d]${R}  ${BOLD}%s${R}\n" "$step" "$TOTAL" "$label"
    echo -e "  ${DIM2}$(printf '─%.0s' $(seq 1 58))${R}"
    printf "  ${MGT}⚡${R}  ${BOLD}%s${R}${DIM2}  skipped by --no-ai-review${R}\n" "$label"
    RESULTS[$i]="skip"
    DURATIONS[$i]=0
    (( SKIP_COUNT++ )) || true
    continue
  fi

  if run_stage "$label" "$script" "$step" "$PREVIOUS_DURATION"; then
    RESULTS[$i]="$STAGE_STATUS"
    DURATIONS[$i]=$STAGE_DURATION
  else
    RESULTS[$i]="fail"
    DURATIONS[$i]=$STAGE_DURATION
    (( FAIL_COUNT++ )) || true
    # Stop on hard failures (not skips)
    echo ""
    echo -e "  ${RED}${BOLD}Pipeline halted.${R}  Fix the error above and re-run."
    echo ""
    break
  fi

  [[ "${RESULTS[$i]}" == "skip" ]] && (( SKIP_COUNT++ )) || true
done

SUITE_END=$(date +%s)
SUITE_DURATION=$(( SUITE_END - SUITE_START ))

if (( FAIL_COUNT == 0 )); then
  write_timing_history
fi

# ── Summary table ─────────────────────────────────────────────────────────────
echo ""
echo -e "${BLU}╭$(_hr 62 ─ | tr -d '\n')╮${R}"
printf "${BLU}│${R}  ${BOLD}%-58s${R}  ${BLU}│${R}\n" "EXECUTION SUMMARY"
echo -e "${BLU}├$(_hr 62 ─ | tr -d '\n')┤${R}"

for i in "${!STAGES[@]}"; do
  IFS='|' read -r label script <<< "${STAGES[$i]}"
  status="${RESULTS[$i]:-—}"
  dur="${DURATIONS[$i]:-0}"

  case "$status" in
    ok)
      icon="${GRN}✓${R}"
      status_str="${GRN}OK${R}"
      ;;
    fail)
      icon="${RED}✗${R}"
      status_str="${RED}FAILED${R}"
      ;;
    skip)
      icon="${MGT}⚡${R}"
      status_str="${MGT}SKIPPED${R}"
      ;;
    *)
      icon="${DIM2}·${R}"
      status_str="${DIM2}—${R}"
      ;;
  esac

  printf "${BLU}│${R}  %b  ${DIM2}%-30s${R}  %b  ${DIM2}%4ds${R}  ${BLU}│${R}\n" \
    "$icon" "$label" "$status_str" "$dur"
done

echo -e "${BLU}├$(_hr 62 ─ | tr -d '\n')┤${R}"
printf "${BLU}│${R}  ${DIM2}%-40s${R}${BOLD}%15ss total${R}  ${BLU}│${R}\n" "" "$SUITE_DURATION"
echo -e "${BLU}╰$(_hr 62 ─ | tr -d '\n')╯${R}"
echo ""

if (( FAIL_COUNT == 0 )); then
  echo -e "  ${GRN}${BOLD}All stages passed.${R}  Reports written to backups/."
  echo ""

  # ── Auto-open generated reports ───────────────────────────────────────────
  BACKUPS_DIR="$(pwd)/backups"
  if (( NO_OPEN == 1 )); then
    echo -e "  ${DIM2}Auto-open skipped by --no-open.${R}"
  elif [[ -d "$BACKUPS_DIR" ]]; then
   REPORT_FILES=()
   while IFS= read -r org_dir; do
     if [[ -f "$org_dir/report.pdf" ]]; then
       REPORT_FILES+=("$org_dir/report.pdf")
     elif [[ -f "$org_dir/report.html" ]]; then
       REPORT_FILES+=("$org_dir/report.html")
     fi
   done < <(find "$BACKUPS_DIR" -mindepth 1 -maxdepth 1 -type d | sort)

    if (( ${#REPORT_FILES[@]} > 0 )); then
      echo -e "  ${OLV}Opening ${#REPORT_FILES[@]} report(s)…${R}"
      # Cross-platform file opener
      _open_file() {
        if command -v xdg-open &>/dev/null; then
          xdg-open "$1"
        elif command -v open &>/dev/null; then
          open "$1"
        elif command -v start &>/dev/null; then
          start "" "$1"
        else
          printf "  ${DIM2}(Cannot auto-open — no suitable opener found)${R}\n"
        fi
      }
      for f in "${REPORT_FILES[@]}"; do
        org_name=$(basename "$(dirname "$f")")
        printf "  ${DIM2}→${R}  %s  ${DIM2}(%s)${R}\n" "$org_name" "$(basename "$f")"
        _open_file "$f"
      done
    else
      echo -e "  ${DIM2}No report files found in backups/ — run the pipeline first.${R}"
    fi
  fi
else
  echo -e "  ${RED}${BOLD}${FAIL_COUNT} stage(s) failed.${R}  Review output above."
fi
echo ""
