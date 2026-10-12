#!/usr/bin/env bash
# data/samples altındaki ham NVR kayıtlarını denetler ve referans kare çıkarır.
#
# Kullanım:
#   scripts/prepare_samples.sh                         # yalnız denetim: kodek, çözünürlük, fps, süre
#   scripts/prepare_samples.sh GUNDUZ.mp4 GECE.mp4     # ayrıca ref_gunduz.png ve ref_gece.png çıkarır
#   REF_AT=00:10:00 scripts/prepare_samples.sh G.mp4 N.mp4   # kare zamanı (varsayılan 00:05:00)
#
# Dosya adlarını değiştirmeyin: kayıt saati dosya adındaki regex'ten okunur.
set -euo pipefail

DIR="${SAMPLES_DIR:-data/samples}"
REF_AT="${REF_AT:-00:05:00}"
mkdir -p "$DIR"

command -v ffprobe >/dev/null || { echo "ffprobe bulunamadı (brew install ffmpeg)"; exit 2; }
command -v ffmpeg >/dev/null || { echo "ffmpeg bulunamadı (brew install ffmpeg)"; exit 2; }

shopt -s nullglob nocaseglob
files=("$DIR"/*.mp4)
shopt -u nocaseglob
if [ ${#files[@]} -eq 0 ]; then
  echo "$DIR içinde .mp4 yok. NVR kayıtlarını (adlarını değiştirmeden) buraya koyun."
  exit 1
fi

bad=0
printf '%-48s %-8s %-11s %-9s %s\n' DOSYA KODEK COZUNURLUK FPS SURE
for f in "${files[@]}"; do
  info=$(ffprobe -v error -select_streams v:0 \
    -show_entries stream=codec_name,width,height,r_frame_rate:format=duration \
    -of default=nw=1 "$f" 2>/dev/null || true)
  codec=$(sed -n 's/^codec_name=//p' <<<"$info")
  w=$(sed -n 's/^width=//p' <<<"$info")
  h=$(sed -n 's/^height=//p' <<<"$info")
  fps=$(sed -n 's/^r_frame_rate=//p' <<<"$info")
  dur=$(sed -n 's/^duration=//p' <<<"$info")
  printf '%-48s %-8s %-11s %-9s %ss\n' "$(basename "$f")" "${codec:-?}" "${w:-?}x${h:-?}" "${fps:-?}" "${dur%.*}"
  if [ "$codec" != "h264" ]; then
    echo "  ! $(basename "$f"): codec_name=${codec:-okunamadı}; H.264 olmalı (NVR'da H.264 dışa aktarımı seçin)"
    bad=1
  fi
done

extract() {  # extract <kaynak> <hedef>
  local src="$1" out="$2" dur at
  [ -f "$src" ] || src="$DIR/$src"
  [ -f "$src" ] || { echo "Bulunamadı: $1"; exit 1; }
  dur=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$src")
  at="$REF_AT"
  if awk -v d="$dur" 'BEGIN{exit !(d < 301)}'; then at="00:00:05"; fi  # kısa kayıt: 5. saniye
  ffmpeg -v error -y -ss "$at" -i "$src" -frames:v 1 "$out"
  echo "Yazıldı: $out  (kaynak: $(basename "$src"), an: $at)"
}

if [ $# -ge 1 ]; then extract "$1" "$DIR/ref_gunduz.png"; fi
if [ $# -ge 2 ]; then extract "$2" "$DIR/ref_gece.png"; fi

exit $bad
