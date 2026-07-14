#!/usr/bin/env bash
#
# Build packaged .skill / .zip downloads from the canonical SKILL.md sources.
#
# The website links its "Download .skill / .zip" buttons at the files in dist/.
# Rerun this whenever a packaged skill's SKILL.md changes so the download never
# goes stale (a stale package is how the pre-migration website drifted behind
# the repo). Archives use a fixed mtime, so re-runs are reproducible and a
# no-op unless the SKILL.md actually changed.
#
set -euo pipefail
cd "$(dirname "$0")/.."

# Skills offered as packaged downloads (matches the website's download buttons).
SKILLS=(amass-biomedical-evidence-scout)

FIXED_MTIME=202601010000.00   # fixed timestamp -> byte-reproducible archives

mkdir -p dist
for name in "${SKILLS[@]}"; do
  src="skills/$name/SKILL.md"
  [ -f "$src" ] || { echo "error: missing $src" >&2; exit 1; }

  stage="$(mktemp -d)"
  mkdir -p "$stage/$name"
  cp "$src" "$stage/$name/SKILL.md"
  touch -t "$FIXED_MTIME" "$stage/$name/SKILL.md"

  ( cd "$stage" && zip -q -X "$name.zip" "$name/SKILL.md" )
  mv "$stage/$name.zip" "dist/$name.zip"
  cp "dist/$name.zip" "dist/$name.skill"   # a .skill is just the .zip, renamed
  rm -rf "$stage"

  echo "built dist/$name.zip and dist/$name.skill"
done
