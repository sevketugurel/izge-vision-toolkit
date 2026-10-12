#!/usr/bin/env bash
# NVR kayıtlarını (.dav, .mp4) H.264 MP4'e çevirir; çıktı `<klasör>/h264/` altına, AYNI dosya adıyla yazılır.
#
# Kullanım:
#   scripts/convert_dav.sh                 # data/samples/*.dav ve *.mp4
#   scripts/convert_dav.sh a.dav b.MP4     # belirli dosyalar
#
# - Kodek zaten H.264 ise yeniden kodlamadan paketler (-c copy): kayıpsız, hızlı.
# - H.265/diğer ise H.264'e kodlar (CRF 23). Ses atılır; analiz yalnız görüntü kullanır.
# - Girdi dosyaları asla değiştirilmez/silinmez. macOS'ta .MP4 ile .mp4 aynı dosyadır;
#   bu yüzden çıktı ayrı alt klasöre yazılır.
set -euo pipefail

DIR="${SAMPLES_DIR:-data/samples}"
OUTDIR="$DIR/h264"
command -v ffmpeg >/dev/null && command -v ffprobe >/dev/null || { echo "ffmpeg/ffprobe gerekli (brew install ffmpeg)"; exit 2; }

if [ $# -gt 0 ]; then
  files=("$@")
else
  shopt -s nullglob nocaseglob
  files=("$DIR"/*.dav "$DIR"/*.mp4)
  shopt -u nocaseglob
fi
[ ${#files[@]} -gt 0 ] || { echo "$DIR içinde .dav/.mp4 yok."; exit 1; }
mkdir -p "$OUTDIR"

fail=0
for src in "${files[@]}"; do
  [ -f "$src" ] || { echo "Bulunamadı: $src"; fail=1; continue; }
  base="$(basename "$src")"; stem="${base%.*}"
  out="$OUTDIR/$stem.mp4"
  if [ -e "$out" ]; then echo "Atlandı (zaten var): $out"; continue; fi

  codec=$(ffprobe -v error -select_streams v:0 -show_entries stream=codec_name -of csv=p=0 "$src" 2>/dev/null || true)
  if [ -z "$codec" ]; then
    echo "HATA: $src okunamadı (video akışı yok). NVR'ın kendi oynatıcısından dışa aktarım deneyin."
    fail=1; continue
  fi

  tmp="$OUTDIR/$stem.partial.mp4"
  if [ "$codec" = "h264" ]; then
    echo "[$codec] paketleniyor (kayıpsız): $base -> h264/$stem.mp4"
    ffmpeg -v error -stats -y -fflags +genpts -i "$src" -map 0:v:0 -c:v copy -an -movflags +faststart -f mp4 "$tmp"
  else
    echo "[$codec] H.264'e yeniden kodlanıyor: $base -> h264/$stem.mp4"
    ffmpeg -v error -stats -y -i "$src" -map 0:v:0 -c:v libx264 -preset medium -crf 23 -pix_fmt yuv420p -an -movflags +faststart -f mp4 "$tmp"
  fi
  mv "$tmp" "$out"
done

echo
echo "Şimdi denetleyin: SAMPLES_DIR=$OUTDIR scripts/prepare_samples.sh"
exit $fail
