import { useEffect, useRef, useState } from "react";
import { request } from "./api";

type Config = {
  name: string;
  kind: string;
  dataset_version: string;
  budget: { max_wall_minutes: number; max_epochs: number };
  epochs: number;
  device: string;
  infrastructure_smoke: boolean;
};
type Metrics = Partial<
  Record<
    | "epoch"
    | "train_loss"
    | "val_loss"
    | "precision"
    | "recall"
    | "f1"
    | "duration_s",
    number
  >
>;
type Job = {
  id: string;
  status: string;
  created: number;
  config: Config;
  comparison_group: string;
  resume_available: boolean;
  metrics?: Metrics[];
  logs?: string[];
};
type Health = {
  status: string;
  device?: string;
  running_jobs?: number;
  message?: string;
  free_disk_bytes?: number;
};
type Dataset = {
  status: string;
  message?: string;
  inventory_counts?: Record<string, number>;
  validation_error_count?: number;
  reviewed_units?: number;
  labels_needed_count?: number | null;
  label_strata?: Record<string, number>;
  note?: string;
  versions: {
    dataset: string;
    version: string;
    status: string;
    sealed_test: boolean | null;
    splits: Record<string, number>;
    config_hash?: string;
  }[];
};
const stateNames: Record<string, string> = {
  connected: "Bağlı",
  unavailable: "Kullanılamıyor",
  auth_error: "Kimlik doğrulama hatası",
  queued: "Sırada",
  running: "Çalışıyor",
  completed: "Tamamlandı",
  failed: "Başarısız",
  cancelled: "İptal edildi",
  interrupted: "Kesintiye uğradı",
  timed_out: "Süre sınırına ulaştı",
  validated: "Doğrulandı",
  invalid: "Doğrulanamadı",
  validated_train_val: "Train/val doğrulandı",
};
const stateName = (state: string) => stateNames[state] || state;
const purpose = (config: Config) =>
  config.infrastructure_smoke
    ? "Altyapı smoke · sentetik sınıflandırma"
    : "Eğitim · saha başarısı doğrulanmadı";
const metricText = (value?: number) =>
  value === undefined ? "Ölçüm yok" : value.toFixed(4);
const endpoint = "/api/control";

export function ControlCenter() {
  const [health, setHealth] = useState<Health | null>(null);
  const [configs, setConfigs] = useState<Config[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [dataset, setDataset] = useState<Dataset | null>(null);
  const [selected, setSelected] = useState("");
  const [detail, setDetail] = useState<Job | null>(null);
  const [configName, setConfigName] = useState("");
  const [compareId, setCompareId] = useState("");
  const [comparison, setComparison] = useState<{
    infrastructure_smoke: boolean;
    jobs: (Job & { validation: Metrics })[];
  } | null>(null);
  const [error, setError] = useState("");
  const [datasetError, setDatasetError] = useState("");
  const [datasetEpoch, setDatasetEpoch] = useState(0);
  const [detailError, setDetailError] = useState("");
  const [busy, setBusy] = useState(false);
  const [epoch, setEpoch] = useState(0);
  const revision = useRef(0);
  const refresh = () => setEpoch((n) => n + 1);
  useEffect(() => {
    let active = true;
    // A failed poll clears stale actionable data. Mutations never run from effects.
    request(`${endpoint}/runner`)
      .then(async (h: Health) => {
        if (!active) return;
        setHealth(h);
        if (h.status !== "connected") {
          setJobs([]);
          setConfigs([]);
          return;
        }
        try {
          const [c, j] = await Promise.all([
            request(`${endpoint}/configs`),
            request(`${endpoint}/jobs`),
          ]);
          if (active) {
            setConfigs(c);
            setJobs(j.reverse());
            setError("");
          }
        } catch (e) {
          if (active) {
            setError((e as Error).message);
            setJobs([]);
            setConfigs([]);
          }
        }
      })
      .catch((e) => {
        if (active) {
          setError(e.message);
          setHealth(null);
          setJobs([]);
          setConfigs([]);
        }
      });
    const timer = window.setTimeout(refresh, 3000);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [epoch]);
  useEffect(() => {
    let active = true;
    request(`${endpoint}/datasets`)
      .then((d) => {
        if (active) {
          setDataset(d);
          setDatasetError("");
        }
      })
      .catch((e) => {
        if (active) {
          setDataset(null);
          setDatasetError(e.message);
        }
      });
    return () => {
      active = false;
    };
  }, [datasetEpoch]);
  useEffect(() => {
    const generation = ++revision.current;
    if (!selected || health?.status !== "connected") {
      setDetail(null);
      return;
    }
    request(`${endpoint}/jobs/${selected}`)
      .then((j) => {
        if (generation === revision.current) {
          setDetail(j);
          setDetailError("");
        }
      })
      .catch((e) => {
        if (generation === revision.current) {
          setDetail(null);
          setDetailError(e.message);
        }
      });
    return () => {
      revision.current++;
    };
  }, [selected, jobs, health?.status]);
  useEffect(() => {
    setComparison(null);
    setCompareId("");
  }, [selected]);
  async function mutate(path: string, data?: unknown) {
    setBusy(true);
    setError("");
    try {
      const job: Job = await request(`${endpoint}${path}`, data, "POST");
      setSelected(job.id);
      setDetail(job);
      setComparison(null);
      refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const approved = configs.find((c) => c.name === configName);
  const connected = health?.status === "connected";
  const comparable = jobs.filter(
    (j) =>
      j.id !== selected &&
      j.status === "completed" &&
      j.comparison_group === detail?.comparison_group,
  );
  return (
    <div className="control-center">
      <section className="card">
        <h2>Runner ve eğitim durumu</h2>
        <p role="status">
          {health ? stateName(health.status) : "Runner durumu alınamadı"}{" "}
          {health?.device &&
            `· ${health.device.toUpperCase()} · ${health.running_jobs} çalışan iş`}
        </p>
        {health?.message && <p>{health.message}</p>}
        <p className="muted">
          Köken: unavailable. Bağlantı ve altyapı ölçümleri, davranış modeli
          doğrulaması değildir. Eğitim yalnız kullanıcı başlatınca çalışır.
        </p>
        <p>
          Geliştirme kanıtı: bu oturumun canlı runner API ve yerel veri
          doğrulaması. Kod incelemesi, CI ve saha eğitimi burada doğrulanmaz.
        </p>
        <button
          onClick={() => {
            refresh();
            setDatasetEpoch((n) => n + 1);
          }}
        >
          Durumu yenile
        </button>
        {error && (
          <p className="alert" role="alert">
            {error}
          </p>
        )}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (approved) void mutate("/jobs", { config_name: approved.name });
          }}
        >
          <label>
            Onaylı eğitim tarifi
            <select
              value={configName}
              onChange={(e) => setConfigName(e.target.value)}
              disabled={!connected || busy}
            >
              <option value="">Tarif seçin</option>
              {configs.map((c) => (
                <option key={c.name} value={c.name}>
                  {c.name} · {c.dataset_version}
                </option>
              ))}
            </select>
          </label>
          <button
            className="primary"
            disabled={!approved || !connected || busy}
          >
            İşi başlat
          </button>
        </form>
        {approved && (
          <div>
            <p>
              {purpose(approved)} · {approved.device} · veri sürümü:{" "}
              {approved.dataset_version}
            </p>
            <p>
              Bütçe: {approved.budget.max_wall_minutes} dakika / en fazla{" "}
              {approved.budget.max_epochs} epoch. Tarif: {approved.epochs}{" "}
              epoch. Değerlendirme: yalnız validation.
            </p>
            {!approved.infrastructure_smoke && (
              <p>
                Önkoşul: operatörün etiketli embedding veya onaylı COCO paketi
                ve ağırlıkları. Envanter manifesti tek başına eğitim paketi
                değildir; runner önkoşulları denetler.
              </p>
            )}
            {approved.infrastructure_smoke && (
              <p>
                Sentetik altyapı deneyi. Sonuçlar hayvan davranışı modelinin
                başarısı değildir.
              </p>
            )}
          </div>
        )}
        {!configs.length && <p>Onaylı tarif alınamadı.</p>}
      </section>
      <section className="card">
        <h2>Deney geçmişi</h2>
        {!jobs.length ? (
          <p>Gösterilebilen iş yok.</p>
        ) : (
          <div className="control-table">
            <table>
              <thead>
                <tr>
                  <th>İş</th>
                  <th>Tarif / veri sürümü</th>
                  <th>Başlangıç</th>
                  <th>Durum</th>
                  <th>Amaç</th>
                </tr>
              </thead>
              <tbody>
                {jobs.map((j) => (
                  <tr key={j.id}>
                    <td>
                      <button
                        aria-pressed={selected === j.id}
                        onClick={() => {
                          setSelected(j.id);
                          setDetail(null);
                        }}
                      >
                        {j.id.slice(0, 8)}
                      </button>
                    </td>
                    <td>
                      {j.config.name} / {j.config.dataset_version}
                    </td>
                    <td>
                      {new Date(j.created * 1000).toLocaleString("tr-TR")}
                    </td>
                    <td>{stateName(j.status)}</td>
                    <td>{purpose(j.config)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {detailError && <p role="alert">{detailError}</p>}
        {detail && (
          <article aria-label="İş ayrıntısı">
            <h3>İş {detail.id}</h3>
            <p>
              {stateName(detail.status)} · {purpose(detail.config)}
            </p>
            <button
              disabled={
                busy ||
                !connected ||
                !["queued", "running"].includes(detail.status)
              }
              onClick={() => void mutate(`/jobs/${detail.id}/cancel`)}
            >
              İşi iptal et
            </button>{" "}
            <button
              disabled={busy || !connected || !detail.resume_available}
              onClick={() => void mutate(`/jobs/${detail.id}/resume`)}
            >
              Checkpoint ile sürdür
            </button>
            <h3>Gerçek çalışma metrikleri</h3>
            {!detail.metrics?.length ? (
              <p>Henüz ölçüm yok.</p>
            ) : (
              <div className="control-table">
                <table>
                  <thead>
                    <tr>
                      <th>Epoch</th>
                      <th>Train loss</th>
                      <th>Validation loss</th>
                      <th>Precision</th>
                      <th>Recall</th>
                      <th>F1</th>
                    </tr>
                  </thead>
                  <tbody>
                    {detail.metrics.map((m, i) => (
                      <tr key={i}>
                        <td>{m.epoch ?? "—"}</td>
                        <td>{metricText(m.train_loss)}</td>
                        <td>{metricText(m.val_loss)}</td>
                        <td>{metricText(m.precision)}</td>
                        <td>{metricText(m.recall)}</td>
                        <td>{metricText(m.f1)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <h3>Runner günlükleri</h3>
            <pre role="log" aria-label="Runner günlükleri">
              {detail.logs?.join("\n") || "Henüz günlük yok."}
            </pre>
            {detail.status === "completed" && (
              <>
                <h3>Validation karşılaştırması</h3>
                <p>
                  Aynı tarif ve veri sürümü; son kaydedilen epoch. Mühürlü test
                  kullanılmaz.
                </p>
                <label>
                  Karşılaştırılacak iş
                  <select
                    disabled={busy}
                    value={compareId}
                    onChange={(e) => {
                      setCompareId(e.target.value);
                      setComparison(null);
                    }}
                  >
                    <option value="">İş seçin</option>
                    {comparable.map((j) => (
                      <option key={j.id} value={j.id}>
                        {j.id.slice(0, 8)}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  disabled={!compareId || busy}
                  onClick={async () => {
                    const generation = revision.current;
                    setBusy(true);
                    setComparison(null);
                    try {
                      const result = await request(
                        `${endpoint}/comparison?left=${detail.id}&right=${compareId}`,
                      );
                      if (generation === revision.current)
                        setComparison(result);
                    } catch (e) {
                      setError((e as Error).message);
                    } finally {
                      setBusy(false);
                    }
                  }}
                >
                  Karşılaştır
                </button>
                {comparison && (
                  <div>
                    <p>
                      {comparison.infrastructure_smoke
                        ? "Altyapı smoke karşılaştırması; saha başarısı değildir."
                        : "Yalnız validation sonuçları."}
                    </p>
                    <div className="control-table">
                      <table>
                        <thead>
                          <tr>
                            <th>İş</th>
                            <th>Epoch</th>
                            <th>Validation loss</th>
                            <th>F1</th>
                          </tr>
                        </thead>
                        <tbody>
                          {comparison.jobs.map((j) => (
                            <tr key={j.id}>
                              <td>{j.id.slice(0, 8)}</td>
                              <td>{j.validation.epoch ?? "—"}</td>
                              <td>{metricText(j.validation.val_loss)}</td>
                              <td>{metricText(j.validation.f1)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}
              </>
            )}
          </article>
        )}
      </section>
      <section className="card">
        <h2>Veri kümesi ve etiket kapısı</h2>
        {datasetError && <p role="alert">{datasetError}</p>}
        {!dataset ? (
          <p>Veri kümesi durumu alınamadı.</p>
        ) : (
          <>
            <p>{stateName(dataset.status)} · Köken: unavailable</p>
            <p>{dataset.message || dataset.note}</p>
            {dataset.inventory_counts && (
              <p>
                Envanter:{" "}
                {Object.entries(dataset.inventory_counts)
                  .map(([kind, count]) => `${kind}: ${count}`)
                  .join(" · ")}
                . Doğrulama hatası: {dataset.validation_error_count}. İnsan
                incelemesiyle kapsanan birim/görev: {dataset.reviewed_units}.
                Eksik etiket birimi/görev:{" "}
                {dataset.labels_needed_count ?? "Bilinmiyor"}.
              </p>
            )}
            {!dataset.versions.length ? (
              <p>Manifest sürümü yok.</p>
            ) : (
              <ul>
                {dataset.versions.map((v) => (
                  <li key={`${v.dataset}/${v.version}`}>
                    <strong>
                      {v.dataset} / {v.version}
                    </strong>{" "}
                    · {stateName(v.status)} · train:{" "}
                    {v.splits.train ?? "Bilinmiyor"}, val:{" "}
                    {v.splits.val ?? "Bilinmiyor"} · test:{" "}
                    {v.sealed_test
                      ? "mühürlü, performans hesaplanmadı"
                      : "doğrulanmadı"}
                    <br />
                    {v.config_hash && `Yapılandırma özeti: ${v.config_hash}`}
                  </li>
                ))}
              </ul>
            )}
            {dataset.label_strata && (
              <details>
                <summary>Eksik etiket dağılımı</summary>
                <ul>
                  {Object.entries(dataset.label_strata).map(([key, count]) => (
                    <li key={key}>
                      {key}: {count}
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </>
        )}
      </section>
    </div>
  );
}
