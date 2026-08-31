# Praxis
## Cahier de conception personnel — v0.3 (Refonte)

**Statut :** refonte structurelle intégrant la revue critique de v0.2
**Usage :** strictement personnel
**Historique :** v0.1 (première maturation du concept) → v0.2 (approfondissement : profil Planification & Suivi-Évaluation, Readiness Score, architecture technique, feuille de route) → **v0.3, ce document** (refonte : les manques identifiés en revue de v0.2 sont intégrés dans l'architecture elle-même, pas ajoutés à la fin)

---

## 0. Note de refonte — ce qui change et pourquoi

La revue de v0.2 avait raison sur le fond : les manques identifiés ne sont pas des fonctionnalités oubliées, ce sont des **pièces de mécanique interne** sans lesquelles le reste du document décrit une belle façade sans la structure qui la porte. Cette refonte ne les ajoute donc pas en annexe — elle révise l'architecture (§2), le modèle d'objets (§3) et le cycle de vie (§5) pour qu'ils s'articulent nativement autour d'eux.

| # | Constat de la revue | Réponse dans cette refonte |
|---|---|---|
| 1 | Aucun composant ne pilote le cycle | `Orchestrator` devient le composant central de l'architecture (§2, §3.5) |
| 2 | `Tool` mélangeait deux niveaux différents | Distinction explicite `Agent` (responsable d'une étape) / `Tool` (capacité primitive) (§9) |
| 3 | La mémoire ne couvre pas les connaissances de domaine | Troisième couche mémoire : `KnowledgeBase`, distincte de `UserMemory` et `ProjectMemory` (§3.6) |
| 4 | Pas de traçabilité des affirmations | `Source` / `Evidence` en objets transversaux (§3.13) |
| 5 | `file_ref` trop vague pour représenter des fichiers de nature différente | Objet `Artifact` avec chaîne de provenance ; `Deliverable` en redevient un sous-ensemble précis (§3.12) |
| 6 | Versionnement mentionné mais pas généralisé | Principe transversal de versionnement, appliqué à tous les objets qui évoluent (§3.19) |
| 7 | « Le système apprend de chaque tâche » trop direct | Chaîne contrôlée `Observation → LearningCandidate → ValidatedPreference` (§3.7) |
| 8 | Un seuil de readiness unique pour toutes les tâches | Modèle de readiness dépendant du `Task.type` (§3.3) |
| 9 | Autonomie binaire (auto / validation humaine) | Quatre niveaux d'autonomie explicites (§4) |
| 10 | Pas de comportement défini après un échec | `Error & Recovery` formalisé, greffé sur `Execution` (§3.16) |
| 11-12 | Dépendances entre tâches et notion de projet sous-développées | `Project` en objet de premier rang, avec graphe de tâches (§3.1, §3.17) |
| 13-14 | Échéances et effort réel absents | `Task` enrichi (`deadline`, `estimated_duration`, `actual_effort`) (§3.2) |
| 15 | Pas de mesure de Praxis lui-même | Nouvelle section Métriques (§14) |
| 16 | Incohérence livrable/artefact dans le cas pilote | Règle explicite : un `Deliverable` est un `Artifact` listé dans `Task.deliverables[]`, rien d'autre ne l'est par défaut (§3.12, §8) |
| 17 | Apprentissage trop ambitieux pour un MVP | MVP = journalisation + suggestion ; adaptation automatique reportée (§3.7, §15) |
| 18 | Compétences de formation traitées comme acquises | Trois niveaux de confiance sur les compétences (§7) |
| 19 | Recherche documentaire sous-développée | `Research Agent` explicite, relié à `KnowledgeBase` et `Source` (§9) |
| 20 | Contraintes non hiérarchisées | Modèle `hard / soft / contextual constraints` (§3.18) |
| 21 | Profil utilisateur trop sommaire | `UserMemory.profile` enrichi (§3.6, §7) |
| 22 | Tout risquait de s'exécuter en synchrone | `Job` comme unité d'exécution asynchrone, file d'attente (§3.10, §12) |
| 23 | Synchronisation multi-appareils non définie | Reprise et précisée en §12.3 |
| 24 | Les 5 ajouts prioritaires listés en vrac | Intégrés chacun à sa place naturelle plutôt qu'en bloc (voir ce tableau) |

Le reste du document (profil de compétences, cas d'usage, écrans, sécurité, feuille de route) est repris de v0.2 et **condensé** plutôt que dupliqué in extenso, avec uniquement les ajustements que la refonte impose.

---

## 1. Résumé exécutif

Praxis est un système de travail intelligent personnel. Il ne se contente pas de conseiller — il reçoit une demande en langage naturel, la structure, mobilise les compétences, méthodes et connaissances pertinentes, planifie, exécute réellement le travail via des agents outillés, contrôle la qualité, produit un livrable exploitable, et capitalise ce qu'il a appris pour la prochaine tâche.

Cette version corrige un défaut de v0.2 : décrire *ce que Praxis doit produire* sans assez décrire *ce qui, à l'intérieur, décide et coordonne*. La refonte ajoute cette mécanique interne — un orchestrateur, des agents responsables, une base de connaissances, des artefacts tracés, une gestion d'erreur, une structure de projet — sans changer la vision de fond ni le choix de rester, dans un premier temps, un outil strictement personnel.

---

## 2. Architecture conceptuelle

```text
                              ┌───────────────┐
                              │  UTILISATEUR  │
                              └───────┬───────┘
                                      ↓
                              ┌───────────────┐
                              │   INTERFACE   │
                              └───────┬───────┘
                                      ↓
                                Requête brute
                                      ↓
                              ┌───────────────┐
                              │     TASK      │◄──── rattachée à ────┐
                              └───────┬───────┘                     │
                                      ↓                         ┌───────────┐
                            ┌───────────────────┐               │  PROJECT  │
                            │  READINESS ENGINE  │               └───────────┘
                            │ (modèle selon type) │                    ▲
                            └──────────┬─────────┘                    │
                                       ↓                              │
                            ┌─────────────────────┐                  │
                            │     ORCHESTRATOR     │──── arbitre dépendances,
                            │  (décide, ne stocke   │      échéances et
                            │      rien lui-même)   │      niveau d'autonomie
                            └──────────┬───────────┘
                                       │
        ┌───────────────┬─────────────┼─────────────┬───────────────┐
        ↓                ↓             ↓             ↓                ↓
  ┌───────────┐   ┌────────────┐  ┌─────────┐  ┌───────────┐  ┌──────────────┐
  │  CONTEXT  │   │   MEMOIRE   │  │  PLAN   │  │  AGENTS   │  │  VALIDATION  │
  │           │   │ User/Project│  │         │  │ + Tools   │  │              │
  │           │   │ /Knowledge  │  │         │  │           │  │              │
  └───────────┘   └────────────┘  └─────────┘  └─────┬─────┘  └──────────────┘
                                                       ↓
                                                 ┌───────────┐
                                                 │    JOB    │  (file d'attente,
                                                 │  QUEUE    │   exécution asynchrone)
                                                 └─────┬─────┘
                                                       ↓
                                                 ┌───────────┐
                                                 │ EXECUTION │──── échec ────┐
                                                 └─────┬─────┘               ↓
                                                       │             ┌───────────────┐
                                                       │             │ ERROR & RECOVERY│
                                                       │             └───────┬────────┘
                                                       │                     │ (retry / autre
                                                       │                     │  stratégie / escalade)
                                                       ↓
                                                 ┌───────────┐
                                                 │ ARTIFACT  │◄──────────────┘
                                                 │(provenance)│
                                                 └─────┬─────┘
                                                       ↓
                                          (sous-ensemble marqué "deliverable")
                                                       ↓
                                                 ┌───────────┐
                                                 │DELIVERABLE│
                                                 └─────┬─────┘
                                                       ↓
                                                 ┌───────────┐
                                                 │ LEARNING  │──── Observation → Candidate → Validated
                                                 └─────┬─────┘
                                                       ↓
                                        UserMemory / KnowledgeBase mises à jour
```

Trois idées structurent ce schéma, absentes de v0.2 :

- **L'Orchestrator ne stocke rien** — c'est une couche de décision, pas un objet de données de plus. Il lit `Task`, `Context`, les trois mémoires et le registre d'`Agent`/`Tool`, et décide (§3.5).
- **La mémoire a trois couches, pas deux** — ce que Praxis sait *de toi* (`UserMemory`), ce qu'il sait *d'un projet* (`ProjectMemory`), ce qu'il peut *consulter sur un domaine* (`KnowledgeBase`).
- **Tout ce qui est produit est un `Artifact`** — les fichiers intermédiaires (données nettoyées, brouillons) et les livrables finaux sont le même type d'objet, distingués seulement par un indicateur et leur place dans la chaîne de provenance.

---

## 3. Le modèle d'objets de Praxis

### 3.1 `Project` — le conteneur de premier rang

Absent comme objet réel en v0.2 (seulement une référence `project_id`). Devient central : une tâche isolée reste possible (`project_id` optionnel), mais tout travail qui se décompose naturellement en plusieurs tâches liées doit vivre dans un `Project`.

| Champ | Description |
|---|---|
| `id`, `name`, `description` | Identité du projet |
| `objectives[]` | Objectifs du projet, distincts des objectifs de chaque tâche |
| `stakeholders[]` | Parties prenantes concernées, le cas échéant |
| `tasks[]` | Les tâches du projet — **graphe**, pas simple liste (voir `Dependency`, §3.17) |
| `resources` | Budget d'appels, contraintes globales |
| `artifacts[]` | Tous les artefacts produits dans le projet |
| `decisions[]` | Décisions prises au niveau projet (pas au niveau d'une tâche isolée) |
| `deliverables[]` | Livrables finaux du projet |
| `deadline` | Échéance globale |
| `status` | actif / en pause / clos |
| `project_memory_ref` | Référence vers la `ProjectMemory` associée |

### 3.2 `Task` — l'unité de travail, enrichie

Reprise du modèle v0.1/v0.2, avec les champs que la revue a justement signalés comme absents.

| Champ | Description |
|---|---|
| `id`, `title`, `raw_request`, `objective`, `type`, `domain` | Inchangés |
| `deliverables[]` | Inchangé — voir la règle de §3.12 pour la distinction avec `Artifact` |
| `constraints` | Désormais structuré en trois niveaux — voir §3.18 |
| `data_sources[]`, `success_criteria[]`, `priority` | Inchangés |
| `status` | État dans le cycle de vie (§5) |
| `readiness_score` | Désormais calculé via un modèle dépendant de `type` (§3.3) |
| `missing_info[]` | Inchangés |
| `project_id` | Optionnel — rattachement à un `Project` |
| `parent_task_id` | Conservé pour compatibilité, mais la structure de dépendances passe désormais par `Dependency` (§3.17), plus expressive qu'une simple hiérarchie parent/enfant |
| `autonomy_level` | **Nouveau** — niveau 0 à 3, voir §4 |
| `deadline`, `estimated_duration`, `actual_effort` | **Nouveaux** — la tâche 13-14 de la revue |
| `created_at`, `updated_at` | Inchangés |

### 3.3 Readiness Engine — modèle dépendant du type de tâche

v0.2 proposait un score multidimensionnel avec un seuil unique (`readiness_global ≥ 75 %`). La revue avait raison : une tâche simple et une réponse à appel d'offres n'ont pas les mêmes dimensions critiques. Le Readiness Engine devient donc paramétrable par `Task.type`, via un objet `ReadinessModel` :

```yaml
ReadinessModel:
  task_type: Analyse de données
  dimensions: [objectif, contexte, données, livrables, contraintes, méthode, ressources]
  critical_dimensions: [données, objectif]     # bloquantes si trop basses
  threshold_global: 75%
  threshold_critical: 50%
```

```yaml
ReadinessModel:
  task_type: Réponse à appel d'offres
  dimensions: [objectif, contexte, données, livrables, contraintes, méthode,
               ressources, preuves, risques]
  critical_dimensions: [preuves, recevabilité_administrative, profil_candidat]
  threshold_global: 85%
  threshold_critical: 60%
```

Règle de passage, généralisée : *`readiness_global ≥ threshold_global` du modèle applicable, **et** aucune dimension listée en `critical_dimensions` sous `threshold_critical` — sinon, questions ciblées sur les seules dimensions critiques défaillantes.*

### 3.4 `Context` — inchangé sur le fond

```text
Context = Task + UserMemory pertinente + ProjectMemory pertinente
        + KnowledgeBase pertinente (nouveau) + Ressources externes disponibles
```

Seul changement : `Context` interroge désormais aussi `KnowledgeBase` (§3.6), pas seulement les deux mémoires personnelles.

### 3.5 `Orchestrator` — le composant manquant

Ce n'est pas un objet de données à instancier et stocker : c'est la logique qui relie tous les autres objets entre eux. Formaliser ses responsabilités évite qu'elles restent implicites (le vrai problème que la revue pointait).

| Décision | Entrée | Sortie | Règle |
|---|---|---|---|
| Passer de UNDERSTANDING à CONTEXTUALIZATION | `Task.readiness_score`, `ReadinessModel` | Transition d'état | Voir §3.3 |
| Choisir le niveau d'autonomie par défaut d'une tâche | `Task.type`, historique dans `UserMemory` | `Task.autonomy_level` proposé | Proposé, jamais imposé (§4) |
| Assigner un `Agent` à chaque étape du `Plan` | `Plan.steps[]`, registre d'agents | `step.agent_assigned` | Agent dont les capacités couvrent l'étape ; en cas d'ambiguïté, celui déjà connu de `UserMemory.tools_mastered` |
| Décider d'une reprise après échec | `ErrorEvent` | Nouvelle tentative / autre agent / escalade | Voir §3.16 |
| Décider si une observation devient une `LearningCandidate` | `Observation`, récurrence dans l'historique | `LearningCandidate` ou rien | Une occurrence isolée ne suffit pas (sauf correction explicite de l'utilisateur) |
| Arbitrer les dépendances d'un `Project` | Graphe de `Dependency` | Ordre d'exécution des tâches | Respect strict des dépendances bloquantes |

### 3.6 Mémoire — trois couches

**`UserMemory`** (persistante, transverse) — reprise de v0.2, profil enrichi comme demandé au point 21 de la revue :

| Champ | Description |
|---|---|
| `profile` | Identité, rôle, **niveau de formation, objectifs professionnels** (nouveau) |
| `skills` | Désormais à trois niveaux de confiance — voir §7 |
| `preferred_methods`, `tools_mastered`, `templates` | Inchangés |
| `writing_style`, `working_habits` | **Nouveaux** — ton, longueur habituelle, horaires de travail préférés |
| `decisions_log`, `preferences`, `learning_log` | Inchangés (voir §3.7 pour le contrôle de ce qui y entre) |

**`ProjectMemory`** — inchangée (§9.5 de v0.2), scoped à un `Project`.

**`KnowledgeBase`** — **nouvelle**, distincte des deux précédentes :

| Champ | Description |
|---|---|
| `id`, `domain` | Sujet couvert (méthodes d'enquête, cadre logique, normes GAR…) |
| `content_type` | méthode / norme / gabarit / référence / cours / article |
| `source_ref` | D'où vient ce contenu |
| `content_ref` | Le contenu lui-même ou son emplacement |
| `confidence` | validé / exploratoire |
| `tags[]` | Pour la recherche |

Règle de distinction, reprise directement de la revue : *`UserMemory` = ce que Praxis sait de toi. `ProjectMemory` = ce que Praxis sait d'un projet. `KnowledgeBase` = ce que Praxis peut consulter sur un domaine.*

### 3.7 `Learning` — chaîne contrôlée

v0.2 disait « chaque tâche rend le système meilleur », formulation que la revue jugeait à raison risquée si elle autorise une mise à jour silencieuse de `UserMemory`. La chaîne devient explicite :

```text
Task terminée
     ↓
Observation           { task_ref, what_was_observed }
     ↓ (seulement si récurrence, ou correction explicite de l'utilisateur)
LearningCandidate      { derived_from, hypothesis, confidence }
     ↓ (soumis à l'utilisateur — jamais appliqué directement)
ValidatedPreference     → écrite dans UserMemory (preferences / preferred_methods / templates)
```

Pour le MVP, la revue a également raison de limiter l'ambition (point 17) : `Learning` en v1 se limite à journaliser (`Observation`) et proposer (`LearningCandidate`) — l'adaptation automatique d'un comportement sans confirmation explicite est reportée à une phase ultérieure (§15).

### 3.8 `Plan` — inchangé, désormais lié à l'autonomie

Reprise de v0.2 (`task_ref`, `version`, `steps[]`, `checkpoints[]`, `estimated_effort`, `dependencies`, `risks`), avec un lien explicite nouveau : le nombre et la nature des `checkpoints[]` dépendent du `Task.autonomy_level` (§4), plutôt que d'être fixes.

### 3.9 `Agent` — distinct de `Tool`

Voir le détail et la liste des agents en §9. En bref : `Tool` répond à « comment faire quelque chose » (une bibliothèque, une API) ; `Agent` répond à « qui est responsable de cette étape » (un rôle qui choisit et enchaîne des `Tool`, porte le jugement métier de l'étape).

### 3.10 `Job` — unité d'exécution asynchrone

**Nouveau**, motivé par le point 22 de la revue : les étapes lourdes (analyse d'un gros fichier, génération d'un document long) ne doivent pas bloquer l'interface. `Job` encapsule une `Execution` pour la traiter en file d'attente.

| Champ | Description |
|---|---|
| `id`, `execution_ref` | Référence |
| `status` | en file / en cours / terminé / échoué |
| `progress` | Avancement, si mesurable |
| `worker_id` | Identifiant du worker qui traite le job |
| `started_at`, `ended_at` | Horodatage |

### 3.11 `Execution` — inchangée, désormais reliée à `Job` et `ErrorEvent`

Reprise de v0.2 (`plan_ref`, `step_ref`, `tool_used`, `started_at`, `ended_at`, `logs`, `outputs_produced`, `status`), avec deux liens nouveaux : chaque `Execution` non triviale passe par un `Job` (§3.10), et tout `status = échec` génère un `ErrorEvent` (§3.16).

### 3.12 `Artifact` — remplace la notion vague de fichier, avec provenance

**Nouveau**, réponse directe au point 5 (et à l'incohérence du point 16).

| Champ | Description |
|---|---|
| `id` | Identifiant |
| `task_ref`, `project_ref` | Rattachement |
| `kind` | `raw_data` / `processed_data` / `analysis` / `chart` / `table` / `draft` / `deliverable` |
| `format` | xlsx, docx, pptx, pdf, png, csv… |
| `file_ref` | Emplacement du fichier |
| `version` | Voir §3.19 |
| `produced_by` | `Agent` et `Execution` à l'origine |
| `derived_from[]` | Artefacts parents — la chaîne de provenance elle-même |

**Règle de distinction Artifact / Deliverable**, qui corrige directement l'incohérence relevée au point 16 : *un `Deliverable` n'est rien d'autre qu'un `Artifact` dont `kind = deliverable`, produit en correspondance exacte avec une entrée de `Task.deliverables[]`. Tout le reste (données nettoyées, tableaux intermédiaires, brouillons) reste un `Artifact` interne — consultable sur demande, mais jamais livré par défaut.* Exemple appliqué au cas pilote en §8.

### 3.13 `Source` / `Evidence` — traçabilité

**Nouveaux**, réponse au point 4.

```text
Affirmation dans un livrable
        ↓
    Evidence            { source_ref, excerpt_or_data_point }
        ↓
     Source              { type, reference, retrieved_at }
```

Règle : toute affirmation non triviale dans un `Deliverable` doit pouvoir être reliée à une `Evidence` — ou être explicitement marquée comme déduction ou hypothèse de Praxis (cohérent avec le principe fondateur n°4, §4.4 de v0.2, désormais outillé plutôt que seulement énoncé).

### 3.14 `Validation` — inchangée

Reprise de v0.2 (`execution_ref`, `checks_run[]`, `corrections_applied[]`, `overall_verdict`) — voir le détail des contrôles en §10.

### 3.15 `Deliverable` — reprécisé

Voir §3.12 : un `Deliverable` est un `Artifact` particulier, plus rien d'autre. Les champs (`format`, `file_ref`, `version`, `validated`, `delivered_at`) restent ceux de v0.2, hérités de la structure `Artifact`.

### 3.16 `Error & Recovery` — formalisé

**Nouveau**, réponse au point 10.

```text
Execution.status = échec
        ↓
   ErrorEvent          { execution_ref, error_type, attempted_recoveries[] }
        ↓
   Orchestrator décide :
        ├── Retry (même stratégie)         — échec transitoire probable
        ├── Change strategy / other Agent   — échec probable de méthode
        ├── Ask user                        — information manquante
        └── Escalate / Abort                — au-delà d'un nombre de tentatives fixé
```

`error_type` distingue au minimum : technique (timeout, format inattendu), données (fichier corrompu, valeurs manquantes bloquantes), permission (accès refusé), méthodologique (l'étape elle-même était mal posée — remonte alors à PLANNING, pas seulement à une nouvelle tentative).

### 3.17 `Dependency` — graphe de tâches

**Nouveau**, réponse aux points 11-12.

```text
Dependency { from_task_id, to_task_id, type: blocking | informational }
```

`Project.tasks[]` n'est donc pas une liste plate mais un graphe résolu par ces relations — l'Orchestrator s'en sert pour ordonner l'exécution (§3.5).

### 3.18 Modèle de contraintes — trois niveaux

**Nouveau**, réponse au point 20. `Task.constraints` n'est plus un champ unique mais trois listes :

| Niveau | Exemple | Remonte à `UserMemory` ? |
|---|---|---|
| `hard_constraints[]` | Format = DOCX, échéance, langue | Jamais automatiquement |
| `soft_preferences[]` | Style préféré, longueur habituelle | Via la chaîne `Learning` uniquement (§3.7) |
| `contextual_preferences[]` | Valable seulement pour ce projet | Jamais — reste dans `ProjectMemory` |

Cela évite exactement le risque que la revue soulevait : qu'une demande ponctuelle (« pour ce rapport-ci, raccourcis la méthodologie ») devienne à tort une règle permanente.

### 3.19 Principe de versionnement — transversal

**Nouveau**, réponse au point 6. Plutôt qu'un versionnement ad hoc sur `Plan` et `Deliverable` uniquement, la règle devient générale : *tout objet qui peut être révisé après sa première création (`Task`, `Plan`, `Artifact`, `ReadinessModel`) porte un champ `version`, et toute nouvelle version conserve une référence à la précédente.* C'est ce qui permet une demande du type « reprends le rapport mais modifie seulement la méthodologie » sans tout régénérer.

---

## 4. Niveaux d'autonomie

**Nouvelle section**, réponse au point 9. Remplace le tout-ou-rien (automatisé / validation humaine) de v0.2.

| Niveau | Nom | Comportement | Checkpoints |
|---|---|---|---|
| 0 | Assistance | Praxis propose, l'utilisateur exécute lui-même | À chaque étape |
| 1 | Exécution supervisée | Praxis exécute après validation du plan | Fin de PLANNING + fin de DELIVERABLE |
| 2 | Autonomie contrôlée | Praxis exécute seul les étapes non critiques, s'arrête sur les critiques | Fin de PLANNING + étapes marquées critiques + fin de DELIVERABLE |
| 3 | Autonomie avancée | Praxis planifie et exécute la tâche entière | Fin de DELIVERABLE uniquement |

Niveau par défaut recommandé pour le MVP : **niveau 1**, pour tous les types de tâches. Les niveaux 2 et 3 ne deviennent pertinents qu'une fois un type de tâche éprouvé (plusieurs occurrences réussies en niveau 1, capitalisées via `Learning`) — l'Orchestrator peut alors *proposer* de relever le niveau, jamais l'imposer.

---

## 5. Cycle de vie complet d'une tâche

Reprise de la structure d'états de v0.2 (déjà une amélioration par rapport à v0.1 : `CLARIFICATION`, `PLAN_VALIDATION` et `FINAL_VALIDATION` y étaient déjà distingués), avec les checkpoints désormais dépendants de `autonomy_level` et une boucle d'erreur explicite.

| État | Déclencheur | Action principale | Sortie |
|---|---|---|---|
| DRAFT | Requête reçue | Création de `Task` | `Task` brute |
| UNDERSTANDING | `Task` créée | Calcul du `readiness_score` via le `ReadinessModel` du `type` (§3.3) | `Task` enrichie |
| CLARIFICATION | Dimensions critiques insuffisantes | Questions ciblées, uniquement sur ces dimensions | Réponses utilisateur |
| CONTEXTUALIZATION | Compréhension suffisante | Assemblage du `Context`, y compris `KnowledgeBase` | `Context` |
| PLANNING | `Context` disponible | Décomposition en `Plan.steps[]`, `Orchestrator` propose un `autonomy_level` | `Plan` |
| PLAN_VALIDATION | `Plan` produit | Checkpoint humain (sauf niveau 3) | `Plan` validé |
| TOOL_SELECTION | `Plan` validé | `Orchestrator` assigne un `Agent` (et ses `Tool`) à chaque étape | `Plan` outillé |
| EXECUTION | Outils assignés | Exécution via `Job`, production d'`Artifact[]` | `Execution[]`, `Artifact[]` |
| ↳ ERROR_RECOVERY | `Execution.status = échec` | `ErrorEvent` → décision de l'`Orchestrator` (§3.16) | Nouvelle tentative, ou escalade |
| VALIDATION | Étapes exécutées | Contrôles qualité (§10), y compris traçabilité `Source`/`Evidence` | `Validation` |
| DELIVERABLE | Validation réussie | Génération des `Artifact` marqués `deliverable` | `Deliverable[]` |
| FINAL_VALIDATION | Livrables générés | Checkpoint humain (systématique, tous niveaux) | Acceptation |
| DEPLOYED | Livrable accepté | Remise effective | Livraison |
| LEARNING | Tâche terminée ou abandonnée | `Observation` → éventuelle `LearningCandidate` (§3.7) | `Learning`, proposition à l'utilisateur |

Boucles de retour : un échec en VALIDATION renvoie vers EXECUTION (défaut localisé) ou vers PLANNING (défaut structurel) — inchangé par rapport à v0.1/v0.2. La différence introduite ici est ERROR_RECOVERY, qui gère spécifiquement les échecs *techniques* d'exécution (avant même d'arriver à VALIDATION), là où v0.2 ne définissait ce comportement nulle part.

---

## 6. Modèle d'interaction humain-système

Les trois checkpoints de v0.2 (Clarification, Validation du plan, Validation du livrable) restent le socle, mais leur application dépend maintenant du niveau d'autonomie (§4) :

- **FINAL_VALIDATION reste systématique à tous les niveaux**, y compris le niveau 3 — c'est un choix de conception délibéré : aucune version de Praxis, même la plus autonome, ne livre sans repasser par l'utilisateur.
- **PLAN_VALIDATION s'efface uniquement au niveau 3.**
- **Les checkpoints intermédiaires (étapes marquées critiques) n'existent qu'au niveau 2**, où justement seules les étapes jugées sensibles remontent.

---

## 7. Profil utilisateur et modèle de compétences

Repris de v0.2 (§5 de ce document), avec l'ajustement demandé au point 18 de la revue : les compétences issues de la formation ne sont plus traitées comme acquises par défaut.

| Niveau de confiance | Signification |
|---|---|
| `known_skill` | Compétence confirmée par l'usage réel dans Praxis ou déclarée explicitement |
| `training_exposure` | Compétence étudiée en formation, non encore confirmée en pratique |
| `unverified` | Mentionnée nulle part, ne doit jamais être supposée |

Le tableau détaillé des domaines (mathématiques et statistiques, économie, démographie, méthodes quantitatives, suivi-évaluation, planification, rédaction…) reste celui de v0.2 — chaque entrée y est désormais implicitement classée `training_exposure` tant qu'elle n'a pas été confirmée par un usage réel dans Praxis, et non `known_skill` comme la formulation initiale le laissait supposer.

---

## 8. Cas d'usage prioritaires et cas pilote

Repris de v0.2 (tableau de priorités P1 à P6, cas pilote = analyse d'un fichier Excel d'enquête → rapport + présentation). Seul changement, correction directe du point 16 :

```text
Artifacts (internes, non livrés par défaut) :
  - données brutes                    (kind: raw_data)
  - données nettoyées                 (kind: processed_data)
  - dictionnaire de variables          (kind: table)
  - tableaux de résultats intermédiaires (kind: table)
  - graphiques                        (kind: chart)

Deliverables (déclarés dans Task.deliverables[]) :
  - rapport (docx)                    (kind: deliverable)
  - présentation (pptx)               (kind: deliverable)
  - fichier de résultats, si demandé explicitement (kind: deliverable)
```

Le fichier Excel nettoyé n'est donc un `Deliverable` que si l'utilisateur le demande explicitement dans sa requête initiale — sinon il reste un `Artifact` interne consultable.

---

## 9. Registre des agents, outils et méthodes

Reprise de la liste d'outils de v0.2 (§15), désormais organisée par `Agent` responsable plutôt qu'en vrac — réponse au point 2 de la revue.

| Agent | Rôle | Tools typiquement mobilisés |
|---|---|---|
| Understanding Agent | Reformule la demande, calcule le `readiness_score` | Aucun outil externe |
| Planning Agent | Construit le `Plan`, propose un `autonomy_level` | Raisonnement seul |
| Data Analysis Agent | Nettoie, analyse, teste | pandas, scipy, statsmodels, numpy |
| Document Agent | Rédige et met en forme | python-docx, Pandoc |
| Presentation Agent | Construit les supports visuels | python-pptx, matplotlib, seaborn |
| Research Agent | Cherche, sourcing, alimente `Evidence` | Recherche web, requêtes sur `KnowledgeBase` |
| Validation Agent | Contrôle qualité (§10) | Règles programmatiques + LLM-juge |
| Error Recovery Agent | Gère les `ErrorEvent` (§3.16) | — (logique de décision uniquement) |

Le registre `Tool` détaillé de v0.2 (pandas, openpyxl, matplotlib, python-docx, python-pptx, Pandoc, PostgreSQL, pgvector…) reste valable tel quel : chaque outil y est mobilisé *par* un agent, jamais directement par l'utilisateur ou l'Orchestrator.

---

## 10. Cadre de contrôle qualité

Repris intégralement de v0.2 (§14) : les huit types de contrôle (cohérence interne, exactitude factuelle, validité méthodologique, conformité aux contraintes, qualité rédactionnelle, complétude, reproductibilité, confidentialité), ainsi que les contrôles spécifiques par type de tâche (analyse statistique, suivi-évaluation, rapport professionnel). Un ajout : le contrôle d'exactitude factuelle s'appuie désormais explicitement sur la chaîne `Evidence` → `Source` (§3.13) plutôt que sur une vérification non tracée.

---

## 11. Interface utilisateur

Repris de v0.2 (§17, dix écrans), avec deux écrans supplémentaires rendus nécessaires par cette refonte :

- **Écran Projets** — déjà prévu en v0.2, mais doit désormais afficher le graphe de dépendances entre tâches (§3.17), pas une simple liste.
- **Écran Base de connaissances** *(nouveau)* — consulter, ajouter ou corriger des entrées de `KnowledgeBase`, distinct de l'écran Mémoire utilisateur.

---

## 12. Architecture technique

### 12.1 Ajout structurant : la file de jobs

Réponse au point 22. Toute étape d'exécution non triviale passe désormais par un `Job` plutôt que d'être traitée en synchrone dans la requête web :

```text
Interface → API → Job créé → File d'attente → Worker → Artifact produit → Notification
```

### 12.2 Options d'hébergement

Inchangées par rapport à v0.2 (§18) : Option A (Next.js + Supabase, rapide) pour un prototype, Option B (VPS, PostgreSQL, MinIO, Docker) pour davantage de contrôle et de confidentialité. La file de jobs (§12.1) s'implémente dans les deux cas — via une queue managée (Option A) ou un worker Docker dédié (Option B).

### 12.3 Synchronisation multi-appareils

Précisée : l'état de référence (tâches, projets, artefacts) vit côté serveur ; chaque appareil en est un client qui reflète cet état, sans état local durable propre. Cela évite d'avoir à gérer des conflits de synchronisation complexes dans une v1 mono-utilisateur — un choix volontairement simple, à réviser seulement s'il devient un problème réel en usage.

---

## 13. Sécurité, confidentialité et provenance

Repris de v0.2 (§19 : authentification, chiffrement, sandbox, permissions, journalisation, sauvegarde, confidentialité des données d'enquête), avec un ajout direct de cette refonte : la chaîne de provenance des `Artifact` (§3.12) et des `Source`/`Evidence` (§3.13) doit elle-même être protégée au même niveau que les données qu'elle documente — la traçabilité perd son sens si elle peut être modifiée après coup sans laisser de trace.

---

## 14. Métriques de performance de Praxis

**Nouvelle section**, réponse au point 15 de la revue — mesurer le système lui-même, pas seulement ce qu'il produit.

| Métrique | Ce qu'elle indique |
|---|---|
| Taux de complétion des tâches | Praxis va-t-il au bout de ce qu'il commence ? |
| Taux d'acceptation du plan (sans modification) | La planification est-elle fiable ? |
| Taux de succès au premier passage en VALIDATION | La qualité d'exécution est-elle bonne d'emblée ? |
| Nombre moyen de corrections par tâche | Combien d'allers-retours sont réellement nécessaires ? |
| Écart entre `estimated_duration` et `actual_effort` | Praxis apprend-il à mieux estimer ? |
| Taux d'intervention utilisateur | L'autonomie progresse-t-elle réellement au fil du temps ? |
| Taux d'acceptation des livrables | Le résultat final correspond-il à l'attente ? |

Objectif de fond, au-delà des chiffres eux-mêmes : pouvoir répondre à la question *combien de temps Praxis fait-il réellement gagner ?* — sans cette mesure, impossible de savoir si le système est réellement utile ou seulement intéressant à construire.

---

## 15. Feuille de route révisée

La feuille de route de v0.2 reste valable dans ses grandes lignes, mais les 5 ajouts prioritaires de la revue doivent entrer dès la Phase 1, pas être reportés :

**Phase 0 — Conception (ce document)**
Geler ce modèle d'objets v0.3 comme référence.

**Phase 1 — Wizard of Oz, enrichi**
Dérouler manuellement 3 à 5 tâches réelles à travers *l'intégralité* du cycle de vie révisé (§5) — y compris simuler à la main les décisions de l'`Orchestrator`, distinguer `Artifact` et `Deliverable` dans les livrables simulés, et tenir un `Project` même sur un cas à tâche unique, pour éprouver le graphe de dépendances sur un cas simple avant un cas complexe.

**Phase 2 — MVP mono-tâche, avec la mécanique interne dès le départ**
Le cas pilote (analyse Excel → rapport + présentation) reste inchangé, mais le MVP implémente désormais, même sous une forme minimale : un `Orchestrator` (même simple, à base de règles), la distinction `Artifact`/`Deliverable`, une gestion d'erreur basique (retry puis escalade), et `Project` comme conteneur — même si `KnowledgeBase`, les niveaux d'autonomie 2-3 et les métriques de performance peuvent attendre la Phase 3.

**Phase 3 — Généralisation**
`KnowledgeBase`, autonomie de niveau 2, `Learning` sous forme de `LearningCandidate` proposés, métriques de performance (§14).

**Phase 4 — Apprentissage et autonomie avancée**
Autonomie de niveau 3 pour les types de tâches éprouvés, adaptation plus fine de `UserMemory`.

**Phase 5 — Extension**
Objets complémentaires de domaine (réponses à appels d'offres : `Requirement`, `BidderProfile`, `CapabilityGap`, `ScoreModel` — cf. v0.1 §7.3), inchangée par rapport à v0.2.

---

## 16. Décisions à trancher avant de coder

Reprise des décisions de v0.2 (périmètre personnel, cas pilote, outils statistiques, hébergement, stockage, modèle IA, format de documentation), avec les décisions supplémentaires que cette refonte introduit :

- **Niveau d'autonomie par défaut du MVP** — recommandation de cette refonte : niveau 1 pour tous les types de tâches, sans exception, tant qu'aucun type n'a été éprouvé.
- **Granularité de `KnowledgeBase` en v1** — vide au départ, alimentée manuellement au fil de l'usage, ou pré-remplie avec quelques références méthodologiques connues (cadre logique, GAR) ? Recommandation : vide au départ, pour éviter de devoir valider a priori la pertinence d'un contenu jamais utilisé.
- **Sévérité du contrôle de traçabilité (`Source`/`Evidence`)** — obligatoire dès le MVP, ou seulement à partir de la Phase 3 ? Recommandation : présent mais non bloquant en MVP (les affirmations non tracées sont signalées, pas empêchées).
- **Implémentation minimale du `Job` en MVP** — file d'attente réelle dès le départ, ou traitement synchrone tant que les tâches restent courtes ? Recommandation : traitement synchrone en Phase 2, `Job` réel à partir de la Phase 3 si la durée des tâches le justifie.

---

## 17. Conclusion

Cette refonte ne change pas ce que Praxis doit faire — produire de vrais livrables à partir de demandes en langage naturel, en mobilisant le capital intellectuel propre de son utilisateur. Elle change *comment le document le décrit* : les mécanismes qui manquaient (orchestration, distinction agent/outil, connaissances de domaine, traçabilité, gestion d'erreur, structure de projet) sont maintenant partie intégrante du modèle d'objets et du cycle de vie, pas une liste séparée de bonnes idées à ajouter plus tard.

La stratégie reste la même qu'en v0.2 : Wizard of Oz avant tout code, cas pilote unique et circonscrit, extension progressive — cette refonte rend simplement cette progression plus sûre, en s'assurant que le MVP de la Phase 2 n'aura pas à être réarchitecturé quand ces mécanismes deviendront nécessaires.
