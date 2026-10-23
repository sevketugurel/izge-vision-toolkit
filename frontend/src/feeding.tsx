import { useCallback, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { request, type Row } from "./api";
import { getMotionVariants } from "./motion/presets";

// Stall feeding pipeline pages: timeline + daily summary, crop config preview, labeling.
export const STATE_LABELS: Record<string, string> = {
  yemde: "Yemde",
  yemde_degil: "Yemde değil",
  gorunmuyor: "Görünmüyor",
  skipped: "Atlandı",
};
const MESAFE: Record<string, string> = { yakin: "Yakın", orta: "Orta", uzak: "Uzak" };
const minutes = (s: number) => `${(s / 60).toFixed(1)} dk`;
const percent = (v: number | null | undefined) =>
  v == null ? "—" : `${Math.round(v * 100)}%`;
const usesDinov3 = (backbone?: string | null) =>
  !!backbone && backbone.toLowerCase().includes("dinov3");

function useLoad<T>(path: string | null, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(false);
  const reload = useCallback(() => {
    if (!path) {
      setData(null);
      return;
    }
    let active = true;
    setLoading(true);
    setError("");
    request(path)
      .then((d) => active && setData(d))
      .catch((e) => active && setError((e as Error).message))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [path, ...deps]);
  useEffect(() => reload(), [reload]);
  return { data, error, loading, reload };
}

// Shared enter/exit variants from motion presets; reduced motion zeroes duration and distance.
function useMotionVariants() {
  const reduced = useReducedMotion() ?? false;
  return getMotionVariants(reduced);
}

function stallCameras(cameras: Row[] | undefined) {
  return (cameras || []).filter((c) => c.config_key);
}

function CameraSelect({
  cameras,
  value,
  onChange,
}: {
  cameras: Row[];
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <label>
      Kamera
      <select
        aria-label="Yemde kamerası"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">Seçin</option>
        {cameras.map((c) => (
          <option key={c.id} value={c.id}>
            {c.name} · {c.config_key}
          </option>
        ))}
      </select>
    </label>
  );
}

function useCamera(cameras: Row[]) {
  const [camera, setCamera] = useState("");
  useEffect(() => {
    if (!cameras.some((c) => c.id === camera)) setCamera(cameras[0]?.id || "");
  }, [cameras.map((c) => c.id).join()]);
  return [camera, setCamera] as const;
}

export function ModelStatus({ base }: { base: string }) {
  const { data, error } = useLoad<Row>(`${base}/feeding/models`);
  if (error) return <div className="alert">{error}</div>;
  if (!data) return null;
  const latest = data.models?.[0];
  if (!latest)
    return (
      <div className="hint no-model" role="note">
        {data.message ||
          "Eğitilmiş model yok. Kesitleri etiketleyip modeli eğitin; model olmadan yemde sonucu üretilmez."}
      </div>
    );
  const overall = latest.metrics?.validation?.overall;
  return (
    <div className="model-status">
      <span className="badge">Model {latest.version}</span>
      <span>
        {latest.backbone} · {latest.label_count} etiket ·{" "}
        {new Date(latest.created_at).toLocaleString("tr-TR")}
      </span>
      <span>
        Gün bazlı doğrulama:{" "}
        {overall
          ? `doğruluk ${percent(overall.accuracy)}, makro F1 ${percent(overall.macro_f1)}`
          : latest.metrics?.validation?.message || "yok"}
      </span>
      {usesDinov3(latest.backbone) && <span className="built-with">Built with DINOv3</span>}
    </div>
  );
}

const STEPS = [1, 5, 10, 15, 30, 60, 120, 180];
const MINUTE = 60_000;

function Timeline({ data, fit }: { data: Row; fit: boolean }) {
  const dayStart = new Date(data.start).getTime(),
    dayEnd = new Date(data.end).getTime();
  const segs: Row[] = data.segments || [];
  let start = dayStart,
    end = dayEnd;
  if (fit && segs.length) {
    const lo = Math.min(...segs.map((s) => new Date(s.start).getTime()));
    const hi = Math.max(...segs.map((s) => new Date(s.end).getTime()));
    start = Math.max(dayStart, Math.floor(lo / (10 * MINUTE)) * 10 * MINUTE);
    end = Math.min(dayEnd, Math.ceil(hi / (10 * MINUTE)) * 10 * MINUTE);
  }
  const span = end - start || 1;
  const step = (STEPS.find((m) => span / (m * MINUTE) <= 12) || 180) * MINUTE;
  const ticks: number[] = [];
  for (let t = Math.ceil(start / step) * step; t <= end; t += step) ticks.push(t);
  const width = 960,
    row = 26,
    left = 70;
  const stalls: Row[] = data.stalls || [];
  const height = stalls.length * row + 26;
  const x = (t: string | number) =>
    left + ((new Date(t).getTime() - start) / span) * (width - left);
  const clock = (t: number | string) =>
    new Date(t).toLocaleTimeString("tr-TR", { timeZone: data.timezone, hour: "2-digit", minute: "2-digit" });
  return (
    <svg
      className="feeding-timeline"
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label="Bölme zaman çizelgesi"
    >
      {ticks.map((t) => (
        <g key={t}>
          <line x1={x(t)} x2={x(t)} y1={0} y2={height - 18} className="tick" />
          <text
            x={x(t)}
            y={height - 4}
            className="tick-label"
            textAnchor={x(t) > width - 24 ? "end" : x(t) < left + 24 ? "start" : "middle"}
          >
            {clock(t)}
          </text>
        </g>
      ))}
      {stalls.map((st, i) => (
        <g key={st.id} data-stall={st.id}>
          <text x={0} y={i * row + 17} className="stall-label">
            {st.id}
          </text>
          <rect x={left} y={i * row + 4} width={width - left} height={row - 8} className="unobserved" />
          {segs
            .filter((s: Row) => s.stall === st.id)
            .map((s: Row, j: number) => (
              <rect
                key={j}
                x={x(s.start)}
                y={i * row + 4}
                width={Math.max(0.5, x(s.end) - x(s.start))}
                height={row - 8}
                className={`state-${s.state}`}
              >
                <title>
                  {STATE_LABELS[s.state]} · {clock(s.start)}–{clock(s.end)} · güven {percent(s.confidence)}
                </title>
              </rect>
            ))}
        </g>
      ))}
    </svg>
  );
}

export function FeedingPage({
  base,
  cameras,
  day,
}: {
  base: string;
  cameras: Row[] | undefined;
  day: string;
}) {
  const list = stallCameras(cameras);
  const [camera, setCamera] = useCamera(list);
  const timeline = useLoad<Row>(
    camera ? `${base}/feeding/timeline?camera_id=${camera}&day=${day}` : null,
  );
  const daily = useLoad<Row>(
    camera ? `${base}/feeding/daily?camera_id=${camera}&start=${day}&end=${day}` : null,
  );
  const runs: Row[] = timeline.data?.runs || [];
  const demo = timeline.data?.is_demo || runs.some((r) => r.origin === "demo");
  const rows: Row[] = daily.data?.rows || [];
  const [fit, setFit] = useState(true);
  const variants = useMotionVariants();
  return (
    <>
      <section className="card">
        <div className="section-head">
          <div>
            <h2>Bölme bazlı yemde süresi</h2>
            <p>
              Saniyelik durum: yemde / yemde değil / görünmüyor · Gün sınırları{" "}
              {timeline.data?.timezone || "Europe/Istanbul"}
            </p>
          </div>
          {demo && <span className="badge demo-badge">DEMO</span>}
        </div>
        <ModelStatus base={base} />
        {!list.length ? (
          <div className="empty">
            <p>
              Kesit config'i bağlı kamera yok. Saha ayarlarında kamera eklerken bir kamera
              config'i seçin.
            </p>
          </div>
        ) : (
          <div className="button-row">
            <CameraSelect cameras={list} value={camera} onChange={setCamera} />
          </div>
        )}
        {(timeline.error || daily.error) && (
          <div className="alert">{timeline.error || daily.error}</div>
        )}
        {timeline.data && (
          <>
            {runs.length ? (
              <p className="muted">
                Kullanılan analiz:{" "}
                {runs
                  .map((r) =>
                    r.origin === "demo"
                      ? "SENTETİK DEMO analizi"
                      : `model ${r.model_version} (${r.backbone})`,
                  )
                  .filter((v, i, a) => a.indexOf(v) === i)
                  .join(", ")}
              </p>
            ) : (
              <p className="muted">Bu gün için tamamlanmış yemde analizi yok.</p>
            )}
            <div className="button-row">
              <label className="inline">
                <input type="checkbox" checked={fit} onChange={(e) => setFit(e.target.checked)} /> Kayıt
                aralığına yakınlaştır
              </label>
            </div>
            <Timeline data={timeline.data} fit={fit} />
            <div className="legend">
              {["yemde", "yemde_degil", "gorunmuyor"].map((s) => (
                <span key={s}>
                  <i className={`state-${s}`} /> {STATE_LABELS[s]}
                </span>
              ))}
              <span>
                <i className="unobserved" /> Kayıt yok
              </span>
            </div>
          </>
        )}
      </section>
      {camera && (
        <section className="card">
          <div className="section-head">
            <div>
              <h2>Günlük özet · {day}</h2>
              <p>Kısa (&lt; config eşiği) boşluklar yemde sayılır; görünmüyor süresi ayrı raporlanır.</p>
            </div>
            <div className="button-row">
              <a href={`${base}/feeding/daily.csv?camera_id=${camera}&start=${day}&end=${day}`}>
                CSV indir (bu gün) ↓
              </a>
              <a href={`${base}/feeding/daily.csv?camera_id=${camera}`}>CSV indir (tümü) ↓</a>
            </div>
          </div>
          {!rows.length ? (
            <div className="empty">
              <p>Bu gün için özet yok.</p>
            </div>
          ) : (
            <table aria-label="Günlük yemde özeti">
              <thead>
                <tr>
                  <th>Bölme</th>
                  <th>Sıra</th>
                  <th>Mesafe</th>
                  <th>Yemde</th>
                  <th>Beslenme periyodu</th>
                  <th>Görünmüyor</th>
                  <th>Gözlenen</th>
                  <th>Ort. güven</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <motion.tr
                    key={r.stall}
                    custom={i}
                    variants={variants}
                    initial="hidden"
                    animate="visible"
                  >
                    <td>
                      <strong>{r.stall}</strong>
                      {r.origins?.includes("demo") && <small>DEMO</small>}
                    </td>
                    <td>{r.sira || "—"}</td>
                    <td>{MESAFE[r.mesafe] || "—"}</td>
                    <td>{minutes(r.feeding_seconds)}</td>
                    <td>{r.bout_count}</td>
                    <td>{minutes(r.not_visible_seconds)}</td>
                    <td>{minutes(r.observed_seconds)}</td>
                    <td>{percent(r.mean_confidence)}</td>
                  </motion.tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}
    </>
  );
}

const MESAFE_COLOR: Record<string, string> = { yakin: "#7fd17a", orta: "#f2c94c", uzak: "#f2994a" };

export function CropPreviewPage({
  base,
  cameras,
  media,
}: {
  base: string;
  cameras: Row[] | undefined;
  media: Row[] | undefined;
}) {
  const configs = useLoad<Row[]>("/api/camera-configs");
  const list = Array.isArray(configs.data) ? configs.data : [];
  const [key, setKey] = useState("");
  useEffect(() => {
    if (!list.some((c) => c.key === key)) setKey(list.find((c) => c.valid)?.key || "");
  }, [list.map((c) => c.key).join()]);
  const detail = useLoad<Row>(
    key && list.find((c) => c.key === key)?.valid ? `/api/camera-configs/${key}` : null,
  );
  const config = detail.data;
  const camera = (cameras || []).find((c) => c.config_key === key);
  const frame = (media || [])
    .filter((m) => m.camera_id === camera?.id)
    .at(-1);
  const w = config?.reference_resolution?.width || 1920,
    h = config?.reference_resolution?.height || 1080;
  return (
    <section className="card">
      <div className="section-head">
        <div>
          <h2>Kesit önizleme</h2>
          <p>
            Kamera config dosyasındaki bölme kesitleri referans kare üzerinde. Düzenleme için config
            dosyasını değiştirin (sürükle-bırak editör v1.1).
          </p>
        </div>
      </div>
      {configs.error && <div className="alert">{configs.error}</div>}
      <label>
        Kamera config'i
        <select aria-label="Kamera config'i" value={key} onChange={(e) => setKey(e.target.value)}>
          <option value="">Seçin</option>
          {list.map((c) => (
            <option key={c.key} value={c.key}>
              {c.valid ? `${c.name} (${c.key})` : `${c.key} — GEÇERSİZ`}
            </option>
          ))}
        </select>
      </label>
      {list
        .filter((c) => !c.valid)
        .map((c) => (
          <div className="alert" key={c.key}>
            Geçersiz config: {c.error}
          </div>
        ))}
      {config && (
        <>
          {config.placeholder && (
            <div className="hint placeholder-warning" role="note">
              <strong>Koordinatlar yer tutucu — doldurulacak.</strong> Bu config ile kesit örnekleme
              ve model analizi reddedilir. Gerçek kesitleri yazıp <code>placeholder: false</code>{" "}
              yapın.
            </div>
          )}
          <svg
            className="crop-preview"
            viewBox={`0 0 ${w} ${h}`}
            role="img"
            aria-label="Kesit önizlemesi"
          >
            <rect width={w} height={h} className="frame-bg" />
            {frame && (
              <image
                href={`${base}/media/${frame.id}/file?kind=thumbnail`}
                width={w}
                height={h}
                preserveAspectRatio="none"
              />
            )}
            {config.stalls.map((st: Row) => (
              <g key={st.id} data-stall={st.id}>
                <polygon
                  points={st.points.map((p: number[]) => p.join(",")).join(" ")}
                  style={{ stroke: MESAFE_COLOR[st.mesafe] }}
                />
                <text x={st.points[0][0] + 6} y={st.points[0][1] + h / 30} style={{ fontSize: h / 30 }}>
                  {st.id}
                </text>
              </g>
            ))}
          </svg>
          <p className="muted">
            Referans çözünürlük {w}×{h} ·{" "}
            {frame
              ? `Arka plan: ${frame.filename} küçük resmi`
              : "Bu config'e bağlı kamerada kayıt yok; arka plan boş"}{" "}
            · Renk: yakın / orta / uzak
          </p>
          <table aria-label="Bölme kesitleri">
            <thead>
              <tr>
                <th>Bölme</th>
                <th>Sıra</th>
                <th>Mesafe</th>
                <th>Köşeler (px)</th>
              </tr>
            </thead>
            <tbody>
              {config.stalls.map((st: Row) => (
                <tr key={st.id}>
                  <td>{st.id}</td>
                  <td>{st.sira}</td>
                  <td>{MESAFE[st.mesafe]}</td>
                  <td>
                    <code>{st.points.map((p: number[]) => `(${p.join(", ")})`).join(" ")}</code>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </section>
  );
}

const KEYS: Record<string, string> = { "1": "yemde", "2": "yemde_degil", "3": "gorunmuyor", s: "skipped" };

function storedLabeler() {
  try {
    return localStorage.getItem("izge.labeler") || "";
  } catch {
    return "";
  }
}

export function LabelingPage({ base, cameras }: { base: string; cameras: Row[] | undefined }) {
  const list = stallCameras(cameras);
  const [camera, setCamera] = useCamera(list);
  const [labeler, setLabeler] = useState(storedLabeler);
  const [current, setCurrent] = useState<Row | null>(null),
    [progress, setProgress] = useState<Row | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const history = useRef<{ id: string; label: string }[]>([]);
  const [last, setLast] = useState("");
  const variants = useMotionVariants();
  const next = useCallback(async () => {
    if (!camera) return;
    try {
      const r = await request(`${base}/labeling/next?camera_id=${camera}`);
      setCurrent(r.sample);
      setProgress(r.progress);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [base, camera]);
  useEffect(() => {
    history.current = [];
    setLast("");
    next();
  }, [next]);
  const label = useCallback(
    async (value: string) => {
      if (!current || busy) return;
      if (!labeler.trim()) {
        setError("Etiketleyen adını girin");
        return;
      }
      setBusy(true);
      setError("");
      try {
        const row = await request(`${base}/labels`, {
          sample_id: current.id,
          label: value,
          labeler: labeler.trim(),
        });
        history.current.push({ id: row.id, label: value });
        setLast(`${current.stall_key} → ${STATE_LABELS[value]}`);
        await next();
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setBusy(false);
      }
    },
    [current, busy, labeler, base, next],
  );
  const undo = useCallback(async () => {
    const item = history.current.pop();
    if (!item || busy) return;
    setBusy(true);
    try {
      const r = await request(`${base}/labels/${item.id}/undo`, { labeler: labeler.trim() || "?" });
      setCurrent(r.sample);
      setLast(`Geri alındı: ${STATE_LABELS[item.label]}`);
      const p = await request(`${base}/labeling/next?camera_id=${camera}`);
      setProgress(p.progress);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }, [busy, base, labeler, camera]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.closest?.("input, select, textarea")) return;
      const k = e.key.toLowerCase();
      if (k === "z") {
        e.preventDefault();
        undo();
      } else if (KEYS[k]) {
        e.preventDefault();
        label(KEYS[k]);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [label, undo]);
  return (
    <section className="card labeling">
      <div className="section-head">
        <div>
          <h2>Hızlı kesit etiketleme</h2>
          <p>
            Kısayollar: <kbd>1</kbd> yemde · <kbd>2</kbd> yemde değil · <kbd>3</kbd> görünmüyor ·{" "}
            <kbd>Z</kbd> geri al · <kbd>S</kbd> atla. Sıra: bölme × saat × gündüz/gece tabakalı.
          </p>
        </div>
        {progress && (
          <span className="badge" aria-label="Etiketleme ilerlemesi">
            {progress.labeled}/{progress.total} etiketli · {progress.open} açık
          </span>
        )}
      </div>
      <div className="button-row">
        {list.length > 0 && <CameraSelect cameras={list} value={camera} onChange={setCamera} />}
        <label>
          Etiketleyen
          <input
            aria-label="Etiketleyen"
            value={labeler}
            maxLength={80}
            onChange={(e) => {
              setLabeler(e.target.value);
              try {
                localStorage.setItem("izge.labeler", e.target.value);
              } catch {
                /* per-browser convenience only */
              }
            }}
          />
        </label>
      </div>
      <AnimatePresence initial={false}>
        {error && (
          <motion.div
            key="error"
            role="alert"
            className="alert"
            variants={variants}
            initial="hidden"
            animate="visible"
            exit="exit"
          >
            {error}
          </motion.div>
        )}
      </AnimatePresence>
      {!list.length ? (
        <div className="empty">
          <p>Kesit config'i bağlı kamera yok.</p>
        </div>
      ) : !current ? (
        <div className="empty">
          <p>
            {progress?.total
              ? "Bu kamerada etiketlenecek örnek kalmadı."
              : "Henüz örnek yok. Video ve işler sayfasında bir kayıt seçip “Etiketleme örnekleri oluştur” düğmesine basın."}
          </p>
        </div>
      ) : (
        <motion.div
          className="label-stage"
          key={current.id}
          data-sample={current.id}
          variants={variants}
          initial="hidden"
          animate="visible"
        >
          <figure>
            <img
              className="label-crop"
              src={`${base}/crops/${current.id}/image?kind=crop`}
              alt={`Bölme ${current.stall_key} kesiti`}
            />
            <figcaption>
              Bölme <strong>{current.stall_key}</strong> ·{" "}
              {new Date(current.captured_at).toLocaleString("tr-TR", { timeZone: "Europe/Istanbul" })} ·{" "}
              {current.daynight === "gece" ? "Gece" : "Gündüz"}
            </figcaption>
          </figure>
          <figure>
            <img
              className="label-context"
              src={`${base}/crops/${current.id}/image?kind=context`}
              alt="Bağlam karesi"
            />
            <figcaption>Bağlam: sarı çerçeve kesit alanı</figcaption>
          </figure>
          <div className="label-buttons">
            {(["yemde", "yemde_degil", "gorunmuyor"] as const).map((s, i) => (
              <button key={s} className={`label-${s}`} disabled={busy} onClick={() => label(s)}>
                <kbd>{i + 1}</kbd> {STATE_LABELS[s]}
              </button>
            ))}
            <button disabled={busy} onClick={() => label("skipped")}>
              <kbd>S</kbd> Atla
            </button>
            <button disabled={busy || !history.current.length} onClick={undo}>
              <kbd>Z</kbd> Geri al
            </button>
          </div>
        </motion.div>
      )}
      <AnimatePresence mode="wait" initial={false}>
        {last && (
          <motion.p
            key={last}
            className="muted"
            role="status"
            aria-label="Son işlem"
            variants={variants}
            initial="hidden"
            animate="visible"
            exit="exit"
          >
            {last}
          </motion.p>
        )}
      </AnimatePresence>
      {progress && (
        <p className="muted">
          Etiket dağılımı: yemde {progress.counts?.yemde ?? 0} · yemde değil{" "}
          {progress.counts?.yemde_degil ?? 0} · görünmüyor {progress.counts?.gorunmuyor ?? 0}
        </p>
      )}
    </section>
  );
}

export function ConfigSelect() {
  const { data } = useLoad<Row[]>("/api/camera-configs");
  return (
    <label>
      Kesit config'i (yemde hattı)
      <select name="config_key" defaultValue="">
        <option value="">Yok</option>
        {(Array.isArray(data) ? data : [])
          .filter((c) => c.valid)
          .map((c) => (
            <option key={c.key} value={c.key}>
              {c.name} ({c.key}){c.placeholder ? " — yer tutucu" : ""}
            </option>
          ))}
      </select>
    </label>
  );
}

/** Recording start: read from the filename via the camera config regex, else required in the form. */
export function RecordedAtField({
  base,
  camera,
  filename,
}: {
  base: string;
  camera: Row | undefined;
  filename: string;
}) {
  const [parsed, setParsed] = useState<string | null>(null);
  const variants = useMotionVariants();
  useEffect(() => {
    setParsed(null);
    if (!camera?.config_key || !filename) return;
    let active = true;
    request(`${base}/cameras/${camera.id}/filename-time?name=${encodeURIComponent(filename)}`)
      .then((r) => active && setParsed(r.recorded_at))
      .catch(() => active && setParsed(null));
    return () => {
      active = false;
    };
  }, [base, camera?.id, filename]);
  if (!camera?.config_key) return null;
  return (
    <label>
      Kayıt başlangıcı (kamera saati, Europe/Istanbul)
      <input
        name="recorded_at"
        type="datetime-local"
        step={1}
        required={!parsed}
        aria-describedby="recorded-at-hint"
      />
      <motion.small
        id="recorded-at-hint"
        key={!filename ? "idle" : parsed ? "parsed" : "required"}
        variants={variants}
        initial="hidden"
        animate="visible"
      >
        {!filename
          ? "Dosya seçin; zaman önce dosya adından okunur."
          : parsed
            ? `Dosya adından okundu: ${new Date(parsed).toLocaleString("tr-TR", { timeZone: "Europe/Istanbul" })} (boş bırakabilirsiniz)`
            : "Dosya adından okunamadı; zorunlu."}
      </motion.small>
    </label>
  );
}
