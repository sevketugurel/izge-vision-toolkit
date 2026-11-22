# Yerel RF-DETR-Seg Small tespit deneyi

Yalnız stage 2 çıkarımı: `RFDETRSegSmall`, rfdetr 1.11.2, revision
`7433d5919d0f7e72cc6cb78663e0a731afd499e0`. Ağırlık kaynağı/checksum
[model.lock.json](model.lock.json) içindedir. Ana Python 3.14 ortamı ve kök `uv.lock` değiştirilmez.
Yerel deney Python 3.12.11, macOS arm64 ve uv 0.8.4 ile doğrulandı. Linux/CUDA ortamı bu kilidin kapsamı değildir.

Repo kökünden:

```sh
uv sync --locked --project experiments/rfdetr_seg --python /opt/homebrew/bin/python3.12
.venv/bin/python scripts/fetch_behavior_model.py --output models/rfdetr/rf-detr-seg-small.pt
experiments/rfdetr_seg/.venv/bin/python scripts/inspect_behavior_detection.py \
  --source ./local-input.mp4 \
  --config config/behavior/reference_barn.yaml \
  --model models/rfdetr/rf-detr-seg-small.pt \
  --device mps --mode diagnostic-full-frame --threshold 0.5 \
  --output-dir artifacts/behavior-stage2-new-run
.venv/bin/python scripts/review_behavior_rois.py \
  --source ./local-input.mp4 \
  --config config/behavior/reference_barn.yaml \
  --notes ./local-roi-notes.md \
  --output-dir artifacts/behavior-stage2-new-run/roi-review
experiments/rfdetr_seg/.venv/bin/python experiments/rfdetr_seg/audit_environment.py \
  --output artifacts/behavior-stage2-new-run/environment_audit.json
```

İlk kurulum/indirme ağ erişimi gerektirir; CLI yalnız checksum'ı doğru yerel ağırlığı yükler. Dolu çıktı klasörü
reddedilir; aynı komutu tekrar çalıştırırken **yeni** output-dir seçin. CPU için `--device cpu`; `auto` MPS
erişilemezse CPU seçer ve nedeni manifest'e yazar. Bu macOS sandbox'ında MPS False, sandbox dışında gerçek
modelle True bulundu. MPS komutu uygun cihaz erişimiyle çalıştırılmalıdır. CUDA bu makinede yoktur.

## Lisans nedeniyle açık runtime daraltması

Resmî paket ve seçilen Seg Small ağırlığı Apache-2.0; tüm RF-DETR/Plus ailesi bu lisans altında değildir.
Standart rfdetr bağımlılığı Supervision 0.29 üzerinden gelen OpenCV 5.0.0.93 ve denenen 4.12.0.88 macOS
wheel'lerinin **gerçek libavcodec** ikilisi GPLv3 ve `--enable-gpl` bildirdi. Bu wheel'ler son ortamdan çıkarıldı.
Ana projenin lisans politikası değiştirilmedi. Ayrıntılar [üçüncü taraf bildirimleri](../../THIRD_PARTY_NOTICES.md).

Bu macOS'a sınırlı projede `override-dependencies = ["supervision; sys_platform == 'win32'"]` açıkça
Supervision'u dışlar. Genel `RFDETR.predict()`/Supervision anotasyon API'si ve eğitim bu ortamda desteklenmez.
Adaptör, **gerçek RFDETRSegSmall ModelContext içindeki LWDETR ağı ve kendi PostProcess katmanını** kullanır;
stub, rastgele ağırlık veya başka model kullanmaz. PyTorch/torchvision RGB→float→bilinear 384×384
(`antialias=False`)→ImageNet normalize akışı sabit sürümün `detr.py` işlemleriyle aynıdır. PostProcess orijinal
girdi H,W ile kutu ve boolean maskeyi geri ölçekler; adaptör ikinci inverse resize uygulamaz. ROI crop varsa
yalnız crop kökeni eklenir. SciPy 1.18.1, rfdetr matcher import'u için açık bağımlılıktır.

48 kurulu dağıtımın metadatası/lisans dosyası checksum'ları `environment_audit.json` ile kaydedilir.
**Önceden açık PyAV 19 codec konusu kapanmadı:** av wheel'i LGPLv3 bildirirken x264/x265 içerir; bu deney
üretim dağıtım lisans kabulü değildir. Doğrudan ağ çağrısı yalnız model indirme aracındadır; çıkarım hiçbir
token, yeni ağırlık, eğitim/Plus/Ultralytics paketi istemez.

`verified-roi` ayrı moddur: `require_analysis_ready`, tam çözünürlük ve referans checksum kapıları uygulanır;
taslakla reddedilir. Doğrulanmış config farklı bir yolda ve gerçek insan doğrulama kaydıyla sağlanmalıdır.
Tam kare modunda `roi_verified=false`, `roi_analysis_allowed=false`, `experiment_only=true` kalır.

Normal `.venv/bin/pytest` model indirmez. Gerçek model smoke komutu yukarıdadır ve ayrı kayıtlıdır.
Yükleme (import+kurucu+cihaz transferi/eval) ve her kare çıkarımı manifest'te ayrıdır; ilk çıkarım soğuktur,
zamanlar dört karelik yerel ölçümlerdir ve kapasite/gerçek zaman garantisi değildir.
