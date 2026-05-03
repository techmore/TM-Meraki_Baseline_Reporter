import os
import subprocess
import sys
from pathlib import Path

import pytest

import merge_recommendations as mr
import ollama_review as orv
from reporting import health


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TestMergeRecommendations:
    def test_main_returns_1_when_no_org_dirs(self, monkeypatch, tmp_path):
        monkeypatch.setattr(mr, "BACKUPS_DIR", str(tmp_path))
        assert mr.main() == 1

    def test_main_returns_0_when_org_dirs_have_no_recommendations(self, monkeypatch, tmp_path):
        org_dir = tmp_path / "Org_A"
        org_dir.mkdir()
        monkeypatch.setattr(mr, "BACKUPS_DIR", str(tmp_path))
        assert mr.main() == 0
        assert not (tmp_path / "master_recommendations.md").exists()

    def test_main_merges_available_recommendations(self, monkeypatch, tmp_path):
        org_a = tmp_path / "Org_A"
        org_b = tmp_path / "Org_B"
        org_a.mkdir()
        org_b.mkdir()

        (org_a / "org_name.txt").write_text("Alpha Org", encoding="utf-8")
        (org_a / "recommendations.md").write_text("# A\n\nAlpha finding\n", encoding="utf-8")
        (org_b / "recommendations.md").write_text("# B\n\nBeta finding\n", encoding="utf-8")

        monkeypatch.setattr(mr, "BACKUPS_DIR", str(tmp_path))
        assert mr.main() == 0

        merged = (tmp_path / "master_recommendations.md").read_text(encoding="utf-8")
        assert "# Meraki Master Recommendations" in merged
        assert "Alpha finding" in merged
        assert "Beta finding" in merged


class TestOllamaReview:
    def test_build_review_chunks_respects_section_boundaries(self):
        content = "# One\n" + ("A" * 20) + "\n# Two\n" + ("B" * 20) + "\n# Three\n" + ("C" * 20)
        chunks = orv.build_review_chunks(content, max_chars=35)
        assert len(chunks) >= 2
        assert all(chunk.strip() for chunk in chunks)
        assert any("# Two" in chunk for chunk in chunks)

    def test_review_content_uses_synthesis_for_multiple_chunks(self, monkeypatch):
        calls = []
        unloads = []

        def fake_stream(content, prompt_template=orv.USER_PROMPT_TEMPLATE):
            calls.append((content, prompt_template))
            if prompt_template == orv.SYNTHESIS_PROMPT_TEMPLATE:
                return "## Final\n\nSynthesized"
            return "## Chunk Review\n\nChunk details"

        monkeypatch.setattr(orv, "stream_ollama", fake_stream)
        monkeypatch.setattr(orv, "unload_ollama_model", lambda: unloads.append(True))
        monkeypatch.setattr(orv, "MAX_INPUT_CHARS", 35)
        content = "# One\n" + ("A" * 40) + "\n# Two\n" + ("B" * 40)
        reviewed = orv.review_content(content)
        assert "section-aware chunks" in reviewed
        assert "## Final" in reviewed
        assert any(prompt == orv.SYNTHESIS_PROMPT_TEMPLATE for _, prompt in calls)
        assert len(unloads) == len(calls)

    def test_stream_ollama_once_unloads_after_generation(self, monkeypatch):
        calls = []
        monkeypatch.setattr(orv, "stream_ollama", lambda content, prompt_template=orv.USER_PROMPT_TEMPLATE: "ok")
        monkeypatch.setattr(orv, "unload_ollama_model", lambda: calls.append("unloaded"))
        assert orv.stream_ollama_once("body") == "ok"
        assert calls == ["unloaded"]

    def test_main_skips_when_master_recommendations_missing(self, monkeypatch, tmp_path):
        monkeypatch.setattr(orv, "BACKUPS_DIR", str(tmp_path))
        assert orv.main() == 0

    def test_main_skips_when_ollama_unavailable(self, monkeypatch, tmp_path):
        master = tmp_path / "master_recommendations.md"
        master.write_text("some recommendations", encoding="utf-8")
        monkeypatch.setattr(orv, "BACKUPS_DIR", str(tmp_path))
        monkeypatch.setattr(orv, "ollama_available", lambda: False)
        assert orv.main() == 0
        assert not (tmp_path / "recommendations_ai_enhanced.md").exists()

    def test_main_skips_when_master_file_is_empty(self, monkeypatch, tmp_path):
        master = tmp_path / "master_recommendations.md"
        master.write_text("   \n", encoding="utf-8")
        monkeypatch.setattr(orv, "BACKUPS_DIR", str(tmp_path))
        monkeypatch.setattr(orv, "ollama_available", lambda: True)
        assert orv.main() == 0
        assert not (tmp_path / "recommendations_ai_enhanced.md").exists()

    def test_main_writes_ai_review_output(self, monkeypatch, tmp_path):
        master = tmp_path / "master_recommendations.md"
        master.write_text("recommendation body", encoding="utf-8")
        monkeypatch.setattr(orv, "BACKUPS_DIR", str(tmp_path))
        monkeypatch.setattr(orv, "ollama_available", lambda: True)
        monkeypatch.setattr(orv, "review_content", lambda content: "## Review\n\nDone")

        assert orv.main() == 0
        out = (tmp_path / "recommendations_ai_enhanced.md").read_text(encoding="utf-8")
        assert "# AI-Enhanced Network Recommendations" in out
        assert "## Review" in out


class TestRunShSmoke:
    def test_help_exits_zero(self):
        result = subprocess.run(
            ["bash", str(PROJECT_ROOT / "run.sh"), "--help"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert "Usage: ./run.sh [options]" in result.stdout

    def test_unknown_flag_exits_two(self):
        result = subprocess.run(
            ["bash", str(PROJECT_ROOT / "run.sh"), "--definitely-invalid"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 2
        assert "Unknown option" in result.stderr

    def test_health_check_flag_exits_zero_for_report_only(self):
        result = subprocess.run(
            ["bash", str(PROJECT_ROOT / "run.sh"), "--report-only", "--health-check"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert "Python runtime" in result.stdout
        assert "MERAKI_API_KEY" in result.stdout


class TestHealthChecks:
    def test_report_only_health_does_not_require_api_key(self, monkeypatch, tmp_path):
        backups = tmp_path / "backups"
        (backups / "Org_A").mkdir(parents=True)
        monkeypatch.setattr(health, "BACKUPS_DIR", backups)
        monkeypatch.delenv("MERAKI_API_KEY", raising=False)
        monkeypatch.setattr(health, "load_env", lambda path: None)
        monkeypatch.setattr(health, "_env_value_from_file", lambda path, key: "")

        checks = health.run_checks(require_api_key=False, require_backups=True)
        by_name = {check.name: check for check in checks}
        assert by_name["MERAKI_API_KEY"].status == "skip"
        assert by_name["Org backups"].status == "ok"
