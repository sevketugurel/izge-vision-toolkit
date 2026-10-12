#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from izge.vision.artifact_io import checksum  # noqa: E402
from izge.vision.proposal_review import proposals  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("--package", type=Path, required=True)
a = p.parse_args()
m = json.loads((a.package / "manifest.json").read_text())
q = json.loads((a.package / "queue.json").read_text())
model = next(r["model_class"] for r in reversed(m["runs"]) if r["status"] == "success")
data = {
    "frames": m["frames"],
    "queue": q,
    "proposals": list(proposals(a.package).values()),
    "model": model,
    "package_sha256": checksum(a.package / "manifest.json"),
}
html = """<!doctype html><html lang="tr"><meta charset="utf-8"><title>İnsan inceleme pilotu</title>
<style>body{font:16px system-ui;background:#15191e;color:#edf3f5;margin:20px}button,select,input{font:inherit;margin:4px;padding:8px}canvas{width:min(100%,1100px);height:auto;touch-action:none;background:black}#status{color:#ffcf55}#objects{max-width:1100px}label{display:inline-block}#feedback{color:#66e0b2}</style>
<h1>Model önerisi üzerinden küçük insan inceleme kuyruğu</h1><p id="status">DENEY — ROI/SPLIT DOĞRULANMADI; DURUŞ MODELİ YOK. Öneri açmak insan/gold etiketi oluşturmaz.</p>
<p>Kesit incelemesi tam kare instance etiketi değildir. Öneriyi reddetmek hayvan yok demek değildir. Unknown/atla eğitim için kullanılmaz. Baş yemlikte olmak yeme/geviş değildir.</p>
<label>Gerçek inceleyen <input id="reviewer" autocomplete="off"></label><label>Önceki JSON <input id="resume" type="file" accept=".json"></label><button id="export">Sürümlü JSON indir</button>
<p>Built with DINOv3 — özellikler örnek seçimi içindir; duruş kararı değildir.</p><p id="position"></p><button id="previous">Önceki</button><button id="next">Sonraki</button><div id="objects"></div><p id="selected"></p>
<button id="goto-crops">Kesit kuyruğu</button><button id="goto-full">Tam kare kuyruğu</button><p><img id="crop-preview" hidden style="max-width:420px;max-height:420px"></p><canvas id="canvas"></canvas><div><label>Düzenleme <select id="mode"><option value="inspect">İncele</option><option value="bbox">Kutu çiz</option><option value="paint">Maskeye ekle</option><option value="erase">Maskeden sil</option><option value="polygon">Yeni maske çokgeni</option></select></label><label>Fırça piksel <input id="radius" type="number" value="20" min="1" max="100"></label><button id="polygon">Çokgenle maskeyi değiştir</button><button id="maskbox">Kutuyu maskeye uydur</button><button id="add">Kaçırılan hayvan ekle</button></div>
<label>Duruş <select id="posture"><option value="">Seçin</option><option value="standing">Ayakta</option><option value="lying">Yatıyor</option><option value="unknown">Belirsiz</option></select></label>"""
for name, label in [("head_visibility", "Baş görünürlüğü"), ("body_visibility", "Gövde görünürlüğü")]:
    html += (
        f'<label>{label} <select id="{name}"><option value="">Seçin</option>'
        + "".join(f"<option>{s}</option>" for s in ["visible", "partial", "not_visible", "uncertain"])
        + "</select></label>"
    )
html += (
    """<label>Neden/not <input id="reason"></label><div>"""
    + "".join(
        f'<button id="{i}">{label}</button>'
        for i, label in [
            ("accept", "Onayla"),
            ("correct", "Düzeltmeyi kaydet"),
            ("reject", "Öneriyi reddet"),
            ("unknown", "Unknown"),
            ("skip", "Atla"),
        ]
    )
    + """</div><div id="complete"><label><input type="checkbox" id="all_visible"> Bütün etiketlenebilir hayvanları inceledim; kaçırılanları ekledim; belirsiz instance kalmadı.</label><button id="finish">Tam kare incelemesini tamamla</button></div><p id="feedback"></p><p>Maske: fırça ekle/sil veya çokgenle değiştir; birleşmiş maskeyi silip ikinci hayvanı ayrı ekleyin. Geometri orijinal piksellerdedir. Aktif süre yalnız yakın aralıklı etkileşimlerden; idle/açık sayfa süresi sayılmaz. JSON'u ayrı revision olarak saklayın; README import doğrulamasını kullanın.</p><script>window.REVIEW_DATA="""
    + json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
    + """;</script><script src="review.js"></script></html>"""
)
with (a.package / "review.html").open("x") as f:
    f.write(html)
(a.package / "review.js").write_text((Path(__file__).parent / "behavior_review/review.js").read_text())
print("Review HTML created; human events remain zero")
