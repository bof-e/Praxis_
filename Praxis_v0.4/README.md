# Praxis v0.4

Atelier de travail intelligent personnel — voir [`docs/PRAXIS_CONCEPTION_v0.3.md`](docs/PRAXIS_CONCEPTION_v0.3.md) pour la vision et l'architecture complète, et [`docs/REFONTE_v0.4.md`](docs/REFONTE_v0.4.md) pour ce qui a changé dans cette refonte.

Stack : **FastAPI + SQLAlchemy** (backend, seule source de vérité) + **Next.js** (frontend, client API pur, sans accès direct à la base).

## Démarrage rapide (sans Docker)

### Backend

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn src.api.main:app --reload --port 8000
```

Par défaut, le backend utilise SQLite (`praxis.db`, créé automatiquement au premier démarrage). Aucune configuration requise. Pour Postgres, voir `.env.example`.

### Frontend

```bash
npm install
NEXT_PUBLIC_API_URL=http://localhost:8000 npm run dev
```

Ouvrir http://localhost:3000.

> ⚠️ `NEXT_PUBLIC_API_URL` est figé **au build**, pas lu au runtime (limitation de Next.js). Si vous changez l'URL du backend, relancez `npm run build`.

## Avec Docker

D'abord, créer le fichier `.env` à partir du modèle — **obligatoire**, `docker compose up` s'arrête avec une erreur claire sinon (`env file .env not found`) :

```bash
cp .env.example .env
```

Éditez `.env` pour y mettre vos clés (`LLM_API_KEY`/`LLM_BASE_URL` pour Qwen, `E2B_API_KEY` si utilisé) — voir les exemples commentés dans le fichier. Sans clé, tout fonctionne quand même en mode heuristique.

```bash
docker compose up --build
```

Lance Postgres + backend (port 8000) + frontend (port 3000). `DATABASE_URL`/`SECRET_KEY`/`ENVIRONMENT`/`CORS_ORIGINS` restent fixés par `docker-compose.yml` (pointent vers le service `db` interne) quoi que contienne votre `.env` — seules vos clés LLM/E2B en sont issues.

## Tests

```bash
pip install -r requirements.txt
pytest
```

La suite (`tests/`) couvre le pipeline complet du cas pilote (§8 du document de conception) de bout en bout : création de tâche → readiness → plan → exécution réelle (nettoyage pandas, génération docx/pptx) → traçabilité, ainsi que trois régressions de bugs trouvés en cours de route (voir `docs/REFONTE_v0.4.md`).

## Structure du projet

```
src/
  api/main.py           # Endpoints FastAPI - la seule interface HTTP
  models/                # Modèles SQLAlchemy (Task, Plan, Execution, Artifact, ...)
  services/
    orchestrator.py      # Machine à états, assignation agent↔étape, stratégie de recovery
    execution_engine.py  # Fait tourner un Plan validé à travers les agents (le cœur du Phase 2)
    readiness_engine.py  # Score de préparation multidimensionnel par type de tâche
    error_recovery.py    # Journal d'erreurs + stratégies (retry/ask_user/escalate)
    validation_service.py# Contrôle qualité automatique avant livraison
    artifact_service.py  # Stockage des artefacts + provenance/lineage + traçabilité
    task_service.py / project_service.py / learning_service.py
  agents/                 # Un agent par type d'étape (nettoyage, rédaction, présentation...)
  config/settings.py
src/app/                  # Pages Next.js (App Router)
src/components/
src/lib/api.ts             # Client API TypeScript - point d'entrée unique vers le backend
tests/
docker/
docs/
```

## Ce qui fonctionne aujourd'hui

**Phase 2 (nettoyage/rapport)**
- Créer une tâche, uploader un fichier de données, évaluer sa préparation (readiness), générer et valider un plan.
- Exécuter le plan pour de vrai : l'agent de données lit un classeur Excel (feuille de données + dictionnaire de variables optionnel + notes de terrain optionnelles), détecte doublons et valeurs hors bornes/liste, applique la stratégie de nettoyage B (recodage en valeur manquante par défaut, stratégie A disponible), et produit une base apurée + un journal d'anomalies.
- Génération réelle d'un rapport `.docx` et d'une présentation `.pptx` à partir de ces résultats.
- Reprise sur erreur : si une étape échoue (ex. fichier de données manquant), la tâche passe en `error_recovery` ; une fois corrigée, relancer l'exécution reprend à l'étape qui a échoué plutôt que de tout refaire.
- Explorateur de traçabilité : `GET /tasks/{id}/traceability?row_id=...` répond à "pourquoi cette observation a-t-elle disparu ou changé ?" en remontant jusqu'au journal d'anomalies — implémente concrètement l'exemple du §6.2 du document de conception.

**Phase 3 (intelligence, connaissance, exécution, isolation)** — chaque brique est optionnelle et dégrade proprement vers le comportement Phase 2 en son absence :
- Reformulation de la demande et génération de plan par un vrai LLM (Anthropic **ou Qwen/tout endpoint compatible OpenAI** — DashScope, Ollama en local, DeepSeek, OpenRouter...) si configuré — sinon repli sur l'heuristique déterministe. Voir `.env.example` pour brancher Qwen concrètement (hébergé ou en local).
- Base de connaissances interrogeable (`/knowledge`, page frontend dédiée) avec recherche TF-IDF locale et `ResearchAgent` réel.
- Exécution asynchrone (`POST /tasks/{id}/execute-async` + polling `GET .../execution-status`) — l'interface ne bloque plus sur un gros fichier.
- Exécution du nettoyage de données dans un sandbox E2B isolé si `E2B_API_KEY` est configurée — voir `docs/REFONTE_v0.4.md` pour le détail de ce qui a été testé et ce qu'il reste à valider toi-même (le round-trip réel vers e2b.dev n'a pas pu être testé depuis l'environnement de développement).

**Phase 3.2 (adaptation à tous les types de tâches)**
- Les 7 types de tâches ont désormais chacun un plan qui produit un vrai livrable (`reponse_ao`, `rapport_evaluation`, `planification`, `autre` ne tombaient avant sur rien).
- `rapport_evaluation` adapte son plan selon qu'un fichier de données est attaché ou non.
- Les documents générés (`DocumentAgent`) ont une structure adaptée au type de tâche — plus de boilerplate "analyse de données" pour une tâche de rédaction ; section "Conformité" dédiée pour les réponses à appel d'offres.
- **Capitalisation de savoir** : uploader un document source (`.txt`, `.md`, `.docx` — vos notes, un rapport existant) pour une tâche de rédaction en fait la matière première du document produit, structurée par l'IA si configurée, reprise telle quelle sinon plutôt qu'ignorée.

**Phase 3.3 (support CSV, profondeur statistique, choix des livrables)**
- Support réel du **CSV** (et de plusieurs fichiers uploadés ensemble : données + dictionnaire + notes séparés), plus seulement Excel.
- Imputation des valeurs manquantes, traitement des valeurs aberrantes (IQR), variables dérivées avant/après, et une **estimation réelle d'effet de traitement** (différence-en-différences, `statsmodels`/`scipy`) — calculée sur vos données, jamais fabriquée.
- Rapport enrichi d'une section "Modélisation économétrique" avec graphique réel embarqué, et d'une section "Analyse qualitative croisée" quand un document qualitatif est uploadé à côté des données.
- **Choix du format de sortie** : une question ("Livrables souhaités") avant la génération du plan permet de ne demander que ce dont vous avez besoin (juste les données nettoyées, juste une présentation...) — le plan s'adapte, les agents inutiles ne tournent pas.
- Résumé exécutif et diapositive "Résultats clés" rédigés par IA à partir des chiffres déjà calculés quand une clé est configurée (jamais recalculés ni inventés) — repli honnête sur le gabarit fixe sinon.
- `PresentationAgent` réécrit pour être adaptatif au contenu réel plutôt qu'un squelette fixe à 2-3 diapositives.

**Praxis v1.0 — Phase 4 : routage de domaine (`docs/PRAXIS_V1_ARCHITECTURE.md`)**
- Une demande n'est plus classée dans un seul type de tâche fixe, mais routée vers un ou plusieurs des 11 pôles d'expertise (`GET /expertise-domains`) — une évaluation d'impact convoque l'Économètre, le Suivi-Évaluation et le Rédacteur à la fois.
- Le plan se compose dynamiquement à partir des pôles identifiés (`POST /tasks/{id}/route-domain`, avant `/plan`), avec repli déterministe sur le comportement existant si aucune IA n'est configurée.
- Les pôles pas encore construits (Statisticien, Démographe, Économiste, Planificateur, Suivi-Évaluation, SIG — Phase 5+) rapportent honnêtement leur absence dans le journal d'exécution plutôt que de produire un plantage ou un silence.

## Ce qui reste (non implémenté, documenté honnêtement dans le code)

- Les agents de pôle eux-mêmes (Phase 5+ de `docs/PRAXIS_V1_ARCHITECTURE.md`) — Statisticien, Démographe, Économiste, Planificateur, Suivi-Évaluation, SIG. Le document précise que chacun représente plusieurs semaines de travail effectif.
- Apprentissage de style, mémoire sémantique, registre de gabarits, référentiels méthodologiques par discipline (Phases 6-9 du même document).
- Recherche **web** — la base de connaissances est locale ; aucun outil de recherche web n'est câblé.
- Embeddings sémantiques réels (pgvector/RAG) pour la base de connaissances — la version actuelle est lexicale (TF-IDF), pas sémantique.
- Authentification / multi-utilisateur.
