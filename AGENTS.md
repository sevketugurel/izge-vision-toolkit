# İzge çalışma kuralları

- Her oturumda önce docs/V1_PLAN.md ve docs/VERIFICATION.md'yi oku; kaldığın kilometre taşından devam et.
- Kullanıcıya Türkçe, kod ve tiplerde İngilizce kullanın. Marka adı `IZGE_APP_NAME` üzerinden gelir.
- Domain modülü ORM, HTTP ve video kitaplıklarına bağımlı olmamalıdır.
- `manual`, `model`, `demo`, `unavailable` kökenlerini koruyun. Demo yalnızca demo işletmesi ve demo medyada çalışır.
- Manuel inceleme otomatik rapor sürelerini değiştirmez; geçmişi güncellemek/silmek yerine ek kayıt oluşturun.
- İşletme ilişkilerini birleşik foreign key ile koruyun. Video endpoint'lerine disk yolu eklemeyin.
- Saat doğrulanmadan güncel yerleşimden hayvan kimliği çıkarmayın. PTS yerine nominal FPS hesabı kullanmayın.
- Son gözlemi dosya sonuna uzatmayın. Ölçülen ve bilinmeyen süreyi ayrı tutun.
- Worker sonuç yazımları ve yayın transaction'ları lease token ve süre kontrolü taşımalı.
- `schema_v1.py` ilk migration'ın dondurulmuş şema kaydıdır. Yeni şema değişikliklerini yeni migration ile yapın.
- PostgreSQL 18 entegrasyon testini gerçek PostgreSQL üzerinde çalıştırın; SQLite veya PostgreSQL 14 başarısını hedef sürüm kanıtı diye sunmayın.
- Tam doğrulama durumu `docs/VERIFICATION.md` içindedir. Çalıştırılmayan kontrolleri başarılı saymayın.
- Secret değerlerini API, export veya loglara yazmayın. RTSP hata mesajlarında ham exception metni kullanmayın.
- Kullanıcı açıkça istemedikçe alt ajan kullanmayın.
