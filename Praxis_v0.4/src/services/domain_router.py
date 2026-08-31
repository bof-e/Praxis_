"""
Domain Router - Praxis v1.0, Phase 4 (docs/PRAXIS_V1_ARCHITECTURE.md §4)

"Aujourd'hui, task_service.py classe la demande en 1 des 7 types fixes.
Ce n'est plus suffisant dès qu'une demande touche plusieurs domaines à la
fois" - a real impact evaluation is Économètre (P3) + Suivi-Évaluation (P9)
+ Rédacteur (P12) at once, not one fixed TaskType.

Two paths, same fallback contract as every other Phase 3 addition
(llm_client.py, sandbox.py): an LLM call when configured, a deterministic
mapping otherwise - never a crash, never a silent wrong answer.

- LLM path: the model sees the closed list of 11 deliverable poles (P2-P12
  - §2; P1/P13/P14 are infrastructure, never rows to route to) with their
  declared capacités, and returns {"poles": [...], "type_livrable": str,
  "confiance": float}. Below DOMAIN_ROUTER_CONFIDENCE_THRESHOLD, the
  result says so (needs_clarification=True) rather than handing back a
  guessed plan - §4 point 3: "pas de plan généré à l'aveugle".
- No-LLM fallback: today's existing TaskType (already explicit, already
  chosen by the person) maps deterministically to the pole(s) it always
  implied, at confiance=1.0 - this isn't a guess, it's exactly what the
  Phase 2/3 heuristic plans already did, expressed as poles instead of a
  single enum. Nothing changes for anyone without an LLM configured.

Note on the doc's own count: §4 says "12 pôles livrables (P2 à P12)" -
P2..P12 inclusive is 11 poles, not 12 (an off-by-one in the source
document's prose; the table in §2 is unambiguous and is what's
implemented here). Flagged rather than silently "corrected" without
comment.
"""
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from ..models import ExpertiseDomain, TaskType
from .llm_client import LLMClient
from ..config import settings


# §2 table, verbatim - the closed list of deliverable poles. `capacites`
# are *declared* step types this pole is meant to eventually produce; for
# poles without a real agent yet (Phase 5+), Orchestrator.assemble_plan_
# from_poles routes their step to an honest "not yet implemented" stub
# (see agents/__init__.py: PoleNotImplementedAgent) rather than silently
# doing nothing or crashing - the same principle already applied to
# ResearchAgent before its Phase 3 build-out.
SEED_DOMAINS: List[Dict] = [
    {
        "code": "P2", "nom": "Statisticien", "categories_source": [3, 7],
        "capacites": ["statistique_descriptive", "analyse_multivariee_acp_afc_cah"],
        "librairies": ["scipy", "statsmodels", "numpy"],
    },
    {
        "code": "P3", "nom": "Économètre", "categories_source": [4, 16],
        # regression/diff_en_diff already real via DataAnalysisAgent._estimate_treatment_effect (v0.4.4)
        "capacites": ["diff_en_diff", "matching_score_propension", "variables_instrumentales", "regression_discontinuite"],
        "librairies": ["statsmodels", "scipy"],
    },
    {
        "code": "P4", "nom": "Collecte & Qualité des données", "categories_source": [5, 29],
        "capacites": ["calcul_taille_echantillon", "journal_qualite_donnees", "nettoyage"],
        "librairies": ["pandas"],
    },
    {
        "code": "P5", "nom": "Data/Analyste technique", "categories_source": [6, 8],
        "capacites": ["nettoyage", "analyse"],  # existing DataAnalysisAgent, already real
        "librairies": ["pandas", "numpy", "openpyxl"],
    },
    {
        "code": "P6", "nom": "Démographe", "categories_source": [9],
        "capacites": ["tables_mortalite", "estimation_indirecte_brass", "pyramide_des_ages"],
        "librairies": [],
    },
    {
        "code": "P7", "nom": "Économiste & Finances publiques", "categories_source": [10, 11, 21],
        "capacites": ["comptabilite_nationale_scn2008", "classification_cofog", "analyse_budgetaire"],
        "librairies": [],
    },
    {
        "code": "P8", "nom": "Planificateur & Gestion de projet", "categories_source": [12, 13, 22],
        "capacites": ["cadre_logique", "theorie_du_changement", "chronogramme"],
        "librairies": [],
    },
    {
        "code": "P9", "nom": "Suivi-Évaluation & Politiques publiques", "categories_source": [14, 15, 16, 17],
        "capacites": ["dictionnaire_indicateurs_smart", "rapport_evaluation_ocde_cad", "cadre_gar"],
        "librairies": [],
    },
    {
        "code": "P10", "nom": "Géographe/SIG", "categories_source": [18],
        "capacites": ["carte_thematique", "diagnostic_territorial"],
        "librairies": ["geopandas", "folium"],
    },
    {
        "code": "P11", "nom": "Chercheur & Sciences sociales", "categories_source": [19, 20, 26],
        "capacites": ["recherche"],  # existing ResearchAgent (KB/TF-IDF), already real
        "librairies": [],
    },
    {
        "code": "P12", "nom": "Rédacteur & Communication", "categories_source": [23, 24, 25],
        "capacites": ["redaction", "presentation"],  # existing DocumentAgent/PresentationAgent, already real
        "librairies": [],
    },
]

POLE_CODES = [d["code"] for d in SEED_DOMAINS]

# No-LLM fallback: today's 7 TaskTypes map deterministically to the
# pole(s) they always implied. P12 (Rédacteur) is appended separately by
# route_domain for every fallback result, matching §4 point 4's fixed
# "... → Rédacteur" tail - not repeated in each entry here.
_TASK_TYPE_TO_POLES = {
    TaskType.ANALYSE_DONNEES: ["P5"],
    TaskType.RAPPORT_EVALUATION: ["P3", "P9"],
    TaskType.REPONSE_AO: [],
    TaskType.PLANIFICATION: ["P8"],
    TaskType.RECHERCHE: ["P11"],
    TaskType.REDACTION: [],
    TaskType.AUTRE: [],
}


def seed_default_domains(db: Session) -> None:
    """Idempotent - safe to call on every app startup. Praxis's own map
    of what it knows how to route to, not user data; there is nothing to
    preserve across a reseed, so an existing row's declared fields are
    refreshed to match SEED_DOMAINS rather than left stale."""
    for entry in SEED_DOMAINS:
        existing = db.query(ExpertiseDomain).filter(ExpertiseDomain.code == entry["code"]).first()
        if existing:
            existing.nom = entry["nom"]
            existing.categories_source = entry["categories_source"]
            existing.capacites = entry["capacites"]
            existing.librairies = entry["librairies"]
        else:
            db.add(ExpertiseDomain(
                code=entry["code"], nom=entry["nom"], categories_source=entry["categories_source"],
                capacites=entry["capacites"], librairies=entry["librairies"],
            ))
    db.commit()


def _deterministic_fallback(task_type: Optional[TaskType]) -> Dict:
    poles = list(_TASK_TYPE_TO_POLES.get(task_type, [])) if task_type else []
    if "P12" not in poles:
        poles.append("P12")
    return {
        "poles": poles, "type_livrable": (task_type.value if task_type else "autre"),
        "confiance": 1.0,  # not a guess - a direct, deterministic mapping of what the person already chose
        "needs_clarification": False, "source": "deterministic_fallback",
    }


def route_domain(
    db: Session, raw_request: str, title: str, task_type: Optional[TaskType] = None,
    domain: Optional[str] = None,
) -> Dict:
    """Returns {"poles": [...], "type_livrable": str, "confiance": float,
    "needs_clarification": bool, "source": "llm"|"deterministic_fallback"}.

    task_type (the existing, already-chosen classification) is used both
    as the no-LLM fallback's basis and as a validity filter on the LLM's
    own answer - a hallucinated pole code never survives either path."""
    seed_default_domains(db)
    domains = db.query(ExpertiseDomain).all()
    known_codes = {d.code for d in domains}

    client = LLMClient()
    if not client.available:
        return _deterministic_fallback(task_type)

    domain_lines = "\n".join(
        f"- {d.code} ({d.nom}) : {', '.join(d.capacites or [])}" for d in domains
    )
    system = (
        "Tu es le routeur de domaine de Praxis. Une demande peut convoquer plusieurs "
        "domaines d'expertise à la fois (une évaluation d'impact convoque l'Économètre, "
        "le Suivi-Évaluation et le Rédacteur, par exemple). Choisis UNIQUEMENT parmi les "
        f"pôles suivants :\n{domain_lines}\n\n"
        "Réponds avec ce format JSON exact : "
        '{"poles": ["P8", "P9"], "type_livrable": "nom_court_du_type_de_livrable", '
        '"confiance": 0.87} où confiance reflète honnêtement ta certitude (0 à 1) - '
        "une demande vague ou ambiguë doit avoir une confiance basse, pas une confiance "
        "gonflée pour paraître utile."
    )
    user = f"Titre : {title}\nDomaine déclaré : {domain or 'non précisé'}\nDemande : {raw_request}"

    result = client.complete_json(system, user)
    if not result or not isinstance(result.get("poles"), list):
        return _deterministic_fallback(task_type)

    valid_poles = [p for p in result["poles"] if p in known_codes]
    if "P12" not in valid_poles:
        valid_poles.append("P12")

    confiance = result.get("confiance")
    confiance = float(confiance) if isinstance(confiance, (int, float)) else 0.0
    confiance = max(0.0, min(1.0, confiance))

    needs_clarification = confiance < settings.DOMAIN_ROUTER_CONFIDENCE_THRESHOLD

    return {
        "poles": valid_poles,
        "type_livrable": str(result.get("type_livrable") or "non_precise"),
        "confiance": confiance,
        "needs_clarification": needs_clarification,
        "source": "llm",
    }
