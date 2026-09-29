#!/bin/sh
# Regenerate examples/sample-report/ — operator decision D1 (2026-09-25), roadmap item 14.
#
#   ./scripts/make-sample-report.sh http://model-host.example.lan:11434 MODEL
#
# MODEL is one the host has installed: read GET /api/tags on the host rather than naming one from
# memory. The course is the synthetic examples/sample-course; nothing here contacts Canvas.
#
# What it does, in order:
#   1. builds the dev image, pulling the base, so the render happens in the environment CI checks it
#      in (the image job re-renders the committed sample there and compares the bytes);
#   2. runs scripts/sample_report_inside.py in that image against a SCRATCH data folder: the normal
#      batch run over examples/sample-course, open-review, and the export through the gate;
#   3. copies the review file, the PDF, the workbook and render.json into examples/sample-report/.
#
# Nothing under src/sightline writes into examples/: the package's write guards stay whole, and the
# copy is this script's, outside the package. Re-run it whenever the committed sample stops
# matching — a change to the phrase bank, the templates, the fonts, uv.lock, or a new base image
# (its pango or harfbuzz release) all move the bytes.
#
# POSIX sh, like scripts/dev.sh.
set -eu

if [ "$#" -ne 2 ]; then
    echo "usage: $0 MODEL_HOST_URL MODEL" >&2
    exit 2
fi
endpoint=$1
model=$2

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
image=${SIGHTLINE_SAMPLE_IMAGE:-sightline:sample-report}
target="$repo_root/examples/sample-report"

docker build --pull --target dev -t "$image" "$repo_root" >&2

scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
mkdir "$scratch/data" "$scratch/out"

# --user: the files come back owned by whoever ran this, not by root.
docker run --rm \
    --user "$(id -u):$(id -g)" \
    -e SIGHTLINE_DATA_DIR=/scratch/data \
    -e HOME=/scratch \
    -v "$scratch:/scratch" \
    -v "$repo_root/scripts:/scripts:ro" \
    "$image" \
    python /scripts/sample_report_inside.py --endpoint "$endpoint" --model "$model" --out /scratch/out

mkdir -p "$target"
for name in review.json report.pdf review.xlsx render.json; do
    cp "$scratch/out/$name" "$target/$name"
done
echo "examples/sample-report/ regenerated; commit it with the change that moved it." >&2
