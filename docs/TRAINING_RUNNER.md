# Native eğitim runner'ı

Swarm Linux container'ı Mac GPU'sunu kullanamaz. Runner native macOS'ta çalışır;
container yalnız onaylı reçete adı ve sınırlı parametre yolları gönderir. Ürün API'si,
worker ve mevcut model kodu değişmez. Marka gösterimi gerekiyorsa `IZGE_APP_NAME` kullanılır.

## Kurulum ve çalıştırma

Repo kökünde uv kurulu olmalıdır. Apple Silicon Mac üzerinde:

```sh
./scripts/runner/setup_macos.sh
```

Bu komut ortamı kurar, gerçek MPS kullanılabilirliğini kontrol eder, runner'ı başlatır
ve kimlik doğrulamalı health yanıtını yazdırır. Linux CPU denemesi veya elle başlatma:

```sh
uv sync --frozen --extra training --extra ml
export PYTHONPATH="$PWD/backend"
export IZGE_DATA_ROOT="$HOME/.izge-runner/data"
export IZGE_RUNS_ROOT="$HOME/.izge-runner/runs"
uv run --extra training --extra ml python -m izge.training.runner
```

Varsayılan bind `127.0.0.1:8765`. Token ilk başlangıçta `~/.izge-runner/token`
konumunda 0600 izniyle oluşturulur; farklı dosya `IZGE_RUNNER_TOKEN_FILE` ile seçilir.
Token içeriğini loga, komut satırına veya repoya koymayın. Dosya erişimini yalnız
runner kullanıcısı ve yetkilendirilmiş container ile sınırlayın.

Docker Desktop erişimi için **açık tercih**:

```sh
uv run --extra training --extra ml python -m izge.training.runner --host 0.0.0.0
```

Bu tüm arayüzlerde dinler; macOS firewall ile yalnız yerel Docker erişimine izin verin.
İnternete port yönlendirmeyin. Token dosyasını container'a salt okunur mount edin.
Container'da (backend paket yolu `PYTHONPATH` üzerinde):

```sh
export IZGE_RUNNER_URL=http://host.docker.internal:8765
export IZGE_RUNNER_TOKEN_FILE=/run/secrets/izge-runner-token
python -m izge.training.client health
python -m izge.training.client submit smoke --overrides '{"device":"cpu","params.epochs":3}'
python -m izge.training.client list
python -m izge.training.client status JOB_ID
python -m izge.training.client logs JOB_ID --tail 50
python -m izge.training.client metrics JOB_ID
python -m izge.training.client cancel JOB_ID
python -m izge.training.client resume JOB_ID
```

Bütün uçlar bearer token ister; `/docs`, `/redoc`, `/openapi.json` kapalıdır.
Reçeteler `config/training/approved/*.yaml` içinde
operatör tarafından yönetilir (`IZGE_TRAINING_CONFIG_ROOT` isteğe bağlı). İstemci komut,
manifest, ağırlık yolu, tracking adresi veya bütçe değiştiremez. Override listesi:
`params.epochs`, `params.learning_rate`, `params.c`, `params.batch_size`,
`params.grad_accum_steps`, `seed`, `device`. Epoch reçetedeki üst sınırı aşamaz.
`eval.split` yalnız `val`; final test split'i eğitim sırasında okunmaz.

## İşler, kaynaklar ve resume

SQLite iş kuyruğu ve run klasörleri `IZGE_RUNS_ROOT` altında kalır. Aynı kökte yalnız
bir runner süreci çalışır (dosya kilidi); aynı anda bir eğitim subprocess'i çalışır.
İptal ve wall timeout tüm process group'u öldürür. Kapanışta çalışan iş interrupted
olur; restart sırasında PID/creation-time doğrulanarak eski süreç öldürülür ve
running kayıtlar interrupted yapılır. Kuyruktaki işler korunur. `resume` yalnız
checkpoint bulunan terminal işte mümkündür; aynı config ve MLflow run id kullanılır.
İptal, timeout, kapanış ve restart recovery sonrasında runner mevcut artifact'ları
MLflow'a yükler ve run durumunu `KILLED` yapar; henüz MLflow run'ı başlamamış iş için
yeni bir run oluşturmaz. RF-DETR resume kapısı ve adaptörü aynı doğal
`rfdetr/checkpoint_*.ckpt` dosyasını seçer. Eğitim subprocess'i sonlanmadan önce
oluşan bu checkpoint yeterlidir; normalize `checkpoints/last.pt` kopyası beklenmez.

Her smoke epoch'u loss, macro precision/recall/F1, süre, CPU/RAM ve MPS bellek
ölçümleri yazar. `status.json`, `metrics.jsonl`, `resources.jsonl`, `train.log`,
`process.log`, `checkpoints/last.pt` ve `best.pt` iş klasöründedir. MLflow params,
metrics ve artifacts alır; varsayılan URI `file:$IZGE_RUNS_ROOT/mlruns`, operatör
`MLFLOW_TRACKING_URI` ile değiştirebilir. Varsayılan file backend için giriş noktası `MLFLOW_ALLOW_FILE_STORE=true` ayarlar;
MLflow 3.17 dosya store kullanımını aksi halde reddeder.
Kaynaklar 0,5 saniyelik aralıklarla ve başlangıç/sonda örneklenir; CPU ölçümü örnek aralığına bağlıdır; GPU/CPU bellek ölçümü peak garantisi değildir.

Doğrudan giriş (servisin timeout/iptal denetimi bu yolda yoktur):

```sh
uv run --extra training --extra ml python -m izge.training.run \
  --config config/training/approved/smoke.yaml --run-dir /tmp/izge-smoke
# Aynı config ile son tam checkpoint'ten:
uv run --extra training --extra ml python -m izge.training.run \
  --config config/training/approved/smoke.yaml --run-dir /tmp/izge-smoke --resume
```

`smoke`, üretilmiş sklearn verisi üzerinde küçük gerçek PyTorch lineer modelidir.
**Yalnız altyapı smoke kanıtıdır, İzge doğruluğu değildir.** Dataset manifest'i bu
reçetede üretilmiş verinin sürümlü kimliğidir; dış veri indirmez.

## Mevcut eğitime adaptörler

`feeding_linear` mevcut `feeding.classifier.fit` ve güvenli NPZ serializer'ı kullanır.
Bir atomik sklearn fit = 1 epoch; `last.pt`/`best.pt` içerikleri NPZ'dir. Manifest:
`version`, `backbone` (mevcut DINOv3 kimliği), `samples` listesi. Her örnek `split`,
`group_id` (kayıt günü/oturum), `embedding` (data root'a göre NPY yolu), `label`,
`origin: manual`, `gold: false` taşır. Train/val/test grupları ayrılır. Embedding
çıkarma mevcut hat ve HF erişimiyle önceden yapılmalıdır; bu adaptör ağırlık indirmez.
Manifest yolu `IZGE_DATA_ROOT/manifests/<dataset>/<version>/manifest.json` biçimindedir.
Dataset üreticisi ayrı modüldür; burada yalnız küçük tüketici kapısı bulunur.

`rfdetr_detect` adı mevcut **Seg Small** betiğine adaptördür; detection-only model
olduğu iddia edilmez. Manifest `dataset_dir` ile insan COCO export'una işaret eder.
Mevcut betiğin Linux x86_64 / Python 3.12 / CUDA ve sabit ağırlık SHA kapıları korunur.
Mac runner bu reçeteyi açık platform önkoşul hatasıyla durdurur. Linux x86_64 üzerinde
ana Python 3.14 süreç, sabit `uv run --project experiments/rfdetr_training --frozen`
komutuyla ayrı Python 3.12 ortamını kullanır. Onaylı ortamda
betiğin doğal `rfdetr/` checkpoint/raporları korunur; classification metrikleri
uydurulmaz. Bu sürüm RF-DETR per-epoch ortak metrik/checkpoint normalizasyonu sağlamaz;
gerçek CUDA verisi/ortamıyla tamamlanması gerekir. Native Mac RF-DETR eğitimi kabulü yoktur.
