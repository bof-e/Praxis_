"""
Builds the standalone script E2BSandboxRunner executes remotely for
DataAnalysisAgent, by pulling the *actual source* of the cleaning
functions via inspect.getsource() instead of hand-copying them into a
second, parallel implementation. If those functions change in
agents/__init__.py, the sandboxed version changes with them automatically
- there is exactly one implementation of the cleaning algorithm, this
just also ships it to run somewhere else.

This split-brain risk (two copies of an algorithm silently drifting
apart) is exactly the kind of bug this file is designed to make
impossible, not just unlikely.
"""
import inspect
import re
import textwrap


def _strip_staticmethod(source: str) -> str:
    """@staticmethod only makes sense on a class body; the generated script
    turns these into plain module-level functions, so drop the decorator
    line rather than leave a broken/misleading artifact of the refactor."""
    lines = [ln for ln in source.splitlines() if ln.strip() != "@staticmethod"]
    return "\n".join(lines)


def _strip_class_prefix(source: str, class_name: str) -> str:
    """The extracted methods call siblings as DataAnalysisAgent._foo(...)
    (needed in the real class); once flattened into free functions in the
    generated script there is no DataAnalysisAgent to qualify against, so
    the prefix is stripped rather than shipping a class shim just to hold
    these calls together."""
    return re.sub(rf"\b{class_name}\.", "", source)


def _extract(func) -> str:
    return _strip_staticmethod(_strip_class_prefix(
        textwrap.dedent(inspect.getsource(func)), "DataAnalysisAgent"
    ))


def build_data_cleaning_script(strategy: str = "B", remote_filenames=None) -> str:
    from ..agents import _normalize, _find_sheet, DataAnalysisAgent

    remote_filenames = remote_filenames or ["input.xlsx"]

    parts = [
        "import json, os, re, unicodedata",
        "from typing import List, Dict, Optional, Any",
        "import pandas as pd",
        "",
        textwrap.dedent(inspect.getsource(_normalize)),
        textwrap.dedent(inspect.getsource(_find_sheet)),
        _extract(DataAnalysisAgent._parse_dictionary),
        _extract(DataAnalysisAgent._parse_dictionary_df),
        _extract(DataAnalysisAgent._looks_like_dictionary),
        _extract(DataAnalysisAgent._parse_notes),
        _extract(DataAnalysisAgent._load_sources),
        _extract(DataAnalysisAgent._clean),
        _extract(DataAnalysisAgent._describe),
    ]

    # Driver: same control flow as DataAnalysisAgent.execute(), operating
    # on the filenames the caller actually uploaded (CSV, Excel, or a mix -
    # _load_sources figures out which is which, same as in-process).
    driver = f'''
STRATEGY = {strategy!r}
REMOTE_FILENAMES = {remote_filenames!r}

loaded = _load_sources([{{"path": name}} for name in REMOTE_FILENAMES])
if "error" in loaded:
    raise RuntimeError(loaded["error"])
df, rules, field_notes = loaded["df"], loaded["rules"], loaded["field_notes"]

id_col = next((c for c in df.columns if "id" in _normalize(c)), None)

cleaned_df, audit_trail = _clean(df, rules, id_col, STRATEGY)
stats_summary = _describe(cleaned_df)

with pd.ExcelWriter("base_apuree.xlsx", engine="openpyxl") as writer:
    cleaned_df.to_excel(writer, sheet_name="Donnees_Apurees", index=False)
    pd.DataFrame(audit_trail).to_excel(writer, sheet_name="Journal_Anomalies", index=False)

with open("audit_trail.json", "w", encoding="utf-8") as f:
    json.dump({{
        "strategy": STRATEGY, "source_file": loaded["sources_description"],
        "n_rows_raw": int(len(df)), "n_rows_cleaned": int(len(cleaned_df)),
        "n_anomalies": len(audit_trail), "field_notes": field_notes,
        "anomalies": audit_trail,
    }}, f, ensure_ascii=False, indent=2, default=str)

with open("stats_summary.json", "w", encoding="utf-8") as f:
    json.dump(stats_summary, f, ensure_ascii=False, indent=2, default=str)

print(json.dumps({{"n_rows_raw": len(df), "n_rows_cleaned": len(cleaned_df), "n_anomalies": len(audit_trail)}}))
'''
    parts.append(driver)
    return "\n\n".join(parts)
