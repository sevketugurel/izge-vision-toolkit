import { useEffect, useState } from "react";
import { request, type Row } from "./api";

const resources = [
  "barns",
  "cameras",
  "jobs",
  "runs",
  "annotations",
  "media",
] as const;
type Resource = (typeof resources)[number];
export function AdminMonitoring({ farm }: { farm: string }) {
  const [data, setData] = useState<Partial<Record<Resource, Row[]>>>({}),
    [missing, setMissing] = useState<string[]>([]);
  const [epoch, setEpoch] = useState(0);
  useEffect(() => {
    let active = true;
    setData({});
    setMissing([]);
    if (farm)
      Promise.allSettled(
        resources.map((r) => request(`/api/v1/farms/${farm}/${r}`)),
      ).then((results) => {
        if (!active) return;
        const next: Partial<Record<Resource, Row[]>> = {},
          unavailable: string[] = [];
        results.forEach((value, i) => {
          if (value.status === "fulfilled") next[resources[i]] = value.value;
          else unavailable.push(resources[i]);
        });
        setData(next);
        setMissing(unavailable);
      });
    return () => {
      active = false;
    };
  }, [farm, epoch]);
  return (
    <div className="admin-page" aria-busy={!!farm && resources.some(r => !data[r] && !missing.includes(r))}>
      <section className="card">
        <div className="admin-heading">
          <div>
            <div className="eyebrow">SAHA / İŞLETME API</div>
            <h2>İşletme ve video hattı</h2>
          </div>
          <button onClick={() => setEpoch((v) => v + 1)}>Sahayı yenile</button>
        </div>
        {!farm && (
          <p className="admin-empty">
            İşletme kaynağı kullanılamıyor · önce işletme seçin.
          </p>
        )}
        {!!missing.length && (
          <p role="alert" className="alert">
            Kullanılamayan kaynaklar: {missing.join(", ")}
          </p>
        )}
        <div className="admin-stats">
          {(
            [
              ["barns", "Ahır"],
              ["cameras", "Kamera"],
              ["media", "Medya"],
              ["annotations", "Etiket kaydı"],
            ] as const
          ).map(([key, label]) => (
            <div key={key}>
              <small>{label}</small>
              <strong>{data[key]?.length ?? "Kullanılamıyor"}</strong>
            </div>
          ))}
        </div>
        <p className="muted">
          Etiket sayısı tamamlanma yüzdesi değildir. Beklenen toplam etiket
          sayısı ve sürekli kamera akış sağlığı bağlı değil. Kamera saati
          doğrulanmadan hayvan kimliği çıkarılmaz.
        </p>
      </section>
      <section className="card">
        <h2>Kamera / ahır görünümü</h2>
        <div className="admin-camera-grid">
          {data.cameras?.map((camera) => (
            <article key={camera.id}>
              <h3>{camera.name}</h3>
              <p>
                Ahır:{" "}
                {data.barns?.find((b) => b.id === camera.barn_id)?.name ||
                  "Kullanılamıyor"}
              </p>
              <p>
                Saat:{" "}
                {camera.clock_verified
                  ? "Doğrulanmış kayıt var"
                  : "Doğrulanmamış"}
              </p>
              <p>Canlı bağlantı: kullanılamıyor</p>
              <small>Kaynak: işletme kamera kaydı</small>
            </article>
          ))}
        </div>
        {data.cameras?.length === 0 && <p>Kayıtlı kamera yok.</p>}
      </section>
      <section className="card">
        <h2>Video iş kuyruğu</h2>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>İş</th>
                <th>Durum</th>
                <th>Deneme</th>
              </tr>
            </thead>
            <tbody>
              {data.jobs?.map((job) => (
                <tr key={job.id}>
                  <td>
                    <code>{job.id}</code>
                  </td>
                  <td>{job.status}</td>
                  <td>{job.attempts ?? "Kullanılamıyor"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!data.jobs && <p className="admin-empty">Kuyruk kullanılamıyor.</p>}
      </section>
      <section className="card">
        <h2>Davranış araştırması ve değerlendirme</h2>
        <p>
          Gerçek analiz çalışmaları, model ve demo kökenleri ayrı gösterilir.
          Araştırma kilometre taşı kaynağı bağlı değil.
        </p>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Çalışma</th>
                <th>Köken</th>
                <th>Model</th>
                <th>Durum</th>
              </tr>
            </thead>
            <tbody>
              {data.runs?.map((run) => (
                <tr key={run.id}>
                  <td>
                    <code>{run.id}</code>
                  </td>
                  <td>{run.origin || "unavailable"}</td>
                  <td>{run.analyzer || "Kullanılamıyor"}</td>
                  <td>{run.status || "Kullanılamıyor"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="muted">
          Ayrıntılı demo/eval çıktıları mevcut Video ve işler, Hayvan detayı ve
          Yemde süresi ekranlarındadır. Mühürlü test performansı bu panelde
          hesaplanmaz.
        </p>
      </section>
    </div>
  );
}
