import { useEffect, useState } from "react";
import { request, type Row } from "./api";
import { type SwarmTask, taskTitle } from "./admin-project";

type Resource = { status: string; rows: Row[]; message?: string };
type Operations = Record<
  "agents" | "providers" | "approvals" | "sessions" | "workflows" | "actions",
  Resource
>;
function ResourceTable({
  title,
  resource,
  fields,
}: {
  title: string;
  resource?: Resource;
  fields: [string, string][];
}) {
  return (
    <section className="card">
      <h2>{title}</h2>
      {resource?.status !== "available" ? (
        <p className="admin-empty">
          Kullanılamıyor · {resource?.message || "Kaynak bekleniyor"}
        </p>
      ) : !resource.rows.length ? (
        <p>Kaynak bağlı; kayıt yok.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                {fields.map(([key, label]) => (
                  <th key={key}>{label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {resource.rows.map((row, i) => (
                <tr key={row.id || row.agentId || i}>
                  {fields.map(([key]) => (
                    <td key={key}>
                      {row[key] === null || row[key] === undefined
                        ? "Kullanılamıyor"
                        : String(row[key])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
export function AdminOperations() {
  const [data, setData] = useState<Operations | null>(null),
    [tasks, setTasks] = useState<SwarmTask[] | null>(null);
  const [epoch, setEpoch] = useState(0),
    [error, setError] = useState("");
  const [sessions, setSessions] = useState<Resource>(),
    [runs, setRuns] = useState<Resource>(),
    [workflow, setWorkflow] = useState("");
  useEffect(() => {
    let active = true;
    setData(null);
    setTasks(null);
    setError("");
    request("/api/admin/operations")
      .then((value) => {
        if (active) setData(value);
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    request("/api/admin/tasks")
      .then((value) => {
        if (active) setTasks(value.tasks);
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    request("/api/admin/operations/sessions")
      .then((value) => {
        if (active) setSessions(value);
      })
      .catch(() => {
        if (active) setSessions(undefined);
      });
    return () => {
      active = false;
    };
  }, [epoch]);
  useEffect(() => {
    let active = true;
    setRuns(undefined);
    if (workflow)
      request(`/api/admin/operations/workflows/${workflow}/runs`)
        .then((value) => {
          if (active) setRuns(value);
        })
        .catch(() => {
          if (active) setRuns(undefined);
        });
    return () => {
      active = false;
    };
  }, [workflow, epoch]);
  const usage = tasks?.filter((t) => t.totalCostUsd !== undefined);
  return (
    <div className="admin-page" aria-busy={!data && !error}>
      <section className="card">
        <div className="admin-heading">
          <div>
            <div className="eyebrow">OPERASYON / CANLI KAYNAKLAR</div>
            <h2>Ajan çalışma alanı</h2>
          </div>
          <button onClick={() => setEpoch((v) => v + 1)}>
            Operasyonu yenile
          </button>
        </div>
        {error && (
          <p role="alert" className="alert">
            {error}
          </p>
        )}
        <div className="admin-stats">
          <div>
            <small>Çalışan görev · son 100 İzge görevi</small>
            <strong>
              {tasks
                ? tasks.filter((t) => t.status === "in_progress").length
                : "Kullanılamıyor"}
            </strong>
          </div>
          <div>
            <small>Başarısız görev</small>
            <strong>
              {tasks
                ? tasks.filter((t) => t.status === "failed").length
                : "Kullanılamıyor"}
            </strong>
          </div>
          <div>
            <small>
              Raporlanan görev maliyeti (USD) · kapsamlı toplam değil
            </small>
            <strong>
              {usage?.length
                ? usage.reduce((sum, t) => sum + t.totalCostUsd!, 0).toFixed(4)
                : "Kullanılamıyor"}
            </strong>
            <small>{usage?.length ?? 0} fiyatlanmış görev</small>
          </div>
        </div>
        <p className="muted">
          Provider kaydı ve ajan durumu kullanılabilirliği gösterir; kota
          garantisi değildir. Otomatik yeniden deneme, kurtarma ve başarısızlık
          nedeni yalnız kaynak bildirirse görünür.
        </p>
      </section>
      <ResourceTable
        title="Ajanlar"
        resource={data?.agents}
        fields={[
          ["name", "Ajan"],
          ["status", "Durum"],
          ["provider", "Provider"],
          ["lastActivityAt", "Son eylem zamanı"],
        ]}
      />
      <ResourceTable
        title="Claude / Codex sağlayıcı durumu"
        resource={data?.providers}
        fields={[
          ["name", "Ajan"],
          ["provider", "Provider"],
          ["status", "Durum"],
          ["lastCheckedAt", "Son kontrol"],
        ]}
      />
      <section className="card">
        <h2>Çalışan işler ve hata analizi</h2>
        {tasks ? (
          <ul className="admin-task-list">
            {tasks
              .filter((t) =>
                ["in_progress", "failed", "paused"].includes(t.status),
              )
              .map((t) => (
                <li key={t.id}>
                  <strong>{taskTitle(t)}</strong>
                  <p>
                    {t.status} · {t.provider || "Provider kullanılamıyor"} ·{" "}
                    {t.model || "Model kullanılamıyor"}
                  </p>
                  <p>{t.progress || "Son eylem kullanılamıyor"}</p>
                  <p>Hata nedeni: {t.failureReason || "Kullanılamıyor"}</p>
                  <p>
                    Önceki görev / kurtarma bağlantısı:{" "}
                    {t.parentTaskId || "Kullanılamıyor"}
                  </p>
                </li>
              ))}
          </ul>
        ) : (
          <p className="admin-empty">Görev kaynağı kullanılamıyor.</p>
        )}
      </section>
      <ResourceTable
        title="Bekleyen onaylar"
        resource={data?.approvals}
        fields={[
          ["title", "Onay"],
          ["status", "Durum"],
          ["createdAt", "Oluşturulma"],
        ]}
      />
      <ResourceTable
        title="Aktif oturumlar"
        resource={data?.sessions}
        fields={[
          ["agentId", "Ajan"],
          ["taskId", "Görev"],
          ["startedAt", "Başlangıç"],
          ["lastHeartbeatAt", "Son sinyal"],
        ]}
      />
      <ResourceTable
        title="İş akışları"
        resource={data?.workflows}
        fields={[
          ["name", "İş akışı"],
          ["enabled", "Etkin"],
          ["lastUpdatedAt", "Son güncelleme"],
        ]}
      />
      <section className="card">
        <label>
          İş akışı geçmişi
          <select
            value={workflow}
            onChange={(e) => setWorkflow(e.target.value)}
          >
            <option value="">İş akışı seçin</option>
            {data?.workflows.rows.map((row) => (
              <option key={row.id} value={row.id}>
                {row.name}
              </option>
            ))}
          </select>
        </label>
      </section>
      {workflow && (
        <ResourceTable
          title="İş akışı çalışmaları · son 30"
          resource={runs}
          fields={[
            ["id", "Çalışma"],
            ["status", "Durum"],
            ["startedAt", "Başlangıç"],
            ["finishedAt", "Bitiş"],
          ]}
        />
      )}
      <ResourceTable
        title="Oturum geçmişi · son 30 İzge oturumu"
        resource={sessions}
        fields={[
          ["id", "Oturum"],
          ["title", "Başlık"],
          ["status", "Durum"],
          ["lastUpdatedAt", "Son eylem"],
        ]}
      />
      <ResourceTable
        title="Son eylemler · son 50 Swarm olayı"
        resource={data?.actions}
        fields={[
          ["eventType", "Eylem"],
          ["agentId", "Ajan"],
          ["taskId", "Görev"],
          ["createdAt", "Zaman"],
        ]}
      />
    </div>
  );
}
