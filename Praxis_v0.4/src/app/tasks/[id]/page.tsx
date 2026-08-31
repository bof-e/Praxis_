"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import {
  api,
  ApiError,
  STATUS_LABELS,
  TASK_TYPE_LABELS,
  TaskDetail,
  TraceabilityAnswer,
} from "@/lib/api";


export default function TaskDetailPage() {
  const params = useParams();
  const id = params.id as string;

  const [detail, setDetail] = useState<TaskDetail | null>(null);
  const [readinessModel, setReadinessModel] = useState<{
    dimensions: string[];
    critical_dimensions: string[];
    labels: Record<string, string>;
  } | null>(null);
  const [dimensionScores, setDimensionScores] = useState<Record<string, number>>({});
  const scoresInitialized = useRef(false);

  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [executing, setExecuting] = useState(false);

  const [traceRowId, setTraceRowId] = useState("");
  const [traceAnswer, setTraceAnswer] = useState<TraceabilityAnswer | null>(null);
  const [traceLoading, setTraceLoading] = useState(false);

  const [reformulating, setReformulating] = useState(false);
  const [reformulateNote, setReformulateNote] = useState<string | null>(null);
  const [planGenerationMethod, setPlanGenerationMethod] = useState<"llm" | "heuristic" | "poles" | null>(null);

  const [routingDomain, setRoutingDomain] = useState(false);
  const [routingResult, setRoutingResult] = useState<{
    poles: string[]; needs_clarification: boolean; source: string;
  } | null>(null);

  const [deliverableChoices, setDeliverableChoices] = useState<{ key: string; label: string }[]>([]);
  const [selectedDeliverables, setSelectedDeliverables] = useState<string[]>([]);
  const deliverablesInitialized = useRef(false);
  const [savingDeliverables, setSavingDeliverables] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [d, rm, dd] = await Promise.all([
        api.getTaskDetail(id),
        api.getReadinessModel(id),
        api.getDefaultDeliverables(id),
      ]);
      setDetail(d);
      setReadinessModel(rm);
      if (!scoresInitialized.current) {
        const initial: Record<string, number> = {};
        rm.dimensions.forEach((dim) => (initial[dim] = 0.5));
        setDimensionScores(initial);
        scoresInitialized.current = true;
      }
      setDeliverableChoices(dd.choices);
      if (!deliverablesInitialized.current) {
        setSelectedDeliverables(dd.current_selection ?? dd.defaults);
        deliverablesInitialized.current = true;
      }
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Erreur inconnue");
    }
  }, [id]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    try {
      await api.uploadData(id, file);
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Échec de l'envoi du fichier");
    } finally {
      setUploading(false);
      e.target.value = "";
    }
  }

  async function handleReadinessSubmit(e: React.FormEvent) {
    e.preventDefault();
    try {
      await api.updateReadiness(id, dimensionScores);
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Échec de la mise à jour");
    }
  }

  async function handleSaveDeliverables() {
    setSavingDeliverables(true);
    try {
      await api.setDeliverables(id, selectedDeliverables);
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Échec de l'enregistrement des livrables");
    } finally {
      setSavingDeliverables(false);
    }
  }

  async function handleProposePlan() {
    try {
      const res = await api.proposePlan(id);
      setPlanGenerationMethod(res.generation_method || null);
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Échec de la génération du plan");
    }
  }

  async function handleReformulate() {
    setReformulating(true);
    setReformulateNote(null);
    try {
      const suggestion = await api.reformulate(id);
      if (!suggestion.available) {
        setReformulateNote("Aucune IA configurée sur ce backend — réglez les curseurs manuellement.");
      } else {
        if (suggestion.dimension_scores) {
          setDimensionScores((s) => ({ ...s, ...suggestion.dimension_scores }));
        }
        setReformulateNote(suggestion.rationale || "Suggestion appliquée — vérifiez avant de valider.");
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Échec de la suggestion IA");
    } finally {
      setReformulating(false);
    }
  }

  async function handleRouteDomain() {
    setRoutingDomain(true);
    try {
      const result = await api.routeDomain(id);
      setRoutingResult(result);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Échec du routage de domaine");
    } finally {
      setRoutingDomain(false);
    }
  }

  async function handleValidatePlan(approved: boolean) {
    if (!detail?.plan) return;
    try {
      await api.validatePlan(detail.plan.id, approved);
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Échec de la validation du plan");
    }
  }

  async function handleExecute(force = false) {
    if (force && !window.confirm("Regénérer tous les livrables depuis zéro ?")) return;
    setExecuting(true);
    setError(null);
    try {
      await api.executeTaskAsync(id, force);
      await pollUntilDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Échec du démarrage de l'exécution");
      setExecuting(false);
    }
  }

  async function pollUntilDone() {
    const deadline = Date.now() + 5 * 60 * 1000; // 5 min safety cap
    while (Date.now() < deadline) {
      const status = await api.getExecutionStatus(id);
      if (!status.running) break;
      await new Promise((resolve) => setTimeout(resolve, 1500));
    }
    await refresh();
    setExecuting(false);
  }

  async function handleValidateDeliverable(deliverableId: string) {
    try {
      await api.validateDeliverable(deliverableId);
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Échec de la validation");
    }
  }

  async function handleExplainRow(e: React.FormEvent) {
    e.preventDefault();
    if (!traceRowId.trim()) return;
    setTraceLoading(true);
    try {
      const answer = await api.explainRow(id, traceRowId.trim());
      setTraceAnswer(answer);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Échec de la recherche");
    } finally {
      setTraceLoading(false);
    }
  }

  if (error && !detail) {
    return (
      <div>
        <Link href="/" className="nav-link">
          ← Toutes les tâches
        </Link>
        <div className="error-banner" style={{ marginTop: 16 }}>
          {error}
        </div>
      </div>
    );
  }

  if (!detail) return <p className="muted">Chargement...</p>;

  const { task, plan, artifacts, deliverables, executions } = detail;
  const hasAuditTrail = artifacts.some((a) => a.kind === "analysis");
  const canExecute = plan && plan.status === "validated";
  const alreadyDelivered = ["deliverable", "final_validation", "deployed"].includes(task.status);

  return (
    <div>
      <Link href="/" className="nav-link">
        ← Toutes les tâches
      </Link>

      <div style={{ marginTop: 10, marginBottom: 20 }}>
        <h2 style={{ margin: "4px 0" }}>{task.title}</h2>
        <span className={`badge ${task.status === "error_recovery" ? "error" : ""}`}>
          {STATUS_LABELS[task.status] || task.status}
        </span>{" "}
        <span className="badge">{TASK_TYPE_LABELS[task.type]}</span>
      </div>

      {task.status === "error_recovery" && (
        <div className="error-banner">
          L&apos;exécution est en pause suite à une erreur. Consultez le journal
          d&apos;exécution ci-dessous, corrigez le problème (ex. fichier de données), puis
          relancez l&apos;exécution : elle reprendra là où elle s&apos;est arrêtée.
        </div>
      )}

      {error && <div className="error-banner">{error}</div>}

      <div className="card">
        <p style={{ marginTop: 0 }}>{task.raw_request}</p>
        {task.objective && <p className="muted">Objectif : {task.objective}</p>}
      </div>

      {/* Data sources */}
      <div className="section">
        <h2>Sources de données</h2>
        <div className="card">
          {task.data_sources && task.data_sources.length > 0 ? (
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {task.data_sources.map((ds, i) => (
                <li key={i}>{ds.original_name}</li>
              ))}
            </ul>
          ) : (
            <p className="muted" style={{ margin: 0 }}>
              Aucun fichier associé — un classeur Excel pour l&apos;analyse de données, ou un
              document (.txt, .md, .docx) comme matière première pour la rédaction.
            </p>
          )}
          <div style={{ marginTop: 12 }}>
            <input type="file" onChange={handleUpload} disabled={uploading} />
          </div>
        </div>
      </div>

      {/* Readiness */}
      <div className="section">
        <h2>Préparation (readiness)</h2>
        <div className="card">
          {task.readiness_score != null && (
            <div style={{ marginBottom: 14 }}>
              <div className="muted">Score global : {Math.round(task.readiness_score * 100)}%</div>
              <div className="progress-track" style={{ marginTop: 4 }}>
                <div
                  className="progress-fill"
                  style={{ width: `${Math.round(task.readiness_score * 100)}%` }}
                />
              </div>
            </div>
          )}
          {readinessModel && (
            <form onSubmit={handleReadinessSubmit}>
              <div style={{ marginBottom: 14, display: "flex", alignItems: "center", gap: 10 }}>
                <button type="button" className="button secondary" onClick={handleReformulate} disabled={reformulating}>
                  {reformulating ? "Suggestion en cours..." : "✨ Suggérer avec l'IA"}
                </button>
              </div>
              {reformulateNote && <p className="muted" style={{ marginTop: -6 }}>{reformulateNote}</p>}
              {readinessModel.dimensions.map((dim) => (
                <div className="field" key={dim}>
                  <label htmlFor={dim}>
                    {readinessModel.labels[dim] || dim}
                    {readinessModel.critical_dimensions.includes(dim) && " *"}
                  </label>
                  <input
                    id={dim}
                    type="range"
                    min={0}
                    max={1}
                    step={0.05}
                    value={dimensionScores[dim] ?? 0.5}
                    onChange={(e) =>
                      setDimensionScores((s) => ({ ...s, [dim]: parseFloat(e.target.value) }))
                    }
                  />
                  <span className="muted">{Math.round((dimensionScores[dim] ?? 0.5) * 100)}%</span>
                </div>
              ))}
              <p className="muted" style={{ marginTop: -6 }}>* dimension critique</p>
              <button className="button" type="submit">
                Mettre à jour la préparation
              </button>
            </form>
          )}
        </div>
      </div>

      {/* Domain routing - Praxis v1.0 Phase 4: which expertise poles this
          request convenes, instead of the single fixed task type */}
      {!plan && (
        <div className="section">
          <h2>Pôles d&apos;expertise</h2>
          <p className="muted">
            Identifie les domaines d&apos;expertise réellement convoqués par la demande (une évaluation
            d&apos;impact convoque l&apos;Économètre, le Suivi-Évaluation et le Rédacteur, par exemple)
            plutôt que le type de tâche unique choisi à la création.
          </p>
          <div className="card">
            <button className="button secondary" onClick={handleRouteDomain} disabled={routingDomain}>
              {routingDomain ? "Routage en cours..." : "Identifier les pôles concernés"}
            </button>
            {routingResult && (
              <div style={{ marginTop: 10 }}>
                {routingResult.needs_clarification ? (
                  <p className="muted">
                    Confiance insuffisante pour router automatiquement — précisez la demande, ou
                    laissez tel quel pour utiliser la génération de plan habituelle.
                  </p>
                ) : (
                  <div>
                    {routingResult.poles.map((p) => (
                      <span className="badge" key={p} style={{ marginRight: 6 }}>{p}</span>
                    ))}
                    <p className="muted" style={{ marginTop: 6, marginBottom: 0 }}>
                      {routingResult.source === "llm"
                        ? "Identifiés par l'IA — le plan sera composé à partir de ces pôles."
                        : "Déduits du type de tâche (aucune IA configurée) — le plan sera composé à partir de ces pôles."}
                    </p>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Deliverable selection - "not every deliverable is necessary" */}
      <div className="section">
        <h2>Livrables souhaités</h2>
        <p className="muted">
          Choisissez ce que vous voulez vraiment obtenir — le plan s&apos;adapte en conséquence
          (pas de présentation générée si vous n&apos;en voulez pas, par exemple).
        </p>
        <div className="card">
          {plan ? (
            <div>
              {selectedDeliverables.map((key) => (
                <span className="badge" key={key} style={{ marginRight: 6 }}>
                  {deliverableChoices.find((c) => c.key === key)?.label || key}
                </span>
              ))}
              <p className="muted" style={{ marginTop: 8, marginBottom: 0 }}>
                Le plan est déjà généré à partir de ce choix.
              </p>
            </div>
          ) : (
            <>
              {deliverableChoices.map((choice) => (
                <label
                  key={choice.key}
                  style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}
                >
                  <input
                    type="checkbox"
                    checked={selectedDeliverables.includes(choice.key)}
                    onChange={(e) => {
                      setSelectedDeliverables((prev) =>
                        e.target.checked
                          ? [...prev, choice.key]
                          : prev.filter((k) => k !== choice.key)
                      );
                    }}
                  />
                  {choice.label}
                </label>
              ))}
              <button
                className="button secondary"
                onClick={handleSaveDeliverables}
                disabled={savingDeliverables || selectedDeliverables.length === 0}
                style={{ marginTop: 4 }}
              >
                {savingDeliverables ? "Enregistrement..." : "Confirmer ce choix"}
              </button>
              {selectedDeliverables.length === 0 && (
                <p className="muted" style={{ marginTop: 6 }}>Choisissez au moins un livrable.</p>
              )}
            </>
          )}
        </div>
      </div>

      {/* Plan */}
      <div className="section">
        <h2>Plan d&apos;exécution</h2>
        <div className="card">
          {!plan && (
            <button className="button" onClick={handleProposePlan}>
              Proposer un plan
            </button>
          )}
          {plan && (
            <>
              <div style={{ marginBottom: 10 }}>
                <span className="badge">{plan.status}</span>{" "}
                {planGenerationMethod && (
                  <span className="badge">
                    {planGenerationMethod === "llm" ? "Plan généré par IA" : "Plan généré (heuristique)"}
                  </span>
                )}
              </div>
              <ol style={{ paddingLeft: 18, margin: 0 }}>
                {plan.steps.map((s, i) => (
                  <li key={i} style={{ marginBottom: 4 }}>
                    {s.name}
                    {s.is_critical && <span className="badge warn" style={{ marginLeft: 6 }}>critique</span>}
                  </li>
                ))}
              </ol>
              {plan.status === "draft" && (
                <div style={{ marginTop: 12, display: "flex", gap: 8 }}>
                  <button className="button" onClick={() => handleValidatePlan(true)}>
                    Valider le plan
                  </button>
                  <button className="button secondary" onClick={() => handleValidatePlan(false)}>
                    Rejeter
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      </div>

      {/* Execution */}
      {plan && (
        <div className="section">
          <h2>Exécution</h2>
          <div className="card">
            <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
              <button className="button" onClick={() => handleExecute(false)} disabled={!canExecute || executing}>
                {executing ? "Exécution en cours..." : task.status === "error_recovery" ? "Reprendre l'exécution" : "Exécuter"}
              </button>
              {alreadyDelivered && (
                <button className="button secondary" onClick={() => handleExecute(true)} disabled={executing}>
                  Regénérer depuis zéro
                </button>
              )}
            </div>
            {!canExecute && !alreadyDelivered && (
              <p className="muted">Le plan doit être validé avant de pouvoir exécuter la tâche.</p>
            )}

            {executing && (
              <p className="muted">
                Exécution en cours en arrière-plan — cette page se met à jour automatiquement,
                vous pouvez naviguer ailleurs pendant ce temps.
              </p>
            )}

            {executions.length > 0 && (
              <div style={{ marginTop: 12 }}>
                <div className="muted" style={{ marginBottom: 6 }}>Journal d&apos;exécution</div>
                {executions.map((ex) => (
                  <div className="list-row" key={ex.id}>
                    <span>
                      {ex.step_index + 1}. {ex.agent_assigned}
                    </span>
                    <span>
                      <span className={`badge ${ex.status === "failed" ? "error" : ""}`}>{ex.status}</span>
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Artifacts & deliverables */}
      <div className="section">
        <h2>Artefacts &amp; livrables</h2>
        <div className="card">
          {artifacts.length === 0 && <p className="muted" style={{ margin: 0 }}>Aucun artefact produit pour l&apos;instant.</p>}
          {artifacts.map((a) => {
            const deliverable = deliverables.find((d) => d.artifact_id === a.id);
            return (
              <div className="list-row" key={a.id}>
                <span>
                  {a.kind} <span className="muted">.{a.format}</span>
                  {deliverable && (
                    <span className={`badge ${deliverable.validated ? "" : "warn"}`} style={{ marginLeft: 8 }}>
                      {deliverable.validated ? "Livrable validé" : "Livrable — à valider"}
                    </span>
                  )}
                </span>
                <span style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <a href={api.downloadUrl(a.id)} target="_blank" rel="noreferrer" className="nav-link">
                    Télécharger
                  </a>
                  {deliverable && !deliverable.validated && (
                    <button className="button secondary" onClick={() => handleValidateDeliverable(deliverable.id)}>
                      Valider
                    </button>
                  )}
                </span>
              </div>
            );
          })}
        </div>
      </div>

      {/* Traceability explorer - original feature */}
      {hasAuditTrail && (
        <div className="section">
          <h2>Explorateur de traçabilité</h2>
          <p className="muted">
            Pourquoi une observation a-t-elle disparu ou changé pendant le nettoyage ? Entrez son identifiant.
          </p>
          <div className="card">
            <form onSubmit={handleExplainRow} style={{ display: "flex", gap: 8, marginBottom: 12 }}>
              <input
                placeholder="ex. MEN_035"
                value={traceRowId}
                onChange={(e) => setTraceRowId(e.target.value)}
                style={{ flex: 1 }}
              />
              <button className="button" type="submit" disabled={traceLoading}>
                {traceLoading ? "Recherche..." : "Expliquer"}
              </button>
            </form>
            {traceAnswer && (
              <div>
                {traceAnswer.found ? (
                  <div>
                    <p>{traceAnswer.explanation}</p>
                    {traceAnswer.matches?.map((m, i) => (
                      <div className="list-row" key={i}>
                        <span>{m.variable}</span>
                        <span className="muted">
                          {m.reason} → {m.action}
                        </span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="muted">{traceAnswer.message}</p>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
