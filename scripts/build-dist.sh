#!/usr/bin/env bash
#
# Build packaged .skill / .zip downloads of whole skill folders.
#
# The website links its "Download skill" buttons at the files in dist/ for the
# skills listed below. A package holds every file git tracks under
# skills/<name>/ (SKILL.md plus any scripts, references and templates), so
# commit a change before rebuilding. Rerun this whenever a packaged skill
# changes, so the download never goes stale (a stale package is how the
# pre-migration website drifted behind the repo).
#
# Archives use a fixed mtime, fixed permissions and a sorted file list with no
# directory entries, so re-runs are byte-reproducible and a no-op unless a file
# actually changed. A single-file skill packages exactly as it always has.
#
set -euo pipefail
cd "$(dirname "$0")/.."

# Skills offered as packaged downloads (matches the website's download buttons).
SKILLS=(
  amass-biomedical-evidence-scout
  amass-watchlist-monitor
  amass-landscape-monitor
)

FIXED_MTIME=202601010000.00   # fixed timestamp -> byte-reproducible archives

mkdir -p dist
for name in "${SKILLS[@]}"; do
  [ -f "skills/$name/SKILL.md" ] || { echo "error: missing skills/$name/SKILL.md" >&2; exit 1; }

  stage="$(mktemp -d)"
  count=0
  while IFS= read -r -d '' file; do
    rel="${file#skills/}"
    mkdir -p "$stage/$(dirname "$rel")"
    cp "$file" "$stage/$rel"
    chmod 644 "$stage/$rel"
    touch -t "$FIXED_MTIME" "$stage/$rel"
    count=$((count + 1))
  done < <(git ls-files -z -- "skills/$name")

  ( cd "$stage" && find "$name" -type f | LC_ALL=C sort | zip -q -X -@ "$name.zip" )
  mv "$stage/$name.zip" "dist/$name.zip"
  cp "dist/$name.zip" "dist/$name.skill"   # a .skill is just the .zip, renamed
  rm -rf "$stage"

  echo "built dist/$name.zip and dist/$name.skill ($count files)"
done
