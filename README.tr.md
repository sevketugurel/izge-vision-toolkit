# İzge

Saha kayıtları, video işleme, PostgreSQL iş kuyruğu, günlük kapsam raporu ve insan incelemesi için modüler monolit. GPU gerekmez. **Gerçek bir davranış modeli bağlı değildir.** Gerçek dosya adaptörü kareleri CPU üzerinde çözer ve `unavailable` gözlemleri üretir. Sentetik analiz yalnızca ayrı demo işletmesinde kullanılabilir.

> Teslim durumu: geçmiş tam doğrulama koşuları ve güncel kontrol sonuçları [doğrulama kaydında](docs/VERIFICATION.md) ayrı tutulur. Bu sandbox'ta Playwright e2e **SKIP** olarak raporlanır; başarılı sayılmaz. Plan: [yol haritası](docs/roadmap.en.md).

## Başlatma

Docker ve paket ağ erişimi olan bir makinede:

```sh
cp .env.example .env
docker compose up --build
```

Arayüz: http://127.0.0.1:8000 · OpenAPI: http://127.0.0.1:8000/docs

PostgreSQL sağlıklı olmadan migration; migration tamamlanmadan API ve worker başlamaz. Uygulama portu yalnızca localhost'a açılır. PostgreSQL portu dışarı yayımlanmaz. Veritabanı ve medya adlandırılmış volume'lerde kalır. `docker compose down` verileri korur; `down -v` verileri siler.

### Sentetik demo

```sh
docker compose exec api python -m izge.demo
```

Üç hayvan, üç yer, iki kamera, 30 saniyelik iki yerel test videosu ve iki analiz işi oluşturur. Gerçek dosyalar bu işletmeye yüklenemez; ayrı bir işletme kullanılır. Worker aynı analiz/lease/yayınlama yolunu kullanır. İşletme, medya, sonuç ve dışa aktarımlar demo bilgisini taşır. Bu komut tekrar çalıştırılabilir; var olan demo işletmesini kullanır. Sayfayı yenileyip **Örnek Çiftlik — SENTETİK DEMO** işletmesini seçin.

### Gerçek kayıt akışı

1. İşletme, ahır, kamera, yer ve hayvan oluşturun. Yerleşim başlangıcını gerçek geçmiş tarih/saatle girin.
2. Kamera için MP4/MOV/MKV/AVI yükleyin. Yükleme zamanı kayıt zamanı değildir.
3. Saha ayarlarında video karesi üzerinde gövde/baş/yemlik çokgenlerini çizin ve doğrulayın. Kamera taşındığında veya çözünürlük değiştiğinde yeniden doğrulama gerekir.
4. Videonun kayıt başlangıcını bir doğrulama notuyla kaydedin. Saatler arayüzde cihaz saat diliminde girilir, UTC'ye dönüştürülür. Raporun günü işletme saat dilimindedir.
5. Analizi başlatın. **Model bağlı değil** durumunda sonuçların bilinmeyen kalması beklenir; sentetik sonuç üretilmez.
6. İnceleme ekranında kaynak video offsetiyle etiket ekleyin; düzeltme için önceki etiketi seçin. Olay varsa kanıt klibini açıp karar ekleyin.
7. JSON/CSV dışa aktarımını indirin. Köken, zaman eşleme sürümü, analiz bağlamı ve inceleme geçmişi korunur.

**Manuel etiket ve doğrulanmış düzeltmeler otomatik günlük süreleri değiştirmez.** Tarihli rapora girmek için kayıt saati doğrulanmış ve analiz başarıyla yayımlanmış olmalıdır. Kayıt saati düzeltmesi önceki sonuçları değiştirmez; yeni analiz oluşturun.

### Bölme bazlı yemde süresi (v1)

1. `config/cameras/` altında kamera config'ini doldurun (örnek: `synthetic_feeding.yaml`, geometri tamamen sentetiktir; gerçek kamerada kendi doğrulanmış koordinatlarınızı kullanın). **Kesit önizleme** sayfası kesitleri referans kare üzerinde gösterir.
2. **Saha ayarları**nda kamerayı bu config ile oluşturun. **Video ve işler**de H.264 MP4 yükleyin; kayıt başlangıcı dosya adından (config regex) okunur, okunamazsa form alanı zorunludur.
3. **Etiketleme örnekleri oluştur** → **Etiketleme** ekranında `1` yemde, `2` yemde değil, `3` görünmüyor, `Z` geri al, `S` atla.
4. Eğitim (DINOv3 ağırlıkları için `HF_TOKEN` gerekir): `docker compose exec worker python -m izge.train --farm <işletme-id>`.
5. **Yemde analizini başlat** → **Yemde süresi**: bölme zaman çizelgesi, günlük özet, CSV, kullanılan model sürümü. Eğitilmiş model yoksa sonuç üretilmez; demo sonuçları **DEMO** olarak işaretlenir. DINOv3 tabanlı modellerde panel "Built with DINOv3" ibaresini gösterir.

6. Altın set (BORIS aggregated events) ve değerlendirme: `python -m izge.gold import|evaluate|list`; raporlar yerel olarak yazılır; gerçek veri veya raporları repoya eklemeyin.

Hız ölçümü: `uv run --extra ml python scripts/bench_feeding.py` (ayrıntı: docs/VERIFICATION.md).

## Yerel geliştirme

Python 3.14, Node 22.18+, FFmpeg/ffprobe ve PostgreSQL 18 gerekir. `btree_gist` extension kurulabilmelidir.

```sh
uv sync --frozen --extra ml   # ml: PyTorch + transformers (DINOv3)
export PYTHONPATH=backend
export DATABASE_URL=postgresql+psycopg://izge:izge-local@127.0.0.1:5432/izge
uv run alembic upgrade head
uv run uvicorn izge.api:app --host 127.0.0.1 --port 8000
# Ayrı terminallerde:
uv run python -m izge.worker
npm ci --prefix frontend
npm run dev --prefix frontend
```

Frontend Vite geliştirme sunucusu `/api` isteklerini port 8000'e yönlendirir. Üretim frontend derlemesi FastAPI tarafından sunulur. Uygulama adı yalnızca `IZGE_APP_NAME` ile belirlenir; arayüz `/api/config` üzerinden alır.

## Tasarım dili

Panel Türkçe metinler, koyu yeşil gezinme alanı, açık zemin ve beyaz kartlar kullanır.
Uygulama adı `IZGE_APP_NAME` → `GET /api/config` → `app_name` üzerinden gelir;
sayfa başlığı ve menü markası bu değeri kullanır. Kaynaklar:
[stil kuralları](frontend/src/style.css), [panel](frontend/src/main.tsx) ve
[yemde/etiketleme bileşenleri](frontend/src/feeding.tsx).

| Kullanım | CSS seçicisi | Özellik | Değer |
|---|---|---|---|
| Ana metin | `:root` | `color` | `#243c37` |
| Sayfa zemini | `:root` | `background` | `#f5f7f4` |
| Yan menü | `aside` | `background` | `#153a35` |
| Etkin menü | `nav button.active` | `background` | `#dce9a5` |
| Birincil eylem | `.primary` | `background` | `#285b48` |
| Demo uyarısı | `.demo-banner` | `background` | `#faf0d7` |
| Yemde | `.state-yemde` | `fill` | `#3f9a5b` |
| Yemde değil | `.state-yemde_degil` | `fill` | `#d7dfcc` |
| Görünmüyor | `.state-gorunmuyor` | `fill` | `#8a8f99` |
| Gözlenmeyen aralık | `.unobserved` | `fill` | `#f6f7f3` |

- Yazı ailesi Inter, ardından sistem yazı tipleridir; temel boyut 14 px'tir.
  Kartlar 11 px köşe yarıçapı ve ince kenarlık kullanır. Form alanları etiketli,
  butonların devre dışı durumu görünür, klavye odağının dış boşluğu 3 px'tir.
- Geniş ekranda sabit yan menü 248 px'tir. `1100px` altında istatistikler iki,
  içerik sütunları bire düşer. `700px` altında menü üstte yatay kaydırılır,
  medya ve etiketleme alanları tek sütuna geçer; tablolar yatay kaydırılabilir.
- Demo, model yok, yer tutucu, yükleme, hata ve boş veri durumları metinle açıklanır.
  Yemde zaman çizelgesinin renkleri açıklama anahtarıyla birlikte gösterilir;
  görünmüyor ile gözlenmeyen aralık ayrı tutulur.

README ekran görüntüsü içermez. Ekran görüntüsü üretimi ve görsel inceleme
**sandbox dışında** yapılır; bu görevde çalıştırılmadı ve başarılı sayılmaz.
Kaynak kontrolleri yerleşim, renk karşıtlığı veya tarayıcı erişilebilirliği kabulü değildir.

## Hareket dili

Tek token kaynağı [design/motion-tokens.json](design/motion-tokens.json) dosyasıdır.
[React presetleri](frontend/src/motion/presets.ts) ve
vanilla JS presetleri (`harness/`, yalnız özel depoda)
bu JSON'u doğrudan okur; süre, easing, mesafe ve stagger değerlerini bileşenlerde
yeniden tanımlamayın. Token süreleri ve stagger milisaniye, mesafeler pikseldir;
React adaptörü `motion/react` için süreleri saniyeye çevirir.

| Token | Değer |
|---|---|
| Süre: fast / normal / slow | 120 / 200 / 320 ms |
| Giriş / çıkış easing | `cubic-bezier(0.2,0,0,1)` / `cubic-bezier(0.4,0,1,1)` |
| Mesafe: small / medium / large | 4 / 8 / 16 px |
| Stagger aralığı | 24–40 ms |
| Overshoot | `false` |
| Spring stiffness / damping | 500 / 40 |

- Hareket, durum değişimini ve işlem geri bildirimini açıklamalıdır. Yeni geçişler
  yalnız `opacity` ve `transform` kullanır; genişlik, yükseklik veya konumla
  yerleşimi canlandırmayın. Overshoot ve zıplama eklemeyin. Spring gerekirse ortak
  tokenları kullanın; mevcut giriş/çıkış presetleri cubic-bezier kullanır.
- Giriş preseti normal süre + medium mesafe, çıkış preseti fast süre + small
  mesafe kullanır; sıra gecikmesi öğe indeksi × minimum stagger'dır.
  Panelde yemde özet satırları, etiketleme kartı, hata/son işlem bildirimi ve
  kayıt başlangıcı yardım metni ortak React varyantlarını kullanır.
- **Yeni görünümde yasaklılar:** cam efekti (`backdrop-filter`,
  `-webkit-backdrop-filter`), `linear-gradient` ve `box-shadow`.
  Token koruma testleri (`harness/core/tests/test_motion_tokens.py`, yalnız özel depoda), yeni hareket
  sabitlerini ve bu efektleri iki arayüz kaynak ağacında denetler. Testteki
  `LEGACY_MOTION` / `LEGACY_EFFECTS` listeleri mevcut satırlar için sayılı
  istisnalardır; yeni kullanımlara izin vermez ve tüm eski arayüzün taşındığı
  anlamına gelmez.
- **Reduced-motion:** `(prefers-reduced-motion: reduce)` seçiliyken süre,
  mesafe ve stagger **0** olur. React tarafında `useReducedMotion()` sonucu
  `getMotionVariants()` seçimine aktarılır; vanilla tüketicisi `matchMedia`
  sonucuyla `getMotionPresets()` seçmelidir. Harness CSS ayrıca animasyon ve
  transition'ları kapatır, yumuşak kaydırmayı kaldırır. İçerik ve durum mesajları
  hareket olmadan da görünür kalmalıdır.

Gzip bundle ölçümü, yöntemi ve çalıştırılan/çalıştırılmayan kontroller
[doğrulama kaydındadır](docs/VERIFICATION.md). Reduced-motion Playwright
senaryolarının bulunması, bu oturumda çalıştırıldıkları anlamına gelmez.

## API sözleşmesi

Çalışan API'nin tam alan/tip şeması `/openapi.json`, etkileşimli belgesi `/docs`
adresindedir. Kaynaklar: [ana API](backend/izge/api.py),
[yemde API'si](backend/izge/feeding_api.py) ve [istemci](frontend/src/api.ts).
İşletme kapsamlı uçlar `/api/v1/farms/{farm_id}` altında UUID kimlikler kullanır;
bu kapsam kontrolü kullanıcı yetkilendirmesi sağlamaz (bkz. operasyon sınırları).

| Yöntem | Yol | İstek / yanıt |
|---|---|---|
| GET | `/api/config` | `app_name`, `model_status`, `default_analyzer`, `local_only`, `pilot_config`, `max_upload_bytes` |
| GET | `/api/health` | DB erişimi başarılıysa `{"status":"ok"}` |
| GET | `/api/v1/farms` | İşletme listesi |
| POST | `/api/v1/farms` | JSON `name`, isteğe bağlı `timezone`; 201, demo olmayan işletme |
| POST | `/api/v1/farms/{farm_id}/media` | Sorguda `camera_id`; multipart `file`, isteğe bağlı `recorded_at`; 201, medya metaverisi |
| GET | `/api/v1/farms/{farm_id}/media/{media_id}/file` | `kind=original\|thumbnail\|preview` (varsayılan `preview`); dosya içeriği |
| GET | `/api/v1/farms/{farm_id}/evidence/{evidence_id}/file` | Kanıt dosyası içeriği |
| POST | `/api/v1/farms/{farm_id}/media/{media_id}/time` | JSON `recorded_at`, `note`; yeni zaman eşleme `version` değeri |
| POST | `/api/v1/farms/{farm_id}/runs` | JSON `media_id`, isteğe bağlı analizör/model/offset alanları; 201, analiz kaydı |
| GET | `/api/v1/farms/{farm_id}/runs/{run_id}` | Analiz, `job` ve `observation_count` |
| GET | `/api/v1/farms/{farm_id}/report` | Zorunlu `day` (YYYY-MM-DD), isteğe bağlı `animal_id`; işletme saat diliminde günlük rapor |
| POST | `/api/v1/farms/{farm_id}/annotations` | Video offsetleri ve etiket; düzeltme `supersedes_id` ile yeni kayıt ekler; 201 |
| POST | `/api/v1/farms/{farm_id}/events/{event_id}/reviews` | İnceleme kararı; `manual` kökenli yeni kayıt; 201 |
| GET | `/api/v1/farms/{farm_id}/export` | `format=json\|csv`; sürüm, köken ve geçmişi koruyan indirme |
| GET | `/api/camera-configs` | Kamera config listesi ve geçerlilik bilgisi |
| GET | `/api/v1/farms/{farm_id}/feeding/models` | Yemde modelleri ve kullanılabilirlik durumu |
| GET | `/api/v1/farms/{farm_id}/feeding/timeline` | Bölme bazlı zaman çizelgesi |
| GET | `/api/v1/farms/{farm_id}/feeding/daily` | Günlük yemde özetleri |
| GET | `/api/v1/farms/{farm_id}/feeding/daily.csv` | Günlük yemde özetlerinin CSV indirmesi |
| POST | `/api/v1/farms/{farm_id}/labeling/samples` | Etiketleme örnekleri oluşturur; 201 |
| GET | `/api/v1/farms/{farm_id}/labeling/next` | Sıradaki örnek |
| POST | `/api/v1/farms/{farm_id}/labels` | İnsan etiketi ekler; 201 |
| POST | `/api/v1/farms/{farm_id}/labels/{label_id}/undo` | Geri alma kaydı ekler; 201 |

JSON isteklerde `Content-Type: application/json` kullanılır. Multipart yüklemede
tarayıcı sınır başlığını kendisi üretir. İstemci 204 yanıtını `null`, diğer başarılı
JSON yanıtlarını nesne/liste olarak okur; dosya uçları indirme veya medya URL'si olarak kullanılır.
Hatalar `detail` alanıyla döner: 404 bulunamayan kaynak, 409 ilişki/benzersizlik/tarihsel
çakışma, 413 yükleme sınırı, 422 geçersiz giriş; değiştirilemeyen geçmiş için 405.
Doğrulama hatasında `detail` liste olabilir; panel bu durumda genel form hatası gösterir.

Yükleme sınırı `/api/config` içindeki `max_upload_bytes` değeridir. Config bağlı kamera
yalnız H.264 MP4 kabul eder; başlangıç dosya adından çözülemezse `recorded_at` gerekir.
Diğer kameralar MP4/MOV/MKV/AVI kabul eder. Video uçları disk yolu almaz;
yanıtlar ve export secret değerlerini veya depolama anahtarlarını yayımlamaz.
`manual`, `model`, `demo`, `unavailable` kökenleri korunur; demo yalnız demo işletmesi
ve demo medyada çalışır. Manuel inceleme otomatik süreleri değiştirmez; zaman düzeltmesi
eski analizi değiştirmez ve yeni analiz gerektirir. Saat doğrulanmadan güncel yerleşimden
hayvan kimliği çıkarılmaz; ölçülen süre ile bilinmeyen süre ayrı tutulur.

## Kontroller

```sh
make verify   # lint, birim, PG18 entegrasyon, frontend build, Compose yığını, Playwright e2e
make test     # yalnız birim testleri
make verify-deploy   # GCP betiklerinin statik doğrulaması (kaynak oluşturmaz)
```

Ayrıntılar ve son sonuçlar: [docs/VERIFICATION.md](docs/VERIFICATION.md). Entegrasyon testleri yalnızca `compose.test.yaml` ile açılan geçici PostgreSQL 18'e karşı koşar; saha veritabanında çalıştırmayın. Suite PostgreSQL 14'ü kabul etmez.

## Operasyon ve sınırlar

- Lease 60 sn, heartbeat 15 sn, en fazla 3 deneme; 5/20 sn geri çekilme. Worker kaybında checkpoint'ten devam edilir. Eski lease sahibinin sonuç yazması reddedilir.
- Ham gözlemler, anotasyonlar, zaman eşlemeleri ve incelemeler eklemeli geçmişlerdir. Kısmi sonuçlar rapora katılmaz.
- Orijinal videolar otomatik silinmez. İncelemeye alınan kanıt tutulur. Diğer olay klipleri varsayılan 90 gün sonra `docker compose exec api python -m izge.maintenance` ile temizlenebilir.
- RTSP adapter sınıfı secret referansı, timeout, reconnect ve sınırlı tampon içerir. **Sürekli canlı kayıt/worker entegrasyonu, gerçek NVR ve saha bağlantısı henüz doğrulanmış veya işletime alınmış değildir.** API kayıtları için health alanı vardır; dosya worker'ı RTSP oturumu başlatmaz.
- Kimlik doğrulama ve kullanıcı/işletme yetkilendirmesi bu localhost sürümünde yoktur. Uzak kullanım öncesi bunlar, TLS ve operasyonel saklama/yedekleme politikası eklenmelidir. Ağ/port yönlendirme değişikliği yapılmadı.
- Dosya işleme ve demo sonuçları gerçek canlı alarm gecikmesinin kanıtı değildir.
- İlk analizde uzun videonun gözlemleri türetme aşamasında belleğe alınır; büyük saha kayıtları için bellek/performans testi ve artımlı türetme gerekir.

[Mimari](docs/architecture.en.md) · [Yol haritası](docs/roadmap.en.md) · [Üçüncü taraflar](THIRD_PARTY_NOTICES.md)
