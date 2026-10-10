# Üçüncü taraf bileşenler

İzge'nin kendi lisansı henüz seçilmedi. Bu belge bir lisans verme beyanı değildir.

## Doğrudan bağımlılıklar

| Bileşen | Sabitlenen sürüm | Lisans / durum |
|---|---:|---|
| FastAPI | 0.143.0 | MIT; resmi sürüm notlarında doğrulandı, bu ortamda kurulamadı |
| Uvicorn | 0.52.4 | BSD-3-Clause; hedef sürüm, backend kurulumu bekliyor |
| Pydantic | 2.13.5 | MIT; hedef sürüm |
| SQLAlchemy | 2.0.44 | MIT; hedef sürüm |
| psycopg / psycopg-binary | 3.2.12 | LGPL-3.0-or-later; binary dağıtımın libpq/OpenSSL bileşenleri ayrıca kaydedilmeli |
| Alembic | 1.17.0 | MIT; hedef sürüm |
| PyAV | 19.0.0 | BSD-3-Clause; wheel içindeki FFmpeg kütüphaneleri ayrıca değerlendirilir |
| python-multipart | 0.0.32 | Apache-2.0; hedef sürüm |
| React / React DOM | 19.2.7 | MIT; yerel önbellekten kuruldu ve build edildi |
| Vite | 8.2.0 | MIT; yerel önbellekten kuruldu ve build edildi |
| TypeScript | 6.0.3 | Apache-2.0; yerel kurulumla kontrol çalıştı |
| @types/react / @types/react-dom | 19.2.17 / 19.2.3 | MIT; geliştirme bağımlılığı |
| pytest / httpx / Ruff | 8.4.2 / 0.28.1 / 0.14.0 | MIT / BSD-3-Clause / MIT; hedef geliştirme sürümleri |

Frontend'in tam çözülmüş paket ağacı, URL'ler ve bütünlük hash'leri `frontend/package-lock.json` içindedir. Paketler resmi npm tarball önbelleğinden kurulmuştur. Backend hedef sürümler `pyproject.toml` içinde sabittir; ağ engeli nedeniyle resolver ve `uv.lock` üretimi tamamlanmamıştır. Bu tablo backend'in kurulduğu veya bütün geçişli bağımlılıkların doğrulandığı iddiası değildir.

## Dağıtımlar ve kodekler

Container hedefleri: `node:22.18.0-bookworm-slim`, `python:3.14.4-slim-bookworm`, `postgres:18.0-bookworm`, `ghcr.io/astral-sh/uv:0.8.4`. Tag sürümleri sabittir; image digest'leri bu ortamda çekilip doğrulanmamıştır. Debian `apt` FFmpeg paketi image build sırasında çözülür; bu nedenle FFmpeg dağıtımı henüz tam tekrarlanabilir biçimde kilitli değildir.

Yerel video testlerinde **Homebrew FFmpeg 8.1**, Apple clang 17.0.6 kullanıldı. Derleme bayrakları:

```text
--enable-shared --enable-pthreads --enable-version3 --enable-ffplay --enable-gpl
--enable-libsvtav1 --enable-libopus --enable-libx264 --enable-libmp3lame
--enable-libdav1d --enable-libvpx --enable-libx265 --enable-openssl
--enable-videotoolbox --enable-audiotoolbox --enable-neon
```

`--enable-gpl`, libx264/libx265 ve `--enable-version3` nedeniyle yerel FFmpeg dağıtımı sade LGPL varsayımıyla tanımlanamaz; GPL v3+ koşulları değerlendirilmelidir. Bu yerel ikili projeye kopyalanmadı. Container'ın Debian FFmpeg derlemesi ayrı bir dağıtımdır; yerel Homebrew bilgisi onun yerine geçmez. PyAV wheel'inin kendi libavcodec/libavformat/libavutil sürümleri de sistem `ffmpeg` komutuyla aynı kabul edilmez.

`scripts/license_inventory.py` gerçek kurulu Python paketlerini, lisans metadata'sını, npm lock ağacını, FFmpeg `-version`/`-L` çıktısını, PyAV kütüphane sürümlerini ve Debian paket listesini JSON'a kaydeder. Docker build bunu `/app/THIRD_PARTY_BUILD.json` olarak üretir. Dağıtım öncesi bu dosya, image digest'leri, lisans metinleri ve gerekli kaynak/yeniden dağıtım yükümlülükleri incelenmelidir. Bu ortamda PyAV kurulu olmadığı için onun gerçek dağıtım kaydı henüz yoktur.

Kaynaklar: [FastAPI sürüm notları](https://fastapi.tiangolo.com/release-notes/), [Vite gereksinimleri](https://vite.dev/guide/), [FFmpeg lisans açıklaması](https://ffmpeg.org/legal.html), [PyAV kaynak/lisans](https://github.com/PyAV-Org/PyAV), [Psycopg lisans](https://www.psycopg.org/docs/license.html), [PostgreSQL lisans](https://www.postgresql.org/about/licence/).
