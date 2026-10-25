import { useEffect, useState, type FormEvent } from "react";
import { request } from "./api";

export const columns = [
  "Backlog",
  "To Do",
  "In Progress",
  "Review",
  "Blocked",
  "Done",
] as const;
type Column = (typeof columns)[number];
export type SwarmTask = {
  id: string;
  title?: string;
  task?: string;
  taskPreview?: string;
  status: string;
  agentId?: string;
  priority?: number;
  dependsOn?: string[];
  progress?: string;
  failureReason?: string;
  provider?: string;
  model?: string;
  totalCostUsd?: number;
  lastUpdatedAt?: string;
  parentTaskId?: string;
  vcsUrl?: string;
};
type Agent = { id: string; name: string; status: string };
type Planning = {
  column: Column;
  priority: number;
  assignee: string | null;
  description: string;
  acceptance: string;
  blockers: string;
  dependencies: string[];
  branch: string;
  commit: string;
  pr_url: string;
};
type Board = {
  tasks: SwarmTask[];
  total?: number;
  planning_enabled: boolean;
  planning: {
    status: string;
    values: Record<string, Planning>;
    truncated?: boolean;
  };
};
type Detail = SwarmTask & {
  overlay_status: string;
  history: { id: string; eventType: string; createdAt: string }[];
  overlay: {
    id: string;
    created_at: string;
    kind: string;
    body: Record<string, unknown>;
  }[];
};
export const taskTitle = (task: SwarmTask) =>
  task.title || (task.task || task.taskPreview || task.id).split("\n")[0];
const nativeColumn = (status: string): Column =>
  (({
    draft: "Backlog",
    backlog: "Backlog",
    unassigned: "To Do",
    pending: "To Do",
    offered: "To Do",
    in_progress: "In Progress",
    reviewing: "Review",
    paused: "Blocked",
    failed: "Blocked",
    completed: "Done",
    cancelled: "Done",
    superseded: "Done",
  })[status] as Column) || "Backlog";
const formValues = (event: FormEvent<HTMLFormElement>) => {
  event.preventDefault();
  return new FormData(event.currentTarget);
};

export function AdminProject() {
  const [board, setBoard] = useState<Board | null>(null),
    [agents, setAgents] = useState<Agent[]>([]);
  const [detail, setDetail] = useState<Detail | null>(null),
    [selected, setSelected] = useState("");
  const [planning, setPlanning] = useState<Planning | null>(null);
  const [offset, setOffset] = useState(0),
    [epoch, setEpoch] = useState(0);
  const [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [busy, setBusy] = useState(false);
  const [creating, setCreating] = useState(false);
  useEffect(() => {
    let active = true;
    setBoard(null);
    setDetail(null);
    setPlanning(null);
    request(`/api/admin/tasks?offset=${offset}`)
      .then((value) => {
        if (active) {
          setBoard(value);
          setError("");
        }
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    request("/api/admin/operations")
      .then((value) => {
        if (active) setAgents(value.agents.rows);
      })
      .catch(() => {
        if (active) setAgents([]);
      });
    return () => {
      active = false;
    };
  }, [offset, epoch]);
  useEffect(() => {
    let active = true;
    setDetail(null);
    setPlanning(null);
    if (selected && board)
      request(`/api/admin/tasks/${selected}`)
        .then((value: Detail) => {
          if (!active) return;
          setDetail(value);
          setPlanning(
            board.planning.values[selected] || {
              column: nativeColumn(value.status),
              priority: value.priority ?? 50,
              assignee: value.agentId || null,
              description: value.task || "",
              acceptance: "",
              blockers: "",
              dependencies: value.dependsOn || [],
              branch: "",
              commit: "",
              pr_url: value.vcsUrl || "",
            },
          );
        })
        .catch((e) => {
          if (active) setError(e.message);
        });
    return () => {
      active = false;
    };
  }, [selected, board]);
  async function mutate(path: string, body: unknown, method = "POST") {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await request(path, body, method);
      setNotice(
        result.audit_status === "unavailable"
          ? "Swarm’a kaydedildi; yerel işlem günlüğü kullanılamıyor. İşlemi tekrarlamayın."
          : "Kaydedildi",
      );
      setEpoch((v) => v + 1);
      setCreating(false);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const canPlan =
    board?.planning_enabled && board.planning.status === "available";
  return (
    <div className="admin-page" aria-busy={!board && !error}>
      <section className="card">
        <div className="admin-heading">
          <div>
            <div className="eyebrow">PROJE / AGENT SWARM</div>
            <h2>İş panosu</h2>
          </div>
          <div className="admin-actions">
            <button disabled={busy} onClick={() => setEpoch((v) => v + 1)}>
              Yenile
            </button>
            <button
              disabled={!board || busy}
              onClick={() => setCreating((v) => !v)}
            >
              Görev oluştur
            </button>
          </div>
        </div>
        <p className="muted">
          Görevler doğrudan Swarm’dan okunur. Kanban ve planlanan sorumlu ek
          planlama alanlarıdır; ajan çalıştırma durumunu veya gerçek atamasını
          değiştirmez. Oluşturulan görev taslak kalır.
        </p>
        {!canPlan && (
          <p className="admin-empty">
            Planlama eki kullanılamıyor. Swarm görevleri ve gerçek durumları
            okunabilir.
          </p>
        )}
        {error && (
          <p className="alert" role="alert">
            {error}
          </p>
        )}
        {notice && (
          <p role="status" className="notice">
            {notice}
          </p>
        )}
        {creating && (
          <form
            className="admin-form"
            onSubmit={(e) => {
              const f = formValues(e);
              void mutate("/api/admin/tasks", {
                title: f.get("title"),
                description: f.get("description"),
                acceptance: f.get("acceptance"),
                priority: Number(f.get("priority")),
                agent_id: f.get("agent_id") || null,
                dependencies: String(f.get("dependencies") || "")
                  .split(/[\s,]+/)
                  .filter(Boolean),
              });
            }}
          >
            <fieldset disabled={busy}>
              <legend>Yeni Swarm taslağı</legend>
              <label>
                Başlık
                <input name="title" required maxLength={120} />
              </label>
              <label>
                Açıklama
                <textarea name="description" required maxLength={12000} />
              </label>
              <label>
                Kabul ölçütleri
                <textarea name="acceptance" required maxLength={6000} />
              </label>
              <label>
                Öncelik (0–100)
                <input
                  type="number"
                  name="priority"
                  min={0}
                  max={100}
                  defaultValue={50}
                  required
                />
              </label>
              <label>
                Gerçek başlangıç ataması
                <select name="agent_id">
                  <option value="">Atanmamış</option>
                  {agents.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.name} · {a.status}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Bağımlılık kimlikleri
                <input name="dependencies" placeholder="UUID, UUID" />
              </label>
              <button type="submit">Swarm’da taslak oluştur</button>
            </fieldset>
          </form>
        )}
        <p>
          {board
            ? `Gösterilen: ${offset + 1}–${offset + board.tasks.length} · toplam ${board.total ?? "kullanılamıyor"}`
            : "Swarm kaynağı bekleniyor veya kullanılamıyor"}
        </p>
        <div className="admin-kanban">
          {columns.map((column) => (
            <section className="kanban-column" key={column}>
              <h3>
                {column}{" "}
                <span>
                  {board?.tasks.filter(
                    (t) =>
                      (board.planning.values[t.id]?.column ||
                        nativeColumn(t.status)) === column,
                  ).length ?? "—"}
                </span>
              </h3>
              {board?.tasks
                .filter(
                  (t) =>
                    (board.planning.values[t.id]?.column ||
                      nativeColumn(t.status)) === column,
                )
                .map((task) => (
                  <button
                    className={`task-card ${selected === task.id ? "selected" : ""}`}
                    key={task.id}
                    disabled={busy}
                    onClick={() => setSelected(task.id)}
                  >
                    <strong>{taskTitle(task)}</strong>
                    <small>Swarm: {task.status}</small>
                    <small>
                      Öncelik:{" "}
                      {board.planning.values[task.id]?.priority ??
                        task.priority ??
                        "—"}
                    </small>
                    <small>
                      Gerçek ajan:{" "}
                      {agents.find((a) => a.id === task.agentId)?.name ||
                        task.agentId ||
                        "Atanmamış"}
                    </small>
                  </button>
                ))}
            </section>
          ))}
        </div>
        <div className="admin-actions">
          <button
            disabled={!offset || busy}
            onClick={() => {
              setOffset((v) => Math.max(0, v - 100));
              setSelected("");
            }}
          >
            Önceki
          </button>
          <button
            disabled={!board || board.tasks.length < 100 || busy}
            onClick={() => {
              setOffset((v) => v + 100);
              setSelected("");
            }}
          >
            Sonraki
          </button>
        </div>
        {board?.planning.truncated && (
          <p role="status">
            Planlama kaynağı 1000 kayıtla sınırlı; bazı ek alanlar
            gösterilmiyor.
          </p>
        )}
      </section>
      {detail && planning && (
        <section className="card admin-detail" key={detail.id}>
          <h2>{taskTitle(detail)}</h2>
          <code>{detail.id}</code>
          <p>
            Gerçek Swarm durumu: {detail.status} · gerçek öncelik:{" "}
            {detail.priority ?? "Kullanılamıyor"} · gerçek atama:{" "}
            {detail.agentId || "Atanmamış"}
          </p>
          <details>
            <summary>Kaynak görev metni</summary>
            <pre>{detail.task}</pre>
          </details>
          <form
            className="admin-form"
            onSubmit={(e) => {
              const f = formValues(e);
              void mutate(
                `/api/admin/tasks/${detail.id}/title`,
                { title: f.get("title") },
                "PATCH",
              );
            }}
          >
            <label>
              Swarm başlığı
              <input
                name="title"
                defaultValue={taskTitle(detail).slice(0, 120)}
                required
                maxLength={120}
              />
            </label>
            <button disabled={busy}>Başlığı güncelle</button>
          </form>
          <form
            className="admin-form"
            onSubmit={(e) => {
              e.preventDefault();
              void mutate(
                `/api/admin/tasks/${detail.id}/planning`,
                planning,
                "PUT",
              );
            }}
          >
            <fieldset disabled={!canPlan || busy}>
              <legend>Swarm KV planlama eki</legend>
              <label>
                Kanban durumu
                <select
                  value={planning.column}
                  onChange={(e) =>
                    setPlanning({
                      ...planning,
                      column: e.target.value as Column,
                    })
                  }
                >
                  {columns.map((c) => (
                    <option key={c}>{c}</option>
                  ))}
                </select>
              </label>
              <label>
                Planlanan öncelik
                <input
                  type="number"
                  min={0}
                  max={100}
                  required
                  value={planning.priority}
                  onChange={(e) =>
                    setPlanning({
                      ...planning,
                      priority: Number(e.target.value),
                    })
                  }
                />
              </label>
              <label>
                Planlanan sorumlu
                <select
                  value={planning.assignee || ""}
                  onChange={(e) =>
                    setPlanning({
                      ...planning,
                      assignee: e.target.value || null,
                    })
                  }
                >
                  <option value="">Atanmamış</option>
                  {agents.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.name}
                    </option>
                  ))}
                </select>
              </label>
              {(
                [
                  ["description", "Açıklama"],
                  ["acceptance", "Kabul ölçütleri"],
                  ["blockers", "Engeller"],
                ] as const
              ).map(([key, label]) => (
                <label key={key}>
                  {label}
                  <textarea
                    value={planning[key]}
                    maxLength={
                      key === "description"
                        ? 12000
                        : key === "acceptance"
                          ? 6000
                          : 3000
                    }
                    onChange={(e) =>
                      setPlanning({ ...planning, [key]: e.target.value })
                    }
                  />
                </label>
              ))}
              <label>
                Planlanan bağımlılıklar (UUID)
                <input
                  value={planning.dependencies.join(", ")}
                  onChange={(e) =>
                    setPlanning({
                      ...planning,
                      dependencies: e.target.value
                        .split(/[,\s]+/)
                        .filter(Boolean),
                    })
                  }
                />
              </label>
              <label>
                Dal
                <input
                  value={planning.branch}
                  onChange={(e) =>
                    setPlanning({ ...planning, branch: e.target.value })
                  }
                />
              </label>
              <label>
                Commit SHA
                <input
                  value={planning.commit}
                  onChange={(e) =>
                    setPlanning({ ...planning, commit: e.target.value })
                  }
                />
              </label>
              <label>
                GitHub PR bağlantısı
                <input
                  type="url"
                  value={planning.pr_url}
                  onChange={(e) =>
                    setPlanning({ ...planning, pr_url: e.target.value })
                  }
                />
              </label>
              <button>Planlama ekini kaydet</button>
            </fieldset>
          </form>
          {planning.pr_url.startsWith("https://github.com/") && (
            <a href={planning.pr_url} target="_blank" rel="noreferrer">
              Bağlı PR ↗
            </a>
          )}
          <h3>Yorumlar ve işlem geçmişi</h3>
          {detail.overlay_status === "unavailable" && (
            <p className="admin-empty">
              Yerel yorum ve işlem günlüğü kullanılamıyor.
            </p>
          )}
          <form
            className="admin-form"
            onSubmit={(e) => {
              const f = formValues(e);
              void mutate(`/api/admin/tasks/${detail.id}/comments`, {
                text: f.get("comment"),
              });
            }}
          >
            <label>
              Yeni yorum
              <textarea name="comment" required maxLength={6000} />
            </label>
            <button disabled={busy || detail.overlay_status === "unavailable"}>
              Yorum ekle
            </button>
          </form>
          <ol className="admin-history">
            {detail.overlay.map((r) => (
              <li key={r.id}>
                <small>
                  {new Date(r.created_at).toLocaleString("tr-TR")} · yerel
                  operatör · {r.kind}
                </small>
                <pre>
                  {r.kind === "comment"
                    ? String(r.body.text)
                    : JSON.stringify(r.body, null, 2)}
                </pre>
              </li>
            ))}
          </ol>
          <h3>Swarm olayları</h3>
          <ul>
            {detail.history.map((r) => (
              <li key={r.id}>
                {new Date(r.createdAt).toLocaleString("tr-TR")} · {r.eventType}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
