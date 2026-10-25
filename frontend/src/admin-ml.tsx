import { useEffect, useState } from "react";
import { request } from "./api";
import { ControlCenter } from "./control-center";

export type Metric = Partial<
  Record<
    | "epoch"
    | "train_loss"
    | "val_loss"
    | "precision"
    | "recall"
    | "map"
    | "map_50",
    number
  >
>;
type Experiment = {
  id: string;
  name: string;
  version: string;
  config: string;
  source: string;
  status: string;
  metrics: Metric[];
  infrastructure_smoke: boolean;
  artifact_url?: string;
  note?: string;
  error_type?: string;
};
export const metricValue = (value: number | undefined) =>
  value === undefined ? "Kullanılamıyor" : value.toFixed(4);

export function LossChart({ metrics }: { metrics: Metric[] }) {
  const points = metrics.filter((m) => m.epoch !== undefined);
  const values = points.flatMap((m) =>
    [m.train_loss, m.val_loss].filter((v): v is number => v !== undefined),
  );
  if (!values.length)
    return (
      <p className="admin-empty">
        Kayıp eğrisi kullanılamıyor · epoch/loss kaydı yok.
      </p>
    );
  const low = Math.min(...values),
    high = Math.max(...values);
  const start = Math.min(...points.map((m) => m.epoch!)),
    end = Math.max(...points.map((m) => m.epoch!));
  const x = (epoch: number) =>
    45 + ((epoch - start) / (end - start || 1)) * 510;
  const y = (value: number) => 160 - ((value - low) / (high - low || 1)) * 125;
  return (
    <figure className="loss-chart">
      <figcaption>
        Epoch bazında kayıp · <span className="train-key">Train</span> /{" "}
        <span className="val-key">Validation</span>
      </figcaption>
      <svg
        viewBox="0 0 600 200"
        role="img"
        aria-label={`Kayıp eğrisi, epoch ${start}–${end}, değer ${low.toFixed(4)}–${high.toFixed(4)}`}
      >
        <path
          d="M40 25 V165 H565"
          fill="none"
          stroke="currentColor"
          opacity=".25"
        />
        <text x="2" y="25">
          {high.toFixed(3)}
        </text>
        <text x="2" y="163">
          {low.toFixed(3)}
        </text>
        <text x="45" y="190">
          {start}
        </text>
        <text x="540" y="190">
          {end}
        </text>
        {(["train_loss", "val_loss"] as const).map((key, index) => {
          // Missing measurements split the line instead of interpolating unknown epochs.
          const segments: Metric[][] = [[]];
          for (const point of points) {
            if (point[key] === undefined) {
              segments.push([]);
              continue;
            }
            segments[segments.length - 1].push(point);
          }
          return (
            <g
              key={key}
              stroke={index ? "#a56436" : "#286e66"}
              fill={index ? "#a56436" : "#286e66"}
            >
              {segments.map((segment, i) => (
                <polyline
                  key={i}
                  fill="none"
                  strokeWidth="2"
                  points={segment
                    .map((m) => `${x(m.epoch!)},${y(m[key]!)}`)
                    .join(" ")}
                />
              ))}
              {points
                .filter((m) => m[key] !== undefined)
                .map((m, i) => (
                  <circle key={i} cx={x(m.epoch!)} cy={y(m[key]!)} r="3" />
                ))}
            </g>
          );
        })}
      </svg>
    </figure>
  );
}

export function AdminML() {
  const [data, setData] = useState<{
    records: Experiment[];
    unreadable_count: number;
    note: string;
  } | null>(null);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState("");
  const [epoch, setEpoch] = useState(0);
  useEffect(() => {
    let active = true;
    request("/api/admin/experiments")
      .then((value) => {
        if (active) {
          setData(value);
          setError("");
        }
      })
      .catch((e) => {
        if (active) {
          setData(null);
          setError(e.message);
        }
      });
    return () => {
      active = false;
    };
  }, [epoch]);
  const detail = data?.records.find((r) => r.id === selected);
  const last = detail?.metrics.at(-1);
  return (
    <div className="admin-page" aria-busy={!data && !error}>
      <section className="card">
        <div className="admin-heading">
          <div>
            <div className="eyebrow">ML / GERÇEK KAYNAKLAR</div>
            <h2>Deney arşivi</h2>
          </div>
          <button onClick={() => setEpoch((v) => v + 1)}>Arşivi yenile</button>
        </div>
        <p className="muted">
          Deney tarifleri ve yerel eğitim çıktıları. Eksik metrikler
          hesaplanmaz; model kilidi tamamlanmış eğitim sayılmaz.
        </p>
        {error && (
          <p role="alert" className="alert">
            {error}
          </p>
        )}
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Model / deney</th>
                <th>Sürüm / config</th>
                <th>Kaynak</th>
                <th>Durum</th>
                <th>Ölçümler</th>
              </tr>
            </thead>
            <tbody>
              {data?.records.map((row) => (
                <tr key={row.id}>
                  <td>
                    <button
                      className="admin-link"
                      onClick={() => setSelected(row.id)}
                    >
                      {row.name}
                    </button>
                    {row.infrastructure_smoke && (
                      <small>Sentetik altyapı smoke; saha metriği değil</small>
                    )}
                  </td>
                  <td>
                    <code>{row.version}</code>
                    <small>{row.config}</small>
                  </td>
                  <td>{row.source}</td>
                  <td>
                    <span className="badge">{row.status}</span>
                  </td>
                  <td>
                    {row.metrics.length
                      ? `${row.metrics.length} kayıt`
                      : "Kullanılamıyor"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {data && !data.records.length && (
          <p className="admin-empty">Deney çıktısı kullanılamıyor.</p>
        )}
        {!!data?.unreadable_count && (
          <p role="status">
            Okunamayan veya sınırı aşan dosya: {data.unreadable_count}
          </p>
        )}
        {detail && (
          <div className="admin-detail">
            <h3>{detail.name}</h3>
            <p>{detail.note}</p>
            <p>Hata türü: {detail.error_type || "Kullanılamıyor"}</p>
            <div className="admin-stats">
              {(["precision", "recall", "map", "map_50"] as const).map(
                (key) => (
                  <div key={key}>
                    <small>{key}</small>
                    <strong>{metricValue(last?.[key])}</strong>
                  </div>
                ),
              )}
            </div>
            <LossChart metrics={detail.metrics} />
            {detail.artifact_url && (
              <a href={detail.artifact_url} target="_blank" rel="noreferrer">
                Güvenli deney özeti (JSON) ↗
              </a>
            )}
          </div>
        )}
        <p className="muted">{data?.note}</p>
      </section>
      <ControlCenter />
    </div>
  );
}
