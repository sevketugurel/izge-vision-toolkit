# Yol haritası

## Teslimin kapanması için öncelikli doğrulamalar

1. Ağ erişimi olan Python 3.14 ortamında `uv lock`, `uv sync --locked`; uyumluluk kontrolü ve gerçek `uv.lock` dosyasını repoya ekleme. Dockerfile'ı kilitli kuruluma geçirme.
2. Boş PostgreSQL 18 veritabanında Alembic upgrade, entegrasyon suite'i ve iki worker ile eşzamanlı claim/lease kesilme denemesi.
3. `docker compose up --build`, API/worker sağlık kontrolleri, demo seed, gerçek fixture yükleme ve yeniden başlatma.
4. Tarayıcı smoke ve gerçek API'yle kullanıcı akışı; masaüstü/mobil SVG sürükleme, video Range seek, kayıt saati ve export.
5. Uzun dosya, disk dolması, beklenmeyen worker kapanışı ve bağlantısız kanıt dosyası temizleme testleri.

Bunlar henüz doğrulanmış saha özelliği değildir. Ayrıntılı mevcut kontrol sonuçları VERIFICATION.md dosyasındadır.

## Gerçek pilot için gerekli girdiler

- Orijinal gündüz/gece video örnekleri; yeniden kodlanmış sosyal medya kopyası yerine kamera/NVR kaydı.
- Doğrulanmış kayıt başlangıçları, saat dilimi ve kameralar arasındaki saat farkı.
- Ahır ve yerleşim krokisi; kamera konumları; tarihli hayvan-yer değişiklikleri.
- Gövde, baş ve yemlik görünürlüğü; örtüşme, karanlık ve örtülme örnekleri.
- Öğün takvimi ve bundan ayrı zamanlı yem varlığı bilgisi.
- NVR markası, modeli, kodekleri, çözünürlük/FPS/bitrate, RTSP desteği ve teknik erişim bilgileri; parolalar ayrı secret kanalıyla.
- GPU modeli/belleği ve hedef donanım; CPU/model seçimi buna göre yapılacak.
- İnsan incelemesiyle etiketlenmiş gerçek engellenme/normal etkileşim örnekleri ve başarı ölçütleri.

## Sonraki geliştirmeler

- Gerçek `Detector` / `BehaviorAnalyzer` ve olay zamanındaki yerleşime bağlı `IdentityResolver` adaptörü.
- Kamera geometrisi için kalibrasyon ve görünürlük kalite skoru; model sürümlerinin kayıt defteri.
- RTSP'den kalıcı segmentleme, yeniden bağlanma boşluklarının veritabanına yazılması, sürekli health ekranı ve gerçek NVR soak testi.
- Uzun dosyalarda belleği sınırlayan artımlı türetme ve çok kameralı rapor performansı.
- Kimlik doğrulama, işletme yetkilendirmesi, TLS, yedek/geri yükleme ve denetim izi.
- Kullanıcı yönetimi, saklama politikası ve insan incelemesine ayrılan kanıtların arşivi.

Mevcut sözleşmeler korunmalı: kaynak offseti/PTS, kayıt saati sürümü, kimlik belirsizliği, görünürlük, duruş/etkinlik ayrımı, kanıt ilişkileri, köken ve değiştirilemeyen inceleme geçmişi. Model eklenmesi veri katmanını yeniden tasarlamamalıdır.
