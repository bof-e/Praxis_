"""Tests for DataAnalysisAgent._enrich / _estimate_treatment_effect - the
statistical-depth upgrade built for real impact-evaluation datasets
(imputation, outlier treatment, derived pre/post variables, a genuine
computed treatment effect). A synthetic dataset with a *known* engineered
effect lets us check the computed ATE is actually correct, not just that
nothing crashed.
"""
import numpy as np
import pandas as pd
import pytest

from src.agents import DataAnalysisAgent


@pytest.fixture()
def engineered_dataset():
    """200 households, true treatment effect of +1000 on revenue built in
    by construction, plus a missing-value gap and one injected outlier."""
    rng = np.random.RandomState(42)
    n = 200
    treatment = np.array([1] * 100 + [0] * 100)
    revenu_pre = rng.normal(50000, 5000, n)
    noise = rng.normal(0, 200, n)
    true_effect = 1000
    revenu_post = revenu_pre + treatment * true_effect + noise

    df = pd.DataFrame({
        "id_menage": range(1, n + 1),
        "village": rng.choice(["A", "B", "C"], n),
        "beneficiaire": treatment,
        "revenu_annuel_pre": revenu_pre,
        "revenu_annuel_post": revenu_post,
        "rendement_hectare": rng.normal(2.5, 0.3, n),
    })
    df.loc[5:9, "revenu_annuel_post"] = np.nan          # 5 missing values
    df.loc[0, "rendement_hectare"] = 999.0              # one obvious outlier
    return df, true_effect


def test_enrich_imputes_all_missing_values(engineered_dataset):
    df, _ = engineered_dataset
    enriched, imputations, _, _ = DataAnalysisAgent._enrich(df, "id_menage")
    assert enriched["revenu_annuel_post"].isna().sum() == 0
    assert len(imputations) == 5
    assert all(imp["variable"] == "revenu_annuel_post" for imp in imputations)


def test_enrich_caps_the_injected_outlier(engineered_dataset):
    df, _ = engineered_dataset
    enriched, _, outlier_caps, _ = DataAnalysisAgent._enrich(df, "id_menage")
    assert enriched.loc[0, "rendement_hectare"] < 999.0
    capped_vars = {o["variable"] for o in outlier_caps}
    assert "rendement_hectare" in capped_vars


def test_enrich_leaves_binary_treatment_column_untouched(engineered_dataset):
    """beneficiaire is 0/1 - must never be "cleaned" as if it were a
    continuous variable with outliers/missing-value imputation."""
    df, _ = engineered_dataset
    enriched, imputations, outlier_caps, _ = DataAnalysisAgent._enrich(df, "id_menage")
    assert set(enriched["beneficiaire"].unique()) == {0, 1}
    assert not any(item["variable"] == "beneficiaire" for item in imputations + outlier_caps)


def test_enrich_derives_impact_score_from_pre_post_pair(engineered_dataset):
    df, _ = engineered_dataset
    enriched, _, _, derived = DataAnalysisAgent._enrich(df, "id_menage")
    assert len(derived) == 1
    assert derived[0]["name"] == "revenu_annuel_impact_score"
    assert "revenu_annuel_impact_score" in enriched.columns


def test_treatment_effect_recovers_the_known_engineered_effect(engineered_dataset):
    df, true_effect = engineered_dataset
    enriched, _, _, _ = DataAnalysisAgent._enrich(df, "id_menage")
    result = DataAnalysisAgent._estimate_treatment_effect(enriched)

    assert result is not None
    assert result["treatment_variable"] == "beneficiaire"
    assert abs(result["ate"] - true_effect) < 150  # noise tolerance
    assert result["p_value"] < 0.05
    assert result["significant_at_5pct"] is True
    assert result["n_treated"] == 100
    assert result["n_control"] == 100


def test_treatment_effect_none_without_treatment_column():
    df = pd.DataFrame({
        "id": range(20),
        "revenu_pre": np.random.normal(1000, 100, 20),
        "revenu_post": np.random.normal(1000, 100, 20),
    })
    assert DataAnalysisAgent._estimate_treatment_effect(df) is None


def test_treatment_effect_none_without_pre_post_pair():
    df = pd.DataFrame({
        "id": range(20),
        "beneficiaire": [1] * 10 + [0] * 10,
        "revenu": np.random.normal(1000, 100, 20),
    })
    assert DataAnalysisAgent._estimate_treatment_effect(df) is None


def test_treatment_effect_none_on_too_small_sample():
    df = pd.DataFrame({
        "id": range(4),
        "beneficiaire": [1, 1, 0, 0],
        "revenu_pre": [100, 110, 90, 95],
        "revenu_post": [150, 160, 91, 96],
    })
    assert DataAnalysisAgent._estimate_treatment_effect(df) is None


def test_full_pipeline_on_a_csv_file_with_real_econometric_output(db_session, tmp_path, engineered_dataset):
    """End-to-end: CSV upload (the original bug report) through to a
    docx containing a real computed treatment-effect table."""
    from src.services.task_service import TaskService
    from src.services.execution_engine import ExecutionEngine
    from src.models import TaskType

    df, true_effect = engineered_dataset
    csv_path = tmp_path / "agri_data.csv"
    df.to_csv(csv_path, index=False)

    ts = TaskService(db_session)
    task = ts.create_task(
        title="Évaluation Agri-Borgou", raw_request="Évaluer l'impact de la subvention",
        task_type=TaskType.RAPPORT_EVALUATION,
    )
    task.data_sources = [{"path": str(csv_path), "original_name": "agri_data.csv"}]
    db_session.commit()
    ts.update_task_readiness(task.id, {
        "objectif": 0.9, "contexte": 0.8, "donnees": 1.0, "livrables": 0.8,
        "contraintes": 0.7, "methode": 0.7, "ressources": 0.8,
    })
    plan = ts.propose_plan(task.id)["plan"]
    ts.validate_plan(plan.id, approved=True)
    result = ExecutionEngine(db_session).execute_plan(task.id)

    assert result["final_status"] == "deliverable"
    te_artifact = next((a for a in result["artifacts"] if a["role"] == "treatment_effect"), None)
    assert te_artifact is not None

    from docx import Document
    from src.services.artifact_service import ArtifactService
    report_artifact_id = next(a["id"] for a in result["artifacts"] if a["role"] == "report_docx")
    artifact = ArtifactService(db_session).get_artifact(report_artifact_id)
    doc = Document(artifact.file_ref)
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "Modélisation économétrique" in text
    assert "Différence-en-différences" in text
