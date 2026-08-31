"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { api, TASK_TYPES, TASK_TYPE_LABELS, TaskType } from "@/lib/api";

export default function NewTaskForm() {
  const router = useRouter();
  const [title, setTitle] = useState("");
  const [rawRequest, setRawRequest] = useState("");
  const [taskType, setTaskType] = useState<TaskType>("analyse_donnees");
  const [objective, setObjective] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!title.trim() || !rawRequest.trim()) {
      setError("Le titre et la description de la demande sont requis.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const task = await api.createTask({
        title: title.trim(),
        raw_request: rawRequest.trim(),
        task_type: taskType,
        objective: objective.trim() || undefined,
      });
      router.push(`/tasks/${task.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erreur inconnue");
      setSubmitting(false);
    }
  }

  if (!open) {
    return (
      <button className="button" onClick={() => setOpen(true)}>
        + Nouvelle tâche
      </button>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="card" style={{ marginBottom: 20 }}>
      <div className="field">
        <label htmlFor="title">Titre</label>
        <input
          id="title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Évaluation Satisfaction Socio-Économique — ENSPD"
        />
      </div>

      <div className="field">
        <label htmlFor="raw_request">Demande (en langage naturel)</label>
        <textarea
          id="raw_request"
          rows={3}
          value={rawRequest}
          onChange={(e) => setRawRequest(e.target.value)}
          placeholder="Analyser l'enquête ménage, nettoyer les données, produire un rapport et une présentation."
        />
      </div>

      <div className="field">
        <label htmlFor="type">Type de tâche</label>
        <select id="type" value={taskType} onChange={(e) => setTaskType(e.target.value as TaskType)}>
          {TASK_TYPES.map((t) => (
            <option key={t} value={t}>
              {TASK_TYPE_LABELS[t]}
            </option>
          ))}
        </select>
      </div>

      <div className="field">
        <label htmlFor="objective">Objectif (optionnel)</label>
        <input
          id="objective"
          value={objective}
          onChange={(e) => setObjective(e.target.value)}
          placeholder="Produire un rapport fiable malgré des anomalies de saisie."
        />
      </div>

      {error && <div className="error-banner">{error}</div>}

      <div style={{ display: "flex", gap: 8 }}>
        <button className="button" type="submit" disabled={submitting}>
          {submitting ? "Création..." : "Créer la tâche"}
        </button>
        <button
          type="button"
          className="button secondary"
          onClick={() => setOpen(false)}
          disabled={submitting}
        >
          Annuler
        </button>
      </div>
    </form>
  );
}
