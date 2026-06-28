#!/usr/bin/env bash
# Bump the JARVIS version everywhere it appears (HACS integration layout).
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
COMP="$ROOT/custom_components/jarvis"
PANEL="$COMP/frontend/jarvis-panel.js"

# Current version from the integration manifest (source of truth).
OLD="$(python3 -c "import json;print(json.load(open('$COMP/manifest.json'))['version'])")"
if [[ "$OLD" == "$NEW" ]]; then
  echo "Already at $NEW — nothing to do."
  exit 0
fi
echo "Bumping $OLD → $NEW"

# Plain X.Y.Z in the manifest.
sed -i "s/\b${OLD//./\\.}\b/${NEW}/g" "$COMP/manifest.json"

# vX.Y.Z in the dashboard footer/masthead.
sed -i "s/v${OLD//./\\.}\b/v${NEW}/g" "$PANEL"

echo "Updated:"
echo "  manifest.json   -> $(python3 -c "import json;print(json.load(open('$COMP/manifest.json'))['version'])")"
echo "  jarvis-panel.js -> $(grep -om1 "v${NEW}" "$PANEL" | head -1 || echo '(check manually)')"
echo
echo "Next: review CHANGELOG.md, then  git commit -am 'Release v$NEW' && git tag v$NEW && git push --tags"
echo "(HACS publishes from the git tag/release — no add-on build.)"
