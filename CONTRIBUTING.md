# Contributing to JARVIS

Thanks for your interest in improving JARVIS. This document covers the repository
layout, the development workflow, and the release process.

## Repository layout

```
jarvis-aio/                         repo root (Home Assistant add-on repository)
├── repository.json                 add-on repository descriptor
├── README.md  CHANGELOG.md  LICENSE
├── .github/                         funding, issue templates, CI
├── scripts/bump_version.sh          one-command version bump
└── jarvis_assistant/                the add-on (slug: jarvis_assistant)
    ├── config.yaml  build.yaml      add-on manifest + build matrix
    ├── Dockerfile   run.sh          container + zero-touch bootstrap
    ├── bootstrap.py
    ├── icon.png  logo.png
    └── jarvis_component/            the bundled custom integration (domain: jarvis)
        ├── manifest.json
        ├── __init__.py + 42 modules
        └── frontend/jarvis-panel.js the dashboard
```

The add-on container copies `jarvis_component/` into `/config/custom_components/jarvis/`
at startup. The integration is what actually runs inside Home Assistant.

## Code standard

This project holds to senior+ engineering output:

- **4-pass audit before every release:** (1) syntax, (2) integration references,
  (3) all modules parse, (4) version bump + package.
- **Simulate tests internally** before shipping — the codebase carries standalone
  test harnesses for the reasoning loop, the Local Mind decision matrix, the
  package state machine, cognition salience tiering, and the speech composer.
- **Honest caveats** documented with every change. Verification before speculation.
- No careless mistakes, and no apologies in place of fixes.

## Local checks

```bash
# Python syntax across all integration modules
cd jarvis_assistant/jarvis_component
for f in *.py; do python3 -c "import ast; ast.parse(open('$f').read())" || echo "FAIL $f"; done

# Dashboard JavaScript parses
node -e "const fs=require('fs');new Function(fs.readFileSync('frontend/jarvis-panel.js','utf8'))"

# Add-on shell script
bash -n ../run.sh
```

## Releasing

Bump the version everywhere it appears with one command:

```bash
./scripts/bump_version.sh 6.3.3
```

This updates `config.yaml`, `build.yaml`, `Dockerfile`, `run.sh`, the integration
`manifest.json`, and the version string in `jarvis-panel.js`. Then commit, tag
(`git tag v6.3.3`), and push the tag — the validation workflow runs on every push.

After updating on a live system, hard-refresh the browser (`Ctrl+Shift+R`) so the
cached dashboard JavaScript reloads.

## Pull requests

Keep changes focused, include a short rationale, and note any limitations. If a
change touches the reasoning pipeline, the camera/Nest paths, or cognition
salience, please describe how you verified it.
