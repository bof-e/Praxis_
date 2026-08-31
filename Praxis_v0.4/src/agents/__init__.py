"""
Praxis v0.3 - Agents Package

Implements the Agent registry from §9:
- Understanding Agent
- Planning Agent
- Data Analysis Agent
- Document Agent
- Presentation Agent
- Research Agent
- Validation Agent
- Error Recovery Agent

Each Agent is responsible for a specific step type and uses appropriate Tools.

Contract (used by src/services/execution_engine.py):
    agent.execute(context: dict) -> dict with keys:
        status: "success" | "failed"
        summary: str (human-readable, stored in Execution.logs)
        error: str, present when status == "failed"
        artifacts: list of dicts, present when status == "success":
            {"role": str, "kind": ArtifactKind value, "format": str,
             "file_path": local path written by the agent, "metadata": dict}
        extra: optional dict of step-specific data the engine may act on
               (currently only used by ValidationAgent to report verdict/score)

`context` always contains: task_id, task_type, title, raw_request, objective,
domain, data_sources, hard_constraints, soft_preferences, work_dir, step,
previous_outputs (dict keyed by "role" -> {file_path, metadata, artifact_id}),
strategy, db (a live SQLAlchemy session — only ValidationAgent/ErrorRecoveryAgent
use it; the data/document/presentation agents are pure file-in/file-out and do
not touch the database, which keeps them safe to later move into an isolated
sandbox per §9/Phase 3 without changing their interface).
"""

from typing import Dict, List, Optional, Any
from abc import ABC, abstractmethod
import json
import os
import re
import unicodedata
from datetime import datetime

import pandas as pd


class BaseAgent(ABC):
    """Base class for all Praxis agents"""

    name: str = "BaseAgent"
    description: str = "Base agent"

    @abstractmethod
    def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the agent's step. See module docstring for the contract."""
        pass

    def get_capabilities(self) -> List[str]:
        """Return list of capabilities this agent provides"""
        return []


# ============================================================================
# UNDERSTANDING AGENT
# ============================================================================

class UnderstandingAgent(BaseAgent):
    """
    §9 - Understanding Agent

    Responsibilities:
    - Reformulate user request
    - Calculate readiness score
    - Identify missing information

    MVP note: reformulation is a deterministic heuristic, not an LLM call
    (no LLM is wired into Praxis yet - see docs/REFONTE_v0.4.md). The
    dimension assessment below is at least grounded in real signals
    (presence of data sources, length of the request, declared constraints)
    rather than fixed placeholder numbers.
    """

    name = "UnderstandingAgent"
    description = "Reformulates requests and assesses readiness signals"

    def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        raw_request = context.get("raw_request", "") or ""

        summary = f"Reformulation (heuristique): {raw_request[:180]}"
        result = {
            "status": "success",
            "summary": summary,
            "artifacts": [],
            "extra": {
                "reformulated_request": summary,
                "readiness_signals": self._assess_dimensions(context),
            },
        }
        return result

    def _assess_dimensions(self, context: Dict) -> Dict[str, float]:
        """Grounded (not LLM-based) heuristic signals - useful as a
        starting point for the user-facing readiness sliders, not a
        replacement for them."""
        raw_request = context.get("raw_request", "") or ""
        data_sources = context.get("data_sources", []) or []
        objective = context.get("objective", "") or ""

        return {
            "objectif": 0.8 if objective else (0.5 if len(raw_request) > 40 else 0.3),
            "contexte": min(1.0, len(raw_request) / 300),
            "donnees": 0.9 if data_sources else 0.2,
            "livrables": 0.6,
        }

    def get_capabilities(self) -> List[str]:
        return ["request_reformulation", "readiness_assessment", "gap_identification"]


# ============================================================================
# PLANNING AGENT
# ============================================================================

class PlanningAgent(BaseAgent):
    """
    §9 - Planning Agent (execution-time role: mostly used when a step
    explicitly needs re-planning; the initial Plan is created by
    TaskService.propose_plan before execution starts).
    """

    name = "PlanningAgent"
    description = "Creates or adjusts execution plans"

    def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "status": "success",
            "summary": "Plan déjà validé par l'utilisateur avant l'exécution — aucune replanification nécessaire.",
            "artifacts": [],
        }

    def get_capabilities(self) -> List[str]:
        return ["plan_generation", "autonomy_proposal", "dependency_mapping"]


# ============================================================================
# DATA ANALYSIS AGENT
# ============================================================================

def _normalize(text: str) -> str:
    """lowercase, strip accents/whitespace - for tolerant header/sheet matching"""
    if text is None:
        return ""
    text = str(text).strip().lower()
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text


def _find_sheet(sheet_names: List[str], keywords: List[str]) -> Optional[str]:
    for name in sheet_names:
        norm = _normalize(name)
        if any(kw in norm for kw in keywords):
            return name
    return None


def _load_json(ref: Optional[Dict]) -> Optional[Dict]:
    """Shared by every agent that reads a JSON artifact another step
    produced (audit trails, stats, treatment effects, research sources) -
    a single implementation rather than one copy per agent class."""
    if not ref:
        return None
    path = ref.get("file_path")
    if not path or not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _extract_raw_material(data_sources: List) -> Optional[str]:
    """Reads any uploaded .txt/.md/.docx as raw material - the user's own
    notes/report to structure (redaction-type documents) or to cross with
    a quantitative finding (DocumentAgent/PresentationAgent's qualitative-
    crossing sections). Excel/CSV files are silently skipped - those are
    DataAnalysisAgent's job. PDF is a known gap (not in requirements.txt;
    would need the pdf skill's extraction tooling) - see docs/REFONTE_v0.4.md.
    Shared across agents rather than duplicated per class."""
    parts = []
    for ds in data_sources:
        path = ds.get("path") if isinstance(ds, dict) else ds
        if not path or not os.path.exists(path):
            continue
        ext = os.path.splitext(path)[1].lower()
        text = None
        try:
            if ext in (".txt", ".md"):
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read()
            elif ext == ".docx":
                from docx import Document as SourceDocument
                source_doc = SourceDocument(path)
                text = "\n".join(p.text for p in source_doc.paragraphs if p.text.strip())
        except Exception:
            text = None
        if text and text.strip():
            parts.append(text.strip())

    if not parts:
        return None
    # Cap length so a very long source doesn't blow the LLM context
    # budget or make the "no LLM" direct-inclusion fallback unreadable.
    combined = "\n\n---\n\n".join(parts)
    return combined[:12000]


def _render_treatment_effect_chart(treatment_effect: Dict, work_dir: Optional[str]) -> Optional[str]:
    """A grouped bar chart (mean outcome pre/post, traité vs contrôle) -
    the single visual that best communicates a DiD result at a glance.
    Returns None (never raises) if matplotlib fails for any reason - the
    deliverable still works, just without the chart. Shared by
    DocumentAgent and PresentationAgent so the docx and the pptx show the
    exact same figure rather than two different renderings of it."""
    if not work_dir:
        return None
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        labels = ["Avant", "Après"]
        treated_vals = [treatment_effect["mean_pre_treated"], treatment_effect["mean_post_treated"]]
        control_vals = [treatment_effect["mean_pre_control"], treatment_effect["mean_post_control"]]

        x = range(len(labels))
        width = 0.35
        fig, ax = plt.subplots(figsize=(6, 4))
        ax.bar([i - width / 2 for i in x], treated_vals, width, label="Bénéficiaires", color="#1f5c4d")
        ax.bar([i + width / 2 for i in x], control_vals, width, label="Groupe de contrôle", color="#a15c1c")
        ax.set_xticks(list(x))
        ax.set_xticklabels(labels)
        ax.set_ylabel(treatment_effect["outcome"].split(" - ")[0].replace("_post", ""))
        ax.set_title("Évolution moyenne du résultat par groupe")
        ax.legend()
        fig.tight_layout()

        chart_path = os.path.join(work_dir, "graphique_effet_traitement.png")
        fig.savefig(chart_path, dpi=150)
        plt.close(fig)
        return chart_path
    except Exception:
        return None


class DataAnalysisAgent(BaseAgent):
    """
    §9 - Data Analysis Agent

    Responsibilities (Phase 2 target - now implemented):
    - Read a raw-data Excel workbook (raw data sheet + optional dictionary
      sheet defining validation rules + optional field-notes sheet)
    - Detect anomalies: duplicate rows and out-of-range / disallowed values
    - Apply a cleaning strategy (Strategy B by default: recode invalid cells
      to NA and drop exact duplicates, sample otherwise preserved; Strategy A
      available: drop any row touched by an anomaly)
    - Produce a cleaned dataset, a structured audit trail (for the "Limites"
      section and Audit_Trail deliverable) and a descriptive-stats summary
      the Document/Presentation agents consume

    Tools used: pandas, openpyxl (both already in requirements.txt).
    """

    name = "DataAnalysisAgent"
    description = "Cleans and analyzes tabular data with a traceable audit trail"

    def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        step_type = _normalize((context.get("step") or {}).get("type", ""))
        previous_outputs = context.get("previous_outputs", {}) or {}

        # If cleaning already happened earlier in this same run (e.g. the
        # plan has both a "nettoyage" and a separate "analyse" step), the
        # second call just confirms/reuses the existing outputs instead of
        # redoing the work.
        # If cleaning already happened earlier in this same run, a
        # subsequent "analyse"/"statistique" step performs the deeper
        # statistical pass (imputation, outlier treatment, derived
        # variables, treatment-effect estimation - see _run_enrichment)
        # rather than a no-op. Falls through to the full pipeline below
        # only if "analyse" runs standalone, without a prior "nettoyage".
        if step_type in ("analyse", "statistique") and "cleaned_data" in previous_outputs and "stats_summary" in previous_outputs:
            return self._run_enrichment(previous_outputs, context)

        data_sources = context.get("data_sources") or []
        if not data_sources:
            return {
                "status": "failed",
                "error": "no_data_source",
                "summary": "Aucune source de données n'est associée à cette tâche.",
            }

        source_path = data_sources[0].get("path") if isinstance(data_sources[0], dict) else data_sources[0]
        if not source_path or not os.path.exists(source_path):
            return {
                "status": "failed",
                "error": "data_source_unreadable",
                "summary": f"Fichier de données introuvable: {source_path}",
            }

        strategy = context.get("strategy") or "B"
        work_dir = context["work_dir"]
        os.makedirs(work_dir, exist_ok=True)

        sandbox_result = self._try_sandbox(data_sources, strategy, work_dir)
        if sandbox_result is not None:
            return sandbox_result

        loaded = self._load_sources(data_sources)
        if "error" in loaded:
            return {
                "status": "failed",
                "error": "data_source_unreadable",
                "summary": loaded["error"],
            }
        df, rules, field_notes = loaded["df"], loaded["rules"], loaded["field_notes"]

        id_col = next((c for c in df.columns if "id" in _normalize(c)), None)

        cleaned_df, audit_trail = self._clean(df, rules, id_col, strategy)

        stats_summary = self._describe(cleaned_df)

        cleaned_path = os.path.join(work_dir, "base_apuree.xlsx")
        with pd.ExcelWriter(cleaned_path, engine="openpyxl") as writer:
            cleaned_df.to_excel(writer, sheet_name="Donnees_Apurees", index=False)
            pd.DataFrame(audit_trail).to_excel(writer, sheet_name="Journal_Anomalies", index=False)

        audit_path = os.path.join(work_dir, "audit_trail.json")
        with open(audit_path, "w", encoding="utf-8") as f:
            json.dump({
                "strategy": strategy,
                "source_file": os.path.basename(source_path),
                "n_rows_raw": int(len(df)),
                "n_rows_cleaned": int(len(cleaned_df)),
                "n_anomalies": len(audit_trail),
                "field_notes": field_notes,
                "anomalies": audit_trail,
            }, f, ensure_ascii=False, indent=2, default=str)

        stats_path = os.path.join(work_dir, "stats_summary.json")
        with open(stats_path, "w", encoding="utf-8") as f:
            json.dump(stats_summary, f, ensure_ascii=False, indent=2, default=str)

        summary = (
            f"{len(df)} lignes lues, {len(cleaned_df)} conservées après nettoyage "
            f"({len(audit_trail)} anomalies traitées selon la stratégie {strategy})."
        )

        return {
            "status": "success",
            "summary": summary,
            "artifacts": [
                {
                    "role": "cleaned_data",
                    "kind": "processed_data",
                    "format": "xlsx",
                    "file_path": cleaned_path,
                    "metadata": {
                        "n_rows_raw": int(len(df)),
                        "n_rows_cleaned": int(len(cleaned_df)),
                        "n_anomalies": len(audit_trail),
                        "strategy": strategy,
                    },
                },
                {
                    "role": "audit_trail",
                    "kind": "analysis",
                    "format": "json",
                    "file_path": audit_path,
                    "metadata": {"subtype": "audit_trail"},
                },
                {
                    "role": "stats_summary",
                    "kind": "analysis",
                    "format": "json",
                    "file_path": stats_path,
                    "metadata": {"subtype": "stats_summary"},
                },
            ],
        }

    def _try_sandbox(self, data_sources: List, strategy: str, work_dir: str) -> Optional[Dict[str, Any]]:
        """Attempts the E2B path; returns the same result shape as the
        in-process path on success, or None (never a partial/broken result)
        to fall through to it - unavailable, unconfigured, and any runtime
        failure (network, quota, timeout) are all treated the same way:
        silently defer to the code that has run in every test in this repo."""
        from ..services import sandbox as sandbox_module
        from ..services.sandbox_scripts import build_data_cleaning_script

        runner = sandbox_module.E2BSandboxRunner()
        if not runner.available:
            return None

        # Upload every source under its own original filename (preserving
        # extension) - the generated script's _load_sources call needs the
        # real extension to know whether a file is CSV or Excel, exactly
        # like the in-process path does.
        upload_files = {}
        remote_names = []
        for i, ds in enumerate(data_sources):
            path = ds.get("path") if isinstance(ds, dict) else ds
            if not path:
                continue
            remote_name = f"source_{i}{os.path.splitext(path)[1].lower()}"
            upload_files[remote_name] = path
            remote_names.append(remote_name)
        if not remote_names:
            return None

        try:
            script = build_data_cleaning_script(strategy, remote_names)
            result = runner.run_script(
                script=script,
                upload_files=upload_files,
                download_files=["base_apuree.xlsx", "audit_trail.json", "stats_summary.json"],
                download_dir=work_dir,
            )
        except Exception:
            return None  # sandbox unreachable/misconfigured - fall back silently

        if not result["success"] or len(result["downloaded"]) < 3:
            return None  # script failed inside the sandbox - fall back rather than report a false success

        audit_path = result["downloaded"]["audit_trail.json"]
        with open(audit_path, "r", encoding="utf-8") as f:
            audit = json.load(f)

        summary = (
            f"[Exécuté dans un sandbox E2B isolé] {audit['n_rows_raw']} lignes lues, "
            f"{audit['n_rows_cleaned']} conservées après nettoyage "
            f"({audit['n_anomalies']} anomalies traitées selon la stratégie {strategy})."
        )
        return {
            "status": "success",
            "summary": summary,
            "artifacts": [
                {
                    "role": "cleaned_data", "kind": "processed_data", "format": "xlsx",
                    "file_path": result["downloaded"]["base_apuree.xlsx"],
                    "metadata": {
                        "n_rows_raw": audit["n_rows_raw"], "n_rows_cleaned": audit["n_rows_cleaned"],
                        "n_anomalies": audit["n_anomalies"], "strategy": strategy, "sandboxed": True,
                    },
                },
                {
                    "role": "audit_trail", "kind": "analysis", "format": "json",
                    "file_path": audit_path, "metadata": {"subtype": "audit_trail", "sandboxed": True},
                },
                {
                    "role": "stats_summary", "kind": "analysis", "format": "json",
                    "file_path": result["downloaded"]["stats_summary.json"],
                    "metadata": {"subtype": "stats_summary", "sandboxed": True},
                },
            ],
        }

    # ------------------------------------------------------------------
    @staticmethod
    def _parse_dictionary(xls: pd.ExcelFile, dict_sheet: str) -> Dict[str, Dict]:
        """Dictionary sheet expected columns (tolerant to naming/order):
        variable, type (numeric/text/date), min, max, allowed_values
        (comma-separated). Missing columns degrade gracefully - a rule
        just won't be checked."""
        try:
            dict_df = xls.parse(dict_sheet)
        except Exception:
            return {}
        return DataAnalysisAgent._parse_dictionary_df(dict_df)

    @staticmethod
    def _parse_dictionary_df(dict_df: pd.DataFrame) -> Dict[str, Dict]:
        """Same parsing as _parse_dictionary, decoupled from Excel so a
        standalone dictionary file (e.g. dictionnaire.csv uploaded
        alongside data.csv) can use it too - see _load_sources."""
        colmap = {_normalize(c): c for c in dict_df.columns}
        var_col = colmap.get("variable") or colmap.get("champ") or colmap.get("nom_variable")
        if not var_col:
            return {}

        rules = {}
        for _, row in dict_df.iterrows():
            var = row.get(var_col)
            if pd.isna(var):
                continue
            var = str(var).strip()
            rule = {}
            type_col = colmap.get("type")
            if type_col and not pd.isna(row.get(type_col)):
                rule["type"] = _normalize(row.get(type_col))
            min_col = colmap.get("min") or colmap.get("minimum") or colmap.get("valeur_min")
            if min_col and not pd.isna(row.get(min_col)):
                rule["min"] = row.get(min_col)
            max_col = colmap.get("max") or colmap.get("maximum") or colmap.get("valeur_max")
            if max_col and not pd.isna(row.get(max_col)):
                rule["max"] = row.get(max_col)
            allowed_col = colmap.get("allowed_values") or colmap.get("valeurs_autorisees") or colmap.get("modalites")
            if allowed_col and not pd.isna(row.get(allowed_col)):
                rule["allowed_values"] = [v.strip() for v in str(row.get(allowed_col)).split(",")]
            if rule:
                rules[var] = rule
        return rules

    @staticmethod
    def _looks_like_dictionary(df: pd.DataFrame) -> bool:
        """Heuristic for telling a standalone dictionary file apart from a
        raw-data file when several files are uploaded together (see
        _load_sources) - a dictionary has a 'variable'-ish column naming
        the fields it documents."""
        normalized_cols = {_normalize(c) for c in df.columns}
        return bool(normalized_cols & {"variable", "champ", "nom_variable"})

    @staticmethod
    def _load_sources(data_sources: List) -> Dict[str, Any]:
        """Loads raw data (+ optional dictionary/notes) from one or more
        uploaded files - the single point of format support, so CSV vs
        Excel (and single-workbook-with-sheets vs several separate files)
        is handled once, the same way in-process and inside the E2B
        sandbox script (see sandbox_scripts.py, which extracts this exact
        method's source via inspect.getsource).

        Two shapes both work:
        - one Excel workbook with raw/dictionnaire/notes as separate sheets
          (the original pilot-case shape), or
        - separate uploaded files: a data.csv (or .xlsx) for the raw table,
          optionally a dictionnaire.csv/.xlsx (recognized by a 'variable'
          column) and/or a notes.txt/.md.

        Returns {"df", "rules", "field_notes", "sources_description"} or
        {"error": str} - never raises, so callers can treat any outcome
        uniformly."""
        paths = []
        for ds in data_sources:
            path = ds.get("path") if isinstance(ds, dict) else ds
            if path:
                paths.append(path)
        if not paths:
            return {"error": "Aucune source de données fournie."}

        raw_df = None
        rules: Dict[str, Dict] = {}
        field_notes: List[str] = []
        descriptions = []

        for path in paths:
            if not os.path.exists(path):
                return {"error": f"Fichier introuvable: {path}"}
            ext = os.path.splitext(path)[1].lower()
            try:
                if ext == ".csv":
                    df = pd.read_csv(path)
                    df.columns = [str(c) for c in df.columns]
                    if DataAnalysisAgent._looks_like_dictionary(df):
                        rules.update(DataAnalysisAgent._parse_dictionary_df(df))
                    elif raw_df is None:
                        raw_df = df
                        descriptions.append(os.path.basename(path))
                elif ext in (".xlsx", ".xls"):
                    xls = pd.ExcelFile(path)
                    sheet_names = xls.sheet_names
                    raw_sheet = _find_sheet(sheet_names, ["brut", "raw", "data", "donnee"])
                    dict_sheet = _find_sheet(sheet_names, ["dictionnaire", "dictionary", "dict"])
                    notes_sheet = _find_sheet(sheet_names, ["note", "terrain", "field"])
                    if raw_sheet is None and raw_df is None:
                        raw_sheet = sheet_names[0]
                    if raw_sheet and raw_df is None:
                        df = xls.parse(raw_sheet)
                        df.columns = [str(c) for c in df.columns]
                        raw_df = df
                        descriptions.append(f"{os.path.basename(path)}::{raw_sheet}")
                    if dict_sheet:
                        rules.update(DataAnalysisAgent._parse_dictionary(xls, dict_sheet))
                    if notes_sheet:
                        field_notes.extend(DataAnalysisAgent._parse_notes(xls, notes_sheet))
                elif ext in (".txt", ".md"):
                    # A plain-text upload among the sources is treated as
                    # field notes - the CSV/multi-file equivalent of a
                    # Notes_Terrain sheet.
                    with open(path, "r", encoding="utf-8", errors="ignore") as f:
                        text = f.read().strip()
                    if text:
                        field_notes.append(text)
                else:
                    return {"error": f"Format non pris en charge: {ext} (formats acceptés: .csv, .xlsx, .xls, .txt, .md)"}
            except Exception as e:
                return {"error": f"Impossible de lire {os.path.basename(path)}: {e}"}

        if raw_df is None:
            return {"error": "Aucun fichier de données tabulaires (.csv/.xlsx) reconnu parmi les sources fournies."}

        return {
            "df": raw_df, "rules": rules, "field_notes": field_notes,
            "sources_description": ", ".join(descriptions) or "source inconnue",
        }

    @staticmethod
    def _parse_notes(xls: pd.ExcelFile, notes_sheet: str) -> List[str]:
        """Extract free-text field notes. Prefers a column whose header
        looks like a note/comment column; falls back to any column with
        long-ish text values (skips short id-like columns such as
        'menage'/'id_menage' which aren't notes themselves)."""
        try:
            notes_df = xls.parse(notes_sheet)
        except Exception:
            return []

        note_cols = [c for c in notes_df.columns if any(
            kw in _normalize(c) for kw in ("note", "remarque", "commentaire", "observation")
        )]
        candidate_cols = note_cols or list(notes_df.columns)

        notes = []
        for col in candidate_cols:
            for val in notes_df[col].dropna().tolist():
                text = str(val).strip()
                # A real note reads as a sentence, not a short code/id
                if len(text) > 15 and " " in text:
                    notes.append(text)
        return notes[:50]

    @staticmethod
    def _clean(df: pd.DataFrame, rules: Dict[str, Dict], id_col: Optional[str], strategy: str):
        df = df.copy()
        audit: List[Dict] = []
        flagged_rows = set()

        # 1) out-of-range / disallowed values per dictionary rule
        for var, rule in rules.items():
            if var not in df.columns:
                continue
            for idx, value in df[var].items():
                if pd.isna(value):
                    continue
                is_anomaly = False
                reason = None
                if "min" in rule or "max" in rule:
                    try:
                        numeric_value = float(value)
                        if "min" in rule and numeric_value < float(rule["min"]):
                            is_anomaly, reason = True, f"< min ({rule['min']})"
                        elif "max" in rule and numeric_value > float(rule["max"]):
                            is_anomaly, reason = True, f"> max ({rule['max']})"
                    except (ValueError, TypeError):
                        pass
                if not is_anomaly and "allowed_values" in rule:
                    if str(value).strip() not in rule["allowed_values"]:
                        is_anomaly, reason = True, "valeur hors liste autorisée"

                if is_anomaly:
                    row_id = df.at[idx, id_col] if id_col else idx
                    audit.append({
                        "row_id": row_id,
                        "variable": var,
                        "original_value": value,
                        "reason": reason,
                        "action": "excluded_row" if strategy == "A" else "recoded_to_na",
                    })
                    if strategy == "A":
                        flagged_rows.add(idx)
                    else:
                        df.at[idx, var] = None

        # 2) duplicates
        dup_subset = [id_col] if id_col else list(df.columns)
        is_dup = df.duplicated(subset=dup_subset, keep="first")
        for idx in df[is_dup].index:
            row_id = df.at[idx, id_col] if id_col else idx
            audit.append({
                "row_id": row_id,
                "variable": id_col or "(ligne complète)",
                "original_value": df.at[idx, id_col] if id_col else None,
                "reason": "doublon",
                "action": "excluded_duplicate",
            })
            flagged_rows.add(idx)

        if flagged_rows:
            df = df.drop(index=list(flagged_rows))

        return df.reset_index(drop=True), audit

    @staticmethod
    def _describe(df: pd.DataFrame) -> Dict[str, Any]:
        summary = {"n_observations": int(len(df)), "variables": {}}
        for col in df.columns:
            series = df[col]
            if pd.api.types.is_numeric_dtype(series):
                summary["variables"][col] = {
                    "type": "numeric",
                    "n_valid": int(series.notna().sum()),
                    "n_missing": int(series.isna().sum()),
                    "mean": round(float(series.mean()), 2) if series.notna().any() else None,
                    "min": round(float(series.min()), 2) if series.notna().any() else None,
                    "max": round(float(series.max()), 2) if series.notna().any() else None,
                }
            else:
                top = series.dropna().astype(str).value_counts().head(5).to_dict()
                summary["variables"][col] = {
                    "type": "categorical",
                    "n_valid": int(series.notna().sum()),
                    "n_missing": int(series.isna().sum()),
                    "top_values": top,
                }
        return summary

    def _run_enrichment(self, previous_outputs: Dict[str, Dict], context: Dict[str, Any]) -> Dict[str, Any]:
        """Second analytical pass, run when an "analyse" step follows a
        "nettoyage" step that already produced a cleaned dataset (the
        ANALYSE_DONNEES/RAPPORT_EVALUATION plan shape). Adds real
        statistical depth beyond dictionary-rule cleaning: missing-value
        imputation, outlier treatment (IQR), auto-detected pre/post
        derived variables, and - when a binary treatment column plus a
        pre/post outcome pair are both present - a genuine treatment-
        effect estimate (§ _estimate_treatment_effect), computed from the
        actual data, never fabricated. Writes its own artifacts/audit
        trail rather than overwriting nettoyage's, so both stages stay
        independently traceable (explain_row still finds the original
        cleaning anomalies under role "audit_trail")."""
        cleaned_ref = previous_outputs["cleaned_data"]
        df = pd.read_excel(cleaned_ref["file_path"], sheet_name="Donnees_Apurees")

        work_dir = context["work_dir"]
        os.makedirs(work_dir, exist_ok=True)

        id_col = next((c for c in df.columns if "id" in _normalize(c)), None)
        enriched_df, imputations, outlier_caps, derived = self._enrich(df, id_col)
        treatment_effect = self._estimate_treatment_effect(enriched_df)
        stats_summary = self._describe(enriched_df)

        enriched_path = os.path.join(work_dir, "base_enrichie.xlsx")
        with pd.ExcelWriter(enriched_path, engine="openpyxl") as writer:
            enriched_df.to_excel(writer, sheet_name="Donnees_Enrichies", index=False)

        enrichment_log = {
            "n_imputations": len(imputations), "imputations": imputations,
            "n_outliers_traites": len(outlier_caps), "outliers_traites": outlier_caps,
            "variables_derivees": derived,
        }
        enrichment_path = os.path.join(work_dir, "audit_trail_enrichissement.json")
        with open(enrichment_path, "w", encoding="utf-8") as f:
            json.dump(enrichment_log, f, ensure_ascii=False, indent=2, default=str)

        stats_path = os.path.join(work_dir, "stats_summary.json")
        with open(stats_path, "w", encoding="utf-8") as f:
            json.dump(stats_summary, f, ensure_ascii=False, indent=2, default=str)

        artifacts = [
            {
                "role": "enriched_data", "kind": "processed_data", "format": "xlsx",
                "file_path": enriched_path,
                "metadata": {"n_imputations": len(imputations), "n_outliers_traites": len(outlier_caps)},
            },
            {
                "role": "audit_trail_enrichissement", "kind": "analysis", "format": "json",
                "file_path": enrichment_path, "metadata": {"subtype": "audit_trail_enrichissement"},
            },
            {
                # Overwrites previous_outputs["stats_summary"] with the
                # enriched numbers - DocumentAgent's existing Résultats
                # table picks this up automatically, no change needed there.
                "role": "stats_summary", "kind": "analysis", "format": "json",
                "file_path": stats_path, "metadata": {"subtype": "stats_summary"},
            },
        ]

        summary_parts = [
            f"{len(imputations)} valeur(s) imputée(s)",
            f"{len(outlier_caps)} valeur(s) aberrante(s) plafonnée(s) (méthode IQR)",
        ]
        if derived:
            summary_parts.append(f"{len(derived)} variable(s) dérivée(s) créée(s)")

        if treatment_effect:
            te_path = os.path.join(work_dir, "treatment_effect.json")
            with open(te_path, "w", encoding="utf-8") as f:
                json.dump(treatment_effect, f, ensure_ascii=False, indent=2, default=str)
            artifacts.append({
                "role": "treatment_effect", "kind": "analysis", "format": "json",
                "file_path": te_path, "metadata": {"subtype": "treatment_effect"},
            })
            summary_parts.append(
                f"effet de traitement estimé sur {treatment_effect['outcome']}: "
                f"{treatment_effect['ate']:+.0f} (p={treatment_effect['p_value']:.3f})"
            )

        return {
            "status": "success",
            "summary": "Analyse statistique approfondie : " + ", ".join(summary_parts) + ".",
            "artifacts": artifacts,
        }

    @staticmethod
    def _enrich(df: pd.DataFrame, id_col: Optional[str]):
        """Missing-value imputation (group-mean when a sensible grouping
        column exists, else overall mean) + IQR outlier capping, on
        numeric columns only - excluding id-like and near-binary columns
        (treatment flags, 0/1 codes) since capping/imputing those would
        corrupt categorical meaning rather than clean data. Also
        auto-detects "<prefix>_pre"/"<prefix>_post" column pairs and
        derives a binary "<prefix>_impact_score" (1 if post > pre) -
        exactly the kind of variable a before/after impact evaluation
        needs, without requiring the user to specify it by hand."""
        df = df.copy()
        imputations: List[Dict] = []
        outlier_caps: List[Dict] = []
        derived: List[Dict] = []

        numeric_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
        target_cols = [
            c for c in numeric_cols
            if c != id_col and "id" not in _normalize(c) and df[c].dropna().nunique() > 2
        ]

        group_col = next(
            (c for c in df.columns if not pd.api.types.is_numeric_dtype(df[c])
             and 1 < df[c].nunique() <= 50),
            None
        )

        # Imputation/capping can introduce float bounds into originally
        # integer columns (e.g. age_chef capped to 78.38) - cast upfront
        # rather than let pandas silently warn/deprecate on a per-cell
        # int->float assignment later.
        if target_cols:
            df[target_cols] = df[target_cols].astype(float)

        for col in target_cols:
            missing_mask = df[col].isna()
            if not missing_mask.any():
                continue
            if group_col:
                group_means = df.groupby(group_col)[col].transform("mean")
                fill_values = group_means.fillna(df[col].mean())
                method = f"moyenne_par_groupe({group_col})"
            else:
                fill_values = pd.Series(df[col].mean(), index=df.index)
                method = "moyenne_globale"
            for idx in df[missing_mask].index:
                row_id = df.at[idx, id_col] if id_col else idx
                imputed_value = round(float(fill_values.loc[idx]), 2)
                imputations.append({
                    "row_id": row_id, "variable": col,
                    "imputed_value": imputed_value, "method": method,
                })
                df.at[idx, col] = imputed_value

        for col in target_cols:
            series = df[col].dropna()
            if len(series) < 4:
                continue
            q1, q3 = series.quantile(0.25), series.quantile(0.75)
            iqr = q3 - q1
            if iqr == 0:
                continue
            lower, upper = q1 - 1.5 * iqr, q3 + 1.5 * iqr
            for idx, value in df[col].items():
                if pd.isna(value) or lower <= value <= upper:
                    continue
                bound = round(float(lower if value < lower else upper), 2)
                row_id = df.at[idx, id_col] if id_col else idx
                outlier_caps.append({
                    "row_id": row_id, "variable": col,
                    "original_value": round(float(value), 2), "capped_value": bound,
                    "method": "iqr",
                })
                df.at[idx, col] = bound

        prefixes: Dict[str, Dict[str, str]] = {}
        for col in df.columns:
            norm = _normalize(col)
            if norm.endswith("_pre"):
                prefixes.setdefault(norm[:-4], {})["pre"] = col
            elif norm.endswith("_post"):
                prefixes.setdefault(norm[:-5], {})["post"] = col
        for prefix, pair in prefixes.items():
            if "pre" not in pair or "post" not in pair:
                continue
            new_col = f"{prefix}_impact_score" if prefix else "impact_score"
            if new_col in df.columns:
                continue
            df[new_col] = (df[pair["post"]] > df[pair["pre"]]).astype("Int64")
            df.loc[df[pair["pre"]].isna() | df[pair["post"]].isna(), new_col] = pd.NA
            derived.append({
                "name": new_col, "rule": f"{pair['post']} > {pair['pre']}",
                "n_positive": int((df[new_col] == 1).sum()),
                "n_negative": int((df[new_col] == 0).sum()),
            })

        return df, imputations, outlier_caps, derived

    @staticmethod
    def _estimate_treatment_effect(df: pd.DataFrame) -> Optional[Dict[str, Any]]:
        """Difference-in-Differences: regresses the pre->post outcome
        change on treatment status. Only runs when the data actually
        contains what that needs - a binary 0/1 treatment-like column and
        a pre/post outcome pair - and reports statistics computed for
        real from the uploaded data, never the illustrative numbers a
        request's example might mention. Uses statsmodels OLS when
        available (proper standard errors/CI), falls back to a Welch
        t-test via scipy otherwise - both are genuine computations, not a
        difference in rigor that matters for a personal tool at this scale."""
        treatment_col = next(
            (c for c in df.columns if _normalize(c) in {
                "beneficiaire", "beneficiaires", "traite", "treatment", "treated",
                "groupe_traitement", "beneficiary", "participant",
            } and set(pd.Series(df[c].dropna().unique()).tolist()) <= {0, 1}),
            None
        )
        if not treatment_col:
            return None

        pre_col = next((c for c in df.columns if _normalize(c).endswith("_pre")), None)
        post_col = next((c for c in df.columns if _normalize(c).endswith("_post")), None)
        if not (pre_col and post_col):
            return None

        subset = df[[treatment_col, pre_col, post_col]].dropna()
        if len(subset) < 10 or subset[treatment_col].nunique() < 2:
            return None

        outcome_change = (subset[post_col] - subset[pre_col]).astype(float)
        treatment = subset[treatment_col].astype(float)

        try:
            import statsmodels.api as sm
            X = sm.add_constant(treatment)
            model = sm.OLS(outcome_change, X).fit()
            ate = float(model.params[treatment_col])
            p_value = float(model.pvalues[treatment_col])
            ci_low, ci_high = model.conf_int().loc[treatment_col].tolist()
            method = (
                "Différence-en-différences (régression OLS de la variation de "
                f"{post_col.replace('_post', '')} sur le statut de traitement, erreurs-types classiques)"
            )
        except ImportError:
            from scipy import stats as scipy_stats
            treated = outcome_change[treatment == 1]
            control = outcome_change[treatment == 0]
            ate = float(treated.mean() - control.mean())
            _, p_value = scipy_stats.ttest_ind(treated, control, equal_var=False)
            se = float((treated.std() ** 2 / len(treated) + control.std() ** 2 / len(control)) ** 0.5)
            ci_low, ci_high = ate - 1.96 * se, ate + 1.96 * se
            method = (
                "Différence-en-différences (test t de Welch sur la variation de "
                f"{post_col.replace('_post', '')} entre groupes traité/contrôle)"
            )

        n_treated = int((subset[treatment_col] == 1).sum())
        n_control = int((subset[treatment_col] == 0).sum())

        return {
            "method": method,
            "treatment_variable": treatment_col,
            "outcome": f"{post_col} - {pre_col}",
            "ate": round(ate, 2),
            "p_value": round(float(p_value), 4),
            "ci_95": [round(float(ci_low), 2), round(float(ci_high), 2)],
            "n_treated": n_treated, "n_control": n_control,
            "significant_at_5pct": bool(p_value < 0.05),
            "mean_pre_treated": round(float(subset.loc[subset[treatment_col] == 1, pre_col].mean()), 2),
            "mean_post_treated": round(float(subset.loc[subset[treatment_col] == 1, post_col].mean()), 2),
            "mean_pre_control": round(float(subset.loc[subset[treatment_col] == 0, pre_col].mean()), 2),
            "mean_post_control": round(float(subset.loc[subset[treatment_col] == 0, post_col].mean()), 2),
        }

    def get_capabilities(self) -> List[str]:
        return ["data_cleaning", "anomaly_detection", "descriptive_statistics"]


# ============================================================================
# DOCUMENT AGENT
# ============================================================================

class DocumentAgent(BaseAgent):
    """
    §9 - Document Agent

    Responsibilities: write and format the report as a real .docx, pulling
    numbers and anomaly details from the Data Analysis Agent's outputs so
    every figure in the "Résultats" / "Limites" sections traces back to
    audit_trail.json / stats_summary.json (§6.4 - Sources et Evidence).

    Tools used: python-docx.
    """

    name = "DocumentAgent"
    description = "Writes the evaluation report as a Word document"

    # Task types whose deliverable is built from DataAnalysisAgent's output
    # (stats/audit trail). Everything else gets the content-driven path.
    DATA_DRIVEN_TYPES = {"analyse_donnees", "rapport_evaluation"}

    def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        from docx import Document

        previous_outputs = context.get("previous_outputs", {}) or {}
        stats = _load_json(previous_outputs.get("stats_summary"))
        audit = _load_json(previous_outputs.get("audit_trail"))
        enrichment = _load_json(previous_outputs.get("audit_trail_enrichissement"))
        treatment_effect = _load_json(previous_outputs.get("treatment_effect"))
        sources = _load_json(previous_outputs.get("research_sources"))
        # A qualitative document (interview notes, field report) can be
        # uploaded alongside the quantitative data file - _load_sources
        # (DataAnalysisAgent) already ignores it as raw data since it isn't
        # tabular; here it becomes the material for a qualitative-crossing
        # section rather than being silently dropped.
        raw_material = _extract_raw_material(context.get("data_sources") or [])

        doc = Document()
        title = context.get("title") or "Rapport"
        doc.add_heading(title, level=0)

        task_type = context.get("task_type")
        work_dir = context["work_dir"]
        # A task not typed as data-driven but that happens to have run a
        # cleaning step anyway (e.g. someone attached a file to a redaction
        # task) still gets the richer data sections - branch on what
        # actually ran, not just the declared type.
        if task_type in self.DATA_DRIVEN_TYPES or stats or audit:
            self._build_data_sections(
                doc, stats, audit, enrichment, treatment_effect, work_dir,
                title=title, task_type=task_type, objective=context.get("objective"),
                raw_request=context.get("raw_request", ""),
            )
            if raw_material:
                self._build_qualitative_crossing_section(doc, treatment_effect, stats, raw_material)
        else:
            self._build_content_sections(doc, context, sources)

        self._add_sources_section(doc, sources)

        doc.add_heading("Conclusion", level=1)
        if task_type in self.DATA_DRIVEN_TYPES or stats or audit:
            conclusion = (
                context.get("objective")
                or "Les résultats ci-dessus répondent à la demande initiale ; voir les artefacts "
                   "de données pour la traçabilité complète (base apurée, journal des anomalies)."
            )
        else:
            conclusion = (
                context.get("objective")
                or "Le contenu ci-dessus répond à la demande initiale ; voir la section Sources "
                   "pour les références utilisées."
            )
        doc.add_paragraph(conclusion)

        os.makedirs(work_dir, exist_ok=True)
        out_path = os.path.join(work_dir, "rapport.docx")
        doc.save(out_path)

        word_count = sum(len(p.text.split()) for p in doc.paragraphs)

        return {
            "status": "success",
            "summary": f"Rapport DOCX généré ({word_count} mots environ).",
            "artifacts": [
                {
                    "role": "report_docx",
                    "kind": "draft",
                    "format": "docx",
                    "file_path": out_path,
                    "metadata": {"word_count": word_count, "language": "fr"},
                }
            ],
        }

    def _build_data_sections(
        self, doc, stats: Optional[Dict], audit: Optional[Dict],
        enrichment: Optional[Dict] = None, treatment_effect: Optional[Dict] = None,
        work_dir: Optional[str] = None, title: str = "", task_type: str = "",
        objective: Optional[str] = None, raw_request: str = "",
    ) -> None:
        """analyse_donnees / rapport_evaluation: Résumé exécutif, Méthodologie,
        Résultats (table), Limites (audit trail), plus - when the analyse
        step went beyond dictionary-rule cleaning (see DataAnalysisAgent.
        _run_enrichment) - a statistical-treatment section and a real,
        computed econometric model section with an embedded chart.

        The Résumé exécutif is LLM-drafted from the real computed numbers
        when an LLM is configured (see llm_assist.draft_executive_summary) -
        added because the fixed template below, while numerically
        accurate, produced the same thin, generic-feeling paragraph
        regardless of how rich the underlying analysis was, even with an
        LLM available. The template stays as the honest no-LLM fallback."""
        from ..services import llm_assist

        doc.add_heading("Résumé exécutif", level=1)
        if stats:
            drafted_summary = llm_assist.draft_executive_summary(
                title=title, task_type=task_type, raw_request=raw_request, objective=objective,
                n_observations=stats.get("n_observations"), n_anomalies=(audit or {}).get("n_anomalies"),
                strategy=(audit or {}).get("strategy"), enrichment=enrichment, treatment_effect=treatment_effect,
            )
            if drafted_summary:
                doc.add_paragraph(drafted_summary)
            else:
                summary_bits = [
                    f"Cette analyse porte sur {stats.get('n_observations', 'N/A')} observations. "
                    f"{(audit or {}).get('n_anomalies', 0)} anomalies ont été détectées et traitées "
                    f"selon la stratégie {(audit or {}).get('strategy', 'B')} "
                    "(recodage en valeur manquante, échantillon préservé sauf doublons exclus)."
                ]
                if enrichment:
                    summary_bits.append(
                        f"{enrichment.get('n_imputations', 0)} valeur(s) manquante(s) imputée(s) et "
                        f"{enrichment.get('n_outliers_traites', 0)} valeur(s) aberrante(s) plafonnée(s) "
                        "lors du traitement statistique approfondi."
                    )
                if treatment_effect:
                    sig = "statistiquement significatif" if treatment_effect["significant_at_5pct"] else "non significatif au seuil de 5%"
                    summary_bits.append(
                        f"L'effet net estimé du traitement est de {treatment_effect['ate']:+,.0f} "
                        f"(p={treatment_effect['p_value']:.3f}), {sig}."
                    )
                doc.add_paragraph(" ".join(summary_bits))
        else:
            doc.add_paragraph("Aucune donnée n'a pu être analysée pour cette tâche.")

        doc.add_heading("Méthodologie", level=1)
        methodo = (
            "Les données brutes ont été validées automatiquement contre le dictionnaire de "
            "variables (bornes min/max, modalités autorisées) et contrôlées pour les doublons. "
            "Les valeurs hors normes ont été recodées en valeur manquante afin de préserver la "
            "taille de l'échantillon ; les lignes strictement dupliquées ont été exclues."
        )
        if enrichment:
            methodo += (
                " Les valeurs manquantes restantes ont été imputées par moyenne (par groupe "
                "quand une variable de regroupement pertinente était disponible, sinon moyenne "
                "globale) ; les valeurs aberrantes ont été plafonnées selon la méthode de "
                "l'écart interquartile (IQR, bornes à 1,5×IQR)."
            )
        if treatment_effect:
            methodo += f" {treatment_effect['method']}."
        doc.add_paragraph(methodo)

        doc.add_heading("Résultats", level=1)
        if stats and stats.get("variables"):
            table = doc.add_table(rows=1, cols=4)
            table.style = "Light Grid Accent 1"
            hdr = table.rows[0].cells
            hdr[0].text, hdr[1].text, hdr[2].text, hdr[3].text = "Variable", "Type", "N valides", "Résumé"
            for var, info in stats["variables"].items():
                row = table.add_row().cells
                row[0].text = str(var)
                row[1].text = info.get("type", "")
                row[2].text = str(info.get("n_valid", ""))
                if info.get("type") == "numeric":
                    row[3].text = f"moyenne={info.get('mean')}, min={info.get('min')}, max={info.get('max')}"
                else:
                    top = info.get("top_values", {})
                    row[3].text = ", ".join(f"{k}: {v}" for k, v in list(top.items())[:3])
        else:
            doc.add_paragraph("Aucune statistique disponible (étape d'analyse des données non exécutée).")

        if enrichment and (enrichment.get("imputations") or enrichment.get("outliers_traites")):
            doc.add_heading("Traitement statistique approfondi", level=1)
            if enrichment.get("imputations"):
                doc.add_paragraph(
                    f"{len(enrichment['imputations'])} valeur(s) manquante(s) imputée(s) :"
                )
                by_var: Dict[str, int] = {}
                for item in enrichment["imputations"]:
                    by_var[item["variable"]] = by_var.get(item["variable"], 0) + 1
                for var, n in by_var.items():
                    doc.add_paragraph(f"{var}: {n} valeur(s) imputée(s)", style="List Bullet")
            if enrichment.get("outliers_traites"):
                doc.add_paragraph(
                    f"{len(enrichment['outliers_traites'])} valeur(s) aberrante(s) plafonnée(s) (méthode IQR) :"
                )
                by_var = {}
                for item in enrichment["outliers_traites"]:
                    by_var[item["variable"]] = by_var.get(item["variable"], 0) + 1
                for var, n in by_var.items():
                    doc.add_paragraph(f"{var}: {n} valeur(s) plafonnée(s)", style="List Bullet")
            if enrichment.get("variables_derivees"):
                doc.add_paragraph("Variable(s) dérivée(s) créée(s) :")
                for dv in enrichment["variables_derivees"]:
                    doc.add_paragraph(
                        f"{dv['name']} = ({dv['rule']}) — {dv['n_positive']} positif(s), {dv['n_negative']} négatif(s)",
                        style="List Bullet",
                    )

        if treatment_effect:
            doc.add_heading("Modélisation économétrique", level=1)
            doc.add_paragraph(treatment_effect["method"])
            table = doc.add_table(rows=1, cols=2)
            table.style = "Light Grid Accent 1"
            hdr = table.rows[0].cells
            hdr[0].text, hdr[1].text = "Indicateur", "Valeur"
            rows_data = [
                ("Variable de traitement", treatment_effect["treatment_variable"]),
                ("Variable de résultat", treatment_effect["outcome"]),
                ("Effet net estimé (ATE)", f"{treatment_effect['ate']:+,.2f}"),
                ("Intervalle de confiance à 95%", f"[{treatment_effect['ci_95'][0]:+,.2f} ; {treatment_effect['ci_95'][1]:+,.2f}]"),
                ("p-value", f"{treatment_effect['p_value']:.4f}"),
                ("Significatif au seuil de 5%", "Oui" if treatment_effect["significant_at_5pct"] else "Non"),
                ("N traités / N contrôle", f"{treatment_effect['n_treated']} / {treatment_effect['n_control']}"),
            ]
            for label, value in rows_data:
                row = table.add_row().cells
                row[0].text, row[1].text = label, str(value)

            interpretation = (
                f"Le résultat est {'statistiquement significatif' if treatment_effect['significant_at_5pct'] else 'non statistiquement significatif'} "
                f"au seuil de 5% (p={treatment_effect['p_value']:.3f}). "
            )
            if treatment_effect["significant_at_5pct"]:
                interpretation += (
                    f"L'intervention est associée à une variation nette de {treatment_effect['ate']:+,.0f} "
                    "sur la variable de résultat, toutes choses égales par ailleurs selon ce modèle. "
                    "Cette estimation reste sujette aux limites habituelles d'une différence-en-différences "
                    "à deux groupes (biais de sélection résiduel si les groupes différaient déjà sur des "
                    "tendances non observées, absence de contrôle d'autres facteurs confondants)."
                )
            else:
                interpretation += (
                    "Il n'est donc pas possible, sur la base de cet échantillon, de conclure à un effet "
                    "net de l'intervention distinct du hasard d'échantillonnage."
                )
            doc.add_paragraph(interpretation)

            chart_path = _render_treatment_effect_chart(treatment_effect, work_dir)
            if chart_path:
                from docx.shared import Inches
                doc.add_picture(chart_path, width=Inches(5.5))

        doc.add_heading("Limites", level=1)
        if audit and audit.get("anomalies"):
            doc.add_paragraph(
                f"{audit.get('n_anomalies', 0)} anomalies ont été identifiées sur "
                f"{audit.get('n_rows_raw', '?')} lignes brutes ({audit.get('n_rows_cleaned', '?')} "
                "lignes conservées). Détail des cas les plus significatifs :"
            )
            for item in audit["anomalies"][:15]:
                doc.add_paragraph(
                    f"• Ligne {item.get('row_id')} — {item.get('variable')} : "
                    f"{item.get('reason')} → {item.get('action')}",
                    style="List Bullet",
                )
            if audit.get("field_notes"):
                doc.add_paragraph("Notes de terrain associées :")
                for note in audit["field_notes"][:10]:
                    doc.add_paragraph(f"• {note}", style="List Bullet")
        else:
            doc.add_paragraph("Aucune anomalie de données recensée.")

        if stats:
            from ..services import llm_assist
            doc.add_heading("Recommandations", level=1)
            recommendations = llm_assist.draft_recommendations(
                title=title, task_type=task_type or "", objective=objective,
                treatment_effect=treatment_effect,
                stats_summary=f"{stats.get('n_observations', 'N/A')} observations analysées." if stats else None,
            )
            if not recommendations:
                recommendations = llm_assist.fallback_recommendation_scaffold(treatment_effect)
            for rec in recommendations:
                doc.add_paragraph(rec, style="List Bullet")

    def _build_qualitative_crossing_section(
        self, doc, treatment_effect: Optional[Dict], stats: Optional[Dict], raw_material: str,
    ) -> None:
        """§8-style qualitative/quantitative crossing: only produced when
        the user actually uploaded qualitative material (field notes,
        interview summaries) alongside the data file - never fabricated
        from thin air. Synthesized via LLM when configured; otherwise the
        raw material is included directly and honestly labeled as such."""
        from ..services import llm_assist

        doc.add_heading("Analyse qualitative croisée", level=1)

        if treatment_effect:
            quant_summary = (
                f"Effet net estimé (ATE) de {treatment_effect['ate']:+,.0f} sur {treatment_effect['outcome']} "
                f"(p={treatment_effect['p_value']:.3f}, "
                f"{'significatif' if treatment_effect['significant_at_5pct'] else 'non significatif'} au seuil de 5%)."
            )
        elif stats:
            quant_summary = f"Analyse quantitative portant sur {stats.get('n_observations', 'N/A')} observations."
        else:
            quant_summary = "Aucun résultat quantitatif disponible."

        synthesis = llm_assist.synthesize_qualitative_crossing(quant_summary, raw_material)
        if synthesis:
            doc.add_paragraph(synthesis)
        else:
            doc.add_paragraph(
                "Aucune IA n'est configurée sur ce backend pour croiser automatiquement ce constat "
                "quantitatif avec la matière qualitative fournie ci-dessous (voir ANTHROPIC_API_KEY "
                "dans .env.example). Matière qualitative reprise telle quelle :"
            )
            for para in raw_material.split("\n"):
                if para.strip():
                    doc.add_paragraph(para.strip())

    def _build_content_sections(self, doc, context: Dict[str, Any], sources: Optional[Dict]) -> None:
        """redaction / recherche / planification / reponse_ao / autre: a
        written document, not a data report. Drafts the body via LLM if
        configured (real value-add for these task types - see
        llm_assist.draft_document_body); otherwise degrades honestly to a
        structured listing of whatever the Knowledge Base found, rather
        than either fabricating prose or showing data-report boilerplate
        that doesn't apply here.

        If the user uploaded a source document (their own notes, an
        existing report - see _extract_text_from_source), that material
        is the priority input: this is what "externalisation de savoir
        acquis" actually is - structuring someone's own experience, not
        generating content from nothing. It's used even without an LLM
        configured (included directly, clearly labeled as the user's
        original material) rather than only unlocking value once a key
        is set."""
        from ..services import llm_assist

        raw_request = context.get("raw_request", "")
        objective = context.get("objective", "")
        task_type = context.get("task_type", "")

        doc.add_heading("Objectif", level=1)
        doc.add_paragraph(objective or raw_request or "Non précisé.")

        raw_material = _extract_raw_material(context.get("data_sources") or [])

        doc.add_heading("Contenu", level=1)
        kb_sources = (sources or {}).get("sources", [])
        drafted = llm_assist.draft_document_body(
            raw_request=raw_request, title=context.get("title", ""), task_type=task_type,
            objective=objective, domain=context.get("domain"), kb_sources=kb_sources,
            raw_material=raw_material,
        )
        if drafted:
            for para in drafted.split("\n\n"):
                if para.strip():
                    doc.add_paragraph(para.strip())
        elif raw_material:
            # No LLM to structure it, but the user's own material is real
            # content - include it directly rather than an empty
            # placeholder. Praxis didn't add value here beyond formatting,
            # and says so plainly.
            doc.add_paragraph(
                "Aucune IA de rédaction n'est configurée sur ce backend (voir ANTHROPIC_API_KEY "
                "dans .env.example) : la matière fournie est reprise telle quelle ci-dessous, "
                "sans restructuration ni reformulation."
            )
            for para in raw_material.split("\n"):
                if para.strip():
                    doc.add_paragraph(para.strip())
        elif kb_sources:
            doc.add_paragraph(
                "Aucune IA de rédaction n'est configurée sur ce backend (voir ANTHROPIC_API_KEY "
                "dans .env.example). Éléments pertinents trouvés dans la base de connaissances, "
                "à intégrer manuellement :"
            )
            for s in kb_sources:
                snippet = (s.get("summary") or s.get("content") or "")[:300]
                doc.add_paragraph(f"{s['title']} ({s['domain']}) — {snippet}", style="List Bullet")
        else:
            doc.add_paragraph(
                "Section à compléter manuellement : aucune IA de rédaction configurée, aucun "
                "document source fourni, et aucune source pertinente trouvée dans la base de "
                "connaissances pour cette demande."
            )

        if task_type == "reponse_ao":
            hard_constraints = context.get("hard_constraints") or []
            doc.add_heading("Conformité", level=1)
            if hard_constraints:
                doc.add_paragraph("Éléments de conformité déclarés pour cette réponse :")
                for c in hard_constraints:
                    doc.add_paragraph(str(c), style="List Bullet")
            else:
                doc.add_paragraph(
                    "Aucune contrainte de conformité n'a été déclarée sur cette tâche — "
                    "à vérifier manuellement contre le cahier des charges avant envoi."
                )

    def _add_sources_section(self, doc, sources: Optional[Dict]) -> None:
        if not sources or not sources.get("sources"):
            return
        doc.add_heading("Sources", level=1)
        if sources.get("synthesis"):
            doc.add_paragraph(sources["synthesis"])
        for i, s in enumerate(sources["sources"]):
            doc.add_paragraph(f"[{i+1}] {s['title']} ({s['domain']})", style="List Bullet")

    def get_capabilities(self) -> List[str]:
        return ["report_writing", "document_formatting", "table_generation"]


# ============================================================================
# PRESENTATION AGENT
# ============================================================================

class PresentationAgent(BaseAgent):
    """
    §9 - Presentation Agent

    Responsibilities: build a slide deck whose length and content track
    what the analysis actually produced - not a fixed 2-3 slide skeleton
    every time regardless of depth. A plain descriptive task and a full
    impact evaluation (methodology, econometric chart, qualitative
    crossing, compliance checklist, recommendations) now produce
    genuinely different decks, mirroring DocumentAgent's own adaptive
    structure rather than lagging behind it.

    Tools used: python-pptx.
    """

    name = "PresentationAgent"
    description = "Builds a slide deck whose length and content track what the analysis actually produced"

    def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        from pptx import Presentation

        previous_outputs = context.get("previous_outputs", {}) or {}
        stats = _load_json(previous_outputs.get("stats_summary"))
        audit = _load_json(previous_outputs.get("audit_trail"))
        enrichment = _load_json(previous_outputs.get("audit_trail_enrichissement"))
        treatment_effect = _load_json(previous_outputs.get("treatment_effect"))
        sources = _load_json(previous_outputs.get("research_sources"))
        raw_material = _extract_raw_material(context.get("data_sources") or [])
        work_dir = context["work_dir"]

        prs = Presentation()

        self._add_title_slide(prs, context)

        if stats:
            self._add_methodology_slide(prs, stats, audit, enrichment, treatment_effect)
            self._add_key_results_slide(prs, stats, audit, treatment_effect, context)
        else:
            # Reachable even for non-data task types if an LLM-generated
            # plan (llm_assist.generate_plan_steps) adds a presentation
            # step - e.g. pitching a PLANIFICATION deliverable.
            self._add_bullet_slide(
                prs, "Points clés",
                [context.get("objective") or context.get("raw_request") or "Voir le document associé pour le détail."],
            )

        if treatment_effect:
            self._add_econometric_slide(prs, treatment_effect, work_dir)

        if enrichment and (enrichment.get("imputations") or enrichment.get("outliers_traites") or enrichment.get("variables_derivees")):
            self._add_statistical_treatment_slide(prs, enrichment)

        if audit and audit.get("anomalies") and not treatment_effect:
            # When a treatment effect exists, the econometric slide is
            # already the headline - repeating the raw anomaly list here
            # too would be exactly the "présentation répétitive" this
            # rewrite exists to avoid. The full detail still lives in the
            # docx either way.
            self._add_data_quality_slide(prs, audit)

        if raw_material:
            self._add_qualitative_slide(prs, treatment_effect, stats, raw_material)

        if sources and sources.get("sources"):
            self._add_sources_slide(prs, sources)

        if context.get("task_type") == "reponse_ao":
            self._add_compliance_slide(prs, context.get("hard_constraints") or [])

        if stats:
            self._add_recommendations_slide(prs, context, treatment_effect, stats)

        os.makedirs(work_dir, exist_ok=True)
        out_path = os.path.join(work_dir, "presentation.pptx")
        prs.save(out_path)

        n_slides = len(prs.slides._sldIdLst)
        return {
            "status": "success",
            "summary": f"Présentation PPTX générée ({n_slides} diapositives, adaptées au contenu réellement disponible).",
            "artifacts": [
                {
                    "role": "presentation_pptx",
                    "kind": "draft",
                    "format": "pptx",
                    "file_path": out_path,
                    "metadata": {"n_slides": n_slides},
                }
            ],
        }

    # ------------------------------------------------------------------
    def _add_bullet_slide(self, prs, title: str, bullets: List[str]):
        slide = prs.slides.add_slide(prs.slide_layouts[1])  # Title and Content
        slide.shapes.title.text = title
        body = slide.placeholders[1].text_frame
        body.clear()
        for i, bullet in enumerate(bullets):
            p = body.paragraphs[0] if i == 0 else body.add_paragraph()
            p.text = str(bullet)
        return slide

    def _add_title_slide(self, prs, context: Dict[str, Any]):
        slide = prs.slides.add_slide(prs.slide_layouts[0])
        slide.shapes.title.text = context.get("title") or "Présentation"
        if len(slide.placeholders) > 1:
            slide.placeholders[1].text = context.get("objective") or ""

    def _add_methodology_slide(self, prs, stats, audit, enrichment, treatment_effect):
        bullets = [
            f"{stats.get('n_observations', 'N/A')} observations, "
            f"{(audit or {}).get('n_anomalies', 0)} anomalie(s) traitée(s) "
            f"(stratégie {(audit or {}).get('strategy', 'B')})",
        ]
        if enrichment:
            bullets.append(
                f"{enrichment.get('n_imputations', 0)} valeur(s) imputée(s), "
                f"{enrichment.get('n_outliers_traites', 0)} valeur(s) aberrante(s) plafonnée(s) (IQR)"
            )
        if treatment_effect:
            bullets.append(treatment_effect["method"])
        self._add_bullet_slide(prs, "Méthodologie", bullets)

    def _add_key_results_slide(self, prs, stats, audit, treatment_effect, context: Dict[str, Any]):
        """Leads with an LLM-drafted headline interpretation when available
        (same fix as DocumentAgent's Résumé exécutif - the fixed factual
        bullets below were the whole slide before, regardless of whether an
        LLM was configured); the factual bullets always follow, since
        they're genuinely useful on a slide even when the headline is
        LLM-drafted."""
        from ..services import llm_assist

        if treatment_effect:
            sig = "significatif" if treatment_effect["significant_at_5pct"] else "non significatif"
            bullets = [
                f"Effet net estimé : {treatment_effect['ate']:+,.0f} sur {treatment_effect['outcome']}",
                f"p-value = {treatment_effect['p_value']:.3f} ({sig} au seuil de 5%)",
                f"{treatment_effect['n_treated']} traités / {treatment_effect['n_control']} contrôle",
            ]
        else:
            bullets = [
                f"{stats.get('n_observations', 'N/A')} observations analysées",
                f"{(audit or {}).get('n_anomalies', 0)} anomalies traitées",
            ]

        headline = llm_assist.draft_executive_summary(
            title=context.get("title") or "", task_type=context.get("task_type") or "",
            raw_request=context.get("raw_request", ""), objective=context.get("objective"),
            n_observations=stats.get("n_observations"), n_anomalies=(audit or {}).get("n_anomalies"),
            strategy=(audit or {}).get("strategy"), treatment_effect=treatment_effect,
        )
        if headline:
            bullets = [headline] + bullets

        self._add_bullet_slide(prs, "Résultats clés", bullets)

    def _add_econometric_slide(self, prs, treatment_effect: Dict, work_dir: str):
        from pptx.util import Inches

        slide = prs.slides.add_slide(prs.slide_layouts[5])  # Title Only
        slide.shapes.title.text = "Modélisation économétrique"
        chart_path = _render_treatment_effect_chart(treatment_effect, work_dir)
        if chart_path:
            slide.shapes.add_picture(chart_path, Inches(1.2), Inches(1.5), width=Inches(7.5))
        else:
            # matplotlib failed - fall back to text instead of an
            # otherwise-near-empty "Title Only" slide.
            tf = slide.shapes.add_textbox(Inches(1), Inches(1.5), Inches(8), Inches(3)).text_frame
            tf.text = treatment_effect["method"]
            p = tf.add_paragraph()
            p.text = (
                f"ATE = {treatment_effect['ate']:+,.2f} (IC95% "
                f"[{treatment_effect['ci_95'][0]:+,.2f} ; {treatment_effect['ci_95'][1]:+,.2f}], "
                f"p={treatment_effect['p_value']:.4f})"
            )

    def _add_statistical_treatment_slide(self, prs, enrichment: Dict):
        bullets = []
        if enrichment.get("imputations"):
            bullets.append(f"{len(enrichment['imputations'])} valeur(s) manquante(s) imputée(s)")
        if enrichment.get("outliers_traites"):
            bullets.append(f"{len(enrichment['outliers_traites'])} valeur(s) aberrante(s) plafonnée(s) (IQR)")
        for dv in enrichment.get("variables_derivees", []):
            bullets.append(f"Variable créée : {dv['name']} ({dv['n_positive']} positif / {dv['n_negative']} négatif)")
        self._add_bullet_slide(prs, "Traitement statistique approfondi", bullets)

    def _add_data_quality_slide(self, prs, audit: Dict):
        bullets = [f"{audit.get('n_rows_cleaned')}/{audit.get('n_rows_raw')} lignes conservées après nettoyage"]
        for item in audit["anomalies"][:5]:
            bullets.append(f"{item.get('variable')}: {item.get('reason')}")
        self._add_bullet_slide(prs, "Qualité des données", bullets)

    def _add_qualitative_slide(self, prs, treatment_effect: Optional[Dict], stats: Optional[Dict], raw_material: str):
        from ..services import llm_assist

        if treatment_effect:
            quant_summary = f"ATE {treatment_effect['ate']:+,.0f} (p={treatment_effect['p_value']:.3f})"
        elif stats:
            quant_summary = f"{stats.get('n_observations', 'N/A')} observations analysées"
        else:
            quant_summary = ""

        synthesis = llm_assist.synthesize_qualitative_crossing(quant_summary, raw_material) if quant_summary else None
        if synthesis:
            bullets = [synthesis]
        else:
            # No LLM - a short excerpt rather than the full raw text (too
            # long for a slide; the complete material is in the docx).
            excerpt = next((line for line in raw_material.strip().split("\n") if line.strip()), "")[:200]
            bullets = [f"Matière qualitative fournie (extrait) : {excerpt}"]
        self._add_bullet_slide(prs, "Analyse qualitative croisée", bullets)

    def _add_sources_slide(self, prs, sources: Dict):
        bullets = [s["title"] for s in sources["sources"][:6]]
        self._add_bullet_slide(prs, "Sources", bullets)

    def _add_compliance_slide(self, prs, hard_constraints: List):
        bullets = list(hard_constraints[:8]) if hard_constraints else [
            "Aucune contrainte de conformité déclarée sur cette tâche - à vérifier contre le cahier des charges."
        ]
        self._add_bullet_slide(prs, "Conformité", bullets)

    def _add_recommendations_slide(self, prs, context: Dict[str, Any], treatment_effect: Optional[Dict], stats: Optional[Dict]):
        from ..services import llm_assist

        recommendations = llm_assist.draft_recommendations(
            title=context.get("title") or "", task_type=context.get("task_type") or "",
            objective=context.get("objective"), treatment_effect=treatment_effect,
            stats_summary=f"{stats.get('n_observations', 'N/A')} observations analysées." if stats else None,
        )
        if not recommendations:
            recommendations = llm_assist.fallback_recommendation_scaffold(treatment_effect)
        self._add_bullet_slide(prs, "Recommandations", recommendations)

    def get_capabilities(self) -> List[str]:
        return ["slide_creation", "chart_integration", "adaptive_content"]


# ============================================================================
# RESEARCH AGENT
# ============================================================================

class ResearchAgent(BaseAgent):
    """
    §9 - Research Agent

    Searches the local Knowledge Base (TF-IDF - see knowledge_service.py)
    for items relevant to the task, and asks the LLM to synthesize a brief
    citing them if one is configured. Honest status on the rest: there is
    no web search here (no general web-search tool is wired into this
    backend), and this is lexical (TF-IDF) retrieval, not the
    pgvector/semantic-embeddings search the design doc describes as the
    long-term target - see docs/REFONTE_v0.4.md. If the Knowledge Base is
    empty or has nothing relevant, this says so plainly instead of
    fabricating sources.
    """

    name = "ResearchAgent"
    description = "Searches the local Knowledge Base and synthesizes a sourced brief"

    def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        from ..services.knowledge_service import KnowledgeService

        db = context.get("db")
        query = " ".join(filter(None, [context.get("objective"), context.get("raw_request")]))
        domain = context.get("domain")

        service = KnowledgeService(db)
        result = service.answer(query, domain=domain, top_k=5)

        if not result["sources"]:
            return {
                "status": "success",
                "summary": (
                    "Aucun élément pertinent trouvé dans la base de connaissances "
                    "(vide, ou rien en rapport avec cette demande)."
                ),
                "artifacts": [],
            }

        work_dir = context["work_dir"]
        os.makedirs(work_dir, exist_ok=True)
        sources_path = os.path.join(work_dir, "sources.json")
        with open(sources_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2, default=str)

        artifacts = [{
            "role": "research_sources", "kind": "analysis", "format": "json",
            "file_path": sources_path,
            "metadata": {"subtype": "research_sources", "n_sources": len(result["sources"])},
        }]

        summary = f"{len(result['sources'])} source(s) pertinente(s) trouvée(s) dans la base de connaissances."
        if result["synthesis"]:
            brief_path = os.path.join(work_dir, "note_de_recherche.md")
            with open(brief_path, "w", encoding="utf-8") as f:
                f.write(f"# Note de recherche\n\n{result['synthesis']}\n\n## Sources\n\n")
                for i, s in enumerate(result["sources"]):
                    f.write(f"[{i+1}] **{s['title']}** ({s['domain']}) — score {s['score']}\n\n")
            artifacts.append({
                "role": "research_brief", "kind": "draft", "format": "md",
                "file_path": brief_path, "metadata": {"subtype": "research_brief"},
            })
            summary += " Synthèse générée par IA."

        return {"status": "success", "summary": summary, "artifacts": artifacts}

    def get_capabilities(self) -> List[str]:
        return ["knowledge_base_search", "sourced_synthesis"]  # no web search - see docstring


# ============================================================================
# VALIDATION AGENT
# ============================================================================

class ValidationAgent(BaseAgent):
    """
    §9 - Validation Agent / §7 - Quality Control

    Runs the real ValidationService checks (needs DB access, unlike the
    other agents - see module docstring) against the deliverable produced
    earlier in the same run.
    """

    name = "ValidationAgent"
    description = "Performs quality control via ValidationService"

    def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        from ..services.validation_service import ValidationService

        db = context.get("db")
        previous_outputs = context.get("previous_outputs", {}) or {}
        report_ref = previous_outputs.get("report_docx")
        artifact_id = report_ref.get("artifact_id") if report_ref else ""

        stats = None
        if previous_outputs.get("stats_summary"):
            path = previous_outputs["stats_summary"].get("file_path")
            if path and os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    stats = json.load(f)

        content = {
            "title": context.get("title"),
            "content": {"title": context.get("title"), "content": report_ref} if report_ref else {},
            "methodology": (
                "Nettoyage par dictionnaire de variables (bornes, modalités autorisées), "
                "détection de doublons, recodage en valeur manquante (stratégie B)."
                if stats else ""
            ),
            "metadata": (report_ref or {}).get("metadata", {}),
            "text": json.dumps(stats) if stats else "",
        }

        service = ValidationService(db)
        validation = service.run_full_validation(
            execution_id=context.get("execution_id", ""),
            artifact_id=artifact_id or "",
            task_type=context.get("task_type", ""),
            constraints={
                "hard_constraints": context.get("hard_constraints", []),
                "soft_preferences": context.get("soft_preferences", []),
            },
            content=content,
        )

        return {
            "status": "success",
            "summary": f"Contrôle qualité: verdict={validation.overall_verdict}, score={round(validation.score or 0, 2)}",
            "artifacts": [],
            "extra": {
                "validation_id": validation.id,
                "verdict": validation.overall_verdict,
                "score": validation.score,
            },
        }

    def get_capabilities(self) -> List[str]:
        return [
            "consistency_check",
            "factual_accuracy",
            "methodological_validity",
            "constraint_compliance",
        ]


# ============================================================================
# ERROR RECOVERY AGENT
# ============================================================================

class ErrorRecoveryAgent(BaseAgent):
    """
    §9 - Error Recovery Agent / §3.16

    Kept in the registry for documentation/completeness. In practice,
    ExecutionEngine calls ErrorRecoveryService + Orchestrator.decide_recovery_strategy
    directly when a step raises, because recovery is triggered by an
    exception rather than being a planned step - there is nothing for the
    Orchestrator to dispatch a "recovery step" to.
    """

    name = "ErrorRecoveryAgent"
    description = "Documents the error-recovery role (invoked directly by ExecutionEngine, not dispatched as a plan step)"

    def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "status": "success",
            "summary": "Voir ErrorRecoveryService - la récupération est déclenchée par le moteur d'exécution, pas par une étape planifiée.",
            "artifacts": [],
        }

    def get_capabilities(self) -> List[str]:
        return ["error_diagnosis", "recovery_strategy", "escalation"]


class PoleNotImplementedAgent(BaseAgent):
    """
    Praxis v1.0, Phase 4 (docs/PRAXIS_V1_ARCHITECTURE.md §4/§8)

    domain_router.py can now convene a pole (Statisticien P2, Démographe
    P6, Économiste P7, Planificateur P8, Suivi-Évaluation P9, SIG P10)
    whose dedicated agent hasn't been built yet (Phase 5+ - see the
    roadmap in §8). This reports that plainly in the step's own log
    instead of silently producing nothing or crashing - the same honest-
    stub principle already applied to ResearchAgent before its Phase 3
    build-out. The step type itself (e.g. "pole_p8") names which pole was
    expected, so the gap is visible in Task detail/executions without
    needing to read code.
    """

    name = "PoleNotImplementedAgent"
    description = "Honestly reports that this pole's dedicated agent isn't built yet (Phase 5+)"

    _POLE_LABELS = {
        "pole_p2": "P2 Statisticien (ACP/AFC/CAH)",
        "pole_p6": "P6 Démographe",
        "pole_p7": "P7 Économiste & Finances publiques",
        "pole_p8": "P8 Planificateur & Gestion de projet",
        "pole_p9": "P9 Suivi-Évaluation & Politiques publiques",
        "pole_p10": "P10 Géographe/SIG",
    }

    def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        step_type = (context.get("step") or {}).get("type", "")
        pole_label = self._POLE_LABELS.get(step_type, step_type)
        return {
            "status": "success",
            "summary": (
                f"Étape non exécutée : le pôle {pole_label} a été identifié comme pertinent "
                "pour cette demande, mais son agent dédié n'est pas encore construit "
                "(voir docs/PRAXIS_V1_ARCHITECTURE.md, feuille de route §8)."
            ),
            "artifacts": [],
        }

    def get_capabilities(self) -> List[str]:
        return []  # honestly empty until the corresponding Phase is built


# ============================================================================
# AGENT REGISTRY
# ============================================================================

AGENT_REGISTRY = {
    "UnderstandingAgent": UnderstandingAgent,
    "PlanningAgent": PlanningAgent,
    "DataAnalysisAgent": DataAnalysisAgent,
    "DocumentAgent": DocumentAgent,
    "PresentationAgent": PresentationAgent,
    "ResearchAgent": ResearchAgent,
    "ValidationAgent": ValidationAgent,
    "ErrorRecoveryAgent": ErrorRecoveryAgent,
    "PoleNotImplementedAgent": PoleNotImplementedAgent,
}


def get_agent(agent_name: str) -> Optional[BaseAgent]:
    """Get an agent instance by name"""
    agent_class = AGENT_REGISTRY.get(agent_name)
    if agent_class:
        return agent_class()
    return None


def list_available_agents() -> List[Dict[str, str]]:
    """List all available agents with their descriptions"""
    return [
        {"name": name, "description": cls.description}
        for name, cls in AGENT_REGISTRY.items()
    ]
