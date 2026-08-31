# Refonte v0.4 — journal de décisions

Ce document explique ce qui a changé entre l'archive que tu m'as fournie (`PraxisFinal.zip`) et cette version, et pourquoi. Il est écrit pour toi et pour n'importe quel agent de code (Claude Code, Gemini, Qwen...) qui reprendrait ce projet ensuite.

## Diagnostic initial

L'archive contenait deux implémentations parallèles et déconnectées :

1. **Un "MVP Phase 1"** Next.js + Prisma + SQLite : un modèle `Task` générique (`status: "pending"`), sans aucun rapport avec le document de conception v0.3.
2. **Un backend FastAPI + SQLAlchemy**, nettement plus riche (770 lignes de modèles, 8 services avec une vraie logique métier — moteur de readiness pondéré, machine à états, recovery, provenance des artefacts) mais **entièrement déconnecté du premier**, et surtout : **rien n'exécutait jamais un plan**. Les agents (`DataAnalysisAgent`, `DocumentAgent`, etc.) étaient des stubs renvoyant des dictionnaires statiques. C'est exactement le trou identifié dans ta propre feuille de route (§11, Phase 2).

## Décisions structurelles

- **FastAPI + SQLAlchemy = seule source de vérité.** Suppression de Prisma, du schéma SQLite côté Node, et de la route `/api/tasks` Next.js qui l'utilisait. Le frontend Next.js devient un client API pur (aucun accès direct à une base de données).
- **Suppression de `PraxisBackup/`** : une sauvegarde d'un état antérieur, déjà intégrée dans `src/`.
- **Suppression de 6 fichiers de documentation redondants/contradictoires** (`README_DEPLOIEMENT*.md`, `README_FICHIERS.md`, `README_INTEGRATION.md`, `TESTER_LOCALEMENT.md`) au profit d'un seul `README.md`.
- **Suppression des workflows CI** (`.github/workflows/`) : ils pointaient vers une structure de dossiers (`praxis/`) différente de la racine réelle du dépôt et référençaient des secrets GCP/Vercel jamais configurés — ils auraient échoué au premier déclenchement. Prématuré pour un outil personnel à ce stade.
- **`docker-compose.yml` ne définissait pas de service frontend** alors que `docker/Dockerfile.frontend` existait — corrigé. Le service `redis` a été retiré (aucune file de jobs n'existe dans ce MVP ; la doc `settings.py` documentait déjà ce choix, `docker-compose.yml` ne le reflétait pas).

## La pièce manquante : `src/services/execution_engine.py`

C'est le vrai travail de cette refonte. Le moteur :
- fait tourner les étapes d'un `Plan` validé une par une, en dispatchant vers l'agent assigné par l'`Orchestrator` ;
- persiste les artefacts produits avec leur lignage (`derived_from`) via `ArtifactService` ;
- route les échecs vers `ErrorRecoveryService` (retry / ask_user / escalate selon le type d'erreur) ;
- **reprend une exécution interrompue** à l'étape qui a échoué plutôt que de tout rejouer (voir tests `test_missing_data_pauses_for_user_then_resumes`) ;
- refuse silencieusement de régénérer un livrable déjà produit sauf `force=True` (idempotence).

Les agents `DataAnalysisAgent`, `DocumentAgent` et `PresentationAgent` ont été réécrits avec du vrai code (pandas pour le nettoyage façon Stratégie B du cas pilote, `python-docx` et `python-pptx` pour les livrables). `ResearchAgent` reste un stub, mais un stub **honnête** : il annonce explicitement que la Knowledge Base/RAG n'existe pas encore (Phase 3), plutôt que de fabriquer de fausses sources.

## Point d'originalité : l'explorateur de traçabilité

Le document de conception justifie tout le système de provenance (§6.2) par un exemple concret : *"si le superviseur demande 'pourquoi le ménage MEN_035 a disparu des moyennes ?', Praxis remonte le graphe."* Ce mécanisme n'était jamais implémenté — le graphe existait sur le papier.

`ArtifactService.explain_row(task_id, row_id)` (endpoint `GET /tasks/{id}/traceability?row_id=...`) répond exactement à cette question : il retrouve le journal d'anomalies le plus récent, cherche l'identifiant, et renvoie la variable, la raison et l'action appliquée. Câblé côté frontend dans un vrai encart "Explorateur de traçabilité" sur la page de détail d'une tâche.

## Bugs silencieux corrigés en cours de route

Aucun n'avait de test avant cette refonte — ils ne se déclenchaient qu'à l'exécution réelle, ce que le code n'avait jamais fait avant.

1. **`readiness_engine.py`** — `DEFAULT_MODELS.get(task_type, DEFAULT_MODELS[TaskType.AUTRE])` plantait pour *toute* tâche : Python évalue la valeur par défaut avant l'appel à `.get()`, et `TaskType.AUTRE` n'avait pas d'entrée dans le dictionnaire. Corrigé en ajoutant l'entrée manquante.
2. **`orchestrator.decide_recovery_strategy`** — comparait un membre d'`Enum` brut à des chaînes de caractères (`error_type == "technical"`), toujours faux pour un `Enum` non-`str`. Toutes les erreurs tombaient silencieusement dans la branche par défaut. Corrigé en comparant `.value`.
3. **`artifact_service.create_artifact`** — construisait `Artifact(metadata=...)`, mais la colonne réelle s'appelle `extra_metadata` (SQLAlchemy réserve `metadata` comme attribut de classe). L'affectation ne levait aucune erreur ; elle écrivait dans un attribut Python fantôme, jamais persisté. Toutes les métadonnées d'artefacts étaient perdues silencieusement depuis le début.
4. **`database.py`** — chaque requête FastAPI ouvrait une session SQLAlchemy jamais fermée (fuite de connexion). Remplacé par une factory de session unique + une dépendance génératrice qui ferme systématiquement la session.
5. **`next.config.mjs`** ne déclarait pas `output: 'standalone'`, et `docker/Dockerfile.frontend` copiait un dossier `public/` qui n'existait pas — le build Docker du frontend aurait échoué. `Dockerfile.frontend` référençait aussi Prisma (`npx prisma generate`), supprimé avec le reste de la stack Prisma.
6. **`docker/Dockerfile.backend`** lance `gunicorn` en production mais `gunicorn` n'était pas dans `requirements.txt` — ajouté.

## Phase 3 (v0.4.1) — intelligence, connaissance, exécution, isolation

Quatre chantiers menés à terme, testés, zéro régression sur les 32 tests existants (35 au total désormais) :

### 1. Intelligence LLM (`llm_client.py`, `llm_assist.py`)

Reformulation de la demande et génération de plan par un vrai appel à Claude (Anthropic), avec repli **systématique et testé** sur l'heuristique déterministe existante quand aucune clé n'est configurée — comportement par défaut inchangé. Le plan généré par l'IA est validé strictement contre le vocabulaire d'étapes connu de l'`Orchestrator` (`AGENT_MAPPING`, hissé en constante de classe pour éviter toute divergence) : toute étape inventée est rejetée, une étape de validation est ajoutée d'office si absente. Endpoint `POST /tasks/{id}/reformulate`, bouton "✨ Suggérer avec l'IA" côté frontend.

### 2. Base de connaissances (`knowledge_service.py`)

Le modèle `KnowledgeBase` existait déjà dans le schéma (§6.3) mais rien ne l'alimentait ni ne le consultait. Recherche **TF-IDF pure Python** (aucune dépendance lourde, aucune clé API requise, aucun accès réseau à un service d'embeddings — indisponible depuis cet environnement) avec repli LLM optionnel pour synthétiser une réponse sourcée. `ResearchAgent` réécrit pour de vrai : cherche, synthétise si possible, reste honnête si la base est vide. `DocumentAgent` enrichi d'une section "Sources". CRUD complet + page frontend dédiée (`/knowledge`).

### 3. Exécution asynchrone (`job_tracker.py`)

Pas de Celery/Redis — disproportionné pour un outil mono-utilisateur (voir le commentaire mis à jour dans `settings.py`). À la place : un thread d'arrière-plan avec verrou anti-double-lancement (`job_tracker.py`, testé unitairement) + endpoint de polling `GET /tasks/{id}/execution-status`. Le moteur SQLite a été renforcé (`timeout=30` sur les connexions) pour l'accès concurrent lecture/écriture entre le thread d'exécution et les requêtes de polling. Le frontend utilise maintenant systématiquement le chemin asynchrone (bouton "Exécuter" ne bloque plus la page).

### 4. Sandbox d'exécution E2B (`sandbox.py`, `sandbox_scripts.py`)

**Décision explicite du produit owner : garder la stratégie E2B du document de conception plutôt que substituer une alternative allégée.**

Point méthodologique important : avant d'écrire une ligne de code, le SDK réel (`e2b-code-interpreter==2.9.1`) a été installé et inspecté (constructeur, signatures de `files.write`/`files.read`/`run_code`, objet `Execution`) pour coder contre la vraie API plutôt que de deviner. Cet environnement de développement n'a en revanche aucune route réseau vers `e2b.dev` — le round-trip réel n'a donc **pas pu être testé de bout en bout ici**, contrairement à l'intégration LLM.

Ce qui *a* pu être validé sans réseau, et qui donne une vraie garantie : `sandbox_scripts.py` génère le script exécuté à distance en extrayant le **code source réel** des méthodes de nettoyage (`DataAnalysisAgent._clean`, `_describe`, etc. — converties en `@staticmethod` pour ça) via `inspect.getsource()`, plutôt que de dupliquer l'algorithme dans un second fichier qui aurait fini par diverger. Ce script généré a été exécuté pour de vrai dans un sous-processus isolé, et ses sorties comparées bit à bit à celles du chemin en-processus sur le même fichier : identiques (`tests/test_sandbox.py`). `DataAnalysisAgent` tente le sandbox en premier si `E2B_API_KEY` est configurée, et retombe silencieusement sur l'exécution en local dans tous les autres cas (indisponible, erreur réseau, échec du script) — testé avec un faux runner.

**Ce qu'il te reste à faire toi-même** : créer un compte sur e2b.dev, poser `E2B_API_KEY` dans `.env`, et vérifier la connexion réelle avec la commande donnée dans `.env.example`. Le préfixe `[Exécuté dans un sandbox E2B isolé]` apparaît dans le journal d'exécution d'une tâche quand ça fonctionne réellement à distance.

Un effet de bord découvert en cours de route : installer `e2b-code-interpreter` a fait remonter `httpx` vers une version incompatible avec `starlette`/`TestClient` (le paramètre `app=` a été retiré). `httpx` est maintenant fixé à `<0.28` dans `requirements.txt`.

## Ce qui reste ouvert après cette Phase 3

- Recherche **web** (pas seulement la base de connaissances locale) — toujours absente, aucun outil de recherche web n'est câblé dans ce backend.
- Base de connaissances en embeddings sémantiques réels (pgvector/RAG) — la version TF-IDF actuelle est une vraie amélioration fonctionnelle, mais reste lexicale, pas sémantique.
- Le round-trip E2B réel, à valider par toi (voir ci-dessus).
- Authentification / multi-utilisateur — toujours hors scope, outil personnel assumé.

## v0.4.2 — adaptation à tous les types de tâches + capitalisation de savoir

Deux chantiers menés en réaction à des tests réels, pas des suppositions :

### Un vrai trou trouvé en testant chaque type de tâche

Sur les 7 valeurs de `TaskType`, seules 3 (`analyse_donnees`, `redaction`, `recherche`) avaient un plan dédié dans `_generate_basic_plan_steps`. Les 4 autres (`reponse_ao`, `rapport_evaluation`, `planification`, `autre`) tombaient sur un plan de repli contenant une étape de type `"execution"` — qui ne correspond à **aucune entrée** de `Orchestrator.AGENT_MAPPING`. Résultat vérifié : ces tâches atteignaient le statut `deliverable` avec **zéro artefact produit** — un faux succès silencieux. Corrigé avec :
- Un plan réel pour chacun des 4 types manquants, construit uniquement à partir de types d'étape que l'`Orchestrator` sait dispatcher (`tests/test_task_type_coverage.py::test_every_plan_step_type_maps_to_a_real_agent` empêche toute régression future de ce genre).
- `rapport_evaluation` devient **adaptatif** : plan orienté données si un fichier est attaché au moment de la génération du plan, sinon plan orienté contenu — une évaluation peut être qualitative.
- `DocumentAgent` bifurque maintenant sa structure selon le type de tâche : les sections "Résultats"/"Limites" façon rapport de données n'apparaissent plus pour les tâches de rédaction/planification/réponse à AO, remplacées par une structure Objectif/Contenu/Sources adaptée — plus la section "Conformité" pour les réponses à AO (`reponse_ao`), construite à partir de `task.hard_constraints`.
- Un bug connexe trouvé au passage : `ValidationService._check_constraint` supposait que chaque contrainte était un dict `{"type", "value"}` et plantait sur une chaîne simple — jamais déclenché avant, car rien ne peuplait vraiment `hard_constraints` avant ce test. Corrigé pour tolérer les contraintes en texte libre.

### Externalisation de savoir acquis (capitalisation)

Besoin explicite : transformer du savoir déjà acquis (notes de terrain, rapports, expérience) en documents structurés — pas seulement rédiger à partir de rien. Le pipeline ne savait lire que des fichiers Excel ; un document source uploadé pour une tâche de rédaction était silencieusement ignoré.

`DocumentAgent._extract_raw_material` lit maintenant `.txt`/`.md`/`.docx` uploadés et les traite comme matière première prioritaire :
- Avec une IA configurée, `llm_assist.draft_document_body` reçoit un prompt spécifique : structurer et clarifier la matière fournie, ne jamais inventer de contenu nouveau.
- Sans IA configurée, la matière est reprise telle quelle dans le document, clairement annotée comme non retravaillée — mieux qu'un placeholder vide, honnête sur ce qui a été fait ou non.
- PDF reste un vrai manque (pas de dépendance d'extraction PDF dans `requirements.txt`) — à ajouter si besoin.

44 tests passent au total.

## v0.4.3 — brancher un autre LLM (Qwen, ou tout endpoint compatible OpenAI)

`llm_client.py` était câblé en dur sur le SDK Anthropic — `settings.LLM_PROVIDER` existait mais n'était lu nulle part, une incohérence trompeuse. Corrigé pour de vrai avec un second provider `openai_compatible`, qui couvre Qwen (hébergé via DashScope, ou auto-hébergé via Ollama/vLLM), DeepSeek, OpenRouter, ou OpenAI lui-même — tous parlent le même schéma `/chat/completions`. Basculer se fait par variables d'environnement, sans toucher au code (voir `.env.example` pour les deux façons concrètes d'utiliser Qwen).

Même diligence que pour E2B : le SDK `openai` réel a été installé et inspecté (constructeur, signature de `chat.completions.create`, forme de la réponse) avant d'écrire le code contre son API. Le round-trip réel n'a pas pu être testé depuis cet environnement (aucun accès réseau à DashScope ni à un Ollama local), mais la construction du client et le dispatch de `complete()` sont testés avec un faux SDK qui vérifie les vrais noms de paramètres et la vraie forme de réponse (`tests/test_llm_client.py`), pas seulement "quelque chose est appelé".

**Un bug sévère trouvé en testant le chargement réel d'un `.env`, jamais déclenché avant** : `Settings` n'avait pas `extra = "ignore"`, donc la moindre variable non déclarée dans `.env` faisait planter tout le backend au démarrage (`extra_forbidden`) — y compris `NEXT_PUBLIC_API_URL`, qui est documentée dans le **même** `.env.example` que les variables backend ! Et même sans ce crash, `ANTHROPIC_API_KEY`/`E2B_API_KEY` posées dans `.env` (plutôt qu'exportées dans le shell) n'étaient jamais lues par le mécanisme de repli — la doc promettait un comportement qui ne marchait pas. Corrigé par `extra = "ignore"` + un appel explicite à `load_dotenv()` avant l'instanciation de `Settings`. Trois tests permanents dans `tests/test_settings_env_loading.py`.

52 tests passent au total.

**Correction post-livraison** : les exemples menaient avec Alibaba Cloud DashScope, mais l'utilisation réelle se fait via **OpenRouter** (`https://openrouter.ai/api/v1`, clé au format `sk-or-v1-...`, slugs de modèle du type `qwen/qwen3.5-7b-instruct` — à vérifier sur openrouter.ai/models, la liste évolue). Aucun changement de code nécessaire : le provider `openai_compatible` était déjà générique, seul `.env.example` a été réordonné pour mettre OpenRouter en premier. Test dédié ajouté (`test_openrouter_configuration_dispatches_correctly`) pour figer cette configuration précise. 53 tests passent désormais.

## v0.4.4 — CSV, profondeur statistique réelle, et choix des livrables

Déclenché par un cas d'usage réel soumis par l'utilisateur : un fichier `agri_data.csv` (500 ménages, groupe traité/contrôle, revenu avant/après) accompagné d'une mission d'évaluation d'impact complète (nettoyage, modélisation économétrique DiD/PSM, synthèse qualitative, note de synthèse).

### Bug bloquant : seul l'Excel était supporté

`DataAnalysisAgent` appelait `pd.ExcelFile()` sans condition — un CSV (le format le plus courant en pratique) faisait échouer la tâche avec "Impossible de lire le classeur Excel". Corrigé en profondeur, pas en surface :
- `DataAnalysisAgent._load_sources()` (nouvelle méthode statique) gère CSV et Excel de façon uniforme, et supporte désormais **plusieurs fichiers uploadés ensemble** (ex. `data.csv` + `dictionnaire.csv` séparés, reconnu par une colonne "variable" ; un `.txt`/`.md` uploadé à côté devient des notes de terrain).
- Le générateur de script sandbox (`sandbox_scripts.py`) a été mis à jour pour rester rigoureusement identique au chemin en-processus (extraction via `inspect.getsource`, comme pour toute la logique de nettoyage) — testé en exécutant le script généré dans un sous-processus isolé sur le fichier réel.

### Profondeur statistique réelle (pas seulement des règles de dictionnaire)

Nouvelle passe d'enrichissement (`DataAnalysisAgent._enrich`), déclenchée par l'étape "analyse" quand elle suit une étape "nettoyage" :
- **Imputation des valeurs manquantes** par moyenne de groupe (quand une variable de regroupement pertinente existe) ou moyenne globale, avec traçabilité complète (ligne, variable, valeur imputée, méthode).
- **Traitement des valeurs aberrantes** par la méthode IQR (plafonnement aux bornes à 1,5×IQR), en excluant automatiquement les colonnes quasi-binaires (indicateurs de traitement, codes 0/1) pour ne pas corrompre leur sens catégoriel.
- **Détection et création automatique de variables dérivées** : toute paire de colonnes `<préfixe>_pre`/`<préfixe>_post` génère une variable binaire `<préfixe>_impact_score`.
- **Estimation réelle d'un effet de traitement** (`DataAnalysisAgent._estimate_treatment_effect`) : différence-en-différences par régression OLS (`statsmodels`) quand une colonne de traitement binaire (`beneficiaire`, `traite`, etc.) et une paire avant/après sont détectées — repli sur un test t de Welch (`scipy`) si `statsmodels` est absent. **Chiffres réellement calculés à partir des données uploadées, jamais les valeurs illustratives d'un énoncé.** Validé sur un jeu de données synthétique à effet connu à l'avance (+1000, bruit gaussien) : l'ATE estimé retombe dans la marge de tolérance attendue (`tests/test_data_enrichment.py`).
- `DocumentAgent` intègre désormais une section "Modélisation économétrique" (méthode, tableau de résultats, interprétation en français, mises en garde méthodologiques) avec un **graphique réellement généré** (matplotlib, barres groupées avant/après par groupe) embarqué dans le docx — et une section "Analyse qualitative croisée" quand un document qualitatif est uploadé à côté des données (synthèse par IA si configurée, matière brute reprise sinon, jamais fabriquée sans source réelle).

### "Tous les livrables ne sont pas nécessaires" — le choix du format de sortie

Constat de l'utilisateur : l'infrastructure produisait toujours un lot générique et fixe de livrables (docx + pptx pour l'analyse de données) sans jamais demander ce qui était réellement voulu — et ce même quand les agents faisaient un vrai travail d'analyse approfondi. Corrigé :
- Nouveau champ `Task.desired_deliverables` (liste parmi `rapport_docx`, `presentation_pptx`, `donnees_nettoyees`), avec **migration sûre** : `init_db()` détecte et ajoute les colonnes manquantes sur une base SQLite existante via `ALTER TABLE`, sans jamais perdre de données (`Base.metadata.create_all()` ne fait que créer les tables manquantes, jamais les modifier — testé avec une vraie base "ancienne forme" contenant une ligne réelle, qui survit intacte).
- `TaskService._prune_plan_for_deliverables` élague le plan selon le choix : pas d'étape "presentation" si le pptx n'est pas voulu, pas d'étape "redaction" si le docx n'est pas voulu — avec un garde-fou qui ne laisse jamais un plan incapable de produire quoi que ce soit (ex. demander uniquement les données nettoyées sur un type de tâche qui n'a jamais d'étape de nettoyage garde la rédaction, faute de mieux). Un bug de conception trouvé et corrigé en testant : demander "présentation seule" gardait quand même l'étape de rédaction (travail gâché, le docx produit n'étant jamais marqué livrable).
- Si la question n'est jamais posée (`desired_deliverables` reste `None`), le comportement d'aujourd'hui est inchangé à l'identique — le choix est une amélioration additive, pas une rupture.
- Endpoints `GET /tasks/{id}/default-deliverables` (choix disponibles + défauts sensés selon le type) et `POST /tasks/{id}/deliverables`, section dédiée dans l'interface avant la génération du plan.

75 tests passent au total, validés en installation fraîche depuis `requirements.txt`.

## v0.4.5 — présentation adaptative, migration Postgres réelle, et le vrai trou d'usage du LLM

### `PresentationAgent` cessait d'être générique

Resté figé à 2-3 diapositives fixes pendant que `DocumentAgent` gagnait en profondeur (imputation, effet de traitement, synthèse qualitative). Réécrit entièrement : nombre et contenu des diapositives dépendent maintenant de ce qui est réellement disponible — méthodologie, graphique économétrique (le même PNG que le docx, extrait en fonction de module partagée pour garantir l'identité), traitement statistique, croisement qualitatif, conformité (réponse à AO), recommandations. Plus de diapositive "qualité des données" redondante quand un effet de traitement existe déjà (évite la répétition). `_load_json`, `_extract_raw_material` et `_render_treatment_effect_chart` remontés au niveau du module pour être partagés sans duplication entre `DocumentAgent` et `PresentationAgent`.

### Migration automatique : le trou Postgres

`init_db()`'s garde-fou de réconciliation de colonnes ne tournait que sur SQLite — je pensais Alembic prendrait le relais sur Postgres, mais aucune migration Alembic réelle n'existe dans ce projet. Un déploiement Docker Compose (Postgres) a heurté exactement le crash que ce mécanisme est censé prévenir dès l'ajout de `Task.desired_deliverables`. Corrigé et **testé contre une vraie instance Postgres locale** (installée dans cet environnement pour l'occasion) : reproduction exacte du bug avec une vraie ligne insérée via l'ORM, puis validation que la migration l'ajoute sans perte de données. Deux tests dédiés (`tests/test_postgres_migration.py`), ignorés proprement si aucun serveur Postgres n'est joignable.

### Le vrai trou : le LLM à peine utilisé pendant l'exécution réelle

Un diagnostic externe (fourni par l'utilisateur à titre indicatif, ses recommandations de restructuration n'ont **pas** été suivies) pointait un problème réel : même avec Qwen configuré, le pipeline d'exécution ne l'utilisait presque pas — seulement pour les recommandations et la rédaction de contenu, jamais pour interpréter les résultats déjà calculés d'une analyse de données. Le "Résumé exécutif" restait un gabarit figé quel que soit le volume réel d'analyse effectué (500 observations, imputation, effet de traitement significatif → toujours le même paragraphe de 195 mots).

Vérifié sur le code réel avant d'agir : le diagnostic était exact pour cette section précise, mais la proposition de restructuration complète du pipeline (un `InterpretationAgent` séparé) n'a pas été retenue — elle contredit d'ailleurs le principe déjà établi ici et dans `docs/PRAXIS_V1_ARCHITECTURE.md` : le calcul reste toujours en Python (pandas/scipy/statsmodels), jamais délégué au LLM. La correction reste dans le même registre que ce qui existe déjà (`draft_recommendations`, `draft_document_body`) : `llm_assist.draft_executive_summary` rédige l'interprétation à partir des chiffres **déjà calculés** (passés explicitement dans le prompt, jamais recalculés ni inventés), avec repli honnête sur le gabarit fixe existant si aucune IA n'est configurée. Branché à la fois dans `DocumentAgent` (Résumé exécutif) et `PresentationAgent` (diapositive "Résultats clés"). Testé : preuve que le contenu change réellement selon la configuration (avec IA simulée vs sans), et que rien n'est jamais inventé sans les faits fournis.

82 tests passent au total.

## Phase 4 de `docs/PRAXIS_V1_ARCHITECTURE.md` — routage de domaine (terminée)

Le document d'architecture v1.0 (ajouté dans `docs/`) tranche lui-même de commencer par cette phase seule, avant tout agent métier — respecté. **Livré et testé rigoureusement**, pas juste esquissé :

- Modèles `ExpertiseDomain` (11 pôles livrables P2-P12, capacités déclarées, bibliothèques) et `UserStyleProfile` (vide, Phase 8) dans `src/models/__init__.py`.
- `src/services/domain_router.py` : classification multi-pôles par LLM (`{"poles": [...], "type_livrable": ..., "confiance": ...}`, validée contre le vocabulaire connu — tout code de pôle halluciné est filtré, exactement comme pour `llm_assist.generate_plan_steps`), avec repli déterministe basé sur le `TaskType` existant quand aucune IA n'est configurée (confiance 1.0 — ce n'est pas une supposition, c'est le mapping exact de ce que l'ancien système faisait déjà). Sous le seuil de confiance (`DOMAIN_ROUTER_CONFIDENCE_THRESHOLD`, 0.6 par défaut), rien n'est stocké — pas de plan généré à l'aveugle (§4 point 3).
- `Orchestrator.assemble_plan_from_poles` : compose dynamiquement un plan à partir des pôles convoqués, ordre fixe Collecte/Qualité → analytique → Rédacteur (§4 point 4), avec déduplication réelle (P3 Économètre et P5 Data/Analyste convergent tous deux vers l'étape "analyse" existante — un seul step, pas deux).
- `PoleNotImplementedAgent` : pour les pôles routés mais pas encore construits (P2, P6, P7, P8, P9, P10 — Phase 5+), rapporte honnêtement dans le journal d'exécution plutôt que de produire un silence ou un plantage — même principe que `ResearchAgent` avant sa construction en Phase 3.
- `Task.assigned_poles` (JSON, nullable — `None` = routage jamais invoqué, comportement d'avant Phase 4 strictement inchangé). `TaskService.route_domain_for_task` + `propose_plan` modifié pour composer depuis les pôles quand ils sont assignés.
- Endpoints `POST /tasks/{id}/route-domain`, `GET /expertise-domains`. Section dédiée dans l'interface, avant génération du plan.
- **Testé de bout en bout sur le cas d'exemple exact du document** (évaluation d'impact → P3+P9+P12) : le routage fonctionne, l'étape "analyse" tourne réellement (l'estimation économétrique déjà construite en v0.4.4), P9 rapporte honnêtement son absence, le rapport final passe le contrôle qualité. Migration de la nouvelle colonne `assigned_poles` vérifiée contre une vraie base Postgres existante (même méthode que pour `desired_deliverables`).
- 13 tests dédiés (`tests/test_domain_router.py`) : seed idempotent, repli déterministe, filtrage des pôles hallucinés, seuil de confiance, déduplication du plan, agent-relais honnête, non-régression du comportement quand le routage n'est jamais invoqué.

### Une divergence mineure avec le document, signalée plutôt que corrigée en silence

Le §4 dit "12 pôles livrables (P2 à P12)" — P2 à P12 inclus, c'est 11 pôles, pas 12 (le tableau du §2 est sans ambiguïté et fait foi ; la prose du §4 semble avoir un décalage d'un). Implémenté avec 11, noté dans le code (`domain_router.py`) plutôt que "corrigé" sans le dire.

### Ce qui n'est PAS fait, et pourquoi ce n'est pas dans cette livraison

Le document précise lui-même (§17e) qu'un agent de pôle correctement construit représente **plusieurs semaines de travail effectif**, avec référentiel méthodologique codé et passage par l'auto-critique comme critère de complétude (§8) — pas quelque chose à expédier en une session sans compromettre la rigueur de test déjà appliquée à tout le reste de ce projet. Restent donc non commencés : les Phases 5 à 9 — les agents de pôle eux-mêmes (Planificateur P8 en premier, comme le document le recommande), l'activation de l'apprentissage de style, la mémoire sémantique, le registre de gabarits, les référentiels méthodologiques par discipline, et la matrice des formats de livrables. Le prochain pas raisonnable, tel que suggéré par le document : construire l'agent Planificateur (P8) seul, avec son référentiel, avant d'enchaîner sur les autres pôles.

95 tests passent au total (13 dédiés à cette phase), validés en installation fraîche avec une vraie instance Postgres.

## v0.4.6 — bug de production réel : course entre workers gunicorn au démarrage

Signalé par l'utilisateur avec les logs réels d'un déploiement Docker Compose (Postgres, 4 workers gunicorn). Diagnostiqué en profondeur plutôt qu'en surface — deux bugs distincts, un correctif chacun.

### Le bug racine : `create_all()` n'est pas sûr en multi-processus sur Postgres

`docker/Dockerfile.backend` lance gunicorn avec `-w 4` — quatre **processus** workers, chacun appelant `init_db()` indépendamment au démarrage. Sur une base réellement fraîche, `Base.metadata.create_all()` crée des **types ENUM natifs Postgres** (un par colonne `Enum`, ex. `TaskStatus`) via `CREATE TYPE`, sans garde atomique équivalent à `IF NOT EXISTS` sur les versions de SQLAlchemy utilisées ici. Quatre workers qui démarrent quasi simultanément se disputent la création du même type — et `create_all()` plante entièrement pour les perdants de la course.

**Reproduit avant de corriger, pas supposé** : quatre vrais processus lancés en parallèle contre une vraie instance Postgres locale (installée dans cet environnement pour l'occasion) sur une base neuve — confirmation exacte du symptôme. Corrigé par un **verrou consultatif Postgres** (`pg_advisory_lock`, le mécanisme standard pour ce problème) autour de tout `init_db()` : le worker qui l'obtient exécute la migration au complet pendant que les autres attendent, puis chacun des autres constate qu'il n'y a plus rien à faire. Testé avec de vrais threads concurrents, chacun ouvrant sa propre connexion (une vraie session Postgres distincte, équivalent fonctionnel de plusieurs processus gunicorn) — `tests/test_postgres_migration.py::test_init_db_survives_concurrent_workers_on_a_fresh_database`.

### Le symptôme rapporté : la course sur `_add_missing_columns`

Une fois le bug racine corrigé par le verrou, le symptôme exact des logs d'Elie (`column "id" of relation "expertise_domains" already exists`) ne se produit plus — mais corrigé aussi en profondeur au cas où le verrou ne suffise pas dans un scénario non anticipé : `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` (Postgres 9.6+, atomique) au lieu du couple vérification-puis-ajout qui laissait une fenêtre de course ; et le message de log distingue maintenant "déjà ajoutée par un worker concurrent" (normal, niveau info) d'un vrai échec (niveau warning). Testé avec le scénario exact reproduit : base existante avec une vraie ligne, colonne manquante, quatre workers concurrents qui doivent tous l'ajouter (`test_init_db_survives_concurrent_workers_adding_a_new_column`).

### Le fichier `.env` était ignoré par Docker Compose

Signalé par l'utilisateur en même temps : même en créant `.env` avec ses clés LLM/E2B, `docker-compose.yml` ne le chargeait jamais dans le conteneur backend — seules quatre variables codées en dur (`DATABASE_URL`, `SECRET_KEY`, `ENVIRONMENT`, `CORS_ORIGINS`) existaient. Toute la configuration Phase 3 (LLM, sandbox) documentée dans `.env.example` ne fonctionnait donc jamais sous Docker Compose. Corrigé avec `env_file: .env` sur le service `backend` — les quatre variables codées en dur restent prioritaires (Docker Compose fait toujours gagner `environment:` sur `env_file:` en cas de conflit), donc `DATABASE_URL` continue de pointer vers le service `db` interne quoi que contienne le `.env` local. `cp .env.example .env` est maintenant documenté comme étape obligatoire dans le README — Docker Compose échoue clairement si `.env` est absent, ce qui est préférable à l'ancien silence.

97 tests passent au total, stabilité de la concurrence vérifiée sur 3 exécutions successives.
