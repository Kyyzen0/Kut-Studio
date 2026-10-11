#!/usr/bin/env bash
# Generate synthetic media for POC benchmarks (identical recipes across runs).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${ROOT}/media"
mkdir -p "${OUT}"
SECONDS_LEN="${1:-6}"

gen() {
  local name="$1" size="$2" rate="$3" encoder="$4" pix="$5"
  local path="${OUT}/${name}.mp4"
  if [[ -f "${path}" ]]; then
    echo "exists ${path}"
    return
  fi
  echo "generating ${path}"
  extra=()
  if [[ "${encoder}" == "libx265" ]]; then
    extra+=(-x265-params log-level=error -tag:v hvc1)
  fi
  ffmpeg -hide_banner -y -f lavfi -i "testsrc2=size=${size}:rate=${rate}" \
    -t "${SECONDS_LEN}" -vf "noise=alls=8:allf=t" \
    -c:v "${encoder}" -preset veryfast -crf 23 -pix_fmt "${pix}" -g 60 \
    "${extra[@]}" "${path}"
}

gen test_1080p30 1920x1080 30 libx264 yuv420p
gen test_1080p60 1920x1080 60 libx264 yuv420p
gen test_4k30 3840x2160 30 libx264 yuv420p
gen test_4k60 3840x2160 60 libx264 yuv420p
if ffmpeg -hide_banner -encoders 2>/dev/null | grep -q libx265; then
  gen test_1080p30_hevc 1920x1080 30 libx265 yuv420p || echo "WARN: HEVC media skipped"
else
  echo "WARN: libx265 unavailable; skipping HEVC media"
fi

echo "Media ready in ${OUT}"
