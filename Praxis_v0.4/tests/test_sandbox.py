"""Tests for the E2B sandbox integration.

Two things are tested without any network access to e2b.dev:
1. The generated standalone script is *correct* - run it for real in a
   local subprocess and compare its output to the in-process agent's
   output on the same file (this is the guarantee sandbox_scripts.py's
   docstring makes: one algorithm, not two that can drift apart).
2. DataAnalysisAgent's fallback contract - a fake E2BSandboxRunner stands
   in for the real one, so we can assert "sandbox unavailable -> silent
   fallback" and "sandbox path returns a well-formed result" without
   needing a real API key. The actual live E2B round trip needs a real
   key and is not exercised here - see docs/REFONTE_v0.4.md.
"""
import json
import subprocess
import sys
from unittest.mock import patch

from src.services.sandbox_scripts import build_data_cleaning_script


def test_generated_script_runs_and_matches_in_process_output(tmp_path, pilot_xlsx):
    from src.agents import DataAnalysisAgent

    # Run the generated script for real, standalone, in a subprocess -
    # this is the actual code that would run inside the E2B container.
    script_dir = tmp_path / "sandboxed"
    script_dir.mkdir()
    (script_dir / "input.xlsx").write_bytes(open(pilot_xlsx["path"], "rb").read())
    script_path = script_dir / "script.py"
    script_path.write_text(build_data_cleaning_script("B"), encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, str(script_path)], cwd=str(script_dir),
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, f"script failed:\n{proc.stderr}"
    assert (script_dir / "audit_trail.json").exists()
    assert (script_dir / "stats_summary.json").exists()
    assert (script_dir / "base_apuree.xlsx").exists()

    # Run the same input through the in-process agent and compare.
    inprocess_dir = tmp_path / "inprocess"
    agent = DataAnalysisAgent()
    agent.execute({
        "step": {"type": "nettoyage"}, "previous_outputs": {},
        "data_sources": [{"path": pilot_xlsx["path"]}],
        "strategy": "B", "work_dir": str(inprocess_dir),
    })

    with open(script_dir / "audit_trail.json") as f:
        sandboxed_audit = json.load(f)
    with open(inprocess_dir / "audit_trail.json") as f:
        inprocess_audit = json.load(f)

    assert sandboxed_audit["anomalies"] == inprocess_audit["anomalies"]
    assert sandboxed_audit["n_rows_cleaned"] == inprocess_audit["n_rows_cleaned"]


def test_data_agent_falls_back_silently_when_sandbox_unavailable(db_session, pilot_xlsx, tmp_path):
    from src.agents import DataAnalysisAgent

    class _UnavailableRunner:
        available = False

    with patch("src.services.sandbox.E2BSandboxRunner", _UnavailableRunner):
        agent = DataAnalysisAgent()
        result = agent.execute({
            "step": {"type": "nettoyage"}, "previous_outputs": {},
            "data_sources": [{"path": pilot_xlsx["path"]}],
            "strategy": "B", "work_dir": str(tmp_path),
        })

    assert result["status"] == "success"
    assert "sandboxed" not in (result["artifacts"][0]["metadata"] or {})


def test_data_agent_uses_sandbox_result_when_available(db_session, pilot_xlsx, tmp_path):
    from src.agents import DataAnalysisAgent

    # Fake runner that "executes" by just running the real script locally -
    # proves DataAnalysisAgent correctly wires uploads/downloads/results
    # through run_script()'s contract without needing the real service.
    class _FakeRunner:
        available = True

        def run_script(self, script, upload_files, download_files, download_dir):
            work = tmp_path / "fake_sandbox"
            work.mkdir(exist_ok=True)
            for remote, local in upload_files.items():
                (work / remote).write_bytes(open(local, "rb").read())
            script_path = work / "script.py"
            script_path.write_text(script, encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, "script.py"], cwd=str(work),
                capture_output=True, text=True, timeout=60,
            )
            downloaded = {}
            if proc.returncode == 0:
                import os
                os.makedirs(download_dir, exist_ok=True)
                for fname in download_files:
                    src = work / fname
                    if src.exists():
                        dst = f"{download_dir}/{fname}"
                        open(dst, "wb").write(src.read_bytes())
                        downloaded[fname] = dst
            return {
                "success": proc.returncode == 0, "error": proc.stderr or None,
                "stdout": [proc.stdout], "stderr": [proc.stderr], "downloaded": downloaded,
            }

    with patch("src.services.sandbox.E2BSandboxRunner", _FakeRunner):
        agent = DataAnalysisAgent()
        result = agent.execute({
            "step": {"type": "nettoyage"}, "previous_outputs": {},
            "data_sources": [{"path": pilot_xlsx["path"]}],
            "strategy": "B", "work_dir": str(tmp_path / "out"),
        })

    assert result["status"] == "success"
    assert result["artifacts"][0]["metadata"]["sandboxed"] is True
    assert "sandbox E2B isolé" in result["summary"]
