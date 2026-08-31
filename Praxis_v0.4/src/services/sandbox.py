"""
E2B Sandbox - Phase 3 (§9: "Sandbox d'Exécution: E2B ou Docker isolé")

Runs a script in an isolated remote container instead of in this
process, for the one place in Praxis that touches a file the user
uploaded rather than data Praxis itself generated: DataAnalysisAgent
reading the raw Excel workbook.

This talks to E2B's cloud API (api.e2b.dev / *.e2b.app), which this
development sandbox has no network route to - so unlike llm_client.py,
the actual round trip could not be tested end to end here. What *was*
verified against the installed `e2b-code-interpreter==2.9.1` package
(see docs/REFONTE_v0.4.md for the inspection session): the real
constructor is `Sandbox.create(...)`, not `Sandbox(...)` (deprecated);
file transfer is `sbx.files.write(path, bytes)` / `sbx.files.read(path,
format="bytes")`; execution is `sbx.run_code(script, timeout=...)`
returning an `Execution` with `.error` / `.logs.stdout` / `.logs.stderr`;
cleanup is `sbx.kill()`. That much is exercised by real (if offline) code
paths and type signatures, not guessed.

Set E2B_API_KEY (get one at e2b.dev) to turn this on. Unset, everything
behaves exactly as before this file existed - DataAnalysisAgent runs
in-process, zero behavior change. This is the same fallback contract as
llm_client.py: never a hard dependency, never a silent wrong answer.
"""
import os
from typing import Dict, List, Optional, Any

from ..config import settings

try:
    from e2b_code_interpreter import Sandbox
    _SDK_AVAILABLE = True
except ImportError:
    _SDK_AVAILABLE = False


def is_configured() -> bool:
    return _SDK_AVAILABLE and bool(settings.E2B_API_KEY or os.environ.get("E2B_API_KEY"))


class E2BSandboxRunner:
    def __init__(self):
        self.api_key = settings.E2B_API_KEY or os.environ.get("E2B_API_KEY")

    @property
    def available(self) -> bool:
        return _SDK_AVAILABLE and bool(self.api_key)

    def run_script(
        self, script: str,
        upload_files: Optional[Dict[str, str]] = None,
        download_files: Optional[List[str]] = None,
        download_dir: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Uploads local files into a fresh sandbox, runs `script` there,
        downloads named output files back to `download_dir`.

        upload_files: {remote_path: local_path}
        download_files: [remote_path, ...] to pull back after a successful run
        Returns {"success", "error", "stdout", "stderr", "downloaded": {remote: local}}.
        Never raises for a failed *script* (that's reported in the returned
        dict) - only raises if the sandbox itself can't be reached/created,
        which the caller (DataAnalysisAgent) catches to fall back in-process.
        """
        if not self.available:
            raise RuntimeError("E2B_API_KEY not configured")

        timeout = timeout or settings.E2B_TIMEOUT_SECONDS
        sbx = Sandbox.create(api_key=self.api_key, timeout=timeout)
        try:
            for remote_path, local_path in (upload_files or {}).items():
                with open(local_path, "rb") as f:
                    sbx.files.write(remote_path, f.read())

            execution = sbx.run_code(script, timeout=timeout)

            result: Dict[str, Any] = {
                "success": execution.error is None,
                "error": str(execution.error) if execution.error else None,
                "stdout": list(execution.logs.stdout) if execution.logs else [],
                "stderr": list(execution.logs.stderr) if execution.logs else [],
                "downloaded": {},
            }

            if result["success"] and download_files:
                os.makedirs(download_dir or ".", exist_ok=True)
                for remote_path in download_files:
                    try:
                        data = sbx.files.read(remote_path, format="bytes")
                        local_out = os.path.join(download_dir or ".", os.path.basename(remote_path))
                        with open(local_out, "wb") as f:
                            f.write(data)
                        result["downloaded"][remote_path] = local_out
                    except Exception as e:
                        result.setdefault("download_errors", {})[remote_path] = str(e)

            return result
        finally:
            try:
                sbx.kill()
            except Exception:
                pass  # best-effort cleanup - a leaked sandbox self-expires after `timeout`
