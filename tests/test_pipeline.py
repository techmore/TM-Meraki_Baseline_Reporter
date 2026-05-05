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

    def test_main_accepts_model_and_chunk_size_args(self, monkeypatch, tmp_path):
        master = tmp_path / "master_recommendations.md"
        master.write_text("recommendation body", encoding="utf-8")
        monkeypatch.setattr(orv, "BACKUPS_DIR", str(tmp_path))
        monkeypatch.setattr(orv, "MODEL", "original-model")
        monkeypatch.setattr(orv, "MAX_INPUT_CHARS", 50_000)
        monkeypatch.setattr(orv, "ollama_available", lambda: True)
        monkeypatch.setattr(orv, "review_content", lambda content: f"{orv.MODEL}:{orv.MAX_INPUT_CHARS}")

        assert orv.main(["--model", "test-model:1b", "--max-input-chars", "1234"]) == 0
        out = (tmp_path / "recommendations_ai_enhanced.md").read_text(encoding="utf-8")
        assert "_Model: test-model:1b" in out
        assert "test-model:1b:1234" in out

    def test_main_rejects_invalid_chunk_size_arg(self, capsys):
        assert orv.main(["--max-input-chars", "0"]) == 2
        assert "max_input_chars must be greater than zero" in capsys.readouterr().err

    def test_main_reports_invalid_env_chunk_size_without_import_failure(self, monkeypatch, capsys):
        monkeypatch.setattr(orv, "CONFIG_ERRORS", ["OLLAMA_MAX_INPUT_CHARS must be an integer"])
        assert orv.main([]) == 2
        assert "OLLAMA_MAX_INPUT_CHARS must be an integer" in capsys.readouterr().err


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
        assert "--demo-report" in result.stdout
        assert "--fixed-now" in result.stdout
        assert "--reports-dir" in result.stdout
        assert "--keep-html" in result.stdout

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

    def test_model_flag_requires_value(self):
        result = subprocess.run(
            ["bash", str(PROJECT_ROOT / "run.sh"), "--model"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 2
        assert "Missing value for --model" in result.stderr

    def test_fixed_now_flag_requires_value(self):
        result = subprocess.run(
            ["bash", str(PROJECT_ROOT / "run.sh"), "--fixed-now"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 2
        assert "Missing value for --fixed-now" in result.stderr

    def test_reports_dir_flag_requires_value(self):
        result = subprocess.run(
            ["bash", str(PROJECT_ROOT / "run.sh"), "--reports-dir"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 2
        assert "Missing value for --reports-dir" in result.stderr

    def test_fixed_now_rejects_invalid_value(self):
        result = subprocess.run(
            ["bash", str(PROJECT_ROOT / "run.sh"), "--fixed-now", "not-a-date", "--health-check"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 2
        assert "Invalid value for --fixed-now" in result.stderr

    def test_demo_report_accepts_fixed_now(self):
        demo_output = PROJECT_ROOT / "backups" / ".demo" / "Fixture_Demo_Org"
        result = subprocess.run(
            [
                "bash", str(PROJECT_ROOT / "run.sh"),
                "--demo-report",
                "--fixed-now", "2026-05-02T21:30:00",
                "--no-open",
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert result.returncode == 0
        assert (demo_output / "Fixture_Demo_Org_Complete_Report_2026-05-02.html").exists()

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


class TestReportingEntrypoint:
    def test_single_source_generation_writes_named_aliases(self, monkeypatch, tmp_path):
        from reporting import app

        source = tmp_path / "source"
        output = tmp_path / "output"
        source.mkdir()
        (source / "recommendations.md").write_text("# Meraki Recommendations: Demo Org\n", encoding="utf-8")

        monkeypatch.setattr(app, "build_org_report", lambda org_dir, org_name, report_kind="full": f"<p>{report_kind}</p>")

        def fake_write_pdf(html_path, pdf_path):
            Path(pdf_path).write_text("pdf", encoding="utf-8")
            return True

        monkeypatch.setattr(app, "write_pdf", fake_write_pdf)

        assert app.main([
            "--source-dir", str(source),
            "--output-dir", str(output),
            "--fixed-now", "2026-05-02T21:30:00",
        ]) == 0
        assert (output / "Demo_Org_Complete_Report_2026-05-02.pdf").exists()
        assert (output / "Demo_Org_2026-05-02_2130_report.pdf").exists()
        assert (output / "report.pdf").exists()

    def test_reports_dir_writes_run_and_latest_without_html_when_pdf_only(self, monkeypatch, tmp_path):
        from reporting import app

        source = tmp_path / "source"
        reports = tmp_path / "reports"
        source.mkdir()
        (source / "recommendations.md").write_text("# Meraki Recommendations: Demo Org\n", encoding="utf-8")

        monkeypatch.setattr(app, "build_org_report", lambda org_dir, org_name, report_kind="full": f"<p>{report_kind}</p>")

        def fake_write_pdf(html_path, pdf_path):
            Path(pdf_path).write_text("pdf", encoding="utf-8")
            return True

        monkeypatch.setattr(app, "write_pdf", fake_write_pdf)

        assert app.main([
            "--source-dir", str(source),
            "--reports-dir", str(reports),
            "--pdf-only",
            "--fixed-now", "2026-05-02T21:30:00",
        ]) == 0

        run_dir = reports / "Demo_Org" / "2026-05-02_2130"
        latest_dir = reports / "latest" / "Demo_Org"
        assert (run_dir / "Demo_Org_Complete_Report_2026-05-02.pdf").exists()
        assert (latest_dir / "Demo_Org_Complete_Report_2026-05-02.pdf").exists()
        assert (latest_dir / "report.pdf").exists()
        assert not (run_dir / "report.pdf").exists()
        assert not (run_dir / "Demo_Org_2026-05-02_2130_report.pdf").exists()
        assert not list(run_dir.glob("*.html"))
        assert not list(latest_dir.glob("*.html"))

    def test_fixed_now_rejects_invalid_timestamp(self):
        from reporting import app

        with pytest.raises(SystemExit) as exc:
            app.main(["--fixed-now", "not-a-date"])
        assert exc.value.code == 2
