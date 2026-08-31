"""Tests for src/services/llm_assist.py - the validation/fallback logic
around LLM-generated plans and reformulation suggestions must hold even
though we can't call a real model in CI. A fake LLMClient stands in."""
from unittest.mock import patch

from src.services import llm_assist


class _FakeClient:
    def __init__(self, json_response=None, available=True):
        self._json_response = json_response
        self.available = available

    def complete_json(self, system, user, max_tokens=None):
        return self._json_response


def test_generate_plan_steps_returns_none_when_llm_unavailable():
    with patch.object(llm_assist, "LLMClient", lambda: _FakeClient(available=False)):
        assert llm_assist.generate_plan_steps("x", "t", "analyse_donnees") is None


def test_generate_plan_steps_drops_unknown_step_types():
    fake_response = {
        "steps": [
            {"type": "nettoyage", "name": "Nettoyer", "is_critical": True},
            {"type": "invente_un_truc_bizarre", "name": "???", "is_critical": False},
            {"type": "redaction", "name": "Rédiger", "is_critical": False},
            {"type": "validation", "name": "Contrôler", "is_critical": True},
        ]
    }
    with patch.object(llm_assist, "LLMClient", lambda: _FakeClient(fake_response)):
        steps = llm_assist.generate_plan_steps("x", "t", "analyse_donnees")
    assert steps is not None
    types = [s["type"] for s in steps]
    assert "invente_un_truc_bizarre" not in types
    assert "nettoyage" in types and "redaction" in types


def test_generate_plan_steps_appends_validation_if_missing():
    fake_response = {"steps": [
        {"type": "nettoyage", "name": "Nettoyer", "is_critical": True},
        {"type": "redaction", "name": "Rédiger", "is_critical": False},
    ]}
    with patch.object(llm_assist, "LLMClient", lambda: _FakeClient(fake_response)):
        steps = llm_assist.generate_plan_steps("x", "t", "analyse_donnees")
    assert steps[-1]["type"] == "validation"


def test_generate_plan_steps_none_when_too_few_valid_steps_survive():
    fake_response = {"steps": [{"type": "invente_un_truc", "name": "?", "is_critical": False}]}
    with patch.object(llm_assist, "LLMClient", lambda: _FakeClient(fake_response)):
        assert llm_assist.generate_plan_steps("x", "t", "analyse_donnees") is None


def test_suggest_reformulation_clamps_and_filters_scores():
    fake_response = {
        "objective": "Produire un rapport fiable",
        "dimension_scores": {"objectif": 1.4, "donnees": -0.2, "inconnu": 0.9, "contexte": 0.6},
        "rationale": "test",
    }
    with patch.object(llm_assist, "LLMClient", lambda: _FakeClient(fake_response)):
        result = llm_assist.suggest_reformulation(
            "x", "t", "analyse_donnees",
            dimensions=["objectif", "donnees", "contexte"], labels={},
        )
    assert result["dimension_scores"]["objectif"] == 1.0   # clamped down
    assert result["dimension_scores"]["donnees"] == 0.0    # clamped up
    assert "inconnu" not in result["dimension_scores"]      # unknown dim dropped
    assert result["dimension_scores"]["contexte"] == 0.6


def test_suggest_reformulation_none_when_llm_unavailable():
    with patch.object(llm_assist, "LLMClient", lambda: _FakeClient(available=False)):
        assert llm_assist.suggest_reformulation("x", "t", "autre", dimensions=["objectif"], labels={}) is None
