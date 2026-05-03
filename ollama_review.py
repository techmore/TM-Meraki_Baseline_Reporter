#!/usr/bin/env python3
"""
Optional pipeline stage: Use local Ollama to review and
enhance merged Meraki recommendations before PDF generation.

Exits 0 (non-fatal) if Ollama is unavailable so the pipeline continues.
Output: <backup_dir>/recommendations_ai_enhanced.md
"""
import json
import logging
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime
from typing import List

log = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BACKUPS_DIR = os.path.join(BASE_DIR, "backups")
OLLAMA_URL = "http://localhost:11434"

# ── Model selection ──────────────────────────────────────────────────────────
# Low-RAM default; override with OLLAMA_MODEL or ./run.sh --model.
#
#   gemma4:e2b     low RAM, fast local review, 128K context
#   gemma4:e4b     stronger small Gemma 4 option if memory allows
#   qwen3.5:9b     larger fallback with strong structured output
#   qwen3.5:27b    high-capability option for 32 GB+ systems
#
# Pull with: ollama pull gemma4:e2b
# Override at runtime: OLLAMA_MODEL=qwen3.5:9b ./run.sh
#                  or: ./run.sh --model qwen3.5:9b
_DEFAULT_MODEL = "gemma4:e2b"
MODEL = os.getenv("OLLAMA_MODEL", _DEFAULT_MODEL)

# Keep chunks conservative so small local models have room for the prompt
# and generated review while still preserving section boundaries.
MAX_INPUT_CHARS = 50_000

SYSTEM_PROMPT = """\
You are a senior network engineer with deep expertise in Cisco Meraki enterprise deployments, \
specifically in K-12 and education environments. You are performing a structured engineering \
review of live network health data exported from the Meraki API.

Rules:
- Be specific: reference device serials, network names, and metric values from the input.
- Prioritise findings by operational risk, not by how much text the input devoted to them.
- Distinguish between confirmed problems (data shows failure) and risks (data shows warning signs).
- For each finding, state: the observed fact, why it matters operationally, and the exact next action.
- Do NOT repeat sections of the input verbatim. Do NOT pad with generic best-practice boilerplate.
- Use concise, direct language. A bullet is better than a paragraph.
- Output clean Markdown only.\
"""

USER_PROMPT_TEMPLATE = """\
Below is a Meraki network health report generated from live API data. \
Produce a prioritised engineering review.

--- BEGIN REPORT ---
{content}
--- END REPORT ---

Respond using EXACTLY this structure (include all six sections even if empty):

## 🔴 Critical  (resolve within 48 hours)
Issues causing or likely to cause immediate outages, security exposure, or license failure.

## 🟠 High Priority  (resolve within 2 weeks)
Degraded performance, recurring errors, or capacity issues with measurable user impact.

## 🟡 Medium Priority  (resolve within 60 days)
Suboptimal configurations, growing risks, or items that need scheduled maintenance.

## 🔵 Long-term Improvements  (next planning cycle)
Architecture, hardware refresh, or strategic changes worth budgeting for.

## ✅ Quick Wins  (< 1 hour each, low risk)
Configuration changes or checks that are easy to do now and will reduce noise or risk.

## 📊 Risk Summary
2–4 sentence executive summary of overall network health and the top risk to address first.\
"""

SYNTHESIS_PROMPT_TEMPLATE = """\
Below are chunked engineering reviews produced from different sections of the same Meraki network health report.

--- BEGIN CHUNK REVIEWS ---
{content}
--- END CHUNK REVIEWS ---

Merge them into one final prioritised engineering review.

Rules:
- Deduplicate repeated findings.
- Preserve the highest severity when multiple chunks mention the same problem.
- Prefer concrete operational actions over generic summaries.
- If chunk reviews disagree, note the uncertainty briefly instead of inventing confidence.

Respond using EXACTLY this structure (include all six sections even if empty):

## 🔴 Critical  (resolve within 48 hours)
Issues causing or likely to cause immediate outages, security exposure, or license failure.

## 🟠 High Priority  (resolve within 2 weeks)
Degraded performance, recurring errors, or capacity issues with measurable user impact.

## 🟡 Medium Priority  (resolve within 60 days)
Suboptimal configurations, growing risks, or items that need scheduled maintenance.

## 🔵 Long-term Improvements  (next planning cycle)
Architecture, hardware refresh, or strategic changes worth budgeting for.

## ✅ Quick Wins  (< 1 hour each, low risk)
Configuration changes or checks that are easy to do now and will reduce noise or risk.

## 📊 Risk Summary
2–4 sentence executive summary of overall network health and the top risk to address first.\
"""


def ollama_available() -> bool:
    """Return True if Ollama is running and the target model is present."""
    try:
        req = urllib.request.Request(f"{OLLAMA_URL}/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        model_base = MODEL.split(":")[0]
        names = [m.get("name", "").split(":")[0] for m in data.get("models", [])]
        if model_base not in names:
            log.warning("Model '%s' not found locally. Pull it first: ollama pull %s", MODEL, MODEL)
            return False
        return True
    except Exception as exc:
        log.warning("Ollama not reachable at %s (%s). Start it with: ollama serve", OLLAMA_URL, exc)
        return False


def split_markdown_sections(content: str) -> List[str]:
    sections: List[str] = []
    current: List[str] = []
    for line in content.splitlines():
        if line.startswith("# ") and current:
            sections.append("\n".join(current).strip())
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append("\n".join(current).strip())
    return [section for section in sections if section]


def build_review_chunks(content: str, max_chars: int = MAX_INPUT_CHARS) -> List[str]:
    chunks: List[str] = []
    current = ""
    for section in split_markdown_sections(content):
        if not current:
            current = section
            continue
        candidate = f"{current}\n\n{section}"
        if len(candidate) <= max_chars:
            current = candidate
        else:
            chunks.append(current)
            if len(section) <= max_chars:
                current = section
            else:
                for start in range(0, len(section), max_chars):
                    piece = section[start:start + max_chars]
                    if len(piece) == max_chars:
                        chunks.append(piece)
                    else:
                        current = piece
                if len(current) == max_chars:
                    chunks.append(current)
                    current = ""
    if current:
        chunks.append(current)
    return chunks or [content[:max_chars]]


def stream_ollama(content: str, prompt_template: str = USER_PROMPT_TEMPLATE) -> str:
    """Stream a generate request to Ollama and return the full response text."""
    payload = json.dumps(
        {
            "model": MODEL,
            "system": SYSTEM_PROMPT,
            "prompt": prompt_template.format(content=content),
            "stream": True,
            "options": {
                "temperature": 0.3,   # lower = more factual
                "num_predict": 2048,
            },
        }
    ).encode()

    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    tokens: list[str] = []
    dot_counter = 0
    print("  Generating", end="", flush=True)

    with urllib.request.urlopen(req, timeout=300) as resp:
        while True:
            line = resp.readline()
            if not line:
                break
            try:
                chunk = json.loads(line.decode())
            except json.JSONDecodeError:
                continue
            token = chunk.get("response", "")
            tokens.append(token)
            dot_counter += 1
            if dot_counter % 40 == 0:
                print(".", end="", flush=True)
            if chunk.get("done", False):
                break

    print(" done", flush=True)
    return "".join(tokens).strip()


def unload_ollama_model() -> None:
    """Ask Ollama to unload the active model so it does not sit in RAM."""
    payload = json.dumps(
        {
            "model": MODEL,
            "prompt": "",
            "stream": False,
            "keep_alive": 0,
        }
    ).encode()
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
        log.info("Unloaded Ollama model: %s", MODEL)
    except Exception as exc:
        log.warning("Could not unload Ollama model '%s': %s", MODEL, exc)


def stream_ollama_once(content: str, prompt_template: str = USER_PROMPT_TEMPLATE) -> str:
    """Run one Ollama pass and unload the model immediately afterward."""
    try:
        return stream_ollama(content, prompt_template=prompt_template)
    finally:
        unload_ollama_model()


def review_content(content: str) -> str:
    chunks = build_review_chunks(content, MAX_INPUT_CHARS)
    if len(chunks) == 1:
        return stream_ollama_once(chunks[0])

    chunk_reviews = []
    for idx, chunk in enumerate(chunks, start=1):
        print(f"  Reviewing chunk {idx}/{len(chunks)}", flush=True)
        reviewed = stream_ollama_once(f"[Chunk {idx}/{len(chunks)}]\n\n{chunk}")
        chunk_reviews.append(f"## Chunk {idx}\n\n{reviewed}")

    synthesis_input = "\n\n".join(chunk_reviews)
    print(f"  Synthesizing {len(chunks)} chunk reviews", flush=True)
    final_review = stream_ollama_once(synthesis_input, prompt_template=SYNTHESIS_PROMPT_TEMPLATE)
    return (
        "> Note: This AI review was generated from section-aware chunks and then synthesized.\n\n"
        + final_review
    )


def main() -> int:
    master_rec = os.path.join(BACKUPS_DIR, "master_recommendations.md")
    if not os.path.exists(master_rec):
        log.warning("master_recommendations.md not found at %s", master_rec)
        log.warning("Run merge_recommendations.py first — skipping AI review.")
        return 0

    log.info("Checking Ollama (%s)...", MODEL)
    if not ollama_available():
        log.info("Skipping AI review — Ollama unavailable.")
        return 0  # non-fatal: rest of pipeline continues

    with open(master_rec, "r", encoding="utf-8") as f:
        content = f.read()

    if not content.strip():
        log.info("No recommendations content — skipping AI review.")
        return 0

    char_count = len(content)
    chunks = build_review_chunks(content, MAX_INPUT_CHARS)
    log.info(
        "Input: %s chars across %d chunk(s)",
        f"{char_count:,}",
        len(chunks),
    )

    enhanced = review_content(content)

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    out_path = os.path.join(BACKUPS_DIR, "recommendations_ai_enhanced.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("# AI-Enhanced Network Recommendations\n\n")
        f.write(f"_Model: {MODEL} · Generated: {ts}_\n\n")
        f.write("---\n\n")
        f.write(enhanced)
        f.write("\n")

    log.info("Saved → backups/%s", os.path.basename(out_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
