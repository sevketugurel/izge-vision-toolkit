# İzge

Saha kayıtları, video işleme, PostgreSQL iş kuyruğu, günlük kapsam raporu ve insan incelemesi için modüler monolit. GPU gerekmez. **Gerçek bir davranış modeli bağlı değildir.** Gerçek dosya adaptörü kareleri CPU üzerinde çözer ve `unavailable` gözlemleri üretir. Sentetik analiz yalnızca ayrı demo işletmesinde kullanılabilir.

> Teslim durumu: `make verify` (lint, birim, PostgreSQL 18 entegrasyon + migration gidiş-dönüş, frontend build, Compose yığını, Playwright e2e) temiz klonda geçiyor. v1 planı: [docs/V1_PLAN.md](docs/V1_PLAN.md). Ayrıntılar: [doğrulama kaydı](docs/VERIFICATION.md).

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

1. `config/cameras/` altında kamera config'ini doldurun (örnek: `synthetic_feeding.yaml`, koordinatlar **yer tutucu**; `placeholder: false` yapılmadan analiz reddedilir). **Kesit önizleme** sayfası kesitleri referans kare üzerinde gösterir.
2. **Saha ayarları**nda kamerayı bu config ile oluşturun. **Video ve işler**de H.264 MP4 yükleyin; kayıt başlangıcı dosya adından (config regex) okunur, okunamazsa form alanı zorunludur.
3. **Etiketleme örnekleri oluştur** → **Etiketleme** ekranında `1` yemde, `2` yemde değil, `3` görünmüyor, `Z` geri al, `S` atla.
4. Eğitim (DINOv3 ağırlıkları için `HF_TOKEN` gerekir): `docker compose exec worker python -m izge.train --farm <işletme-id>`.
5. **Yemde analizini başlat** → **Yemde süresi**: bölme zaman çizelgesi, günlük özet, CSV, kullanılan model sürümü. Eğitilmiş model yoksa sonuç üretilmez; demo sonuçları **DEMO** olarak işaretlenir. DINOv3 tabanlı modellerde panel "Built with DINOv3" ibaresini gösterir.

6. Altın set (BORIS aggregated events) ve değerlendirme: `python -m izge.gold import|evaluate|list`; raporlar `docs/reports/` altına yazılır. Ayrıntı: [docs/reports/README.md](docs/reports/README.md).

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

[GCP dağıtımı](docs/DEPLOY_GCP.md) · [Mimari](docs/ARCHITECTURE.md) · [Proje bağlamı](docs/CONTEXT.md) · [Yol haritası ve pilot girdileri](docs/ROADMAP.md) · [Üçüncü taraflar](docs/THIRD_PARTY_LICENSES.md)
