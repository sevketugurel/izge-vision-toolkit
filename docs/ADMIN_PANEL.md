# Admin paneli

Mevcut `frontend/` React uygulamasına ML deneyleri, proje yönetimi, ajan operasyonları ve saha izleme sayfaları eklenir. `backend/izge/admin_api.py` aynı FastAPI uygulamasına bağlanır. Eğitim başlatma, iptal, sürdürme, validation karşılaştırması ve veri kapısı PR #3'teki `ControlCenter` bileşeninden gelir. İkinci bir eğitim uygulaması veya görev veritabanı yoktur. `panel/` mevcut harness gidişat ekranıdır; Agent Swarm görevleriyle aynı kaynak değildir ve değiştirilmedi.

## Başlatma

Mevcut uygulamanın veritabanı ve migration kurulumu geçerlidir. Admin API, kontrol merkezinin yerel operatör sınırını kullanır. Varsayılan kapalıdır; uzaktan kimlik doğrulama sağlamaz.

```sh
export IZGE_CONTROL_CENTER_ENABLED=1
export IZGE_SWARM_URL=http://localhost:3013
export IZGE_SWARM_TOKEN_FILE=/operator/private/swarm-token
export IZGE_SWARM_AGENT_ID=OPERATOR_INTEGRATION_AGENT_UUID
export IZGE_ADMIN_PLANNING_ENABLED=1
PYTHONPATH=backend uv run uvicorn izge.api:app --host 127.0.0.1 --port 8000
```

İkinci terminal:

```sh
npm --prefix frontend ci
npm --prefix frontend run dev -- --port 5174
```

API başka yerel porttaysa dev sunucusunda `IZGE_API_URL=http://127.0.0.1:18768` ayarlanabilir. Proxy, Host başlığını korur; backend'in Origin kontrolünü kaldırmaz. Marka adı mevcut `IZGE_APP_NAME` ve `/api/config` üzerinden gelir.

`IZGE_SWARM_TOKEN`, dosya yerine yalnız sunucu ortamında kullanılabilir. Token, tarayıcıya, URL'ye, repoya veya `VITE_*` değişkenlerine konmaz. Swarm için yalnız HTTP loopback veya sayısal özel ağ adresi kabul edilir. Bu konteynerde Swarm `localhost:3013` yerine iç ağ IP'sindedir; önizleme operatör ortamından sunucu tarafında bağlanmıştır. Uzak/public adresler ve redirect reddedilir. API token'ına gereken en dar görev/ajan/oturum/workflow/KV izinleri verilmelidir. Ajan kimliği KV namespace'ini sabitler; farklı operatör kimliği farklı planlama ekleri görür.

## Kaynaklar ve adaptörler

| Sayfa | Gerçek kaynak | Eksiklik davranışı |
| --- | --- | --- |
| ML arşivi | `experiments/*/model.lock.json`, `*.model.lock.json`; `IZGE_RUNS_ROOT/*/{status.json,config.yaml,metrics.jsonl}` | Model kilidi eğitim sonucu sayılmaz; ölçüm yoksa `unavailable` / Kullanılamıyor |
| Eğitim ve karşılaştırma | `/api/control/*` üzerinden mevcut token'lı native runner | Runner bağlı değilse eğitim ve karşılaştırma kapalı |
| Dataset / etiket kapısı | Mevcut `IZGE_CONTROL_INVENTORY`, `IZGE_DATA_ROOT`, manifest doğrulaması | Sayı ve sürüm uydurulmaz; yapılandırılmamış kaynak açıkça gösterilir |
| Görevler | Swarm `GET /api/tasks` (İzge araması, sayfada 100 kayıt), görev detayı ve olayları | Bağlantısızken görev oluşturulmaz ve yerel görev kopyası oluşturulmaz |
| Görev oluşturma / başlık | Swarm `POST /api/tasks`, `PATCH /api/tasks/{id}/title` | Yetki veya doğrulama hatası gösterilir; kendiliğinden tekrar gönderilmez |
| Kanban planlaması | Aynı Swarm'da görev UUID'sine bağlı KV kaydı | Flag veya KV izni yoksa ek alanlar salt okunur/devre dışı |
| Yorum / audit | `IZGE_ADMIN_OVERLAY`, varsayılan `~/.izge-admin/comments.sqlite3` | Eklemeli yerel kayıt; yerel yazma hatası başarılı Swarm işlemini başarısız gibi göstermez |
| Ajanlar / provider | Swarm `/api/agents`, `/api/agents/credential-status` | Eksik yetki ayrı kaynak için Kullanılamıyor; credential değerleri alınmaz |
| Onay / oturum / eylem | `/api/approval-requests?status=pending`, `/api/active-sessions`, `/api/sessions`, `/api/logs` | Her kaynak ayrı durum taşır; serbest log payload'ları aktarılmaz |
| Workflow geçmişi | `/api/workflows`, seçilen workflow için `/api/workflows/{id}/runs?limit=30` | Kayıt yok ile bağlantı yok farklı gösterilir |
| Saha | Seçilen işletmenin mevcut `/api/v1/farms/{id}/{barns,cameras,jobs,runs,annotations,media}` uçları | Kaynak bazında eksiklik; kökenler korunur, saatten hayvan kimliği çıkarılmaz |

Arşiv yalnız sabit, sınırlandırılmış JSON alanlarını okur. Symlink ile kök dışına çıkılmaz; dosya başına 2 MB sınır ve 200 koşu sınırı vardır. Metrikler yalnız `eval.split=val` kaydıyla gösterilir; final/test ölçümü okunmaz. Kısmi veya bozuk satır, boolean ve NaN/Infinity ölçüm sayılmaz. RF-DETR dış çıktılarının farklı log formatları otomatik dönüştürülmez. mAP kaydı yoksa sıfır yazılmaz.

Artifact bağlantısı temizlenmiş deney JSON özetidir. Ham log, checkpoint/ağırlık ve video dosyası sunmaz. Eğitim günlükleri mevcut kontrol merkezinin güvenli sayısal günlük görünümünden okunur; hata türü dışındaki serbest provider hataları açılmaz. Ham hata gövdeleri ve credential metadata'sı proxy edilmez.

## Görev düzenlemesinin sınırı

İncelenen Agent Swarm REST sözleşmesi: [tasks API](https://github.com/desplega-ai/agent-swarm/blob/main/docs-site/content/docs/api-reference/tasks.mdx), [OpenAPI](https://github.com/desplega-ai/agent-swarm/blob/main/openapi.json). Kurulu sunucuda canlı okuma, taslak oluşturma, KV yazma ve başlık sözleşmesi doğrulandı.

Yeni görev **draft** olarak oluşturulur. Açıklama ve kabul ölçütleri kaynak görev metnine yazılır; öncelik, başlangıç ataması ve bağımlılıklar native oluşturma alanlarıdır. Swarm, atama seçilmezse kendi varsayılan ajanını atayabilir; panel dönen gerçek atamayı gösterir. Panel taslağı promote etmez ve ajan başlatmaz.

Mevcut görevde başlık native Swarm API'ye yazılır. Genel açıklama/öncelik/atama düzenleme ucu bulunmadığı için Kanban sütunu, planlanan öncelik/sorumlu, açıklama değişikliği, kabul ölçütleri, engeller, planlanan bağımlılıklar ve branch/commit/PR bağlantısı **planlama eki** olarak aynı Swarm'ın KV'sinde saklanır: `izge:admin:planning:{taskUUID}`. Bunlar native çalışma durumunu, scheduler bağımlılığını veya gerçek atamayı değiştirmez. Ekran hem planı hem gerçek Swarm durumunu gösterir. Görevi Swarm'dan silince yerel görev kopyası kalmaz; ek kaydı tek başına bir görev üretmez.

Planlama bütün belge olarak kaydedilir: aynı görevde eşzamanlı düzenlemede son yazan kazanır; KV CAS/version precondition sağlamaz. Bu MVP tek yerel operatör içindir. Yorumlar ayrı UUID'li ek kayıtlardır; eşzamanlı yorumlar üzerine yazılmaz. Kaydetme geçmişi ve yorumlar yereldir; makine/overlay dosyası taşınmadan diğer operatöre aktarılmaz. Uzak yazma başarılı, yerel audit başarısızsa panel bunu açıkça bildirir; tekrar görev oluşturmayın. Timeout sonrasında yeniden göndermeden Swarm'daki durumu yenileyin.

## Eksik entegrasyonlar ve kalan işler

- Native genel görev düzenleme, yeniden atama ve Kanban durum geçişi API'si geldikten sonra planlama ekini gerçek scheduler işlemlerine bağlamak; planlanan alanlar bugün scheduler komutu değildir.
- Birden çok operatör için kimlik doğrulama, kullanıcıya atfedilen audit, ortak yorum deposu ve çakışma denetimi. Mevcut aktör yerel operatördür.
- Provider kota garantisi ve otomatik görev retry/recovery durumu API'den gelmiyor. Ajan durumu, başarısızlık nedeni ve üst görev bağlantısı görülebilir; retry başarısı tahmin edilmez. Maliyet yalnız kaynakta fiyatı bulunan son 100 İzge görevinin kısmi toplamıdır.
- Oturum geçmişi son 30 İzge oturumu, eylemler son 50 Swarm olayı, workflow geçmişi seçilen workflow'un son 30 çalışmasıdır. Global toplam veya tam geçmiş iddiası yoktur. KV ilk 1000 kayıtla sınırlıdır; aşım bildirilir.
- Canlı kamera bağlantı sağlığı, etiket tamamlanma paydası ve araştırma kilometre taşı kaynağı bağlı değil. Kamera kaydı aktif akış sayılmaz. Var olan model/demo analizleri mevcut medya/analiz sayfalarında açılır.
- Gerçek saha mAP değerlendirmesi, CUDA/MPS eğitimi ve PostgreSQL 18 / Compose entegrasyonu bu admin tesliminde çalıştırılmadı.

## Doğrulama

Tekrarlanabilir, yazma yapmayan tarayıcı kontrolü:

```sh
python3 scripts/admin_panel_smoke.py --url http://127.0.0.1:5174 --out /tmp/admin-qa
```

`agent-browser` kurulu ve uygulama çalışır olmalıdır. Kontrol, dört sayfanın gerçek render'ını ve her sayfada 390 px taşma sınırını sınar. Geçersiz `{}` gövdesiyle aynı kaynak için 422, dış Origin için 403 bekler; bu istekler görev oluşturamaz. Kaynakta `smoke` koşusu varsa gerçek kayıp grafiğini de açar. Ekran görüntüleri ve ham tarayıcı çıktısı `--out` altında kalır; çıktı PASS/FAIL ve kanıt yoludur.

Teslim kanıtı: reports/admin-panel/README.md. Tam doğrulama kaydı: [VERIFICATION.md](VERIFICATION.md).
