"""
LLM-assisted reformulation and planning - Phase 3.

Both functions here are suggestions, never silent decisions (§1.3 principle
2): they return None (or empty) whenever the LLM is unavailable, fails, or
produces something unusable, and the caller (TaskService) is responsible
for falling back to the Phase 2 deterministic heuristic. Nothing here ever
touches the database directly.
"""
from typing import Dict, List, Optional, Any

from .llm_client import LLMClient
from .orchestrator import Orchestrator


def suggest_reformulation(
    raw_request: str, title: str, task_type: str,
    dimensions: List[str], labels: Dict[str, str],
    domain: Optional[str] = None, existing_objective: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Ask the LLM to reformulate the request and pre-score each readiness
    dimension. Returns {"objective": str, "dimension_scores": {dim: 0..1},
    "rationale": str} or None if unavailable/unusable. This is a *starting
    point* for the user-facing sliders (§1.3: Praxis explicite l'incertitude,
    il ne devine pas) - the user still reviews and submits them."""
    client = LLMClient()
    if not client.available:
        return None

    dim_list = "\n".join(f"- {d}: {labels.get(d, d)}" for d in dimensions)
    system = (
        "Tu es le module de compréhension de Praxis, un atelier de travail "
        "assisté par IA pour un profil Planification / Suivi-Évaluation. "
        "Ta tâche : reformuler une demande utilisateur en objectif clair, et "
        "estimer honnêtement, pour chaque dimension listée, dans quelle mesure "
        "l'information disponible dans la demande suffit à démarrer le travail "
        "(0 = information totalement absente, 1 = parfaitement claire). "
        "Sois conservateur : une dimension non mentionnée dans la demande doit "
        "avoir un score bas, pas un score par défaut optimiste."
    )
    user = (
        f"Titre : {title}\n"
        f"Type de tâche : {task_type}\n"
        f"Domaine : {domain or 'non précisé'}\n"
        f"Demande brute : {raw_request}\n"
        f"Objectif déjà déclaré : {existing_objective or '(aucun)'}\n\n"
        f"Dimensions à évaluer :\n{dim_list}\n\n"
        'Réponds avec ce format JSON exact : {"objective": "...", '
        '"dimension_scores": {"<dimension>": 0.0}, "rationale": "..."} '
        "où dimension_scores contient une entrée pour CHAQUE dimension listée ci-dessus."
    )

    result = client.complete_json(system, user)
    if not result or "dimension_scores" not in result:
        return None

    # Clamp/validate: only known dimensions, only numeric 0-1 scores.
    scores = {}
    for dim in dimensions:
        val = result.get("dimension_scores", {}).get(dim)
        if isinstance(val, (int, float)):
            scores[dim] = max(0.0, min(1.0, float(val)))
    if not scores:
        return None

    return {
        "objective": result.get("objective") or existing_objective,
        "dimension_scores": scores,
        "rationale": result.get("rationale", ""),
    }


def draft_document_body(
    raw_request: str, title: str, task_type: str, objective: Optional[str] = None,
    domain: Optional[str] = None, kb_sources: Optional[List[Dict]] = None,
    raw_material: Optional[str] = None,
) -> Optional[str]:
    """Draft the body text of a non-data deliverable (redaction, recherche,
    planification, reponse_ao, autre) - this is where an LLM adds the most
    real value in Praxis, since these task types are fundamentally about
    writing, not computation. Returns None if unavailable (caller falls
    back to listing the raw KB sources instead of fabricating prose - see
    DocumentAgent._build_content_section).

    raw_material: text extracted from a source file the user uploaded
    (see DocumentAgent._extract_text_from_source) - notes, an existing
    report, a transcript. When present, the instruction is to *structure
    and clarify that material*, not write from scratch - this is what
    "externalisation de savoir acquis" (knowledge capitalization) actually
    is: making someone's own experience explicit and transferable, not
    inventing new content."""
    client = LLMClient()
    if not client.available:
        return None

    sources_block = ""
    if kb_sources:
        sources_block = "\n\nSources disponibles (cite-les par [n] dans le texte) :\n" + "\n".join(
            f"[{i+1}] {s['title']} ({s['domain']}): {(s.get('summary') or s.get('content') or '')[:400]}"
            for i, s in enumerate(kb_sources)
        )

    if raw_material:
        system = (
            "Tu es le rédacteur de Praxis, un atelier de travail assisté par IA pour un "
            "profil Planification / Suivi-Évaluation. La personne fait un travail de "
            "capitalisation : elle a fourni sa propre matière (notes, rapport, expérience "
            "vécue) et attend que tu la structures et la clarifies en document professionnel "
            "- PAS que tu inventes un contenu nouveau. Reformule, structure, complète les "
            "articulations manquantes, mais chaque idée doit venir de la matière fournie ou "
            "des sources citées. Réponds uniquement avec le texte du corps du document "
            "(plusieurs paragraphes séparés par une ligne vide), sans titre et sans balises markdown."
        )
        user = (
            f"Titre : {title}\nType de document : {task_type}\nDomaine : {domain or 'non précisé'}\n"
            f"Objectif : {objective or '(non précisé)'}\nDemande : {raw_request}\n\n"
            f"Matière fournie par l'utilisateur à structurer :\n{raw_material}{sources_block}"
        )
        return client.complete(system, user, max_tokens=2500)

    system = (
        "Tu es le rédacteur de Praxis, un atelier de travail assisté par IA pour un "
        "profil Planification / Suivi-Évaluation. Rédige le corps d'un document "
        "professionnel en français, ton clair et direct, sans fioritures ni formules "
        "creuses. Base-toi uniquement sur la demande et les sources fournies - n'invente "
        "aucun fait, aucune source, aucun chiffre. Réponds uniquement avec le texte du "
        "corps du document (plusieurs paragraphes séparés par une ligne vide), sans titre "
        "et sans balises markdown."
    )
    user = (
        f"Titre : {title}\nType de document : {task_type}\nDomaine : {domain or 'non précisé'}\n"
        f"Objectif : {objective or '(non précisé)'}\nDemande : {raw_request}{sources_block}"
    )
    return client.complete(system, user, max_tokens=1500)


def draft_executive_summary(
    title: str, task_type: str, raw_request: str, objective: Optional[str],
    n_observations: Optional[int], n_anomalies: Optional[int], strategy: Optional[str],
    enrichment: Optional[Dict] = None, treatment_effect: Optional[Dict] = None,
) -> Optional[str]:
    """Drafts the executive summary of a data-driven report from numbers
    already computed by DataAnalysisAgent (pandas/scipy/statsmodels) -
    never asks the LLM to compute anything itself (docs/PRAXIS_V1_
    ARCHITECTURE.md §1: "un LLM ne fait pas de statistique, il interprète
    des résultats déjà calculés"). Every fact passed in is real; the
    instruction is strictly to interpret/contextualize them against the
    original request, never to invent a number, fact, or source beyond
    what's listed. Returns None if unavailable or there's nothing to
    summarize - caller falls back to the fixed templated paragraph
    (still fully accurate, just less tailored to the specific request)."""
    client = LLMClient()
    if not client.available:
        return None

    facts = []
    if n_observations is not None:
        facts.append(f"{n_observations} observations analysées.")
    if n_anomalies is not None:
        facts.append(f"{n_anomalies} anomalies détectées et traitées (stratégie {strategy or 'B'}).")
    if enrichment:
        facts.append(
            f"{enrichment.get('n_imputations', 0)} valeur(s) manquante(s) imputée(s), "
            f"{enrichment.get('n_outliers_traites', 0)} valeur(s) aberrante(s) plafonnée(s)."
        )
    if treatment_effect:
        sig = "statistiquement significatif au seuil de 5%" if treatment_effect["significant_at_5pct"] else "non significatif au seuil de 5%"
        facts.append(
            f"Effet net estimé (ATE) : {treatment_effect['ate']:+,.2f} sur {treatment_effect['outcome']} "
            f"(IC95% [{treatment_effect['ci_95'][0]:+,.2f} ; {treatment_effect['ci_95'][1]:+,.2f}], "
            f"p={treatment_effect['p_value']:.4f}), {sig}. Méthode : {treatment_effect['method']}."
        )
    if not facts:
        return None

    system = (
        "Tu rédiges le résumé exécutif d'un rapport d'analyse pour Praxis. Tu ne calcules "
        "rien toi-même : chaque chiffre ci-dessous a déjà été calculé par un pipeline "
        "statistique réel (pandas/scipy/statsmodels) - ton rôle est UNIQUEMENT d'interpréter "
        "et de contextualiser ces résultats par rapport à la demande initiale, jamais "
        "d'inventer un chiffre, un fait ou une source absent de la liste donnée. Rédige un "
        "paragraphe dense et concret (100 à 180 mots) qui explique ce que ces résultats "
        "signifient pour la demande posée - pas une simple reformulation des chiffres. "
        "Réponds uniquement avec le paragraphe, sans titre ni balises markdown."
    )
    user = (
        f"Titre : {title}\nType de tâche : {task_type}\nDemande : {raw_request}\n"
        f"Objectif : {objective or 'non précisé'}\n\nRésultats déjà calculés :\n" + "\n".join(facts)
    )
    return client.complete(system, user, max_tokens=500)


def draft_recommendations(
    title: str, task_type: str, objective: Optional[str] = None,
    treatment_effect: Optional[Dict] = None, stats_summary: Optional[str] = None,
    qualitative_notes: Optional[str] = None, n_recommendations: int = 3,
) -> Optional[List[str]]:
    """Actionable recommendations grounded in the actual computed findings
    - never a generic template. Returns None if no LLM is configured or
    there is nothing to ground a recommendation in; DocumentAgent/
    PresentationAgent fall back to a scaffold keyed on the *sign and
    significance* of the treatment effect (still adaptive, never
    invented specific policy content) rather than a fixed placeholder."""
    client = LLMClient()
    if not client.available:
        return None

    findings = []
    if treatment_effect:
        sig = "statistiquement significatif" if treatment_effect["significant_at_5pct"] else "non significatif"
        findings.append(
            f"Effet de traitement estimé : {treatment_effect['ate']:+,.0f} sur {treatment_effect['outcome']} "
            f"(p={treatment_effect['p_value']:.3f}, {sig} au seuil de 5%)."
        )
    if stats_summary:
        findings.append(stats_summary)
    if qualitative_notes:
        findings.append(f"Éléments qualitatifs : {qualitative_notes[:1500]}")
    if not findings:
        return None

    system = (
        "Tu es le conseiller en politique publique de Praxis. À partir des résultats "
        f"fournis, propose {n_recommendations} recommandations concrètes et directement "
        "actionnables pour la suite du programme. Chaque recommandation doit découler "
        "des résultats donnés, sans inventer de fait nouveau. Une phrase par "
        'recommandation. Réponds avec ce format JSON exact : {"recommendations": ["...", "..."]}'
    )
    user = (
        f"Titre : {title}\nType : {task_type}\nObjectif : {objective or 'non précisé'}\n\n"
        "Résultats :\n" + "\n".join(findings)
    )
    result = client.complete_json(system, user)
    if not result or not isinstance(result.get("recommendations"), list):
        return None
    recs = [str(r).strip() for r in result["recommendations"] if str(r).strip()]
    return recs[:n_recommendations] or None


def fallback_recommendation_scaffold(treatment_effect: Optional[Dict]) -> List[str]:
    """No-LLM honest fallback for the Recommandations section - keyed on
    the actual sign/significance of the computed effect (still adaptive,
    never a single fixed sentence) rather than inventing specific policy
    content Praxis has no basis for."""
    if not treatment_effect:
        return [
            "Recommandations à formuler avec l'équipe projet — aucune IA de rédaction "
            "n'est configurée sur ce backend pour les rédiger automatiquement (voir "
            "ANTHROPIC_API_KEY dans .env.example)."
        ]
    if treatment_effect["significant_at_5pct"] and treatment_effect["ate"] > 0:
        return [
            "Documenter les mécanismes explicatifs de cet effet positif avant d'envisager "
            "une extension du programme à plus grande échelle.",
            "Étudier la reproductibilité de l'effet sur une autre cohorte ou zone géographique "
            "avant la phase suivante.",
            "Recommandations spécifiques à affiner avec l'équipe projet — aucune IA de "
            "rédaction configurée pour aller au-delà de ce constat général.",
        ]
    if treatment_effect["significant_at_5pct"] and treatment_effect["ate"] < 0:
        return [
            "Examiner les causes possibles de cet effet négatif avant de reconduire le "
            "programme en l'état.",
            "Envisager une revue qualitative approfondie des cas les plus défavorablement "
            "affectés par l'intervention.",
            "Recommandations spécifiques à affiner avec l'équipe projet — aucune IA de "
            "rédaction configurée pour aller au-delà de ce constat général.",
        ]
    return [
        "L'échantillon actuel ne permet pas de conclure à un effet statistiquement "
        "significatif — envisager d'augmenter la taille de l'échantillon ou la durée "
        "de suivi avant de décider de la suite du programme.",
        "Recommandations spécifiques à affiner avec l'équipe projet — aucune IA de "
        "rédaction configurée pour aller au-delà de ce constat général.",
    ]


def synthesize_qualitative_crossing(
    quantitative_summary: str, raw_material: str,
) -> Optional[str]:
    """Nuances a quantitative finding (e.g. a treatment-effect estimate)
    with qualitative material the user uploaded alongside the data file
    (field notes, interview summaries). Returns None if unavailable -
    caller includes the raw material directly instead (see
    DocumentAgent._build_data_sections), never fabricating this synthesis
    without real source content on both sides."""
    client = LLMClient()
    if not client.available:
        return None

    system = (
        "Tu es l'analyste de Praxis. On te donne un résultat quantitatif et une "
        "matière qualitative (notes de terrain, entretiens). Rédige un paragraphe "
        "de synthèse en français (environ 150 mots) qui nuance le résultat "
        "quantitatif à la lumière du vécu qualitatif - ni validation aveugle, ni "
        "contradiction forcée : montre où les deux se recoupent et où ils "
        "divergent. N'invente aucun fait qui ne soit pas dans les deux sources. "
        "Réponds uniquement avec le paragraphe, sans titre ni markdown."
    )
    user = f"Résultat quantitatif :\n{quantitative_summary}\n\nMatière qualitative :\n{raw_material[:6000]}"
    return client.complete(system, user, max_tokens=500)


def generate_plan_steps(
    raw_request: str, title: str, task_type: str, domain: Optional[str] = None,
    desired_deliverables: Optional[List[str]] = None,
) -> Optional[List[Dict]]:
    """Ask the LLM to tailor a plan to this specific request. Every step's
    "type" is validated against Orchestrator.AGENT_MAPPING - anything the
    LLM invents that doesn't map to a real agent is dropped, and a
    validation step is force-appended if the LLM forgot one, so a plan
    coming out of this function is exactly as executable as one built by
    the Phase 2 heuristic. Returns None if fewer than 2 valid steps survive
    (caller falls back to the heuristic plan entirely).

    desired_deliverables, when the person has chosen (see
    orchestrator.DELIVERABLE_CHOICES), tells the LLM not to bother
    proposing a presentation/redaction step for something nobody asked
    for - TaskService._prune_plan_for_deliverables enforces this
    regardless afterward, so this is an optimization, not the safety net."""
    client = LLMClient()
    if not client.available:
        return None

    known_types = sorted(set(Orchestrator.AGENT_MAPPING.keys()))
    deliverables_note = ""
    if desired_deliverables is not None:
        deliverables_note = (
            f"\nLivrables voulus par la personne : {', '.join(desired_deliverables) or '(aucun explicitement précisé)'}. "
            "N'ajoute une étape 'presentation' que si 'presentation_pptx' en fait partie ; "
            "n'ajoute une étape 'redaction' que si 'rapport_docx' en fait partie (sauf si c'est le seul "
            "moyen de produire quoi que ce soit pour ce type de tâche)."
        )
    system = (
        "Tu es le module de planification de Praxis. Tu proposes une séquence "
        "d'étapes pour accomplir une tâche, en choisissant UNIQUEMENT parmi "
        f"ces types d'étape reconnus par le système : {', '.join(known_types)}. "
        "N'invente jamais un autre type. Une étape de type 'validation' ou "
        "'controle' doit toujours être la dernière étape."
    )
    user = (
        f"Titre : {title}\n"
        f"Type de tâche : {task_type}\n"
        f"Domaine : {domain or 'non précisé'}\n"
        f"Demande : {raw_request}{deliverables_note}\n\n"
        'Réponds avec ce format JSON exact : {"steps": [{"type": "...", '
        '"name": "Nom court en français", "is_critical": true|false}, ...]}'
    )

    result = client.complete_json(system, user)
    if not result or not isinstance(result.get("steps"), list):
        return None

    valid_steps = []
    for step in result["steps"]:
        if not isinstance(step, dict):
            continue
        step_type = str(step.get("type", "")).lower().strip()
        # Same substring-match semantics as Orchestrator.assign_agent_to_step,
        # so "accepted here" really does mean "will resolve to a real agent".
        if not any(keyword in step_type for keyword in known_types):
            continue
        valid_steps.append({
            "type": step_type,
            "name": str(step.get("name") or step_type.capitalize())[:120],
            "is_critical": bool(step.get("is_critical", False)),
        })

    if len(valid_steps) < 2:
        return None

    has_validation = any(
        "validation" in s["type"] or "controle" in s["type"] for s in valid_steps
    )
    if not has_validation:
        valid_steps.append({"type": "validation", "name": "Contrôle qualité", "is_critical": True})

    return valid_steps
