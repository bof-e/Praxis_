# Praxis v1.0 — De l'atelier générique à l'infrastructure d'agents experts

Ce document répond à un problème précis : Praxis v0.4 sait nettoyer des données et produire un docx/pptx générique, mais il ne sait pas encore **qui tu es professionnellement**. Il traite toute demande avec les mêmes réflexes (nettoyage → rapport), alors que tes compétences couvrent 31 domaines très différents (économétrie, démographie, SIG, suivi-évaluation, cadre logique...) qui n'ont chacun ni les mêmes calculs, ni les mêmes livrables, ni les mêmes conventions de présentation.

Ce document tranche : il ne te donne pas d'options, il te donne l'architecture retenue.

---

## 1. Principe directeur (non négociable)

**Séparation stricte entre la couche métier et la couche rédaction.**

- La couche métier (un agent par pôle) calcule des résultats réels — statistiques, économétrie, indicateurs démographiques, cadre logique — jamais générés par un LLM. Un LLM ne fait pas de statistique, il **interprète des résultats déjà calculés**.
- La couche rédaction (un seul agent, `RedacteurAgent`) assemble ces résultats en document, avec ton style appris. Elle ne recalcule jamais rien.
- Le routeur de domaine décide, à partir du prompt, quels pôles métier sont convoqués — un même prompt peut en convoquer plusieurs (ex. "évalue l'impact de ce projet" = Économètre + Suivi-Évaluation + Rédacteur).

C'est la règle qui empêche le système de redevenir générique : chaque pôle *sait* ce qu'un professionnel de son domaine ferait, et le vérifie avant de livrer.

Concrètement, "savoir ce qu'un professionnel ferait" n'est pas laissé à l'appréciation du LLM au moment de la génération — c'est codifié à l'avance, pôle par pôle, à partir de référentiels qui existent déjà et font autorité dans chaque discipline. La Partie II (§11 à §15) précise ce point : c'est la partie qui empêche concrètement le système de rester générique.

---

## 2. Cartographie complète : tes 31 catégories → 14 pôles

Aucune catégorie de ton parcours n'est laissée de côté. Certaines deviennent des agents livrables, d'autres deviennent l'infrastructure elle-même (P1, P13, P14 ne produisent pas de document — ils font tourner le système).

| Pôle | Nom | Catégories couvertes (n° de ta liste) | Nature | Fichier |
|---|---|---|---|---|
| P1 | Fondations quantitatives | 1 Mathématiques, 2 Probabilités | Librairie partagée, pas un agent | `core_math.py` |
| P2 | Statisticien | 3 Statistique, 7 Analyse multivariée | Agent livrable | `statisticien_agent.py` |
| P3 | Économètre | 4 Économétrie, 16 Évaluation d'impact (méthodo) | Agent livrable | `econometre_agent.py` |
| P4 | Collecte & Qualité des données | 5 Collecte et méthodes d'enquête, 29 Gestion de la qualité des données | Agent livrable | `collecte_agent.py` |
| P5 | Data/Analyste technique | 6 Analyse de données, 8 Outils statistiques et informatiques | Extension du `DataAgent` existant | `data_agent.py` |
| P6 | Démographe | 9 Démographie | Agent livrable (nouveau) | `demographe_agent.py` |
| P7 | Économiste & Finances publiques | 10 Économie, 11 Comptabilité nationale, 21 Finance publique et gestion publique | Agent livrable (nouveau) | `economiste_agent.py` |
| P8 | Planificateur & Gestion de projet | 12 Planification, 13 Gestion de projets, 22 Analyse institutionnelle et organisationnelle | Agent livrable — **priorité 1** | `planificateur_agent.py` |
| P9 | Suivi-Évaluation & Politiques publiques | 14 Suivi-évaluation, 15 Évaluation des politiques publiques, 17 Gestion axée sur les résultats, 16 Évaluation d'impact (reporting) | Agent livrable — **priorité 1** | `suivi_evaluation_agent.py` |
| P10 | Géographe/SIG | 18 SIG et analyse spatiale | Agent livrable (nouveau) | `sig_agent.py` |
| P11 | Chercheur & Sciences sociales | 19 Recherche scientifique, 20 Sciences sociales, 26 Gestion de l'information | Extension du `ResearchAgent` existant | `research_agent.py` |
| P12 | Rédacteur & Communication | 23 Communication professionnelle, 24 Rédaction et production documentaire, 25 Anglais professionnel | Extension du `DocumentAgent` existant | `document_agent.py` |
| P13 | Plateforme/IA (méta-infra) | 27 Compétences numériques, 28 Intelligence artificielle appliquée, 30 Gestion de projets numériques | N'est pas un agent — c'est Praxis lui-même | — |
| P14 | Méta-cognition transversale | 31 Compétences transversales | Gouverne l'orchestrateur et l'apprentissage | `orchestrator.py` / `learning_service.py` |

*Chemins complets (dossier `src/agents/` ou `src/services/`) dans l'arborescence de la section 3.*

**Lecture clé de P14** : ta catégorie 31 (« transformer une problématique en plan d'action », « des données en informations », « une demande en livrable ») n'est pas une compétence-métier à outiller — c'est littéralement la définition algorithmique de ce que ton `orchestrator.py` doit faire. C'est le cahier des charges de la boucle principale, pas un agent de plus.

---

## 3. Arborescence cible (additions au code existant)

```
src/
  services/
    core_math.py                 # NOUVEAU - primitives partagées (P1)
    domain_router.py             # NOUVEAU - classification multi-pôle du prompt
    style_learning_service.py    # NOUVEAU - apprentissage de ton style (§5)
    template_registry.py         # NOUVEAU - templates docx/pptx par pôle × type de livrable
    learning_service.py          # EXISTANT - étendu pour stocker les corrections
    orchestrator.py              # EXISTANT - étendu : composition dynamique de pôles
    readiness_engine.py          # EXISTANT - étendu : checks spécifiques par pôle
    validation_service.py        # EXISTANT - étendu : contrôle qualité par pôle
  agents/
    statisticien_agent.py        # NOUVEAU (P2)
    econometre_agent.py          # NOUVEAU (P3)
    collecte_agent.py            # NOUVEAU (P4)
    demographe_agent.py          # NOUVEAU (P6)
    economiste_agent.py          # NOUVEAU (P7)
    planificateur_agent.py       # NOUVEAU (P8) — priorité 1
    suivi_evaluation_agent.py    # NOUVEAU (P9) — priorité 1
    sig_agent.py                 # NOUVEAU (P10)
    data_agent.py                # EXISTANT - étendu (P5)
    research_agent.py            # EXISTANT - étendu (P11)
    document_agent.py            # EXISTANT - étendu (P12, bilingue)
  models/
    expertise_domain.py          # NOUVEAU - table des pôles + capacités
    user_style_profile.py        # NOUVEAU - ton profil de style par pôle
    exemplaire_valide.py         # NOUVEAU - bibliothèque de tes livrables corrigés
    deliverable_template.py      # NOUVEAU - structures de documents par pôle
```

---

## 4. Le routeur de domaine — comment un prompt devient un plan multi-pôle

Aujourd'hui, `task_service.py` classe la demande en 1 des 7 types fixes. Ce n'est plus suffisant dès qu'une demande touche plusieurs domaines à la fois — ce qui est ton quotidien (une évaluation d'impact convoque économétrie + suivi-évaluation + rédaction).

**Nouveau fonctionnement (`domain_router.py`) :**

1. Le LLM configuré (Anthropic ou Qwen, comme déjà en place) reçoit le prompt et **une liste fermée des 12 pôles livrables** (P2 à P12, hors les 3 pôles infrastructure), avec leurs capacités déclarées dans `expertise_domain.py`.
2. Il retourne une structure JSON stricte : `{"poles": ["P8","P9","P12"], "type_livrable": "note_de_politique_publique", "confiance": 0.87}`.
3. Si confiance < seuil (0.6 par défaut) → question de clarification posée à l'utilisateur (pas de plan généré à l'aveugle).
4. Le plan est ensuite **assemblé dynamiquement** en concaténant les étapes déclarées par chaque pôle convoqué, dans un ordre fixe : Collecte/Qualité → pôle(s) analytique(s) → Rédacteur. Ce n'est plus un plan figé par type de tâche, c'est une composition.

C'est le changement le plus important de cette refonte : **le type de tâche n'est plus un enum fixe, c'est une combinaison de pôles**.

---

## 5. La boucle d'apprentissage de ton style (sans fine-tuning)

Tu ne peux pas fine-tuner Claude ou Qwen via API — et ce n'est de toute façon pas le bon outil ici. Le mécanisme retenu est **few-shot piloté par un profil de style + une bibliothèque d'exemplaires validés**, alimentée automatiquement à chaque livraison.

**Mécanisme concret :**

1. **Table `exemplaire_valide`** : après chaque livraison, tu corriges ou valides le document. La version finale (post-corrections) est stockée, indexée par `(pôle, type_livrable)`.
2. **Table `user_style_profile`** : `style_learning_service.py` analyse le diff entre la version générée et ta version corrigée, et met à jour un profil structuré par pôle — exemples de champs concrets :
   - Méthode préférée quand plusieurs sont possibles (ex. « toujours IQR pour les aberrantes, jamais z-score », « toujours DID si panel dispo, sinon avant-après »)
   - Structure de document (ordre des sections que tu gardes systématiquement, celles que tu supprimes systématiquement)
   - Niveau de technicité du texte (tu simplifies souvent le jargon économétrique dans le corps, tu le gardes en annexe — ce genre de pattern est détectable et stockable)
   - Ton et longueur des résumés exécutifs
3. **Au moment de la génération** : le prompt envoyé au LLM inclut systématiquement (a) le profil de style du pôle concerné, (b) les 2-3 exemplaires validés les plus proches du type de livrable demandé, comme few-shot. Pas de mémoire vague — des exemples concrets de *toi* faisant ce travail.
4. Ce mécanisme s'améliore mécaniquement avec l'usage : plus tu utilises Praxis, plus la bibliothèque d'exemplaires est fournie, plus les livrables ressemblent à ce que tu aurais produit toi-même.

Décision tranchée : ce système démarre **vide** et n'a de valeur qu'après un historique réel — ce n'est donc pas la première brique à construire (voir feuille de route, Phase 8).

**Règle de frontière, non négociable** : le profil de style ne gouverne jamais que la forme (structure, ton, longueur, phrasé). Il ne peut jamais assouplir une exigence méthodologique du référentiel d'un pôle (§11). Exemple concret : si tu ne relèves jamais l'absence de test de tendances parallèles dans tes anciens rapports DiD, le système n'a pas le droit d'apprendre à l'omettre — ce test reste obligatoire parce qu'il est dans le référentiel, pas dans le profil de style. Le détail de cette séparation à trois niveaux est en §15.

---

## 6. Pipeline complet, du prompt au livrable

```
1. Prompt libre (+ fichiers joints, + reprise de contexte d'une conversation précédente)
        ↓
2. domain_router.py → liste des pôles convoqués + type de livrable + score de confiance
        ↓  (si confiance basse → clarification à l'utilisateur, pas de plan à l'aveugle)
3. readiness_engine.py → checks spécifiques par pôle convoqué
   (ex. Économètre : "variable de traitement identifiée ?" / Démographe : "structure par âge dispo ?")
        ↓
4. Génération du plan : assemblage dynamique des étapes déclarées par chaque pôle,
   y compris le choix du format et du gabarit de sortie par livrable (grille §16, défaut modifiable par toi)
        ↓
5. Validation du plan (auto si confiance haute, sinon confirmation utilisateur)
        ↓
6. Exécution réelle par chaque agent de pôle (sandbox E2B si configuré) — calculs réels, jamais fabriqués
        ↓
6bis. Auto-critique de l'agent contre son référentiel (§11 et §12) — checklist du pôle,
   cascade de repli documentée si une méthode idéale n'est pas applicable (§13)
        ↓
7. RedacteurAgent (P12) assemble les sorties de tous les pôles dans le document final,
   injecte le profil de style + les exemplaires validés (avec rotation anti-répétition, §14) du pôle dominant
        ↓
8. validation_service.py → contrôle qualité spécifique par pôle, contre le même référentiel qu'en 6bis
   (ex. "chaque test statistique a sa p-value rapportée", "le cadre logique a ses 4 niveaux complets")
        ↓
9. Livraison + traçabilité (comme en Phase 2 actuelle)
        ↓
10. Capture de ta correction/validation → mise à jour du profil de style + bibliothèque d'exemplaires
        ↓ (boucle fermée vers l'étape 7 pour la prochaine demande similaire)
```

---

## 7. Nouveaux modèles de données (esquisse)

```python
# expertise_domain.py
class ExpertiseDomain(Base):
    code: str            # "P8", "P9", ...
    nom: str
    categories_source: list[int]   # ex. [12, 13, 22] — traçabilité vers ta liste de compétences
    capacites: JSON       # liste des étapes/calculs que ce pôle sait produire
    librairies: list[str] # ex. ["statsmodels", "scipy"] pour P3

# user_style_profile.py
class UserStyleProfile(Base):
    pole_code: str
    methode_preferee: JSON     # {"outliers": "IQR", "impact": "DID_si_panel_sinon_avant_apres"}
    structure_document: JSON   # ordre de sections observé, sections systématiquement retirées
    ton_redaction: str
    derniere_maj: datetime

# exemplaire_valide.py
class ExemplaireValide(Base):
    pole_code: str
    type_livrable: str
    contenu_final: text        # version post-corrections, utilisée en few-shot
    task_id: FK -> Task
    date: datetime

# deliverable_template.py
class DeliverableTemplate(Base):
    pole_code: str
    type_livrable: str
    formats_disponibles: list[str]   # ex. ["docx", "xlsx"] pour un cadre logique
    format_par_defaut: str
    gabarit_visuel: str               # référence vers un template concret dans template_registry.py
```

---

## 8. Feuille de route — phases tranchées

Je ne te donne pas un ordre "logique dans l'absolu" — je tranche selon ton métier réel : ta formation s'intitule *Planification et Suivi-Évaluation*, c'est ton cœur de compétence, donc c'est la priorité, pas une nouveauté périphérique comme le SIG.

| Phase | Contenu | Pourquoi cet ordre |
|---|---|---|
| **Phase 4** | Plomberie : `domain_router.py`, modèles `expertise_domain` + `user_style_profile`, `orchestrator.py` étendu pour composition dynamique de pôles | Sans ça, aucun agent spécialisé n'a de moyen d'être convoqué correctement — c'est un prérequis, pas une option |
| **Phase 5** | **P8 Planificateur** (générateur de cadre logique, théorie du changement, chronogramme) + **P9 Suivi-Évaluation** (dictionnaire d'indicateurs SMART, rapports d'évaluation à mi-parcours/finale) | Ton cœur de métier déclaré. C'est ici que la différence avec un outil générique se sent immédiatement |
| **Phase 6** | Enrichissement **P2 Statisticien** (ACP/AFC/CAH) et **P3 Économètre** (matching, variables instrumentales, régression discontinue — la DID existe déjà en v0.4) | Ta boîte à outils analytique quotidienne, déjà partiellement câblée |
| **Phase 7** | **P6 Démographe** et **P10 SIG** (nouveaux agents, nouvelles libs : `geopandas`, `folium`) | Utiles mais plus ponctuels dans ton usage que P8/P9/P2/P3 |
| **Phase 8** | **P11 Chercheur** enrichi (revue de littérature structurée) + **P12 Rédacteur bilingue** (anglais professionnel) + activation complète de la **boucle d'apprentissage de style** (§5) | La boucle de style n'a de valeur qu'avec un historique réel — inutile de la construire avant d'avoir des livrables Phase 5/6 à apprendre |
| **Phase 9** | Recherche web réelle (déjà notée manquante dans ton README) + remplacement du TF-IDF par des embeddings sémantiques | Amélioration de fond, non bloquante pour les phases précédentes |

**Précision importante sur cette feuille de route** : le référentiel méthodologique et la boucle critique (§11-§12) ne sont **pas une phase à part** — ce serait une erreur de les reporter après coup. Ils font partie de la définition-of-done de chaque agent : un agent de pôle livré sans son référentiel codé et sans passage par l'auto-critique n'est pas considéré comme terminé, même s'il produit un document qui a l'air correct. Un `planificateur_agent.py` qui génère un cadre logique sans la colonne "hypothèses/risques" n'est pas une version 1 simplifiée du bon agent — c'est un agent non conforme à livrer en Phase 5.

---

## 9. Ce qui est explicitement tranché (pas d'agent, pas de fonctionnalité)

- **Pas de fine-tuning de modèle.** Le few-shot + profil de style (§5) fait le travail à moindre coût et reste modifiable par toi à tout moment.
- **Pas de 31 agents.** Les compétences transverses (maths, probas) et les compétences d'infrastructure (numérique, IA appliquée, gestion de projet numérique) ne produisent pas de livrable — elles font tourner le système. Un agent = un ou plusieurs pôles avec un livrable identifiable.
- **Pas d'authentification multi-utilisateur** dans cette refonte — Praxis reste un outil personnel, cohérent avec le README actuel.
- **La recherche web reste hors scope avant la Phase 9** — déjà assumé comme non fait dans ta v0.4, pas de raison de le précipiter.

---

## 10. Prochaine étape concrète

Démarrer la Phase 4 seule, avant tout agent métier : `domain_router.py` + les deux modèles (`expertise_domain`, `user_style_profile`) + l'extension de `orchestrator.py` pour accepter une liste de pôles plutôt qu'un type de tâche unique. Une fois cette plomberie posée, le premier agent à construire dessus est le **Planificateur (P8)**, avec comme premier livrable concret un générateur de cadre logique — c'est le point d'entrée le plus représentatif de ton métier et le plus facile à valider toi-même (tu sais immédiatement si un cadre logique généré est correct ou non).

---

# Partie II — Affinement qualité : référentiels, anti-généricité, anticipation de la complexité

Cette partie répond à un problème précis : un agent qui "sait faire une régression" ou "sait faire un cadre logique" en général reste générique. Ce qui rend un livrable non générique, c'est qu'il respecte les conventions qu'un professionnel sérieux de ce domaine précis appliquerait sans même y penser — et qu'il documente honnêtement quand il doit s'en écarter. Rien ci-dessous n'est inventé pour ce projet : chaque référentiel cité existe déjà, indépendamment de Praxis, et fait autorité dans sa discipline.

## 11. Référentiels méthodologiques reconnus, par pôle — le garde-fou anti-générique

Chaque agent de pôle embarque un référentiel figé dans `expertise_domain.py` (champ `checklist_qualite`), consulté à l'étape 6bis du pipeline (§6) et re-vérifié à l'étape 8. Ce référentiel n'est **pas** ce que le LLM "pense" être une bonne pratique au moment de la génération — c'est une checklist déterministe, écrite une fois, appliquée à chaque exécution.

| Pôle | Référentiel existant mobilisé | Ce qu'il impose concrètement (non négociable) |
|---|---|---|
| P2 Statisticien | Conventions de reporting statistique standard (façon stargazer/esttab) ; école française d'analyse des données (Lebart, Escofier-Pagès) pour l'analyse multivariée | Jamais une moyenne seule sans écart-type/IC ; toute ACP/AFC/CAH rapporte systématiquement le % de variance par axe, les contributions et les cos² |
| P3 Économètre | Angrist & Pischke (*Mostly Harmless Econometrics*) pour les standards d'identification causale ; règle de Stock-Yogo | Coefficients toujours accompagnés d'erreurs-types robustes et de N ; **DiD** → test de tendances parallèles obligatoire avant toute interprétation causale ; **VI** → F du premier stade > 10 sinon avertissement "instrument faible" ; **RDD** → test de manipulation de McCrary + sensibilité à la largeur de bande ; **appariement** → test d'équilibre (différences standardisées < 0,1, Rosenbaum & Rubin) |
| P4 Collecte & Qualité | Formule de Cochran avec correction population finie (standard en méthodologie d'enquête) ; dimensions qualité type DAMA-DMBOK/ISO 8000 | Taille d'échantillon jamais estimée "au jugé" ; journal d'anomalies couvrant systématiquement exactitude, complétude, cohérence, actualité — pas seulement doublons/aberrantes |
| P6 Démographe | Manuel X des Nations Unies, méthodes de Brass (estimation indirecte en contexte de données d'état civil incomplètes) | Recours systématique aux méthodes indirectes quand l'état civil est lacunaire, avec marge d'erreur explicitée ; tables de mortalité au format standard (lx, dx, qx, ex) |
| P7 Économiste | Système de Comptabilité Nationale 2008 (SCN 2008, ONU) ; classification fonctionnelle des dépenses COFOG | Tout agrégat macro présenté selon la nomenclature SCN 2008 ; toute table budgétaire classée selon COFOG, jamais une nomenclature maison |
| P8 Planificateur | Matrice de cadre logique officielle EuropeAid/Banque mondiale (4x4) ; structure standard de théorie du changement (USAID/UNDP) | Cadre logique toujours à 4 colonnes (logique d'intervention × IOV × sources de vérification × hypothèses/risques) — jamais 3 colonnes qui oublient les risques ; théorie du changement présentée comme diagramme causal avec hypothèses explicites à chaque flèche, pas une simple liste |
| P9 Suivi-Évaluation | Critères OCDE-CAD 2019 (pertinence, cohérence, efficacité, efficience, impact, durabilité) — exactement ta catégorie 15 | Tout rapport d'évaluation structuré autour de ces 6 critères, jamais un plan libre ; tout indicateur listé avec ses 4 attributs obligatoires (valeur de référence, cible, source de vérification, fréquence) |
| P10 SIG | Conventions cartographiques professionnelles standard | Toute carte livrée avec légende, échelle, orientation, source des données et système de projection — sinon carte non conforme |
| P11 Chercheur | Logique de revue systématique (question de recherche → critères d'inclusion/exclusion → synthèse thématique), version allégée des standards type PRISMA adaptée au non-médical | Jamais un résumé séquentiel source par source ; synthèse organisée par thème/argument |
| P12 Rédacteur | Convention "constat → implication → recommandation" pour les résumés exécutifs | Résumé exécutif jamais une simple compression du corps du texte — toujours cette structure en une page maximum |

## 12. Boucle Plan-Exécute-Critique (pattern déjà éprouvé, contre la généricité et la répétition)

Un agent qui exécute une fois et transmet son résultat sans se relire produit des sorties inégales. Le pattern retenu — agent-exécutant puis passage critique séparé avant transmission — est déjà éprouvé dans les frameworks d'agents existants (séparation acteur/critique dans AutoGen et CrewAI, boucles de reflexion type Self-Refine/Reflexion dans la littérature sur les agents LLM). Praxis n'a pas besoin d'adopter ces frameworks — l'orchestrateur maison existant peut porter le même pattern :

1. L'agent de pôle exécute et produit un résultat brut (étape 6).
2. Une passe de critique (même agent re-sollicité avec la checklist du référentiel §11, ou fonction déterministe légère quand la vérification est mécanisable — ex. vérifier programmatiquement qu'une p-value est bien présente dans chaque table de régression) compare le résultat à la checklist du pôle.
3. Deux issues possibles, jamais une troisième silencieuse : **auto-correction** si l'écart est mécanique (ex. ajouter l'erreur-type manquante), ou **remontée à `error_recovery.py`** comme "qualité insuffisante" si l'écart nécessite un choix humain (ex. tendances parallèles non vérifiées et pas de donnée panel alternative).

Ce passage 6bis (déjà intégré au pipeline en §6) est ce qui distingue un agent qui "sait faire" d'un agent qui "vérifie qu'il a bien fait" — c'est la différence entre un stagiaire et quelqu'un qui a l'habitude de rendre ce type de livrable.

## 13. Cascades de repli explicites par méthode (anticiper la complexité, jamais de dégradation silencieuse)

Le README documente déjà un repli au niveau système (pas de LLM configuré → heuristique déterministe). Cette logique doit descendre à l'intérieur de chaque pôle, à chaque choix méthodologique, avec une règle stricte : **une méthode simplifiée est acceptable, une méthode simplifiée non signalée ne l'est jamais.**

| Pôle / méthode idéale | Condition de repli | Repli appliqué | Mention obligatoire dans le livrable |
|---|---|---|---|
| Économètre — DiD | Pas de données panel disponibles | Avant-après simple | "Limite : absence de groupe de contrôle, causalité non établie" |
| Économètre — VI | F du premier stade < 10 | Passage en OLS | "Instrument faible : coefficient à interpréter comme corrélationnel, non causal" |
| Démographe | État civil incomplet | Méthodes indirectes (Brass) | Marge d'erreur élargie explicitée |
| Collecte | Base de sondage incomplète | Échantillonnage par grappes en repli de l'aléatoire simple | Effet de plan (design effect) sur la précision mentionné |
| SIG | Pas de coordonnées précises | Géocodage approximatif au niveau communal | Niveau de précision affiché sur la carte elle-même |

Cette table doit être étendue au fur et à mesure que chaque agent est construit (Phase 5 et suivantes) — elle n'est pas exhaustive ici, elle donne le principe et les cas déjà identifiables.

## 14. Anti-répétition structurelle entre livrables similaires

Le risque, même avec profil de style et exemplaires (§5), est de converger vers un moule unique si le système retient toujours l'exemplaire le plus proche. Trois garde-fous :

- **Rotation des exemplaires** : injection en few-shot de 2-3 exemplaires tirés parmi les 3-5 plus proches (pondérés par proximité, pas uniquement le plus proche) pour éviter la convergence vers un phrasé unique.
- **Détection de similarité entre livrables consécutifs du même type** : si un nouveau document dépasse un seuil de similarité textuelle/structurelle avec le précédent du même type, reformulation partielle forcée avant livraison.
- **Profil de style en plage, pas en valeur figée** (ex. "résumé exécutif : 150-300 mots" plutôt qu'un nombre unique) — une plage laisse de la variation naturelle d'un document à l'autre.

## 15. Mémoire à trois niveaux (distinction déjà formalisée dans les architectures cognitives d'agents)

La distinction mémoire de travail / mémoire épisodique / mémoire sémantique est une distinction classique en architecture d'agents (formalisée par exemple dans le cadre CoALA pour les agents de langage). Elle clarifie une chose importante que §5 laissait implicite :

1. **Mémoire de travail** — le contexte de la tâche en cours : prompt, fichiers joints, historique de la conversation. Vit le temps d'une tâche.
2. **Mémoire épisodique** — tes exemplaires validés (§5, §14) : spécifique à toi, s'enrichit avec l'usage, gouverne uniquement la forme.
3. **Mémoire sémantique** — les référentiels méthodologiques (§11) : stables, ne changent pas avec l'usage, ne t'appartiennent pas en propre puisqu'ils font autorité dans leur discipline indépendamment de toi.

Cette séparation empêche une confusion précise : la mémoire épisodique (adaptative) ne doit jamais pouvoir écraser la mémoire sémantique (fixe). C'est la même règle qu'en §5, formulée ici comme principe d'architecture plutôt que comme cas d'usage — à implémenter comme deux tables distinctes (`user_style_profile` vs `expertise_domain.checklist_qualite`) qui ne s'écrivent jamais l'une dans l'autre.

---

# Partie III — Diversité des formats de livrables, et réalisme d'exécution

## 16. Grille des formats et gabarits de mise en forme par type de livrable

Un cadre logique n'a rien à faire dans un docx narratif — c'est une matrice que tu révises collaborativement, elle appartient à un xlsx. Un tableau de bord d'indicateurs de suivi-évaluation devient obsolète dès qu'il est figé dans un fichier — il a intérêt à vivre comme page HTML dans le frontend Next.js déjà existant. Un rapport économétrique technique lu par un décideur non spécialiste n'a pas besoin des tables de régression complètes dans le corps du texte — elles vont en annexe. Ce n'est pas un détail cosmétique : le mauvais format rend un livrable correct inutilisable par son destinataire.

Cette grille prolonge directement la fonctionnalité "Choix du format de sortie" déjà présente en Phase 3.3 (question "livrables souhaités" avant génération du plan). Elle ne change pas ce mécanisme, elle l'enrichit : on ne choisit plus seulement *quels* fichiers produire, mais aussi *quel gabarit visuel* pour chacun — avec une valeur par défaut sensée par pôle × type de livrable, que tu peux à tout moment outrepasser.

| Pôle | Type de livrable | Format(s) pertinent(s) | Gabarit / convention | Pourquoi ce format et pas un autre |
|---|---|---|---|---|
| P8 Planificateur | Cadre logique | **xlsx** (défaut) + export docx figé | Matrice officielle 4x4 éditable | Document de travail collaboratif, pas un texte à lire une fois |
| P8 Planificateur | Théorie du changement | Diagramme (svg/png intégré au docx) | Flux causal avec hypothèses annotées | C'est un objet visuel, jamais une liste à puces |
| P8 Planificateur | Chronogramme/plan d'action | xlsx (Gantt simplifié) ou pptx pour restitution en réunion | — | Dépend si c'est un outil de gestion ou un support de présentation |
| P9 Suivi-Évaluation | Rapport d'évaluation complet | docx long, sommaire automatique | Chapitrage sur les 6 critères OCDE-CAD | Document de référence archivé, doit être navigable |
| P9 Suivi-Évaluation | Tableau de bord d'indicateurs | **HTML dashboard interactif** (frontend existant) plutôt qu'un fichier figé | Vue par indicateur : valeur de référence / cible / dernière collecte | Un indicateur suivi dans le temps n'a pas sa place dans un document statique qui devient faux dès la collecte suivante |
| P9 Suivi-Évaluation | Note pour décideur | docx court, 2-4 pages | Gabarit "note de politique publique" : messages clés en encadré, zéro jargon dans le corps | Un décideur ne lit pas un rapport de 40 pages |
| P3 Économètre | Rapport technique complet | docx, corps simplifié + **annexe technique séparée** | Tables de régression complètes en annexe uniquement | Cohérent avec le niveau de technicité appris en §5/§11 |
| P3 Économètre | Restitution orale | pptx | Un graphique par diapositive, jamais un tableau brut projeté | Lisibilité en salle |
| P10 SIG | Carte pour rapport | Image haute résolution (png/svg) intégrée au docx | Légende, échelle, orientation, source (§11) | Norme cartographique professionnelle |
| P10 SIG | Diagnostic territorial multi-couches | Dashboard HTML avec carte navigable | — | Utile seulement si l'exploration interactive apporte quelque chose au-delà d'une carte figée |
| P7 Économiste | Tables de comptabilité nationale/budget | **xlsx natif** | Nomenclature SCN 2008 / COFOG | L'utilisateur final de ce type de table veut recalculer et filtrer, pas seulement lire |
| P2 Statisticien | Rapport d'analyse | docx (tableaux + graphiques) ; xlsx si l'objectif est l'exploration | — | Dépend si le destinataire lit ou manipule |
| P12 Rédacteur | Réponse à appel d'offres | docx conforme à la structure du cahier des charges | Jamais un gabarit maison qui ignore les sections demandées | Contrainte externe non négociable |
| P12 Rédacteur | Note technique courte | docx, 1 page | Format mémo constat/implication/recommandation (§11) | — |

Cette table n'est pas figée — elle s'enrichit à chaque nouvel agent construit (Phase 5 et suivantes), au même rythme que la table de repli §13.

## 17. Réalisme d'exécution : ce qui peut mal se passer, et comment l'anticiper maintenant

Un plan qui ne nomme pas ce qui peut échouer n'est pas complet. Voici les points de friction réels, anticipés avant qu'ils ne deviennent des blocages.

**a) Coût et latence.** Un pipeline multi-pôle enchaîne plusieurs appels LLM (routage, exécution par pôle, critique, rédaction) — le coût et le temps de réponse grossissent avec la richesse du système. Réponse : *tiering de modèle*. Les étapes mécaniques (classification du routeur, vérification de checklist simple) tournent sur un modèle rapide et économique (Qwen local via Ollama, déjà supporté selon ton README) ; le modèle le plus capable est réservé à la rédaction finale et aux décisions ambiguës. Le résultat du routeur est mis en cache pour des prompts structurellement proches.

**b) Fiabilité et hallucination malgré le référentiel.** Un référentiel codé (§11) réduit le risque, il ne l'annule pas — un LLM peut mal appliquer un test correctement décrit. Réponse : **validation humaine obligatoire et non négociable** pour tout livrable à enjeu réel (évaluation officielle, réponse à appel d'offres, document destiné à publication), au moins tant qu'un historique de fiabilité n'est pas mesuré pôle par pôle. Un "niveau de confiance par pôle", dérivé du taux de correction observé dans la bibliothèque d'exemplaires (§5), peut ensuite justifier d'alléger progressivement la relecture — jamais la supprimer.

**c) Stratégie de test incrémentale, avant de faire confiance à un agent.** Chaque pôle doit avoir son propre jeu de données "golden" avec un résultat attendu connu (ex. pour l'Économètre : un panel simulé avec un effet de traitement connu, pour vérifier que le DiD calculé retombe sur la bonne valeur) — construit *avant* la mise en usage réel de l'agent, pas après un premier incident. Extension naturelle du dossier `tests/` déjà existant : un sous-dossier par pôle.

**d) Dépendances externes non encore éprouvées.** Le README signale déjà que le sandbox E2B n'a jamais été testé en aller-retour réel. Principe à généraliser : toute nouvelle dépendance (`geopandas`/`folium` pour le SIG, fonctions avancées de `statsmodels` pour VI/RDD) est testée isolément avant d'être posée en dépendance de production — ne jamais supposer qu'une librairie "devrait marcher" parce qu'elle est réputée.

**e) Effort réaliste, pas un sprint.** Un agent de pôle correctement construit (référentiel + code + tests golden + premier usage réel + ajustement du référentiel si besoin) représente plusieurs semaines de travail effectif, pas quelques jours. Conséquence directe sur la feuille de route : la Phase 5 (§8) ne se lance pas en parallèle complet sur Planificateur et Suivi-Évaluation — Planificateur d'abord, parce que sa structure (une matrice figée, le cadre logique) est plus simple à valider que Suivi-Évaluation, qui s'appuie ensuite dessus.

**f) Registre de risques synthétique.**

| Risque | Mitigation retenue |
|---|---|
| Coût API qui explose avec la richesse du pipeline | Tiering de modèle + cache du routage |
| Hallucination méthodologique malgré le référentiel | Validation humaine obligatoire tant que le taux de correction par pôle n'est pas mesuré comme faible |
| E2B jamais testé en conditions réelles (déjà noté au README) | Test isolé avant toute dépendance de production ; repli en exécution locale sinon |
| Sur-ingénierie : trop de pôles construits en parallèle | Séquencement strict — un agent validé avant que le suivant démarre (§8) |
| Profil de style qui se fige sur trop peu d'exemples | Seuil minimum d'exemplaires (5 par type de livrable) avant que le profil influence fortement la génération ; gabarit neutre par défaut en dessous de ce seuil |
