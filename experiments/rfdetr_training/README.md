# Ayrı RF-DETR-Seg Small eğitim hazırlığı

**Eğitim başlamadı.** Bu proje Mac çıkarımından farklı Linux x86_64/Python3.12.11/CUDA12.6 ortamıdır.
Ana proje ve `experiments/rfdetr_seg/uv.lock` değiştirilmez. Hesap/GPU ve insan etiketli bağımsız split eksik.

- [Colab notebook](colab_seg_small.ipynb): normal notebook/uv akışı, private seçilmiş COCO zip, checksum,
  insan/split gate, GPU check, batch/accumulation, bir epoch smoke, persistent checkpoint/resume, validation,
  kilitli test release ve artifact export hücreleri. Varsayılan RUN_SMOKE/RUN_TRAINING False.
- `train.py --check-environment`: gerçek training import/config ve sentetik COCO loader sözleşmesi; fit/model
  eğitimi yapmaz. CPU import check ile CUDA eğitim kabulü ayrı raporlanır.
- `train.py --allow-training ...`: yalnız human segmentation dataset, bağımsız split, doğru ağırlık SHA256
  ve CUDA GPU varsa eğitim açılır. Bbox-only veya model_proposal export reddedilir.
- `Dockerfile`: yalnız image/COCO eğitim; video/PyAV/OpenCV/Supervision/Roboflow istemcisi yok. Grid, uzak
  dataset indirme ve bu sarmalayıcı API'leri desteklenmez; torchvision augmentation / faster-coco-eval değerlendirmesi kullanılır.

Linux'ta veya GPU Colab runtime'ında, repo/kod kiti kökünden:

```sh
uv sync --locked --project experiments/rfdetr_training --python 3.12.11
experiments/rfdetr_training/.venv/bin/python experiments/rfdetr_training/train.py --check-environment
experiments/rfdetr_training/.venv/bin/python experiments/rfdetr_training/train.py \
  --allow-training --smoke --dataset /path/to/verified-human-coco \
  --weights /path/to/rf-detr-seg-small.pt --output-dir /persistent/path/smoke-new \
  --batch-size 1 --grad-accum-steps 4
```

İkinci komut eğitim değildir. Üçüncü komut bu tur **çalıştırılmadı**; gerçek insan maskeleri/split olmadan
başlatılmamalı. Smoke başarılıysa ayrı yeni output-dir ile `--epochs 20`; gerekirse tam
`checkpoint_{epoch}.ckpt` için `--resume`. Kalıcı çıktı dizini her epoch checkpoint saklamalıdır;
Colab geçici `/content` tek kopya olamaz. `--evaluate-test` geliştirmede kapalı kalır.

GCP alternatifinde aynı image `docker run --gpus all` ile yalnız kullanıcının seçilmiş projesinde/GPU'da
çalışır; bu tur ücretli VM yok. Dataset readonly, output persistent volume olmalıdır.
Yerel image API testi örneği (NVIDIA GPU yoksa CUDA False):

```sh
docker run --rm --platform linux/amd64 izge-rfdetr-training:1.11.2 --check-environment
```

Image build için kaynak kökünden Dockerfile kullanılabilir. Bu tur build context'e yalnız backend kodu ve
eğitim recipe kopyalandı; data/samples ve model ağırlıkları context'e girmedi. Kurulum CUDA wheel'leri nedeniyle
birkaç GB indirebilir. Lock 98 kayıt (97 dağıtım+proje) içerir; exact bağımlılık sürümleri lock'tadır.
Mac sandbox/çıkarım ortamına bu paketleri kurmayın.

[Compute/bütçe planı](../../docs/TRAINING_COMPUTE_PLAN.md),
[insan etiketleme iş akışı](../../docs/ANNOTATION_WORKFLOW.md),
[lisans envanteri](../../docs/THIRD_PARTY_LICENSES.md).
