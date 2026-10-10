import React, { useEffect, useState, useRef, type FormEvent } from "react";
import { createRoot } from "react-dom/client";
import "./style.css";
import { request, type Row } from "./api";
import { FeedingPage, CropPreviewPage, LabelingPage, ConfigSelect, RecordedAtField } from "./feeding";
const sections = [
  "İşletme özeti",
  "Saha ayarları",
  "Video ve işler",
  "Hayvan detayı",
  "Olaylar",
  "İnceleme",
  "Yemde süresi",
  "Kesit önizleme",
  "Etiketleme",
];
const labels: Row = {
  standing: "Ayakta",
  lying: "Yatıyor",
  feeding: "Yem yiyor",
  idle: "Dinleniyor",
  unknown: "Bilinmiyor",
  queued: "Sırada",
  running: "İşleniyor",
  succeeded: "Tamamlandı",
  failed: "Başarısız",
  confirmed: "Doğrulandı",
  rejected: "Reddedildi",
  needs_evidence: "Ek kanıt gerekli",
  pending: "İnceleme bekliyor",
  manual: "Manuel",
  model: "Model",
  unavailable: "Model bağlı değil",
  affected: "Etkilenen",
  intervening: "Müdahale eden",
  no_supported_observation: "Desteklenen gözlem yok",
  model_unavailable: "Model bağlı değil",
  clock_unverified: "Kamera saat uyumu doğrulanmadı",
  camera_conflict: "Kamera sonuçları çelişiyor",
  unknown_observation: "Gözlem belirsiz",
  connected: "Bağlı",
  disconnected: "Bağlantı yok",
  file_only: "Dosya kaynağı",
};
const tr = (s: string) => labels[s] || s;
const localDate = () =>
  new Intl.DateTimeFormat("sv-SE", { timeZone: "Europe/Istanbul" }).format(
    new Date(),
  );
const seconds = (n: number) =>
  n >= 3600 ? `${(n / 3600).toFixed(1)} sa` : `${Math.round(n)} sn`;
const when = (s: string) => new Date(s).toLocaleString("tr-TR");
function App() {
  const [config, setConfig] = useState<Row>({ app_name: "İzge" }),
    [farms, setFarms] = useState<Row[]>([]),
    [farm, setFarm] = useState(""),
    [page, setPage] = useState(0);
  const [data, setData] = useState<Record<string, Row[]>>({}),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [busy, setBusy] = useState(false);
  const [day, setDay] = useState(localDate),
    [report, setReport] = useState<Row | null>(null),
    [mediaId, setMediaId] = useState(""),
    [animalId, setAnimalId] = useState(""),
    [event, setEvent] = useState<Row | null>(null);
  const [animalEvents, setAnimalEvents] = useState<Row[]>([]);
  const [uploadCamera, setUploadCamera] = useState(""),
    [uploadName, setUploadName] = useState("");
  const stallUpload = !!data.cameras?.find((c) => c.id === uploadCamera)
    ?.config_key;
  const [epoch, setEpoch] = useState(0),
    [loaded, setLoaded] = useState(false);
  const generation = useRef(0);
  const base = `/api/v1/farms/${farm}`,
    selected = farms.find((f) => f.id === farm),
    media = data.media?.find((m) => m.id === mediaId),
    mediaCamera = data.cameras?.find((c) => c.id === media?.camera_id);
  const refresh = () => setEpoch((e) => e + 1);
  useEffect(() => {
    if (selected?.timezone)
      setDay(
        new Intl.DateTimeFormat("sv-SE", {
          timeZone: selected.timezone,
        }).format(new Date()),
      );
  }, [selected?.id, selected?.timezone]);
  useEffect(() => {
    Promise.all([request("/api/config"), request("/api/v1/farms")])
      .then(([c, f]) => {
        setConfig(c);
        document.title = c.app_name;
        setFarms(f);
        setFarm(f[0]?.id || "");
        setLoaded(true);
      })
      .catch((e) => {
        setError(e.message);
        setLoaded(true);
      });
  }, []);
  useEffect(() => {
    if (!farm) return;
    const gen = ++generation.current;
    setBusy(true);
    const resources = [
      "barns",
      "cameras",
      "stalls",
      "animals",
      "occupancies",
      "neighbors",
      "zones",
      "media",
      "runs",
      "jobs",
      "events",
      "annotations",
      "feeding-windows",
      "feed-presence",
    ];
    Promise.all(resources.map((r) => request(`${base}/${r}`)))
      .then((results) => {
        if (gen !== generation.current) return;
        setData(Object.fromEntries(resources.map((r, i) => [r, results[i]])));
      })
      .catch((e) => {
        if (gen === generation.current) setError(e.message);
      })
      .finally(() => {
        if (gen === generation.current) setBusy(false);
      });
    request(`${base}/report?day=${day}`)
      .then((r) => {
        if (gen === generation.current) setReport(r);
      })
      .catch((e) => {
        if (gen === generation.current) setError(e.message);
      });
  }, [farm, epoch, day]);
  useEffect(() => {
    if (!data.jobs?.some((j) => ["queued", "running"].includes(j.status)))
      return;
    const id = setInterval(refresh, 3000);
    return () => clearInterval(id);
  }, [data.jobs]);
  useEffect(() => {
    let active = true;
    setAnimalEvents([]);
    if (farm && animalId)
      request(`${base}/animals/${animalId}`)
        .then((detail) => {
          if (active) setAnimalEvents(detail.events);
        })
        .catch((e) => {
          if (active) setError(e.message);
        });
    return () => {
      active = false;
    };
  }, [farm, animalId, epoch]);
  const act = async (fn: () => Promise<unknown>) => {
    setError("");
    setNotice("");
    setBusy(true);
    try {
      await fn();
      setNotice("Kaydedildi");
      refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const post = (resource: string, body: unknown) =>
    act(() => request(`${base}/${resource}`, body));
  const form = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    return Object.fromEntries(new FormData(e.currentTarget)) as Record<
      string,
      string
    >;
  };
  const options = (resource: string) =>
    data[resource]?.map((r) => (
      <option key={r.id} value={r.id}>
        {r.number ? `${r.number} · ` : ""}
        {r.name}
      </option>
    ));
  const choose = (next: string) => {
    // Re-selecting the current farm must not invalidate its in-flight load.
    if (next === farm) return;
    generation.current++;
    setFarm(next);
    setData({});
    setReport(null);
    setMediaId("");
    setAnimalId("");
    setEvent(null);
    setError("");
  };
  const openEvent = async (id: string) => {
    const gen = generation.current;
    setError("");
    try {
      const detail = await request(`${base}/events/${id}`);
      if (gen === generation.current) setEvent(detail);
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const animal = report?.animals.find((a: Row) => a.animal_id === animalId);
  return (
    <div className="shell">
      <aside>
        <div className="brand">
          <span className="brandmark">ı</span>
          <div>
            <strong>{config.app_name}</strong>
            <small>Sahayı anla.</small>
          </div>
        </div>
        <div className="workspace-label">ÇALIŞMA ALANI</div>
        <select
          aria-label="İşletme"
          value={farm}
          onChange={(e) => choose(e.target.value)}
        >
          <option value="">İşletme seçin</option>
          {farms.map((f) => (
            <option value={f.id} key={f.id}>
              {f.name}
            </option>
          ))}
        </select>
        <nav>
          {sections.map((title, i) => (
            <button
              key={title}
              className={page === i ? "active" : ""}
              onClick={() => setPage(i)}
            >
              <span>{["◫", "⌘", "▷", "◎", "⚑", "✓", "▤", "▣", "⌨"][i]}</span>
              {title}
            </button>
          ))}
        </nav>
        <div className="sidebar-foot">
          <span className="dot" /> Yerel çalışma alanı
          <p>
            İnsan incelemesiyle desteklenen
            <br />
            video kayıt sistemi
          </p>
          <a href="/docs" target="_blank">
            API belgeleri ↗
          </a>
        </div>
      </aside>
      <main>
        <header>
          <div>
            <div className="eyebrow">SAHA GÖZLEM PLATFORMU</div>
            <h1>{sections[page]}</h1>
            <p>{selected?.name || "İlk işletmenizi oluşturarak başlayın."}</p>
          </div>
          <div className="header-actions">
            <span className="badge">
              CPU · {config.model_status || "Model bağlı değil"}
            </span>
            <label>
              Rapor günü
              <input
                type="date"
                value={day}
                onChange={(e) => setDay(e.target.value)}
              />
            </label>
          </div>
        </header>
        {selected?.is_demo && (
          <div className="demo-banner">
            <strong>SENTETİK DEMO</strong> Bu işletmenin görüntüleri ve analiz
            sonuçları yapay olarak üretilmiştir. Saha doğrulaması değildir.
          </div>
        )}
        {error && (
          <div role="alert" className="alert">
            {error}
            <button onClick={() => setError("")} aria-label="Hatayı kapat">
              ×
            </button>
          </div>
        )}
        {notice && (
          <div role="status" className="notice">
            {notice}
          </div>
        )}
        {busy && (
          <div role="status" className="loading">
            İşlem sürüyor…
          </div>
        )}
        {!loaded ? (
          <div className="empty">İşletmeler yükleniyor…</div>
        ) : !farm ? (
          <section className="card welcome">
            <h2>İlk saha kaydını oluşturun</h2>
            <p>
              Ahır, kamera, yer ve hayvanları tanımlayın. Videolarınızı orijinal
              zamanlarıyla inceleyin.
            </p>
            <form
              onSubmit={(e) => {
                const f = form(e);
                act(async () => {
                  const created = await request("/api/v1/farms", {
                    name: f.name,
                    timezone: f.timezone,
                  });
                  setFarms([...farms, created]);
                  choose(created.id);
                });
              }}
            >
              <label>
                İşletme adı
                <input name="name" required placeholder="Örn. Kuzey Çiftliği" />
              </label>
              <label>
                Saat dilimi
                <input
                  name="timezone"
                  defaultValue="Europe/Istanbul"
                  required
                />
              </label>
              <button className="primary">İşletme oluştur</button>
            </form>
            <p className="muted">
              Demo için terminalde:{" "}
              <code>docker compose exec api python -m izge.demo</code>
            </p>
          </section>
        ) : (
          <>
            {page === 0 && (
              <>
                <div className="stats">
                  <Stat
                    label="Kayıtlı hayvan"
                    value={data.animals?.length || 0}
                    detail="İşletmeye özgü kimlik"
                  />
                  <Stat
                    label="Kamera"
                    value={data.cameras?.length || 0}
                    detail="Video kaynağı"
                  />
                  <Stat
                    label="İnceleme adayı"
                    value={data.events?.length || 0}
                    detail="İnsan kararı bekleyen kanıt"
                  />
                  <Stat
                    label="Video"
                    value={data.media?.length || 0}
                    detail="Orijinal kayıt korunur"
                  />
                </div>
                <section className="card">
                  <div className="section-head">
                    <div>
                      <h2>Günlük görünüm</h2>
                      <p>
                        Yerleşim kapsamındaki geçmiş süre · {report?.timezone}
                      </p>
                    </div>
                    <span className="badge">
                      {report?.status === "measured"
                        ? "Ölçülen aralıklar"
                        : "Yetersiz veri"}
                    </span>
                  </div>
                  <p className="muted">
                    Otomatik rapor. Manuel etiketler ve doğrulanmış düzeltmeler
                    bu süreleri değiştirmez.
                  </p>
                  {!!report?.undated_runs_excluded && (
                    <div className="hint">
                      Kayıt saati doğrulanmayan {report.undated_runs_excluded}{" "}
                      analiz tarihli rapora dahil edilmedi.
                    </div>
                  )}
                  {!report?.animals.length ? (
                    <Empty text="Henüz hayvan kaydı yok. Saha ayarlarından hayvan ve tarihli yerleşim ekleyin." />
                  ) : (
                    <table>
                      <thead>
                        <tr>
                          <th>Hayvan</th>
                          <th>Ölçülen duruş</th>
                          <th>Bilinmeyen</th>
                          <th>Kapsam</th>
                          <th>Etkinlik</th>
                        </tr>
                      </thead>
                      <tbody>
                        {report.animals.map((a: Row) => (
                          <tr
                            key={a.animal_id}
                            onClick={() => {
                              setAnimalId(a.animal_id);
                              setPage(3);
                            }}
                          >
                            <td>
                              <strong>{a.number}</strong>
                              <small>{a.name}</small>
                            </td>
                            <td>
                              {seconds(a.dimensions.posture.measured_seconds)}
                            </td>
                            <td>
                              {seconds(a.dimensions.posture.unknown_seconds)}
                            </td>
                            <td>
                              <Coverage value={a.dimensions.posture.coverage} />
                            </td>
                            <td>
                              {Object.entries(a.dimensions.activity.seconds)
                                .map(
                                  ([k, v]) =>
                                    `${tr(k)}: ${seconds(v as number)}`,
                                )
                                .join(" · ") || "Yetersiz veri"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </section>
                <div className="two-col">
                  <section className="card">
                    <h2>Verinin sınırları görünür</h2>
                    <p>
                      Gerçek videolar CPU üzerinde çözülür. Bir davranış modeli
                      bağlı olmadığı için otomatik sonuçlar bilinmeyen olarak
                      kaydedilir.
                    </p>
                    <button onClick={() => setPage(2)}>Video yükle →</button>
                  </section>
                  <section className="card">
                    <h2>Kayıt ve inceleme ayrı</h2>
                    <p>
                      Her düzeltme yeni bir kayıt oluşturur. Önceki etiketler,
                      zaman eşlemeleri ve analiz bağlamları korunur.
                    </p>
                    <a href={`${base}/export?format=json`}>JSON dışa aktar ↗</a>
                    <a href={`${base}/export?format=csv`}>CSV dışa aktar ↗</a>
                  </section>
                </div>
              </>
            )}
            {page === 1 && (
              <>
                <div className="two-col">
                  <section className="card">
                    <h2>Saha yapısı</h2>
                    <form
                      onSubmit={(e) => {
                        const f = form(e);
                        post("barns", { name: f.name });
                      }}
                    >
                      <label>
                        Ahır adı
                        <input name="name" required />
                      </label>
                      <button>Ahır ekle</button>
                    </form>
                    <form
                      onSubmit={(e) => {
                        const f = form(e);
                        post("cameras", {
                          name: f.name,
                          barn_id: f.barn_id,
                          config_key: f.config_key || null,
                        });
                      }}
                    >
                      <label>
                        Kamera adı
                        <input name="name" required />
                      </label>
                      <label>
                        Ahır
                        <select name="barn_id" required>
                          <option value="">Seçin</option>
                          {options("barns")}
                        </select>
                      </label>
                      <ConfigSelect />
                      <button>Kamera ekle</button>
                    </form>
                    <form
                      onSubmit={(e) => {
                        const f = form(e);
                        post("stalls", f);
                      }}
                    >
                      <label>
                        Yer adı
                        <input name="name" required />
                      </label>
                      <label>
                        Ahır
                        <select name="barn_id" required>
                          <option value="">Seçin</option>
                          {options("barns")}
                        </select>
                      </label>
                      <button>Yer ekle</button>
                    </form>
                  </section>
                  <section className="card">
                    <h2>Hayvan ve yerleşim</h2>
                    <form
                      onSubmit={(e) => {
                        const f = form(e);
                        post("animals", f);
                      }}
                    >
                      <label>
                        Görünen numara
                        <input name="number" required />
                      </label>
                      <label>
                        Ad
                        <input name="name" />
                      </label>
                      <button>Hayvan ekle</button>
                    </form>
                    <form
                      onSubmit={(e) => {
                        const f = form(e);
                        post("occupancies", {
                          animal_id: f.animal_id,
                          stall_id: f.stall_id,
                          start: new Date(f.start).toISOString(),
                          end: f.end ? new Date(f.end).toISOString() : null,
                        });
                      }}
                    >
                      <label>
                        Hayvan
                        <select name="animal_id" required>
                          <option value="">Seçin</option>
                          {options("animals")}
                        </select>
                      </label>
                      <label>
                        Yer
                        <select name="stall_id" required>
                          <option value="">Seçin</option>
                          {options("stalls")}
                        </select>
                      </label>
                      <label>
                        Başlangıç (cihaz saati)
                        <input name="start" type="datetime-local" required />
                      </label>
                      <label>
                        Bitiş (isteğe bağlı)
                        <input name="end" type="datetime-local" />
                      </label>
                      <button>Yerleşim kaydet</button>
                    </form>
                    <form
                      onSubmit={(e) => {
                        const f = form(e);
                        post("move", {
                          animal_id: f.animal_id,
                          stall_id: f.stall_id,
                          at: new Date(f.at).toISOString(),
                        });
                      }}
                    >
                      <label>
                        Hayvan
                        <select name="animal_id" required>
                          {options("animals")}
                        </select>
                      </label>
                      <label>
                        Yeni yer
                        <select name="stall_id" required>
                          {options("stalls")}
                        </select>
                      </label>
                      <label>
                        Değişim saati
                        <input name="at" type="datetime-local" required />
                      </label>
                      <button>Yer değiştir</button>
                    </form>
                  </section>
                </div>
                <section className="card">
                  <h2>Yerleşim geçmişi</h2>
                  <table>
                    <thead>
                      <tr>
                        <th>Hayvan</th>
                        <th>Yer</th>
                        <th>Başlangıç</th>
                        <th>Bitiş</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.occupancies?.map((o) => (
                        <tr key={o.id}>
                          <td>
                            {
                              data.animals?.find((a) => a.id === o.animal_id)
                                ?.number
                            }
                          </td>
                          <td>
                            {
                              data.stalls?.find((a) => a.id === o.stall_id)
                                ?.name
                            }
                          </td>
                          <td>{when(o.start)}</td>
                          <td>{o.end ? when(o.end) : "Devam ediyor"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </section>
                <ZoneEditor base={base} data={data} post={post} />
                <section className="card">
                  <h2>Kamera kaynak durumu</h2>
                  <p>
                    Dosya worker'ı canlı RTSP oturumu başlatmaz. Canlı saha
                    bağlantısı henüz doğrulanmadı.
                  </p>
                  {data.cameras?.map((camera) => (
                    <div key={camera.id} className="timeline-row">
                      <strong>{camera.name}</strong>
                      <span>{tr(camera.health?.state || "file_only")}</span>
                      <span>
                        Son kare:{" "}
                        {camera.health?.last_frame
                          ? when(camera.health.last_frame)
                          : "Henüz yok"}
                      </span>
                      {camera.health?.error && (
                        <span>{camera.health.error}</span>
                      )}
                    </div>
                  ))}
                </section>
                <div className="two-col">
                  <section className="card">
                    <h2>Komşuluk</h2>
                    <form
                      onSubmit={(e) => {
                        const f = form(e);
                        post("neighbors", f);
                      }}
                    >
                      <label>
                        Ahır
                        <select required name="barn_id">
                          {options("barns")}
                        </select>
                      </label>
                      <label>
                        Birinci yer
                        <select required name="left_id">
                          {options("stalls")}
                        </select>
                      </label>
                      <label>
                        İkinci yer
                        <select required name="right_id">
                          {options("stalls")}
                        </select>
                      </label>
                      <button>Komşuluk ekle</button>
                    </form>
                    {data.neighbors?.map((n) => (
                      <p key={n.id}>
                        {data.stalls?.find((s) => s.id === n.left_id)?.name} ↔{" "}
                        {data.stalls?.find((s) => s.id === n.right_id)?.name}
                      </p>
                    ))}
                  </section>
                  <section className="card">
                    <h2>Öğün ve yem bilgisi</h2>
                    <p className="muted">
                      Öğün takvimi yem varlığı anlamına gelmez.
                    </p>
                    <form
                      onSubmit={(e) => {
                        const f = form(e);
                        post("feeding-windows", {
                          barn_id: f.barn_id,
                          start: new Date(f.start).toISOString(),
                          end: new Date(f.end).toISOString(),
                        });
                      }}
                    >
                      <label>
                        Ahır
                        <select name="barn_id" required>
                          {options("barns")}
                        </select>
                      </label>
                      <TimeFields />
                      <button>Öğün ekle</button>
                    </form>
                    <form
                      onSubmit={(e) => {
                        const f = form(e);
                        post("feed-presence", {
                          stall_id: f.stall_id,
                          start: new Date(f.start).toISOString(),
                          end: new Date(f.end).toISOString(),
                          present:
                            f.present === "unknown"
                              ? null
                              : f.present === "true",
                        });
                      }}
                    >
                      <label>
                        Yer
                        <select name="stall_id" required>
                          {options("stalls")}
                        </select>
                      </label>
                      <TimeFields />
                      <label>
                        Yem
                        <select name="present">
                          <option value="true">Var</option>
                          <option value="false">Yok</option>
                          <option value="unknown">Bilinmiyor</option>
                        </select>
                      </label>
                      <button>Yem varlığı kaydet</button>
                    </form>
                  </section>
                </div>
                <section className="card">
                  <h2>Yeni işletme</h2>
                  <form
                    onSubmit={(e) => {
                      const f = form(e);
                      act(async () => {
                        const created = await request("/api/v1/farms", {
                          name: f.name,
                        });
                        setFarms([...farms, created]);
                        choose(created.id);
                      });
                    }}
                  >
                    <label>
                      Ad
                      <input name="name" required />
                    </label>
                    <button>İşletme oluştur</button>
                  </form>
                </section>
              </>
            )}
            {page === 2 && (
              <>
                <section className="card">
                  <div className="section-head">
                    <div>
                      <h2>Bir kaydı incelemeye alın</h2>
                      <p>
                        MP4, MOV, MKV, AVI · En fazla{" "}
                        {Math.round(
                          (config.max_upload_bytes || 2147483648) / 1024 ** 3,
                        )}{" "}
                        GiB
                      </p>
                    </div>
                    <span className="badge">Orijinal dosya korunur</span>
                  </div>
                  <form
                    onSubmit={(e) => {
                      e.preventDefault();
                      const f = new FormData(e.currentTarget);
                      const camera = f.get("camera_id");
                      f.delete("camera_id");
                      if (!f.get("recorded_at")) f.delete("recorded_at");
                      act(() =>
                        request(`${base}/media?camera_id=${camera}`, f),
                      );
                    }}
                  >
                    <label>
                      Kamera
                      <select
                        name="camera_id"
                        required
                        value={uploadCamera}
                        onChange={(e) => setUploadCamera(e.target.value)}
                      >
                        <option value="">Seçin</option>
                        {options("cameras")}
                      </select>
                    </label>
                    <label className="upload">
                      Video dosyası
                      <input
                        type="file"
                        name="file"
                        accept={
                          stallUpload ? ".mp4" : ".mp4,.mov,.mkv,.avi"
                        }
                        required
                        onChange={(e) =>
                          setUploadName(e.target.files?.[0]?.name || "")
                        }
                      />
                    </label>
                    <RecordedAtField
                      base={base}
                      camera={data.cameras?.find((c) => c.id === uploadCamera)}
                      filename={uploadName}
                    />
                    <button className="primary" disabled={busy}>
                      Yükle ve doğrula
                    </button>
                  </form>
                </section>
                <div className="media-grid">
                  {data.media?.map((m) => (
                    <button
                      className={`media-card ${mediaId === m.id ? "selected" : ""}`}
                      key={m.id}
                      onClick={() => setMediaId(m.id)}
                    >
                      <img
                        src={`${base}/media/${m.id}/file?kind=thumbnail`}
                        alt="Video karesi"
                      />
                      <div>
                        <strong>{m.filename}</strong>
                        <p>
                          {seconds(m.metadata.duration)} · {m.metadata.width}×
                          {m.metadata.height}
                        </p>
                        <span className="badge">
                          {m.origin === "demo"
                            ? "SENTETİK DEMO"
                            : m.current_time_version
                              ? "Kayıt saati doğrulandı"
                              : "Kayıt saati bekleniyor"}
                        </span>
                      </div>
                    </button>
                  ))}
                </div>
                {!data.media?.length && (
                  <Empty text="Henüz video yok. Bir kamera oluşturup ilk kaydı yükleyin." />
                )}
                {media && (
                  <section className="card">
                    <h2>{media.filename}</h2>
                    <video
                      controls
                      src={`${base}/media/${media.id}/file`}
                      key={media.id}
                    />
                    <p>
                      Yükleme: {when(media.created_at)} · Kayıt zamanı sürümü:{" "}
                      {media.current_time_version || "Doğrulanmadı"}
                    </p>
                    <form
                      onSubmit={(e) => {
                        const f = form(e);
                        post(`media/${media.id}/time`, {
                          recorded_at: new Date(f.recorded_at).toISOString(),
                          note: f.note,
                        });
                      }}
                    >
                      <label>
                        Kayıt başlangıcı (cihaz saat dilimi)
                        <input
                          name="recorded_at"
                          type="datetime-local"
                          required
                        />
                      </label>
                      <label>
                        Doğrulama kaynağı
                        <input
                          name="note"
                          required
                          placeholder="Kamera saati kontrol edildi…"
                        />
                      </label>
                      <button>Saati doğrula</button>
                    </form>
                    <div className="button-row">
                      <button
                        className="primary"
                        onClick={() =>
                          post("runs", {
                            media_id: media.id,
                            analyzer:
                              media.origin === "demo"
                                ? "demo-1"
                                : config.default_analyzer || "unavailable-1",
                          })
                        }
                      >
                        Analizi başlat
                      </button>
                      {mediaCamera?.config_key && (
                        <>
                          <button
                            onClick={() =>
                              post("labeling/samples", { media_id: media.id })
                            }
                          >
                            Etiketleme örnekleri oluştur
                          </button>
                          <button
                            onClick={() =>
                              post("runs", {
                                media_id: media.id,
                                analyzer:
                                  media.origin === "demo"
                                    ? "feeding-demo-1"
                                    : "feeding-model-1",
                              })
                            }
                          >
                            Yemde analizini başlat
                          </button>
                        </>
                      )}
                      <button onClick={() => setPage(5)}>Etiketle →</button>
                      <a href={`${base}/media/${media.id}/file?kind=original`}>
                        Orijinali aç ↗
                      </a>
                    </div>
                    <p className="muted">
                      {media.origin === "demo"
                        ? "Deterministik sentetik analiz."
                        : "Model bağlı değil. Kareler gerçekten çözülür; davranış sonuçları bilinmeyen kalır."}
                    </p>
                  </section>
                )}
                <section className="card">
                  <h2>İş kuyruğu</h2>
                  <table>
                    <thead>
                      <tr>
                        <th>Analiz</th>
                        <th>Durum</th>
                        <th>İlerleme</th>
                        <th>Deneme</th>
                        <th>Açıklama</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.jobs?.map((j) => (
                        <tr key={j.id}>
                          <td>{j.run_id.slice(0, 8)}</td>
                          <td>{tr(j.status)}</td>
                          <td>
                            <Coverage value={j.progress} />
                          </td>
                          <td>{j.attempts}/3</td>
                          <td>{j.error || "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </section>
              </>
            )}
            {page === 3 && (
              <>
                <section className="card">
                  <h2>Hayvan seçin</h2>
                  <select
                    aria-label="Hayvan detayı"
                    value={animalId}
                    onChange={(e) => setAnimalId(e.target.value)}
                  >
                    <option value="">Seçin</option>
                    {options("animals")}
                  </select>
                </section>
                {animal ? (
                  <>
                    <div className="stats">
                      <Stat
                        label={`${animal.number} · ${animal.name}`}
                        value={seconds(
                          animal.dimensions.posture.denominator_seconds,
                        )}
                        detail="Yerleşim ve geçmiş gün kapsamı"
                      />
                      <Stat
                        label="Ölçülen duruş"
                        value={seconds(
                          animal.dimensions.posture.measured_seconds,
                        )}
                        detail="Desteklenen aralıklar"
                      />
                      <Stat
                        label="Bilinmeyen"
                        value={seconds(
                          animal.dimensions.posture.unknown_seconds,
                        )}
                        detail="Son gözlem uzatılmaz"
                      />
                    </div>
                    <section className="card">
                      <h2>Zaman çizelgesi</h2>
                      <Coverage value={animal.dimensions.posture.coverage} />
                      <p>
                        {Object.entries(
                          animal.dimensions.posture.unknown_reasons,
                        )
                          .map(([k, v]) => `${tr(k)}: ${seconds(v as number)}`)
                          .join(" · ")}
                      </p>
                      {animal.timeline.length ? (
                        <div className="timeline">
                          {animal.timeline
                            .slice(0, 300)
                            .map((t: Row, i: number) => (
                              <div key={i} className="timeline-row">
                                <span>
                                  {new Date(t.start * 1000).toLocaleTimeString(
                                    "tr-TR",
                                    { timeZone: report?.timezone },
                                  )}
                                </span>
                                <span
                                  className={`timeline-bar ${t.dimension}`}
                                />
                                <strong>{tr(t.label)}</strong>
                                <span>{seconds(t.end - t.start)}</span>
                                <small>
                                  {t.origin === "demo"
                                    ? "SENTETİK DEMO"
                                    : tr(t.origin)}
                                </small>
                              </div>
                            ))}
                        </div>
                      ) : (
                        <Empty text="Desteklenen gözlem aralığı yok. Bilinmeyen süre davranış olarak yorumlanamaz." />
                      )}
                      {animal.timeline.length > 300 && (
                        <p className="muted">
                          İlk 300 aralık gösteriliyor. Tüm aralıklar JSON/CSV
                          dışa aktarımında korunur.
                        </p>
                      )}
                    </section>
                    <section className="card">
                      <h2>Bu hayvanın olayları</h2>
                      {animalEvents.length ? (
                        animalEvents.map((e) => (
                          <button
                            key={e.id}
                            onClick={() => {
                              setPage(4);
                              openEvent(e.id);
                            }}
                          >
                            {e.sufficient
                              ? "Muhtemel engellenme"
                              : "İnceleme adayı"}{" "}
                            · {e.start_offset.toFixed(1)}–
                            {e.end_offset.toFixed(1)} sn
                          </button>
                        ))
                      ) : (
                        <p>Bu hayvana bağlı olay yok.</p>
                      )}
                    </section>
                  </>
                ) : (
                  <Empty text="Zaman çizelgesi ve kapsamı görmek için bir hayvan seçin." />
                )}
              </>
            )}
            {page === 4 && (
              <>
                <section className="card">
                  <h2>Muhtemel yem erişimi engellenmesi</h2>
                  <p>
                    Yaklaşma → müdahale → geri çekilme dizisi. Tek yaklaşma
                    alarm üretmez.
                  </p>
                  {!data.events?.length ? (
                    <Empty text="Henüz olay yok. Yeterli kanıt oluştuğunda burada görünür." />
                  ) : (
                    <div className="event-list">
                      {data.events.map((e) => (
                        <button key={e.id} onClick={() => openEvent(e.id)}>
                          <span
                            className={`event-dot ${e.sufficient ? "sufficient" : ""}`}
                          />
                          <div>
                            <strong>
                              {e.sufficient
                                ? "Muhtemel engellenme"
                                : "Yetersiz kanıtlı inceleme adayı"}
                            </strong>
                            <p>
                              {e.start_offset.toFixed(1)}–
                              {e.end_offset.toFixed(1)} sn ·{" "}
                              {e.origin === "demo" ? "SENTETİK DEMO" : e.origin}
                            </p>
                          </div>
                          <span>
                            {tr(e.review_status || "pending")} · İncele →
                          </span>
                        </button>
                      ))}
                    </div>
                  )}
                </section>
                {event && (
                  <section className="card">
                    <h2>Olay kanıtı</h2>
                    <span className="badge">{tr(event.review_status)}</span>
                    <p>
                      Katılımcılar:{" "}
                      {event.participants
                        .map(
                          (p: Row) =>
                            `${data.animals?.find((a) => a.id === p.animal_id)?.number || p.animal_id} (${tr(p.role)})`,
                        )
                        .join(", ") || "Kimlik belirsiz"}
                    </p>
                    <p>
                      Kaynak offseti: {event.start_offset}–{event.end_offset} sn
                      · Kural {event.details.rule_version}
                    </p>
                    <p>
                      Kaynak: {event.source.filename} · Kayıt saati sürümü:{" "}
                      {event.source.time_version || "Doğrulanmadı"}
                    </p>
                    {event.evidence.map((ev: Row) => (
                      <div key={ev.id}>
                        <video
                          controls
                          src={`${base}/evidence/${ev.id}/file`}
                        />
                        <small>
                          Klip kaynak aralığı: {ev.start_offset}–{ev.end_offset}{" "}
                          sn
                        </small>
                      </div>
                    ))}
                    <form
                      onSubmit={(e) => {
                        const f = form(e);
                        act(async () => {
                          await request(
                            `${base}/events/${event.id}/reviews`,
                            f,
                          );
                          await openEvent(event.id);
                        });
                      }}
                    >
                      <label>
                        Karar
                        <select name="verdict">
                          <option value="confirmed">Doğrulandı</option>
                          <option value="rejected">Reddedildi</option>
                          <option value="needs_evidence">
                            Ek kanıt gerekli
                          </option>
                        </select>
                      </label>
                      <label>
                        İnceleme notu
                        <textarea name="note" required />
                      </label>
                      <button className="primary">İnceleme ekle</button>
                    </form>
                    <h3>İnceleme geçmişi</h3>
                    {event.reviews.map((r: Row) => (
                      <p key={r.id}>
                        {when(r.created_at)} · {tr(r.verdict)} · {r.note}
                      </p>
                    ))}
                  </section>
                )}
              </>
            )}
            {page === 5 && (
              <>
                <section className="card">
                  <h2>Video üzerinde manuel etiket</h2>
                  <p>
                    Etiketler orijinal video offsetine bağlıdır. Kayıt saati
                    bilinmese de etiketleyebilirsiniz.
                  </p>
                  <select
                    aria-label="Etiketlenecek video"
                    value={mediaId}
                    onChange={(e) => setMediaId(e.target.value)}
                  >
                    <option value="">Video seçin</option>
                    {data.media?.map((m) => (
                      <option key={m.id} value={m.id}>
                        {m.filename}
                      </option>
                    ))}
                  </select>
                  {media && (
                    <>
                      <video
                        controls
                        src={`${base}/media/${media.id}/file`}
                        key={media.id}
                      />
                      <form
                        onSubmit={(e) => {
                          const f = form(e);
                          post("annotations", {
                            media_id: media.id,
                            animal_id: f.animal_id || null,
                            start_offset: Number(f.start_offset),
                            end_offset: Number(f.end_offset),
                            label: f.label,
                            note: f.note,
                            supersedes_id: f.supersedes_id || null,
                          });
                        }}
                      >
                        <label>
                          Başlangıç (sn)
                          <input
                            name="start_offset"
                            type="number"
                            step="0.001"
                            min="0"
                            max={media.metadata.duration}
                            required
                          />
                        </label>
                        <label>
                          Bitiş (sn)
                          <input
                            name="end_offset"
                            type="number"
                            step="0.001"
                            min="0"
                            max={media.metadata.duration}
                            required
                          />
                        </label>
                        <label>
                          Hayvan
                          <select name="animal_id">
                            <option value="">Belirsiz</option>
                            {options("animals")}
                          </select>
                        </label>
                        <label>
                          Etiket
                          <input
                            name="label"
                            required
                            placeholder="Yem yeme / görünmez / müdahale…"
                          />
                        </label>
                        <label>
                          Önceki kaydı düzelt
                          <select name="supersedes_id">
                            <option value="">Yeni etiket</option>
                            {data.annotations
                              ?.filter((a) => a.media_id === media.id)
                              .map((a) => (
                                <option key={a.id} value={a.id}>
                                  {a.start_offset}–{a.end_offset} · {a.label}
                                </option>
                              ))}
                          </select>
                        </label>
                        <label>
                          Not
                          <textarea name="note" />
                        </label>
                        <button className="primary">Etiketi kaydet</button>
                      </form>
                    </>
                  )}
                </section>
                <section className="card">
                  <div className="section-head">
                    <h2>Etiket geçmişi</h2>
                    <div>
                      <a href={`${base}/export?format=json`}>JSON ↗</a>
                      <a href={`${base}/export?format=csv`}>CSV ↗</a>
                    </div>
                  </div>
                  <table>
                    <thead>
                      <tr>
                        <th>Aralık</th>
                        <th>Etiket</th>
                        <th>Köken</th>
                        <th>Zaman sürümü</th>
                        <th>Not</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.annotations?.map((a) => (
                        <tr key={a.id}>
                          <td>
                            {a.start_offset}–{a.end_offset} sn
                          </td>
                          <td>
                            {a.label}
                            {a.supersedes_id ? " (düzeltme)" : ""}
                          </td>
                          <td>Manuel</td>
                          <td>{a.time_version || "Doğrulanmadı"}</td>
                          <td>{a.note}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </section>
              </>
            )}
          </>
        )}
        {farm && page === 6 && (
          <FeedingPage base={base} cameras={data.cameras} day={day} />
        )}
        {farm && page === 7 && (
          <CropPreviewPage base={base} cameras={data.cameras} media={data.media} />
        )}
        {farm && page === 8 && <LabelingPage base={base} cameras={data.cameras} />}
        <footer>
          {config.app_name} · İlk sürüm · Yerel kullanım ·{" "}
          {selected?.is_demo
            ? "SENTETİK DEMO"
            : config.model_status || "Gerçek davranış modeli bağlı değil"}
        </footer>
      </main>
    </div>
  );
}
function Stat({
  label,
  value,
  detail,
}: {
  label: string;
  value: React.ReactNode;
  detail: string;
}) {
  return (
    <div className="stat">
      <p>{label}</p>
      <strong>{value}</strong>
      <small>{detail}</small>
    </div>
  );
}
function Empty({ text }: { text: string }) {
  return (
    <div className="empty">
      <span>◎</span>
      <p>{text}</p>
    </div>
  );
}
function Coverage({ value }: { value: number }) {
  return (
    <div className="coverage">
      <div>
        <i style={{ width: `${Math.max(0, Math.min(100, value * 100))}%` }} />
      </div>
      <span>%{(value * 100).toFixed(1)}</span>
    </div>
  );
}
function TimeFields() {
  return (
    <>
      <label>
        Başlangıç
        <input name="start" type="datetime-local" required />
      </label>
      <label>
        Bitiş
        <input name="end" type="datetime-local" required />
      </label>
    </>
  );
}
function ZoneEditor({
  base,
  data,
  post,
}: {
  base: string;
  data: Record<string, Row[]>;
  post: (resource: string, body: unknown) => unknown;
}) {
  const [cameraId, setCameraId] = useState(""),
    [stallId, setStallId] = useState(""),
    [role, setRole] = useState("body"),
    [points, setPoints] = useState<number[][]>([]),
    [drag, setDrag] = useState<number | null>(null);
  const camera = data.cameras?.find((c) => c.id === cameraId),
    media = data.media?.find((m) => m.camera_id === cameraId);
  const position = (e: React.PointerEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    return [
      Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)),
      Math.max(0, Math.min(1, (e.clientY - r.top) / r.height)),
    ];
  };
  return (
    <section className="card">
      <h2>Kamera bölgeleri</h2>
      <p>
        Gövde, baş ve yemlik bölgelerini kareye tıklayarak çizin. Köşeleri
        sürükleyerek düzenleyin.
      </p>
      <div className="button-row">
        <select
          aria-label="Bölge kamerası"
          value={cameraId}
          onChange={(e) => {
            setCameraId(e.target.value);
            setPoints([]);
          }}
        >
          <option value="">Kamera seçin</option>
          {data.cameras?.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
        <select
          aria-label="Bölge yeri"
          value={stallId}
          onChange={(e) => setStallId(e.target.value)}
        >
          <option value="">Yer seçin</option>
          {data.stalls
            ?.filter((s) => s.barn_id === camera?.barn_id)
            .map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
        </select>
        <select
          aria-label="Bölge rolü"
          value={role}
          onChange={(e) => setRole(e.target.value)}
        >
          <option value="body">Gövde</option>
          <option value="head">Baş</option>
          <option value="feeder">Yemlik</option>
        </select>
      </div>
      {camera && (
        <>
          <p>
            <span className="badge">
              Geometri v{camera.geometry_version} ·{" "}
              {camera.geometry_valid ? "Doğrulandı" : "Doğrulama bekliyor"}
            </span>
          </p>
          {media ? (
            <svg
              className="zone-canvas"
              viewBox="0 0 1000 562.5"
              style={{
                aspectRatio: `${media.metadata.width}/${media.metadata.height}`,
              }}
              preserveAspectRatio="none"
              onPointerDown={(e) => {
                if (
                  e.target === e.currentTarget ||
                  (e.target as Element).tagName === "image"
                )
                  setPoints([...points, position(e)]);
              }}
              onPointerMove={(e) => {
                if (drag !== null) {
                  const p = position(e);
                  setPoints(points.map((old, i) => (i === drag ? p : old)));
                }
              }}
              onPointerUp={() => setDrag(null)}
              onPointerLeave={() => setDrag(null)}
            >
              <image
                href={`${base}/media/${media.id}/file?kind=thumbnail`}
                width="1000"
                height="562.5"
                preserveAspectRatio="none"
              />
              {data.zones
                ?.filter(
                  (z) =>
                    z.camera_id === cameraId &&
                    z.geometry_version === camera.geometry_version,
                )
                .map((z) => (
                  <polygon
                    key={z.id}
                    points={z.polygon
                      .map((p: number[]) => `${p[0] * 1000},${p[1] * 562.5}`)
                      .join(" ")}
                    className="saved-zone"
                    onClick={() => {
                      setPoints(z.polygon);
                      setRole(z.role);
                      setStallId(z.stall_id);
                    }}
                  />
                ))}
              <polygon
                points={points
                  .map((p) => `${p[0] * 1000},${p[1] * 562.5}`)
                  .join(" ")}
              />
              {points.map((p, i) => (
                <circle
                  key={i}
                  cx={p[0] * 1000}
                  cy={p[1] * 562.5}
                  r="8"
                  onPointerDown={(e) => {
                    e.stopPropagation();
                    setDrag(i);
                  }}
                />
              ))}
            </svg>
          ) : (
            <Empty text="Bölge çizmek için bu kameraya bir video yükleyin." />
          )}
          <div className="button-row">
            <button onClick={() => setPoints([])}>Çizimi temizle</button>
            <button
              disabled={points.length < 3 || !stallId || !media}
              onClick={() =>
                post("zones", {
                  camera_id: cameraId,
                  stall_id: stallId,
                  role,
                  polygon: points,
                  width: media?.metadata.width,
                  height: media?.metadata.height,
                })
              }
            >
              Bölge kaydet
            </button>
            <button
              disabled={!media}
              onClick={() =>
                post(`cameras/${cameraId}/validate`, {
                  width: media?.metadata.width,
                  height: media?.metadata.height,
                })
              }
            >
              Geometriyi doğrula
            </button>
            <button onClick={() => post(`cameras/${cameraId}/moved`, {})}>
              Kamera taşındı
            </button>
            <button
              onClick={() =>
                post(`cameras/${cameraId}/clock`, {
                  verified: !camera.clock_verified,
                })
              }
            >
              {camera.clock_verified
                ? "Saat uyumunu kaldır"
                : "Saat uyumunu doğrula"}
            </button>
          </div>
        </>
      )}
    </section>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
