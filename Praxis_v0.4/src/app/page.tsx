"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, STATUS_LABELS, TASK_TYPE_LABELS, TaskType, ApiError } from "@/lib/api";
import NewTaskForm from "@/components/new-task-form";

type TaskRow = {
  id: string;
  title: string;
  status: string;
  type: TaskType | null;
  readiness_score: number | null;
  created_at: string;
};

const COLUMNS: { key: string; label: string; statuses: string[] }[] = [
  { key: "prep", label: "À préparer", statuses: ["draft", "understanding", "clarification", "contextualization"] },
  { key: "plan", label: "Planification", statuses: ["planning", "plan_validation", "tool_selection"] },
  { key: "running", label: "En cours", statuses: ["execution", "validation"] },
  { key: "blocked", label: "Action requise", statuses: ["error_recovery"] },
  { key: "done", label: "Livrable", statuses: ["deliverable", "final_validation", "deployed", "learning"] },
];

export default function Dashboard() {
  const [tasks, setTasks] = useState<TaskRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .listTasks()
      .then((rows) => setTasks(rows as unknown as TaskRow[]))
      .catch((e) =>
        setError(
          e instanceof ApiError
            ? `Impossible de joindre l'API (${e.message}). Vérifiez que le backend tourne sur ${
                process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"
              }.`
            : "Erreur inconnue"
        )
      );
  }, []);

  return (
    <div>
      <NewTaskForm />

      {error && <div className="error-banner">{error}</div>}

      {tasks && tasks.length === 0 && (
        <p className="muted">Aucune tâche pour l'instant — crée la première ci-dessus.</p>
      )}

      {tasks && tasks.length > 0 && (
        <div className="board">
          {COLUMNS.map((col) => {
            const colTasks = tasks.filter((t) => col.statuses.includes(t.status));
            return (
              <div className="board-column" key={col.key}>
                <h3>
                  {col.label} ({colTasks.length})
                </h3>
                {colTasks.map((t) => (
                  <Link href={`/tasks/${t.id}`} key={t.id} className="task-card">
                    <div className="title">{t.title}</div>
                    <div className="muted">{t.type ? TASK_TYPE_LABELS[t.type] : "—"}</div>
                    <div style={{ marginTop: 8 }}>
                      <span className={`badge ${t.status === "error_recovery" ? "error" : ""}`}>
                        {STATUS_LABELS[t.status] || t.status}
                      </span>
                    </div>
                    {t.readiness_score != null && (
                      <div className="progress-track" style={{ marginTop: 8 }}>
                        <div
                          className="progress-fill"
                          style={{ width: `${Math.round(t.readiness_score * 100)}%` }}
                        />
                      </div>
                    )}
                  </Link>
                ))}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
