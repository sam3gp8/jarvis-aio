#!/usr/bin/env bash
# Bump the JARVIS version everywhere it appears.
# Usage: ./scripts/bump_version.sh 6.3.3
set -euo pipefail

NEW="${1:-}"
if [[ -z "$NEW" ]]; then
  echo "Usage: $0 <new-version>   e.g. $0 6.3.3" >&2
  exit 1
fi
if ! [[ "$NEW" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "Version must look like X.Y.Z (got '$NEW')" >&2
  exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ADDON="$ROOT/jarvis_assistant"
COMP="$ADDON/jarvis_component"

# Current version from the integration manifest (source of truth).
OLD="$(python3 -c "import json;print(json.load(open('$COMP/manifest.json'))['version'])")"
if [[ "$OLD" == "$NEW" ]]; then
  echo "Already at $NEW — nothing to do."
  exit 0
fi
echo "Bumping $OLD → $NEW"

# Plain X.Y.Z occurrences (manifest, config, build args, Dockerfile).
sed -i "s/\b${OLD//./\\.}\b/${NEW}/g" \
  "$COMP/manifest.json" \
  "$ADDON/config.yaml" \
  "$ADDON/build.yaml" \
  "$ADDON/Dockerfile"

# vX.Y.Z occurrences (run.sh banner + dashboard footer).
sed -i "s/v${OLD//./\\.}\b/v${NEW}/g" \
  "$ADDON/run.sh" \
  "$COMP/frontend/jarvis-panel.js"

echo "Updated:"
echo "  manifest.json   -> $(python3 -c "import json;print(json.load(open('$COMP/manifest.json'))['version'])")"
echo "  config.yaml     -> $(grep -m1 '^version:' "$ADDON/config.yaml")"
echo "  build.yaml      -> $(grep -m1 'JARVIS_VERSION' "$ADDON/build.yaml")"
echo "  Dockerfile      -> $(grep -m1 'JARVIS_VERSION' "$ADDON/Dockerfile")"
echo "  run.sh          -> $(grep -om1 "v${NEW}" "$ADDON/run.sh" | head -1 || echo '(check manually)')"
echo "  jarvis-panel.js -> $(grep -om1 "v${NEW}" "$COMP/frontend/jarvis-panel.js" | head -1 || echo '(check manually)')"
echo
echo "Next: review CHANGELOG.md, then  git commit -am 'Release v$NEW' && git tag v$NEW && git push --tags"
