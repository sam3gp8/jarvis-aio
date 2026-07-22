# Changelog

All notable changes to JARVIS are documented here. This project uses semantic-ish
versioning (`MAJOR.MINOR.PATCH`); UI reskins and capability expansions bump MINOR,
bug fixes bump PATCH.

## [6.57.0] — semantic search via Ollama, no ChromaDB
The 6.56.0 approach hit a wall: ChromaDB's embedded mode depends on
onnxruntime, which has no wheel for the Python 3.14 that Home Assistant now
runs on, so the install could never succeed on this platform. Rather than
wait on an upstream wheel, this replaces it with something more in the
JARVIS-AIO spirit — reuse what's already here.

Semantic search now runs on the **Ollama server JARVIS already talks to**.
Embeddings come from Ollama's `/api/embed` (`nomic-embed-text` by default) —
no API key, no Python package, no native wheel to compile, works on any
Python version. Vectors are stored in JARVIS's own `jarvis.db` SQLite file
alongside the keyword index, and similarity is plain cosine computed in
stdlib Python (no numpy). No new service, no ChromaDB, no 300–500 MB
download.

Enable it in Settings → Document Library: it runs a live Ollama health
check, and once on, re-ingesting embeds your documents so retrieval matches
on meaning instead of keywords. It degrades to keyword (FTS5) search
automatically whenever Ollama or the embed model isn't reachable — the
document tools and panel keep working either way. Requires an Ollama host
(set `llm_base_url`) and a pulled embed model (`ollama pull
nomic-embed-text`).

New: `embeddings.py` (Ollama calls + SQLite vector store + cosine search),
a `jarvis/semantic_search` WS command (status/enable/disable/test), async
`ingest_directory_async` / `search_documents_async` in documents.py, and a
reworked search-engine banner. The obsolete ChromaDB `vector_backend.py` is
removed. 17 new embeddings tests (vector math, store, mocked Ollama
batch/legacy endpoints) and updated panel smoke checks.

## [6.56.1] — fix ChromaDB install; modernize CI actions
Two fixes surfaced by real deployment of 6.56.0.

**Semantic-search install failed** with "HA package helper unavailable:
cannot import name 'async_install_package'." There is no
`async_install_package` in `homeassistant.util.package` — the real helper
is the synchronous `install_package`. Now the enable button calls that
through an executor job (it shells out to pip/uv, so it must stay off the
event loop), and semantic search installs as intended. Keyword search was
never affected.

**CI Node 20 deprecation warning.** `actions/checkout@v4` and
`actions/setup-python@v5` run on Node 20, which GitHub is retiring in favor
of Node 24 — the Validate workflow was green but annotated with a warning.
Bumped to `actions/checkout@v5` and `actions/setup-python@v6` (both on Node
24), clearing it. No behavior change to the checks themselves.

## [6.56.0] — optional ChromaDB: semantic search, one button
JARVIS-AIO leans further into "all-in-one" without punishing small hosts.
Memory and document retrieval have always worked everywhere via the
built-in SQLite FTS5 keyword search; now you can upgrade both to true
semantic vector search by installing ChromaDB from a single button in
Settings → Document Library — no separate add-on, no manual pip, no HA
restart.

It's deliberately opt-in, not a hard requirement: ChromaDB pulls ~300–500 MB
(onnxruntime, tokenizers, etc.) that can be slow or fail to build on a Pi /
HA Green / Yellow. So JARVIS ships light and the panel tells you plainly
what enabling costs and where it's a good idea. The install runs through
Home Assistant's own package helper (lands in the env HA imports from) and
then re-initializes the memory and document stores in place, so vector
search activates immediately. If the host can't build it, the install
fails gracefully and keyword search keeps working — nothing breaks.

New: `vector_backend.py` (detect / install / re-init), a
`jarvis/vector_backend` WS command, and a search-engine banner in the
Document Library panel showing KEYWORD vs SEMANTIC with an enable button
and honest host guidance. 7 unit tests (detection, re-init resilience,
install flow with mocked HA helper, graceful failure) and 4 panel smoke
checks.

Also: the CI workflow gained a `workflow_dispatch` trigger (so Validate can
be re-run on demand from the Actions tab without a throwaway commit), plus
an explicit `permissions: {}` block and branch scoping on push. Note that
Actions simply hadn't run since 6.47.0 because nothing had been pushed since
that commit — the workflow was healthy and green, just idle. Verified the
repo is fully HACS-default-compatible: brand icons are correctly sized
(256×256 / 512×512) and satisfy the HACS brands check in-repo, manifest keys
and hassfest ordering are valid, and hacs.json carries the required name.

## [6.55.0] — Document RAG: JARVIS reads your manuals
The last un-built agent from the home-agent blueprint. JARVIS can now
answer from the household's own paperwork: drop appliance manuals and
receipts (PDF, .txt, .md) into `/config/jarvis/documents`, ingest them,
and ask "what's the furnace filter size?" or "when did we buy the
dishwasher?" — it retrieves the relevant excerpt and answers, citing the
source document, instead of guessing.

Built on the same ChromaDB the memory system already runs, but as a
*separate* collection — a manual isn't a conversation turn, and a furnace
query shouldn't surface old chats. Documents are chunked with overlap on
paragraph/sentence boundaries for good recall, embedded via Chroma's
default function (no extra model dependency), cosine-scored. When ChromaDB
isn't installed it falls back to FTS5 keyword search in the existing
jarvis.db, so retrieval works on a minimal install too. PDF text
extraction degrades honestly across pypdf / pdfplumber / PyPDF2 and, if
none is present, says so and skips the file rather than crashing — plain
text always works. `pypdf` is now a manifest requirement so PDFs work out
of the box.

Two agent tools (`search_documents`, `ingest_documents`), a
`jarvis/documents` WS command, and a **Document Library** panel in Settings
with an ingest button, live source list, and a test-search box. 17 unit
tests (the pure chunker, extraction routing, ingest/search through a
simulated collection, honest fallbacks) plus 4 panel smoke checks.

This completes every blueprint agent that belongs inside Home Assistant.

## [6.54.0] — the floor plan glows from live mmWave
The residence model now lights up room-by-room from genuine mmWave/presence
detection, not just the binary area-occupancy flag. A room whose presence
sensor is actively detecting gets a distinct, punchy aqua-green glow with a
brighter border — visibly the "hottest" room — while a room lit only by
generic area occupancy stays standard cyan, and the dominant room keeps its
mint. At a glance you can now tell *where a body actually is* versus where
HA merely thinks a zone is active.

Mechanically: `_house3dLit()` overlays the `jarvis/mmwave_overview` feed
onto the plan's lit map as a new `mmwave` state that flows through the
whole 3D builder — floor fill, walls, label, and pulse dot all render it
distinctly. The plan rebuilds when fresh mmWave data lands, so detection
appears live. Verified by rasterizing the plan and eyeballing that the
three presence states are actually distinguishable before shipping.

## [6.53.0] — mmWave presence overview
The residence tab gains a live **mmWave Presence** panel: every room with a
presence, motion, or occupancy sensor, showing whether it's occupied right
now, how many of its sensors are detecting, and — when clear — how long
since the last detection. Occupied rooms glow green and pulse; the header
summarizes at a glance ("2/5 OCCUPIED"). It's the ground truth behind the
floor-plan glow, surfaced directly instead of inferred.

This reads genuine sensor state, not the binary area-occupancy flag — a
room lit only by a door contact won't masquerade as mmWave presence here.
A new `jarvis/mmwave_overview` WS command assembles the per-room breakdown
from the occupancy sensors already mapped to HA areas; outdoor rooms are
tagged so yard sensors don't read as living space. Refreshes live on the
poll while the tab is open. 6 panel smoke checks; verified by rasterizing
the panel and eyeballing it before ship.

## [6.52.1] — learned automations for locks and covers are now valid
A latent bug in the pattern generator became reachable the moment 6.52.0
started installing suggestions. Every learned action was built as
`{domain}.turn_{state}` — fine for lights and switches, but nonsense for
other domains: a learned door-lock routine (the pattern module's own
flagship example, "front door locks after garage closes") would emit
`lock.turn_locked`, and a garage-cover routine `cover.turn_closed` —
invalid services that would write a broken automation to `automations.yaml`.

Actions now resolve through a domain-aware `service_for()`: locks get
`lock.lock`/`unlock`, covers get `open_cover`/`close_cover`, on/off domains
keep `turn_on`/`turn_off`, and cover transient states (`opening`/`closing`)
settle to their end state. Domains that need parameters we can't infer from
a bare state (climate, media_player) return no mapping, so those patterns
become advisory suggestions rather than broken automations. Time routines
were already gated to on/off and unaffected. 6 new tests, including the
end-to-end proof that a lock sequence installs `lock.lock`, not a
`turn_`-prefixed impossibility.

## [6.52.0] — the pattern engine closes the loop
The learning pipeline had a dead end: it observed behavior, detected
patterns, generated automation YAML, surfaced suggestions — and approving
one only flipped a database flag to `approved`. Nothing was ever installed.
The user saw "learned a routine," approved it, and… nothing happened.

Approval now **installs the automation into Home Assistant**. A new
`install_approved_suggestion` bridges the gap: it reads the suggestion's
stored automation, normalizes the generator's legacy shape into HA's
current format (translating `platform`→`trigger` and `service`→`action`),
and writes it live through the existing automation creator — then records
the suggestion as `installed`. Both approval paths use it: the panel's ✓
button and the voice tool ("JARVIS, approve that suggestion"). Concrete
suggestions (time routines, sequences, presence) install and go live
immediately; advisory-only ones (vague repeated-command notes) are still
acknowledged as approved but honestly reported as needing a human to
design — no fabricated automations. The panel toast and the agent both
relay which outcome occurred, and a `LEARN` line logs each install.

16 new tests: the normalizer across every pattern shape and malformed
input, plus the installer wiring end-to-end (installs, advisory skip,
missing suggestion, write-failure). Plus a panel smoke check for the
approve→install path.

## [6.51.2] — roadmap: local GPU inference shipped
Local GPU inference is done — Ollama on a dedicated GPU box (via a HAOS
GPU AI setup), so the reasoning chain runs templates → cache → local model
→ cloud on your own hardware. Moved it out of the roadmap's "on the
horizon" list into "shipped recently," and updated the Requirements note so
the local GPU server reads as supported now rather than a future item.

## [6.51.1] — README visuals, Nest streaming guide, roadmap trim
Documentation pass. The README gains faithful HUD visuals — a hero banner
and a two-up gallery of the Cognitive Core feed and the Camera Watch/DIAG
panel — rendered as SVG from the panel's actual design tokens (real cyan,
real fonts, real layout), so they represent the aesthetic without stale
screenshots to maintain. Added a full **"Continuous streaming for Google
Nest cameras"** guide: the go2rtc restream setup that defeats Google's
5-minute expiring streams, with the exact `nest:` source config, where to
find each credential, the optional Frigate hand-off, and the JARVIS
`camera_overrides` mapping that ties it together. Trimmed the roadmap —
UI Phase 3 (real-time WebSocket subscriptions, sparklines, entity cards,
log/feed search) has shipped, so it moved to a "shipped recently" note;
Document RAG is now called out as the last un-built blueprint agent.

## [6.51.0] — three new agents, and JARVIS finally sounds like JARVIS
The home-agent blueprint, reconciled against what already existed. Most of
its twelve agents were already here under other names — the Supervisor is
the agentic core, the Memory agent is the Chroma vector store, Vision is
the camera stack, Voice is the Wyoming pipeline. Three were genuinely
missing; two of the twelve (OS control, code execution) are deliberately
NOT built — arbitrary desktop automation and code execution inside the HA
process are security weight a home butler shouldn't carry, and there's no
display to drive in the sandbox anyway.

**Web Research agent.** A new `web_research` tool: ask about the outside
world and JARVIS looks it up, then relays the gist in its own voice.
DuckDuckGo's Instant Answer API by default (no key, no signup), switchable
to a self-hosted SearXNG. Results are summarized, capped, and sanitized —
never a raw page dump — and a failed lookup returns an honest "couldn't
find that," never an exception.

**Communication agent.** A new `calendar_agenda` tool reading the
`calendar.*` entities HA already exposes: upcoming events plus conflict
detection — overlaps, and back-to-back transitions tighter than a
configurable gap. Email is deliberately untouched; reading an inbox from
inside HA is privacy weight better handled by exposing specific mail as an
entity.

**MCU-JARVIS persona.** The voice now leans into Stark's JARVIS — dry,
clever, unflappable — with an engineered safety valve: full wit only at
light/neutral register, automatically silenced at urgent/grave. JARVIS
does not quip during a smoke alarm, and that's now structurally guaranteed
(the urgent/grave phrase pools can never gain banter lines — there's a test
that asserts exactly this). A **banter level** knob (plain / dry / full)
in Settings → JARVIS Character tunes it live, flowing into both the phrase
pools and the LLM's own prompt.

New: `web_research.py`, `comms.py`, banter valve in `persona.py`, two agent
tools, four panel-writable config keys, a JARVIS Character settings panel,
26 tests. The HTTP paths can't be exercised from the build sandbox (no
egress to the search endpoints), so they're covered by pure-shaper tests
and run live where HA has normal network access.

## [6.50.0] — camera management moves to Settings
The ✎ NAME button and its overlay are gone from Command Center — camera
renaming and indoor/outdoor designation now live in a **Cameras** panel on
the Settings tab, one row per camera: entity id (with the restream
override arrow when one is mapped), a name field (Enter or blur saves,
unchanged blur makes no call, blank reverts — placeholder shows the HA
name), and the AUTO/⌂ INDOOR/▲ OUTDOOR chips with instant save and the
resolved heuristic on AUTO. All cameras visible and editable at once
instead of one-at-a-time through an overlay, and Command Center's Camera
Watch head is back to just DIAG.

Two mechanics worth noting: the Settings tab already skips poll re-renders,
so typing a name is never wiped mid-edit; and the row refresh no longer
depends on `CSS.escape`, which isn't guaranteed in every embedding (found
by the smoke harness — the chip toggle silently died in its catch).

Same backend as 6.48/6.49 — `jarvis/rename_camera` and
`jarvis/camera_location` unchanged.

## [6.49.0] — tell JARVIS which side of the walls a camera lives on
The ✎ camera overlay gains a **LOCATION** row: AUTO / ⌂ INDOOR /
▲ OUTDOOR, saving instantly per click. AUTO shows what the heuristics
currently resolve — "AUTO (outdoor)" — so overriding is an informed choice,
not a guess.

This isn't cosmetic. `outdoor.py` is the single source of truth the whole
cognitive stack consults — the intrusion investigator (outdoor sensors must
not seed or confirm indoor investigations), the notable-outdoor-event
filter, and the motion scan. Its most-authoritative layer has always been
the user's word (`indoor_entities` / `outdoor_entities`); the new chips pin
the exact entity id into those lists via `jarvis/camera_location`, so a
designation immediately governs everything downstream. AUTO unpins and the
heuristics resume. Hand-written globs in those lists are preserved
untouched and still classify — they just read as AUTO in the picker, since
they aren't a per-camera pin.

Regression-tested end-to-end: an INDOOR pin beats an outdoor name keyword
(`camera.backyard_playroom` stays inside), an OUTDOOR pin makes a hallway
camera exterior for the whole stack, and unpinning restores heuristics.

## [6.48.0] — call your cameras what you actually call them
Cameras can now be renamed **inside JARVIS only** — HA entity names and
Frigate stream names stay untouched. Useful now that restream twins exist:
`eliana_restream` can just be "Eliana's Room" on the panel.

A **✎ NAME** button in the Camera Watch head opens an inline overlay for
the active camera: type a name, Enter saves, Esc cancels, blank reverts to
the HA name (shown as the placeholder so you always know what blank means).
The name applies everywhere the panel shows a camera — chips, the SRC
strip (including the override-mapping arrow), pickers — via a
`camera_names` runtime map, persisted through a new `jarvis/rename_camera`
WS command with a CONFIG line in the activity log.

Server logs and DIAG probes deliberately keep entity ids — display names
are for humans, entity ids are for debugging, and mixing them costs
precision exactly when it matters.

**Also in 6.48.0 — config.json can no longer take the panel down.** A
hand-edited `/config/jarvis/config.json` (adding `camera_overrides` by
hand) that parsed to something other than a JSON object crashed
`async_setup_entry` *before* panel registration — the integration never
loaded and the JARVIS tab died with "Unable to load custom panel."
`jarvis_config` is now self-healing: an unparseable or non-object file is
**sidelined** (preserved as `config.json.corrupt-<timestamp>`, never
deleted), defaults load, every accessor guards the cache type, and a
persistent notification explains exactly what happened and where your
edits went. A typo in that file now costs you a notification, not the
integration. Seven regression tests, including the exact live failure.

## [6.47.2] — a cloud blip is not a disarm
Live bug: lockdown was lifting itself overnight. Cause: when the
Cove/Alula integration lost its cloud connection, the alarm panel entity
went `unavailable` — and the alarm→lockdown sync only knew two states.
`_alarm_armed()` said "not armed," the sync read that as a disarm, and an
auto-engaged lockdown disengaged (with an announcement) because a cloud
API hiccupped.

The sync now sees three states: **armed** (any panel armed → engage, as
before), **disarm confirmed** (no panel armed AND at least one
affirmatively reporting `disarmed` → lift, as before), and
**indeterminate** (all panels unavailable/unknown, or none exist → HOLD
everything). During a dropout nothing engages, nothing lifts, the
manual-exit suppression isn't reset, and a throttled SAFETY line (once
per 10 min) records "alarm panel unavailable — holding lockdown" in the
activity feed so the outage itself is visible. Recovery to armed re-adopts
silently; a genuine disarm after recovery lifts exactly as it always did.

Six regression tests, including the precise live sequence:
armed_night → unavailable → held → disarmed → lifted.

## [6.47.1] — local model, cloud provider: auto-corrected
The GPU server's first contact produced a confusing error: Google's API
404ing on `models/gemma4:26b`. Root cause: `model` was pointed at the
local Ollama model but `llm_provider` still said a cloud provider, so
JARVIS faithfully forwarded an Ollama tag to Google. Two fixes:

  • **Routing correction** at the single provider choke point: a
    colon-tagged model (Ollama syntax — no cloud provider uses it)
    configured against groq/gemini/openai/anthropic now auto-routes to
    the ollama provider with the configured `llm_base_url` (or Ollama's
    default), with a clear correction logged. Explicit `ollama`/`custom`
    settings are never touched.
  • **Smarter fallback**: the agent's failure path used to replay the
    SAME model on gemini — a 404'd model 404s everywhere identically.
    Model-not-found is now detected as a settings problem (with an
    actionable ERROR log naming the fix), and the fallback goes through
    the reasoning tier's own provider+model instead.

Test-infra fix along the way: the `homeassistant.helpers.llm` stub moved
from a per-file guard into conftest — agent.py only loaded if a file that
happened to stub it ran first, and the loader caches half-executed
modules (the exact order-dependence the standing test lessons warn about).

## [6.47.0] — camera_overrides: let the restream do the work
The durable fix for Nest's expiring streams and placeholder snapshots
isn't more heuristics — it's not using Google's transport for frames at
all. The community-standard architecture is a go2rtc restream (Frigate
bundles one): go2rtc's native `nest:` source speaks SDM directly, handles
the 5-minute stream extension, and republishes solid RTSP that HA and
JARVIS consume like any local camera.

JARVIS now meets that halfway with one runtime key:

    camera_overrides: { "camera.eliana_s_camera": "camera.eliana_restream" }

The original entity keeps its identity everywhere — chips, names, doorbell
events, Nest event metadata — while every FRAME transparently comes from
the twin: the panel tile (stream URL, token, stills), the JARVIS snapshot
tier, the package monitor, vision analysis, all via one server-side
`resolve_camera_source()` mirrored client-side. The cam strip shows the
mapping (`SRC eliana_s_camera → eliana_restream`), DIAG probes and labels
the actual source, and a missing/typo'd target safely falls back to the
original. 3 new smoke checks, 3 new unit tests.

## [6.46.3] — the black-frame case, cracked by DIAG
First live DIAG run told the whole story in three lines: `nest×2` (the
integration is fine), `state=streaming`, and "snapshot: **OK 2KB (13ms)**"
— declared a success. A real camera frame is tens of KB and takes hundreds
of ms; a 2KB instant response is a placeholder thumbnail. Meanwhile the
tile sat in MJPEG mode because the stream *decodes* — a steady all-black
feed — so `naturalWidth > 0` stood the watchdog down. Every tier reported
victory while delivering garbage. Fixes on both ends:

**Server**: a first-pass snapshot under 12KB (`SMALL_SUSPECT_SIZE`) is now
treated as a placeholder even when it isn't literally black — the stream
gets woken and re-shot for a real frame, with the tiny one kept only as a
last resort. The DIAG probe reports luminance stats (`lum μ σ W×H`) on
every frame it sees and calls out SUSPECT sizes instead of declaring
victory, so the next screenshot self-diagnoses.

**Panel**: the watchdog and the load-listener are now content-aware — a
decoded frame only proves a tier works if it isn't near-black (mean
luminance sampled via a 32×32 canvas; unverifiable frames get the benefit
of the doubt). A black MJPEG stream now escalates exactly like a dead one.
DIAG gains a **TILE** line reporting the client half of the story: render
mode, decoded dimensions, and the current frame's luminance — including an
explicit "BLACK STREAM (decodes fine, shows nothing)" verdict.

## [6.46.2] — stop guessing: camera diagnostics
Three versions of fixing blank Nest tiles blind is enough. The Camera
Watch head gains a **DIAG** button that probes the active camera
end-to-end — the exact tiers `_get_best_image` walks, instrumented:
backend match and fetch (with *why* it was empty), standard snapshot
(including the blank-frame check), and the stream-wake retry, each with
its result and the whole thing timed. The verdict is actionable — a Nest
camera failing every tier gets told about event media and Pub/Sub
subscriptions, not just "no frame" — and is also written to the Logs tab.

The probe response always includes a platform histogram of every camera
entity HA has, which answers the question underneath all of this in one
glance: **if `nest×N` isn't in that list, the Google Nest integration
isn't delivering camera entities to HA at all**, and no amount of
JARVIS-side code can render what HA doesn't have.

New: `jarvis/camera_diagnostics` WS command, `camera.probe_camera()`
(kept tier-for-tier in sync with `_get_best_image`), 6 unit tests, 5
panel smoke checks.

## [6.46.1] — the fallback chain learns about hangs
6.46.0's camera escalation was driven entirely by `<img>` error events —
and the most common Nest failure mode fires none. HA's proxy endpoints
often HANG for a WebRTC camera (HTTP 200, connection held open, zero
frames ever sent) while it tries to start a stream that will never
produce one. No error event → no escalation → tile still blank.

A no-frame watchdog now backs up the error path: if no decoded pixels
arrive within the window (6s stream / 5s stills — checked via
`naturalWidth`), the tier escalates exactly as an error would have. A
frame arriving stands the watchdog down.

Also fixed a self-inflicted diagnosis gap: the JARVIS snapshot tier
swallowed WS errors silently. If the command doesn't exist — the classic
case being HA not restarted after updating — the tile now says
"restart Home Assistant" instead of showing nothing. Other WS errors
render their message. Server-side, empty or failed snapshot fetches now
write a CAMERA line to the activity log (throttled to one per entity per
5 min) so the Logs tab answers "why is there no frame" directly.

## [6.46.0] — Nest cameras visible, phantom packages gone
Two long-standing camera complaints, both traced to real bugs.

**Nest tiles were permanently blank.** The panel's fallback was
stream → stills, but WebRTC-only Nest cameras fail *both* — no MJPEG
stream exists, and an idle WebRTC camera can't produce stills through
`/api/camera_proxy` — leaving the tile in a silent error loop. The chain
now escalates a third time to a new `jarvis/camera_snapshot` WS command
that pulls frames through JARVIS's own backend registry (Nest event media,
stream-wake), polling gently at 6s. A camera that resolves to this tier is
remembered, so re-renders jump straight there instead of blank-flashing
through two 404s. If even JARVIS can't get a frame, the tile now *says so*
with a pointer at the Nest integration rather than showing nothing.

**"A package has been delivered" — when none was.** Three compounding
causes, all fixed:
  • The doorbell-press path matched keywords with no negation handling —
    an analysis reading "person at the door, **no package** visible"
    literally contains "package" and announced a delivery. Negated spans
    (including "no packages or mail" chains) are now stripped first.
  • Backend-sourced frames (Nest event media, Frigate snapshots) skipped
    the blank-frame check that guards the standard snapshot path — a black
    wake-up frame fed to a vision model is a hallucination machine. Blank
    frames are now dropped before classification.
  • A single frame could announce an arrival. A NEW positive now triggers
    one immediate re-capture + re-classify, and only two independent
    frames agreeing announce — one extra vision call, only when an
    announcement is on the line. Pickups still register from one frame.

Also: README gains a proper "Nest cameras (prerequisite)" section — the
Google Device Access / SDM / Application Credentials setup lives on the
official Nest integration, which JARVIS consumes; that's the only path
Google's licensing allows, now documented instead of tribal knowledge.

## [6.45.2] — hassfest gets its way
The 6.45.1 push tripped hassfest on four counts, all now fixed:

  • `assist_pipeline` (used by the voice bootstrap's pipeline creation) and
    `recorder` (used by the sparkline history fetch since 6.43) are now
    declared in `after_dependencies` — both are opportunistic uses that
    hassfest rightly wants on the record.
  • The `llm_base_url` field description in `strings.json` and
    `translations/en.json` contained a literal example URL, which the
    translations validator forbids. Reworded to convey the same Ollama
    default (host/port/path) without a URL.

Also reordered manifest.json keys to hassfest's canonical form (domain,
name, then alphabetical) — currently only a preference, but the Cove
project got bitten by it once and it costs nothing to be ahead of it.

## [6.45.1] — JARVIS gets its face back
The integration now ships its brand icon at
`custom_components/jarvis/brand/` (icon.png 256×256 + icon@2x.png 512×512,
web-optimized). Since Home Assistant 2026.3, custom integrations serve
brand images directly from this folder through the local brands proxy —
taking priority over the CDN, no home-assistant/brands submission needed.
This is also now HACS's required form for brand assets, so it checks a
default-store submission box at the same time. Users on HA older than
2026.3 still see no icon until/unless a brands-repo PR is made; that's
optional now, not blocking.

## [6.45.0] — the add-on era is officially over
The last roadmap item from the great cutover: removing the machinery that
existed to bridge the old add-on and the integration. None of it had a
living counterpart anymore — the add-on that wrote `jarvis_config.json` is
gone, its orchestration long since re-homed into the in-process voice
bootstrap.

Removed:
  • The ~95-line "addon-owned keys" reconcile block that ran on every
    setup, hashing a config file nothing writes. Worse than dead weight: a
    stale leftover file could have shadowed Configure-dialog choices after
    any future change to the key list.
  • The `jarvis_config.json` import triggers (`async_setup`,
    `async_setup_post_start`) — the latter had no callers at all.
  • The v5.8.03 old-path migration in `jarvis_config.py`.
  • The legacy path from the config flow's auto-import.

Kept, deliberately: the auto-import itself. `/config/jarvis/config.json`
is the panel's runtime store and survives integration removal, so deleting
and re-adding JARVIS picks all your settings back up with zero re-entry —
that was never an add-on feature, just a good one.

JARVIS is now cleanly config-entry-only: users who still have `jarvis:` in
configuration.yaml get a proper warning, the conversation agent registers
through the platform as it always did, and setup has exactly one path.
Also updated the README's voice-setup note, which still described the
in-process bootstrap as a future plan.

## [6.44.0] — activity feed search (and the feed is actually live now)
The Command Center's Activity Feed gets the same treatment the Logs tab got
in 6.43: a search box that filters events by message or tag as you type,
with a "1 of 30" count and a clear empty state.

Wiring it exposed a quiet bug worth its own line: the Activity Feed never
updated in place. The 5s/real-time refresh patched status rows, the
dominant room, and area tiles — but not the feed, which only redrew on a
full structural re-render (an area being added or removed). In practice the
feed silently went stale the moment you opened the dashboard. It now
rebuilds its rows on every refresh, respecting whatever search is active,
without stealing focus from the search box.

## [6.43.0] — UI Phase 3: real-time, sparklines, entity cards, log search
Four things, in build order.

**Real-time entity subscriptions.** The dashboard polled every 5s flat. It
now subscribes to HA's native `state_changed` events (the same pattern
Camera Watch already used for its own events) and refreshes within ~2s of
anything actually changing — throttled so a burst of activity coalesces
into one refresh, not one per entity. The 5s poll is now a 20s safety net,
since real-time now covers the common case.

**Area tile sparklines.** Every area tile with a temperature or humidity
sensor now shows a compact trend line, not just the instant reading. This
needed a new data path: `state_changes` (patterns.db) deliberately excludes
sensor/binary_sensor domains as noise for pattern learning — exactly the
domains a sparkline needs — so this is the integration's first use of HA's
`recorder` history API, polled separately and slowly (5 min) since history
queries are heavier than the rest of the panel payload. This is the one
piece I couldn't exercise against a live recorder from here — worth a close
look on first deploy.

**Entity cards.** Click an area tile → a drill-down detail card: full-size
temp/humidity readouts with their sparklines, lights with the same toggle
as the tile, last motion, capabilities. Area tiles picked up `temp`/
`humidity` for the first time too — previously only the dominant room ever
got that data.

**Log search.** A text box next to the category filters, debounced,
filtering message and category text together with whatever category's
selected. A count line ("12 of 340") and a real empty-state message when a
search or filter matches nothing, instead of a blank pane.
Two things v6.40 and v6.41 built now have somewhere to show up.

**Goals card** (Command Center): every active goal, with its step progress,
next-check or deadline countdown, and a cancel button — plus recently
finished ones for a moment of "oh, it got that done." This existed in the
backend since the goal planner shipped with no way to see it short of asking
JARVIS directly.

**Person Routines** (Memory tab): the per-person habits JARVIS has learned
with enough confidence to attribute to one person by name, grouped and
confidence-scored, sitting next to the household-wide facts it already showed.

Also fixed along the way: the Suggestions card — live since the pattern
engine shipped — was silently reading `undefined` for its data the whole
time. `_data()` normalizes the raw panel payload into a fixed shape and never
carried the `suggestions` field through, so the card only ever rendered
empty. Both suggestions and the new goals data go through the same fix.
Since v6.29, every voice command has been tagged with the resolved person —
but that signal went nowhere. The pattern analyzer only ever learned
household-wide habits, and a `person_patterns` table has sat in the schema
since the goal planner shipped, unused.

State changes now carry a person too — stamped cheaply, sole-occupant only,
by the same listener that logs them for pattern learning (the full face/voice
resolver is too costly to run on every light flip; that's reserved for the
much lower-volume conversation path). When one person accounts for the clear
majority of an entity's routine, or a repeated command, JARVIS now says so:
"turns on around 7:00 most days when Sam is home" instead of a blanket
household statement — and attributes the learned fact to *that person's*
knowledge subject, not the household's. Mixed or ambiguous patterns behave
exactly as before.

`person_patterns` finally has a writer: person-owned routines land there,
independent of the household suggestions/automations flow, ready for a
per-person Routines card whenever UI work resumes.

This is data-layer only — no new UI this round. Next up: surfacing it.
JARVIS now has a goal planner: hand it an *outcome* and it will keep working
toward it — across minutes, hours, or days — until it's achieved, impossible, or
you call it off.

  "Get the house ready for guests by Saturday afternoon."
  "Warm the living room to 72 and let me know when it's actually there."
  "Keep an eye on the basement humidity today and run the dehumidifier if it climbs."

When you ask for something like that, JARVIS breaks it into concrete steps and
opens a goal. From then on it re-engages on its own cadence with its full
toolset — checking states, acting (every action still self-verifies), marking
steps off, and deciding when to check back next. It works **quietly**: progress
lands in the activity log, not your ears. You hear from it when the goal
*finishes* — done or failed — with a plain-spoken result, through the normal
announcement routing (so quiet hours still apply). Ask "what are you working
on?" anytime for status, or tell it to drop one.

Deadlines are honored honestly: if time runs out, JARVIS wraps up what it can
and closes the goal rather than pretending. And it can't run away with itself —
active goals are capped, each goal has an engagement budget, and a hiccup
mid-run (say the LLM being briefly unreachable) just means it tries again at
the next check instead of giving up.

This completes the agency ladder: `execute_plan` does many things *now*,
follow-ups handle one thing *later*, and goals pursue an *outcome* until it's
real.

## [6.39.0] — JARVIS knows outside from inside
An audit of how JARVIS classifies the outside world found the biggest remaining
sources of intrusion false alarms — and one filter from the original design that
had been sketched but never actually connected. All fixed:

  • **A delivery driver can no longer "confirm" a break-in.** Person detections
    from OUTDOOR cameras (driveway, doorbell, backyard) no longer count as proof
    someone is inside the house. Only an indoor camera seeing a person confirms
    an intrusion; the courier at your door stays a doorstep event.
  • **Outdoor motion can't start an intrusion investigation.** Previously only a
    handful of hard-coded names ("backyard", "porch"…) were recognized as
    outdoor — a sensor called *patio*, *deck*, *shed*, *garden*, *pool*, or
    *doorbell* was treated as motion **inside your house**. JARVIS now uses a
    proper classifier: your Home Assistant areas, a much richer set of outdoor
    names, and — decisively — your own say-so.
  • **An open yard gate isn't an open house.** Property-perimeter openings (a
    driveway or side gate) no longer corroborate a break-in the way an open
    window does. The garage still counts — it's part of the house.
  • **You get the final word.** Three new settings — `outdoor_areas`,
    `outdoor_entities`, and `indoor_entities` (globs; indoor wins) — let you
    force-classify anything the auto-detection gets wrong, no renaming required.

The notable-event policy (a person, package, mail, or damage outdoors is worth
telling you about; wind, passing cars, and animals are not) is now wired into
the same classifier, ready for the vision layer to consult.

## [6.38.0] — JARVIS closes its own loops
Two upgrades that make JARVIS genuinely agentic — acting across time and
confirming its own work — instead of only reacting turn by turn:

**It schedules its own follow-ups.** JARVIS can now queue work for its future
self and run it autonomously: "close the garage" can become *close it, then
check in five minutes that it actually shut*; adjusting the thermostat can come
with *confirm the room reached temperature in half an hour*; "remind me the
oven's on in 45 minutes" just works. When a follow-up comes due, JARVIS runs it
with its full toolset — checking states, acting if needed — and reports the
outcome out loud through the normal announcement channel (so quiet hours still
apply). Ask "what do you have queued?" to review or cancel them.

**It verifies what it was asked to do.** Every deterministic action — on/off,
lock/unlock, open/close — is now checked a few seconds later. If the device
didn't reach the target, JARVIS retries once; if it *still* didn't, that shows
up honestly in the activity log ("the garage door did not respond to close even
after a retry — it may be jammed, obstructed, or offline") instead of the
command silently going nowhere. Successes stay silent; only trouble surfaces.

Both build on everything JARVIS already learned this cycle: follow-ups announce
through the same routing as its other proactive speech, failures land in the
same activity log the root-cause analyzer reads, and the graduated-trust
autonomy model keeps the user in charge of what runs silently.

## [6.37.0] — ask JARVIS *why* something happened
JARVIS can now perform root cause analysis. Ask it things like "why did the
kitchen lights turn off?", "what caused the heat to kick on at 3am?", or "who
unlocked the front door?" — and instead of just reporting the state, it
investigates: it pulls its own state history, recent voice/text commands, and
its own actions from around the event, builds a timeline, and ranks the likely
causes with confidence:

  • a **recorded trigger** — when the change was captured with its cause attached
  • an **upstream failure** — a related device or hub going unavailable moments
    before (the classic "everything on that hub dropped" cascade)
  • a **person's request** — someone asked for it by voice or text, and who
  • a **JARVIS action** — it did it itself (lockdown, a routine, an announcement)
  • a **recurring schedule** — the same change happens at this hour most days,
    pointing at a Home Assistant automation
  • **related room activity** — something else changed in the same room just
    before

When the evidence is thin it says so honestly rather than inventing a story.
Everything runs locally over history JARVIS already keeps — no cloud calls to
analyze — and the answer comes back as a spoken-style explanation with the
timeline behind it. It's also available to the dashboard for an entity-by-entity
"why" view.

## [6.36.2] — the Memory forget button works now
Removing a memory with the ✕ on the Memory tab did nothing. The panel was sending
the fact's id in a field named `id`, but Home Assistant's WebSocket layer reserves
`id` for its own message numbering and overwrites it — so the request arrived
asking to forget the wrong thing, and nothing was deleted. The id now travels in
its own field, so ✕ removes the memory as expected (and the list updates
immediately). Added a guard so no future panel action can trip over the same
reserved field.

## [6.36.1] — spoken replies fall back to your real speakers
Following on from 6.36.0: if your voice satellite can't play audio itself — a
mic-only board (Waveshare with the speaker DAC off), or one in a room with no
real speaker — a spoken reply had nowhere to go and was silently lost, even
though proactive announcements (the briefing) played fine on your chosen
speakers. Now, when a reply would land on a satellite that can't speak, JARVIS
routes it to the same reply/broadcast speakers the briefing already uses. So if
the briefing is audible, replies will be too.

(You can still pin a specific speaker per satellite under Settings → satellite
pairings for room-accurate replies; the fallback only kicks in when there's no
usable speaker otherwise.)

## [6.36.0] — voice replies come back reliably
If JARVIS answered typed questions but went silent over voice, this is the fix.
Two of JARVIS's own reply-routing safeguards could swallow a spoken reply while
leaving text untouched (text never goes through them):

  • **Room-presence gating is now off by default.** JARVIS used to check the
    satellite's room for occupancy and stay silent there if a sensor said the
    room was empty — meant to keep only the right room answering. But if the
    room's mmWave/occupancy sensor hadn't registered you yet (or was flaky), it
    silenced the very satellite you were talking to. The multi-satellite dedup
    already prevents several speakers answering at once, so this gate is now
    opt-in (`presence_gate: true`) for homes with rock-solid per-room presence.
  • **A dead reply speaker no longer eats the reply.** When you have a reply/Cast
    speaker configured, JARVIS silences the satellite and speaks through that
    speaker instead — but it was doing so even when the speaker was offline,
    losing the reply entirely. Now it only hands off to a reply speaker that's
    actually reachable; otherwise the satellite speaks.

Net effect: the satellite you spoke to answers, unless you've deliberately set up
room-targeted or Cast-speaker replies and those are healthy.

## [6.35.0] — intrusion checks start at the door and follow the route
JARVIS now reasons about *where* activity is before crying wolf. When motion
happens while no one's home, it anchors the search at the **point of entry** —
the room with the open window or door — and only concludes there's an intruder
when activity forms a plausible route from there: motion at the breach, then into
the room next to it, and onward, the way a person actually moving through a house
looks. A camera spotting a person still confirms immediately.

Crucially, motion that has *nothing* to do with the open entry — a blip in a far
room while the open window is elsewhere, with no activity near it — no longer
trips a full intrusion alert. JARVIS keeps watching it (as you asked — it still
investigates activity anywhere), but it won't conclude an intrusion from
unrelated motion. That's what eliminates the occasional false alarm.

To follow the route it uses your **Residence floor plan** to know which rooms are
next to which. If a breach room has no motion sensor, or the layout isn't mapped,
it falls back to requiring sustained movement through several rooms rather than a
momentary two-sensor blip. Either way the bar for "intrusion" is higher and
better-reasoned.

The investigation now also reports the breach point and the route it's tracking,
so the Residence view can show where an intruder is and where they've been.

## [6.34.0] — JARVIS knows your voice
JARVIS can now tell who it's talking to by **voice**, and learn people's voices
over time from ordinary conversation — the strongest signal yet for its
per-person features.

It works by consuming a dedicated speaker-recognition service rather than running
a voice model itself (that keeps Home Assistant light and your GPU free for the
LLM). Point it at a service like **VoiceBM** or **speaker-recognition** — anything
that publishes "who's speaking" to Home Assistant as an entity — and JARVIS folds
voice into its existing identity picture alongside who's home and who's on camera.
When the voice is certain it's used; when it isn't, JARVIS falls back gracefully.

**Learning over time is hands-free.** The service does the enrolling, but JARVIS
supplies the missing piece — the *name*. When it already knows who's speaking
(you're the only one home, or a camera just recognized your face) but the voice
service hasn't learned that voice yet, JARVIS flags it so the sample can be
enrolled under the right person automatically. Voice profiles build themselves
from normal conversation, no sit-down training session.

Set it up in Configure → Identity: enable the voice tier and give it your
service's speaker entity (e.g. `binary_sensor.*_voice`). A full setup guide,
including the auto-enrollment automation, ships alongside this release.

## [6.33.0] — one alert, then JARVIS investigates and escalates
Motion while no one's home no longer turns into a stream of repeat alerts. Now
JARVIS alerts **once** and then investigates quietly:

  • A window or door left open on purpose is still a valid way in, so motion near
    it gets the one alert — JARVIS doesn't ignore it, and doesn't nag about it.
  • After that single alert it watches silently, tracking whether the motion
    stays put (a pet, a blind, one sensor) or **spreads through the house** the
    way a person moving room to room would — and it also watches your cameras for
    a person. It keeps investigating for as long as it takes to decide.
  • If it's **nothing** — motion settles, stays in one spot — it quietly stands
    down. No second alert.
  • If it **confirms an intrusion** — motion across multiple rooms, or a person
    on camera — it escalates hard: it announces out loud to the whole house
    **regardless of the time of day**, and pushes to **every device** connected
    to your home, not just one phone. A persistent notification is left too.

If residents come home mid-investigation, JARVIS stands down on its own.

You can tune it: `intrusion_spread_zones` (how many rooms of motion means
"someone's moving through", default 2), or turn the whole corroboration
requirement off with `intrusion_require_corroboration: false`.

## [6.32.0] — doors show open, quieter motion alerts, tuned for Ollama
Three things:

**Doors now actually show open on the Residence tab.** The house model was
never receiving live door state — the panel was quietly dropping it before it
reached the 3D view, so garage doors (and every other door) always drew closed
no matter what. Fixed at the source; open doors now render open, and combined
with the door-mapping added earlier you can make them match your home exactly.

**Motion alerts only fire when something's actually wrong.** When no one's home,
plain motion — a pet, a robot vacuum, blinds moving in the airflow, sun on a
sensor — no longer sets off an intrusion alert. JARVIS now only alerts on motion
while away when it's corroborated: the alarm is armed, or a door/window is open
(a real entry). If you'd rather be alerted on any motion, set
`intrusion_require_corroboration: false`.

**Optimized for a local Ollama server.** If you point JARVIS at Ollama, it now
keeps the model loaded between requests (no reload lag), uses a much larger
context window than Ollama's small default (so long prompts aren't silently
truncated), and allows a generous timeout for cold-start model loads. Point it
at your Ollama endpoint and it'll run local without the first-token stalls.

## [6.31.0] — lockdown closes what it can, and stops repeating itself
Two things, both from real use:

**It stops nagging.** Lockdown was re-announcing "lockdown engaged" on every
restart and reload — so during a day of tinkering you'd get the same alert over
and over. Now an already-armed alarm is adopted silently on startup; the
announcement only fires when the alarm actually arms (or you engage it yourself).
And anything it can't secure is mentioned once, never on a loop.

**It actually secures what it can.** On engage, lockdown now:
  • locks every motorized lock that's unlocked;
  • **closes motorized openings** — garage doors and powered covers. These have
    safety sensors, so if something's in the way the close just fails (and you're
    told it couldn't close), rather than forcing shut on a car or person;
  • for openings it can't close remotely — a plain window contact with no motor —
    it alerts you once so you can close it by hand, then treats it as
    intentionally open and leaves it alone.

So a typical engage now reads like "Sir, lockdown engaged — I locked the front
door and closed the Garage Door, but Sam's Window 1 is open and I can't secure it
remotely — you'll want to close it," and you hear it once, not every few minutes.

## [6.30.1] — lockdown tells you what's actually open
The lockdown announcement could come out nonsensical — "everything already
locked. 1 opening already open will be left as-is" — which made it sound like
JARVIS did nothing and was shrugging off the one door that was actually open.
During a lockdown an open door is the thing that matters, so the message now
names it and tells you to deal with it: e.g. "Sir, lockdown engaged. Everything
was already locked, but the Garage Door is open and I can't secure it remotely —
you'll want to close it." When nothing needs locking and nothing is open, it
simply says the home was already fully secured, instead of announcing a non-event.

## [6.30.0] — the Residence doors reflect reality now
The 3D house shows your doors open and closed live, but it had to *guess* which
of your entities was the garage, the front door, the cellar, and so on — purely
from their names. If your garage door's entity didn't happen to contain the word
"garage", or was exposed without a device class (common), it never showed as
open. That guessing is why the door states felt unreliable.

Two fixes:

  • **You can now map doors explicitly.** A new section on the Residence tab lets
    you point each door slot — Front, Garage, Garage Side/Rear, Kitchen↔Garage,
    Cellar, Basement — at the exact entity in your home (a cover, a door/contact
    sensor, or a lock). Mapped doors are read directly, with no guessing, so they
    always match. Leave a slot blank to keep auto-detection.
  • **Auto-detection is smarter.** Garage doors exposed as a cover with no device
    class are now recognized by name, while window coverings (shades, blinds) are
    excluded so they're never mistaken for doors.

So your garage door — and the rest — will track correctly: map it once and it's
certain, or rely on the improved auto-detection.

## [6.29.2] — the Residence tab saves your settings now
Changing anything on the **Residence** tab — home style, number of floors,
whether there's a basement, dormers, garage bays, chimney side, square footage,
bed/bath counts — was silently failing with an error, because the backend was
rejecting those settings as "not writable from the panel." Only the room layout
and background-image editor actually saved. Every Residence control now persists
correctly, so you can describe your home and have the 3D model match it.

## [6.29.1] — the Configure dialog actually configures now
If you opened **Settings → Devices & Services → JARVIS → Configure** and got a
step that showed a heading but no fields — just a Submit button — that's fixed.
The Configure dialog is a proper four-step setup (Core, Routing, Observer,
Identity) with real controls, pre-filled with your current values:

  • **Core** — what JARVIS calls you, its directive/personality preset (or a
    custom directive), the conversation model, and whether it can control the home.
  • **Routing** — your bedroom areas, a broadcast speaker group, and a phone
    notify service.
  • **Observer** — turn proactive awareness on, with its Gemini vision key, the
    model tiers, and quiet hours.
  • **Identity** — per-person recognition: on/off, the confidence threshold, and
    the voice-fingerprint tier (the one that needs a GPU).

This is in addition to the in-app JARVIS panel, which still holds the full set of
settings. (The empty dialog was leftover skeleton steps from an earlier build;
the fields had never been wired in.)

## [6.29.0] — JARVIS knows who it's talking to
Until now JARVIS treated everyone the same — it remembered facts and learned
routines, but couldn't tell who was speaking. It can now figure out *who* it's
talking to and tailor itself to that person: your preferences surface for you,
your spoken "remember that I…" is filed under you (not shared), and the routines
it learns get attributed to the right person instead of a generic "someone."
One resident's private facts no longer leak into another's conversations.

**It works without any special hardware.** JARVIS figures out who you are from
signals your home already has, in tiers:

  • **Who's home** — if you're the only person home, that's almost certainly who
    it's talking to. (Just Home Assistant person tracking — nothing to set up.)
  • **Recent face** — if a camera recognized someone moments ago, that's a strong
    clue. (Uses your existing Frigate/DoubleTake setup, which runs on the camera
    side — no GPU on your Home Assistant box.)
  • **Voice** *(optional, needs a GPU)* — recognizing people by their voice is the
    most direct signal, but it needs local AI horsepower, so it's **off by
    default**. When your GPU server is online you can switch it on; until then,
    the two tiers above give a non-power-user a fully working setup with zero
    configuration.

JARVIS only commits to a person when it's reasonably sure — when the signals are
ambiguous (say, two people home and no camera match), it stays neutral rather
than guessing wrong. The whole feature can be turned off, and the confidence
threshold tuned, in config.

## [6.28.0] — zero-touch voice setup is back
The convenience the old add-on gave you — automatically installing the voice
stack and setting up JARVIS's voice — now lives inside the integration, so the
HACS install gets it too. After you add JARVIS, on Home Assistant OS / Supervised
it quietly does the legwork in the background: installs the Piper, Whisper, and
openWakeWord add-ons if they're missing, downloads the JARVIS voice, restarts
Piper to pick it up, reconnects Wyoming, and builds an Assist pipeline with
JARVIS as the conversation agent. You don't have to touch any of it.

It's careful about it: the setup runs once per version (not on every restart),
never re-installs things you already have, and if any step can't complete it
just tells you the one manual step to finish in Settings → Voice Assistants
rather than failing. On Home Assistant Container/Core (no Supervisor) it cleanly
does nothing — there are no add-ons to install there — and you set up voice the
normal way. Power users can turn the whole thing off with `auto_bootstrap: false`.

With this, the move to a HACS integration is complete: install JARVIS and
everything — conversation, vision, the cognitive core, memory, the dashboard,
and now voice — comes up on its own.

## [6.27.0] — JARVIS is now a HACS integration
JARVIS installs through **HACS** now, as an ordinary Home Assistant integration —
no separate add-on. It runs entirely inside Home Assistant, so there's no extra
container to manage, and updates come through HACS like any other integration.

To install: add this repository to HACS as a custom **Integration**, install
"JARVIS AI Assistant," restart, then add it under Settings → Devices & Services
and enter your API key (or a local LLM URL). Everything else is still configured
from the JARVIS panel.

If you were running the old add-on: your data is safe. Everything under
`/config/jarvis/` — learned patterns, the new knowledge store, your persona, and
your settings — stays on disk, and JARVIS automatically imports your existing
configuration on first start, so nothing is re-entered.

One thing is still in flight: the add-on used to auto-install the voice stack
(Piper, Whisper, openWakeWord), download the JARVIS voice, and build the Assist
pipeline for you. That convenience is being re-homed into the integration. Until
it lands, set the voice pipeline up once via Settings → Voice Assistants with
JARVIS as the conversation agent. Everything else — conversation, vision, the
cognitive core, the dashboard, memory — works immediately on install.

## [6.26.0] — JARVIS learns your routines on its own
The pattern engine that watches how you use the house now does two new things
with what it sees.

First, the strongest routines it spots become things JARVIS simply *knows* —
they show up in the Memory tab on their own, marked with a "~" so you can tell
what it figured out by watching versus what you told it directly. So after a
week or two you might open Memory and find "porch light turns on → around 18:00
most days," or "asks 'goodnight' → usually around 23:00," with no effort on your
part. Anything you've stated yourself always wins and won't be overwritten by a
guess, and you can forget any of these with the ✕ like any other fact.

Second, a fix: JARVIS can now actually notice when one thing reliably follows
another — "the kitchen light comes on right after the hallway light" — and offer
to automate it. That detection had been silently failing; it works now, so the
"shall I automate this?" suggestions will be richer.

As before, suggested automations still wait for your yes/no — nothing is created
behind your back.

## [6.25.0] — JARVIS remembers
JARVIS can now hold on to durable facts and preferences — the kind of thing a
real butler just knows about your household — and bring them up naturally in
conversation. Tell it "remember that trash is Tuesday," or "remember I run cold
at night," and it keeps that. Ask later and it answers from what it knows; it
also quietly factors these in whenever it talks to you.

There's a new **Memory** tab to see and curate everything JARVIS knows:

  • Each fact is listed plainly — "trash day → Tuesday" — grouped into things
    about the household and things about you.
  • Teach it something on the spot with the box at the top, no voice needed.
  • Forget anything with the ✕ — this is your control over what it retains.
  • Facts it picked up by observation rather than being told are marked with a
    small "~", so you can see at a glance what it's sure of versus inferring.
  • Facts can be made to expire on their own — handy for the ephemeral ("the
    sitter comes at 3 today") so they don't linger as stale knowledge.

This sits alongside the conversation memory JARVIS already had (which recalls the
gist of past chats); the new layer is curated knowledge you can read and edit
directly, and it's the foundation the per-person and goal-planning features to
come will build on.

## [6.24.3] — Lockdown holds, and handles open doors the way you'd expect
Lockdown now stays put. The earlier "it flips on then flips back" was the header
not being told the real lockdown state on its regular refresh — it is now, so the
switch reflects exactly what the house is doing and holds there.

Lockdown is also smarter about doors and windows. Anything already open when you
engage is treated as deliberate and left alone — no fighting you over a window you
opened on purpose. From then on it watches for things that were shut and then open:

  • if it's something JARVIS can close or lock (a smart garage door, a smart lock),
    it secures it — and if that doesn't actually take, it alerts you;
  • if it's something JARVIS can't operate (a plain open/closed sensor with no
    motor or lock behind it), it assumes you meant to open it and leaves it be;
  • if JARVIS closes something and you open it right back, it takes the hint, leaves
    it open, and tells you once.

Worth knowing: because un-closeable openings are now assumed intentional, a window
JARVIS can't physically close that opens mid-lockdown is left alone rather than
alerted — your call, as requested. Auto-arm-with-the-alarm and surviving reboots
from the last update are unchanged.


## [6.24.2] — Lockdown that actually engages (and follows your alarm)
Fixed the lockdown toggle for good and made it dependable. It now engages every time you flip
it, the header switch reflects it immediately, and lockdown follows your alarm on its own — it
arms whenever any alarm panel is armed and lifts when you disarm, and it holds that state across
reboots and updates. (Manually flipping it off while armed still wins until you next disarm.)

Why it was stuck: lockdown used to be set up deep inside the optional observer's startup, so any
hiccup there left it silently switched off — which is exactly why the toggle did nothing while
everything else worked. It's now its own always-on security feature, created on demand if needed,
watching your alarm directly so arming/disarming takes effect the instant it happens instead of
waiting on a background cycle. The add-on log also spells out each lockdown action now, so if
anything misbehaves it's clear what happened.


## [6.24.1] — Basement door
Added the basement door at the foot of the cellar stairs. It lines up directly under the
cellar bulkhead and appears on the basement floor view, opening and closing in step with its
door sensor like every other door.

## [6.24.0] — Your doors, live on the model
Your doors now appear on the 3D home and light up the moment they open. JARVIS watches your
door and garage-door sensors and shows each one's state on the model — an open door glows amber
and swings ajar, a closed one sits flush in cyan, refreshing within a few seconds.

The home you see is a Cape Cod — the developer's own house, included as a worked example you'd
reshape into your own (see Settings → Residence / Home). It models a front entry, three garage
bays, a garage rear/man-door, a cellar bulkhead under the kitchen window, and an interior
kitchen↔garage door. Exterior doors show on the main view; interior doors show on the matching
floor view.

JARVIS matches your sensors to these doors by name — e.g. a sensor with "cellar" or "bulkhead"
lands on the cellar, "garage" + "side"/"man" on the garage man-door, "front" on the entry. If a
door never lights up, its sensor name didn't line up with one of those doors.

## [6.23.2] — Lockdown shows its real state · smoother phone rotation
Lockdown now reflects what's actually happening. Engage it — or have your alarm arm and trigger
it automatically — and the header switch and status both flip to ARMED and stay there. And on a
phone, spinning the 3D home no longer drags the page with it: a sideways swipe rotates the
model, an up/down swipe scrolls the page.

## [6.23.1] — Lockdown is a real switch
The lockdown control in the header is now an unmistakable on/off switch instead of a vague
banner. On, it slides over and glows red ("ARMED"); off, it sits grey. One glance tells you
whether the house is locked down.

## [6.23.0] — Make the model your home
The residence model is no longer fixed to one house. From Settings → Residence / Home you can
set it up for your own place: choose a home type (Cape Cod, Colonial, Ranch, Two-Story,
Craftsman, Modern, Townhouse, Apartment, or Cabin) and your specs — garage bays, dormers,
chimney side, basement, bedrooms, bathrooms, square footage, and address — and the 3D model and
the property readout update to match. Out of the box it's a Cape Cod (the developer's own home)
as a starting point; change the type and specs to make it yours. The floor tabs follow along,
too — single-story homes drop the 2nd-floor tab, basement-less homes drop the basement.

## [6.22.0] — JARVIS on your phone
The whole panel now works on a phone, not just a desktop or tablet. The tab bar scrolls instead
of running off the edge, the 3D home shrinks to fit the screen, the header and controls stack,
and you can drag to spin the model without the page fighting you. Nothing changes on desktop.

## [6.21.0] — Rotatable 3D residence model (default)
The residence overview is now a real, drag-rotatable 3D model of the home, replacing the
fixed cabinet-projection drawing. It is a pure-geometry axonometric projection rendered to
SVG (no build step, no CDN), so the same code rotates in the browser and rasterizes for
release verification.
- **Drag to rotate** to any angle; the model re-projects live.
- **Floor isolation** (All / 1st / 2nd / Basement). The "All" view shows the exterior with
  presence as lit windows; each floor view drops the shell and shows that level's rooms as
  labeled translucent volumes with per-room occupancy (cyan occupied, green dominant).
- **Built to the real house** — dimensions from the architectural plan (63′×24′ footprint,
  garage 30×24, house 33×24, dormered ~400 sf second floor); room layout and labels from the
  floor-plan editor. Correct gable roof, two front dormers, the round-window rear dormer
  (upstairs bath), three-car garage, and the exterior chimney on the east gable.
- **Occupancy is data-driven** from live HA areas matched by name, so rooms light up as people
  move through the house.
- **Not hard-wired to one home:** the house spec (dimensions, room list, garage doors, dormers)
  is a single labeled default-config block at the top of the inlined `JARVIS3D` module — edit
  it for a different house. Address still comes from config.
- Retired the leader-line presence callouts (a rotating model can't anchor fixed leaders);
  presence now reads directly off the lit windows and labeled rooms. Smoke test updated to
  assert the rotatable model, the three garage doors, and floor-isolation labels.

## [6.20.3] — Real-home geometry: 3-car garage + corrected room windows
Calibrated the cabinet-projection house against the actual property photos.
- **Three garage doors.** The left wing now renders three evenly-spaced single doors
  (was two), matching the real garage. All three light together from the `cover.*garage*`
  state.
- **Front facade corrected.** The wide left window is now a single Living Room picture
  window (two sections, no longer a stray "Kitchen" pane); front door and Dining window
  to the right are unchanged.
- **Corner rooms on the right gable.** The two windows flanking the end chimney now map to
  the rooms that actually sit at that corner — Dining Room (front of the stack) and
  **Kitchen** (behind the stack, rear-right corner side window).
- **Projection note.** The Guest Bedroom (rear-left) and the upstairs Bath (the round
  rear-dormer window) face the two elevations this fixed front-right angle can't show, so
  they appear in the presence callouts rather than as lit windows. Keeping the front-right
  view is deliberate — it's the only angle that shows the garage doors.
- Smoke test extended to assert the garage renders exactly three doors (21 checks).

## [6.20.2] — Flanking-window rooms
The two windows either side of the end chimney now map to distinct rooms (Guest Room in
front of the stack, a bath window behind) instead of both showing the living room.

## [6.20.1] — Home corrections (chimney, garage doors, windows)
From marked-up feedback on the render:
- **Chimney** moved to the right gable end as a tall exterior stack (was floating mid-roof).
- **Garage doors** redrawn so they clearly read as doors — bolder frame, panel courses, and
  vertical seams.
- **Windows added flanking the end chimney** (living-room windows either side of the
  fireplace), plus the front-facade window set adjusted (Living / Kitchen / door / Dining).

## [6.20.0] — Residence is now a solid home, not a diagram
Replaced the isometric room-plate cutaway with a real, solid-massed house drawn in cabinet
projection — walls, a gabled roof with dormers, the attached garage, a chimney, a front
door. It reads as a *home*, and it is still a fixed SVG that cannot rotate or zoom.
- **Presence shows as lit windows.** Occupied rooms glow cyan, the dominant room glows
  green with a brighter halo, idle rooms stay dark — like a house at dusk with lights on
  where people are. Garage doors light when the garage is active; basement windows light
  for the basement.
- **Window-to-room map:** dormers = Master Bedroom / Eliana's Room; first-floor windows =
  Living Room / Kitchen / Guest Room; garage doors = Garage; base windows = Basement.
- **Floor tabs focus a level** by dimming the other floors' windows.
- Property banner, sq-ft / bed-bath / style / occupied stats, and the systems callouts
  stay as the HUD surround. Audit clean, smoke 20/20, 170 tests passing.

## [6.19.1] — Lock down the iso view
Confirmed and hardened that the residence drawing cannot rotate or zoom: the 3D
transform/drag/wheel methods are empty no-ops, no pointer listeners are attached, and the
SVG is a fixed viewBox with no transform. Also removed the leftover grab cursor so the
drawing no longer even looks draggable.

## [6.19.0] — Residence is now a 2D isometric cutaway (no more fragile 3D)
Replaced the CSS-3D house with a fixed 2D isometric SVG drawing. It renders identically
every time — there is no rotation or zoom, so nothing can collapse to flat lines or blow
up and scatter the way the 3D model kept doing. This is the isometric look from earlier in
the project, re-themed to the panel's cyan and wired to live data.
- **Always-correct cutaway.** Basement, first floor (garage with doors, kitchen, dining,
  living room, guest room, hallway), and the dormered second floor (master bed, Eliana's
  room) drawn as a clean Iron-Man-HUD isometric.
- **Live presence.** Occupied rooms light up; the dominant room is brightest with a
  pulsing node and a "◉ DOMINANT" tag; idle rooms stay dim — same data that drove the old
  view.
- **Floor tabs emphasise a level.** All / 1st / 2nd / Basement dim the other floors so you
  can focus one. The property banner, sq-ft / bed-bath / style stats, and the OCCUPIED
  count (now replacing the old ANGLE readout) sit over the drawing, with the systems
  callouts down the sides.
- Removed the 3D drag/zoom/angle controls and machinery entirely. Smoke test updated to
  assert the SVG renders, the rooms draw, and the occupied count wires up. Audit clean,
  170 tests passing.


6.18.0 restored the right renderer but presented it badly: the auto-fit zoom blew the
house up to its ceiling on the wide Residence tab, and the default tilt was too top-down,
so the massing looked exploded and scattered instead of compact like the approved view.
- **Tamed the zoom.** Auto-fit is now capped at 1.5× (was 2.4×) and targets a compact,
  focal object — the house no longer fills the tab and overlaps itself.
- **Near-front hero angle.** Default and Fit now sit at ~22° rotation / -18° tilt — a
  gentle near-front view (matching the angle the approved preview was shown at) where the
  gable roof and dormers read as a solid mass instead of a foreshortened aerial.
- Scroll-zoom, drag-rotate, the 45/135/225/315 presets, and Fit are unchanged; Fit returns
  you to the hero view. Audit clean, smoke 20/20, 170 tests passing.


The 3D residence now uses the solid-walled Cape Cod renderer that was approved earlier
in this project (the one with a real gable roof, dormers, and chimney) — not the flat
floor-plate version that had crept in and collapsed to lines at low view angles.
- **Real house, real roof.** Walls render as solid volumes; the roof is an actual gable
  (front/back slopes + ridge + gable ends) with three dormers and a chimney. Garage
  doors sit at ground level and read open/closed from any `cover.*garage*` entity.
- **Live + dominant aware.** Occupied rooms glow cyan, the dominant room brightest with a
  pulsing node, idle rooms dim — driven by real presence.
- **Style selector drives the roof.** Cape Cod / Colonial / etc. keep the gabled roof;
  Modern / Apartment switch to a flat parapet cap. Rooms stay the same underneath.
- **Angle presets + Fit.** New 45° / 135° / 225° / 315° buttons and a Fit reset on the
  Residence tab, so a stray drag to a flat angle is one click to recover (the flat view
  was why the house looked broken). Scroll still zooms; drag still rotates; the house
  auto-fits the full-width tab.
- Smoke test (20 checks) now asserts the solid house actually builds (30+ faces, not flat
  plates) and the angle presets render. Audit clean, 170 tests passing.

> The renderer is the CSS-3D version pulled from this chat's history. It's a faithful
> stylized model of the actual house, not a Three.js/satellite reconstruction — that
> remains the separate, larger track if you want true engine-grade 3D.


The residence overview moves out of the cramped dashboard column into a dedicated
full-width tab, which is what unlocks the annotated-house treatment.
- **New "Residence" tab** between Command Center and Settings. Command Center keeps
  System Status / Camera Watch / Activity — the camera now owns the full center column
  (more room for the feed).
- **Full-width residence + callouts restored.** With the width back, the leader-line
  callouts return in the margins like the concept render: live presence on the left
  (dominant red, occupied cyan, idle dim) and system layers on the right, around a
  larger 3D house that auto-fits the wider canvas.
- **Home-style templates.** A HOME STYLE selector picks the massing/roof shell that the
  floor-plan rooms populate — Cape Cod, Colonial, Ranch, Two-Story, Craftsman, Modern,
  Townhouse, Apartment, Cabin. Peaked styles render a gable (end-walls + ridge), flat
  styles a parapet cap; the choice persists via `residence_style` config and tags the
  banner. This is the foundation for the template-driven 3D you described.
- Smoke test now exercises both tabs (18 checks): Command Center camera at native
  aspect with the residence moved out, and the Residence tab's scene, style selector,
  banner stats, restored callouts, and live dominant-room flag. Audit clean, 170 tests.

> Honest scope: the roof massing is a first pass built in CSS, and I can't visually
> verify 3D in my environment — the gable/flat shells differentiate styles but may need
> a tuning pass from a screenshot. True per-style accuracy, solid sloped/hip roofs, and
> satellite-imagery-derived geometry are the WebGL/Three.js build, which I'd take on as
> its own track on your go-ahead.


Corrections to the 6.16.0 dashboard from live feedback.
- **Camera shows its native aspect ratio.** The feed was a tall flex box with
  `object-fit: cover`, which cropped a 16:9 stream into an ultra-wide strip. The feed
  now sizes to the image's own ratio (`width:100%; height:auto`, no crop), so the
  picture is whole and correctly proportioned.
- **3D house auto-fits its column.** The house was scaled for the full-width centre it
  had before the camera split; in the narrower shared column it oversized and clipped.
  It now computes a fit-zoom from the scene width on every build (honoring a manual
  wheel-zoom once set), so it stays whole whatever the column width. Reminder: drag
  rotates — the default ~45° isometric is the intended view; a near-0° drag flattens
  the floor plates to lines.
- **Sq-ft estimate sane.** The estimate used a wrong factor and printed ~14,450. It's
  now clamped to a believable range (and still overridable via `floor_plan_sqft`).
- **Perimeter callouts pulled back.** They need generous side margins like the concept
  render; in the narrow secondary column they overlapped the house. The property banner
  + stats stay; the callouts return only when the residence has the width (see note).
- Smoke test updated (12 checks): native-aspect feed, banner + sane stats, callouts
  cleared. Python audit clean, 170 tests passing.

> Note: the residence can't carry the annotated-house concept *and* be a narrow panel
> beside a primary camera — that composition needs width. Make the residence the wide/
> primary element and the full callout treatment fits; keep the camera primary and the
> residence stays a clean house + banner.


The 3D Residence Overview now carries the identity of the "satellite + architectural
data-merge" concept — rendered in the panel's own medium (CSS/SVG, no engine, no
build), not a photoreal CGI reproduction.
- **Property banner.** Top-left header — `PROPERTY · <address> · SATELLITE +
  ARCHITECTURAL DATA MERGE` — with the address pulled from `floor_plan_address`.
- **Live stat block.** Top-right: EST SQ FT (from `floor_plan_sqft`, else estimated
  from the floor-plan geometry and labelled `~`), BED / BATH (counted from real
  area metadata), and the live rotation angle.
- **Leader-line callouts.** Annotation labels pinned to the scene perimeter with
  connector lines + nodes, the way the concept image annotates rooms. The left column
  is fed by **real presence** — dominant room flagged red, occupied rooms cyan, the
  rest dim — and refreshes every poll. The right column annotates the system layers
  (HVAC / electrical / plumbing / network mesh). Callouts are pinned to the frame, not
  projected onto the geometry, so they stay correct while you drag-rotate the house.
- **Wireframe glow** on the house, and the prior `house3d-hud` corner labels are
  replaced by the banner/stat overlay. The 3D isometric house, drag-rotate, floor
  tabs, presence glow and per-room light toggles are all unchanged underneath.
- Smoke test extended (now 13 checks) to assert the banner, populated stats, callout
  rendering, and dominant-room flagging. Python audit clean, 170 tests passing.

Not photoreal: this is a stylized HUD interpretation, not the ray-traced render. A true
volumetric version would need a WebGL/Three.js scene and a real satellite-image asset —
a separate, much larger build if you ever want to go there.


The separate Command Center panel is retired; its capability now lives in the main
JARVIS panel, which keeps its 3D isometric floor plan (the 2D top-down experiment is
dropped).
- **Camera Watch in the dashboard.** The live/selectable camera feed with event
  auto-focus — ported from the standalone panel into `jarvis-panel.js` — now sits in
  the Command Center tab. The dashboard centre is a 2-up: **Camera Watch (primary,
  wider) beside the 3D Residence Overview**. Cameras read from `config.cameras`, stream
  via HA's MJPEG proxy with the entity's access token, switch on chip click, and
  auto-focus the relevant feed on a `jarvis_camera_event` / `jarvis_face_recognized`
  (banner + 25s revert), with a still-image fallback for Nest/WebRTC. Subscriptions and
  timers are torn down in `_stopIntervals`.
- **Single panel.** `panel_register.py` registers only the JARVIS panel now and removes
  the stale `/jarvis-command` sidebar entry on upgrade. `jarvis-command.js` deleted.
- **JS behavioural test.** `scripts/smoke_panel.js` retargeted to the combined panel:
  renders it under jsdom with a realistic payload and asserts the dashboard draws —
  styles, the 3D scene, and the folded-in Camera Watch (chips from `config.cameras`,
  auto-selected stream wired with the token). 9/9 pass. Python audit clean, 170 tests
  passing.


First real-deployment look at the new panel surfaced two frontend bugs (data was
flowing — areas, presence, live log all correct — but the panel was broken):
- **Orphaned stylesheet.** The component's CSS (`JC_STYLES`) was defined but never
  injected into the shadow DOM — the `innerHTML` started at `<div class="app">` with
  no `<style>`. Result: a plain unstyled text stack, no grid/borders/colours. Now
  injected as `<style>${JC_STYLES}</style>…`.
- **Data-contract mismatches.** The panel read `d.cameras`, `d.presence`, and
  `d.lockdown`, but `get_panel_data` returns presence under `dominant` and nests
  `cameras` + `lockdown` inside `config`. So the camera picker was always empty
  ("NO CAMERA SELECTED") and the dominant-area temp showed "—". Now reads
  `d.config.cameras`, `d.dominant`, and `d.config.lockdown` (with fallbacks).
- **Why the audit missed it + the fix.** The release gates were Python-only
  (`scripts/audit.py`, pytest) plus `node --check`, which validates JS *syntax* but
  not behaviour — an orphaned const and a wrong object path are both valid syntax.
  Added `scripts/smoke_panel.js`: renders the component under jsdom with a realistic
  `get_panel_data` payload and asserts it actually draws (styles injected, grid +
  modules present, camera chips populated from `config.cameras`, dominant area/temp
  shown, live MJPEG `src` wired with the access token). 10/10 pass. Python audit
  clean, 170 tests passing.


Proactive audit (not wait-and-see) of the code paths that only began loading after
6.14.1 surfaced two real bugs in `intent/intent_router.py`:
- **`from . import audio_routing` → `from ..`.** `intent_router` lives in the
  `intent/` subpackage, so the single-dot form resolved to the non-existent
  `intent.audio_routing` instead of the top-level `audio_routing`. It's a lazy
  import inside `_area_of`, and `_call_domain_in_area` calls `_area_of` inside a
  `try/except` that swallows the `ImportError` — so `secure_area`/`lights_off`
  would have silently matched zero entities and done nothing. Now `..audio_routing`.
- **`from .automation.mutex import Priority` → `from ..automation.mutex`.** Same
  class of bug (added in 6.13.0): `.automation` resolved to `intent.automation`
  (doesn't exist) rather than the top-level `automation` package; would have thrown
  on any guarded intent execution. Now `..automation.mutex`.
- **New `scripts/audit.py`.** A real compile gate plus a cross-file resolver that
  verifies every relative import (top-level and lazy) points at a name that actually
  exists — the check that catches wrong levels and stale exports. Both bugs above
  compiled clean and passed the unit tests (which exercise pure functions, not these
  paths), which is exactly why this gate is now part of the release process. Audit
  reports clean; 170 tests passing.


- **Bug.** `audio/__init__.py` carried two module docstrings (a v6.13.0 edit
  prepended a second without removing the original), which pushed
  `from __future__ import annotations` to line 3 → `SyntaxError: from __future__
  imports must occur at the beginning of the file`. This aborted the whole
  integration import on HA startup (`Unable to import component: jarvis`). It was
  latent through 6.13.0–6.14.0 and only surfaced on the first HA restart after
  deploying. Fixed by collapsing to a single docstring.
- **Why the release audit missed it.** The pre-release syntax gate used
  `ast.parse`, which does **not** enforce `__future__` positioning — it parsed the
  broken file clean. Switched the gate to a real `compile()` / `py_compile`
  (bytecode compile), which catches `__future__` placement and matches how HA
  actually imports. Re-audited the full tree: all 62 modules compile clean. 170
  tests passing.


The tactical HUD ships as a real panel — a second sidebar entry, "Command Center"
(`/jarvis-command`), alongside the existing detailed JARVIS panel.
- **New panel (`frontend/jarvis-command.js`, `jarvis-command`).** The operational
  HUD: monospace/terminal styling, ASCII rules, the three-over-two layout. All data
  is live off `jarvis/get_panel_data` + `jarvis/get_activity_log` (polled): system
  status (observer/cognition/satellite count/LLM link), a 2D top-down occupancy plan
  whose nodes are driven by real per-area presence (dominant area labelled with live
  temp), and a live activity log. Quick actions are wired to real services —
  `jarvis.briefing`, `jarvis/set_lockdown` (toggles, reflects live state),
  `jarvis.diagnose_doorbell`.
- **Live, selectable camera feeds.** The camera panel streams the selected camera via
  HA's MJPEG proxy using the entity's rotating `access_token`, with a chip selector
  built from the live camera list and a still-image fallback for cameras that don't
  serve MJPEG (e.g. Nest/WebRTC). 
- **Event auto-focus.** `camera.py` now fires a `jarvis_camera_event` when a
  Frigate/Nest detection lands (entity, label, confidence); the panel subscribes to
  it (and to `jarvis_face_recognized`) and automatically switches the main feed to
  the camera of the event, banners "EVENT FOCUS · <label> <conf>%", flags the area on
  the plan, then reverts to the user's selection after ~25s.
- **Registration.** `panel_register.py` refactored to a shared `_register_one` helper
  registering both panels from the same static dir, each with independent content-hash
  cache-busting. The installer already copies `frontend/*.js`, so the new component
  ships with no run.sh change. No regressions — 170 passing.


Two additions from the resilience blueprint. (§7's `LocalSemanticMemory` was again
**not** re-added — `memory/` would shadow `memory.py`; the recovery ledger remains
top-level.)
- **Entity concurrency mutex (`automation/mutex.py`).** `EntityLockRegistry` enforces
  per-entity mutual exclusion on a priority ladder (PREDICTIVE < ROUTINE < INTENT <
  VISUAL < SAFETY): an equal-or-lower priority request is discarded while a lock is
  held, and a strictly-higher one preempts the holder — so a real-time presence
  command beats a stochastic predictive one rather than colliding. The intent router
  now acquires per-entity locks (at INTENT priority) before acting and releases after,
  skipping any entity already held at higher priority. Lock ops are synchronous dict
  mutations (safe on HA's single-threaded loop); the registry is stdlib-only and
  unit-tested, with an async `guard` context manager.
- **Differential noise gating (`audio/noise_gate.py`).** `NoiseGate` checks appliance
  power signatures (`sensor.<appliance>_power`) and subtracts the dominant running
  appliance's known dB contribution from the raw `ambient_db` before it reaches
  prosody — so a running dishwasher or microwave doesn't push JARVIS to project
  louder. Wired into `jarvis.speak`'s telemetry build; dB is floored at 0 and None
  passes through.
- **Tests:** +18 (mutex acquire/discard/preempt/release/guard, noise-gate threshold/
  dominant-attenuation/floor/passthrough) — 170 passing.


Two additions from the resilience blueprint. (§5's `LocalSemanticMemory` was again
**not** re-added, and the recovery ledger was placed at the top level rather than
the spec's `memory/ledger.py` — a `memory/` package would shadow `memory.py`.)
- **Heartbeat + failover (`diagnostics/heartbeat.py`).** `HeartbeatMonitor` probes
  fixed-IP audio satellites, flags a node unavailable after 3 missed cycles, and
  reroutes audio to the first available adjacent speaker (then any available node,
  then None). The probe defaults to a short TCP connect to the ESPHome API port but
  is injectable; the failover state machine is pure and fully unit-tested. This is a
  configured capability — construct it with your satellites' IPs/adjacency and drive
  `run_once` from an interval; it is not auto-started.
- **Write-ahead state ledger (`state_ledger.py`).** `StateLedger` durably appends a
  device intent (fsync'd JSON-lines) before a high-stakes action fires and a
  completion record after. On boot the integration reconciles any intent that never
  completed against the device's current state, logging actions a crash or power loss
  interrupted, then compacts the log. The intent router now records intent before
  `secure_area` (cover/lock) via an injected, duck-typed ledger — the router stays
  import-free and standalone-testable.
- **Tests:** +20 (heartbeat miss-threshold/recovery/failover ladder/injected probe,
  ledger record/complete/pending/reconcile/compact/torn-line tolerance) — 152 passing.


Three additions from the resilience blueprint (the spec's `LocalSemanticMemory`
was deliberately **not** re-added — it's the package that collided with `memory.py`;
its fault-history role already lives in `diagnostics/fault_log.py`).
- **Boot guard + alert queue (`boot_guard.py`).** `jarvis.speak` calls that arrive
  before the integration finishes initialising — or during a config-entry reload —
  are now buffered in a bounded in-memory queue and replayed in arrival order once
  setup reports ready, instead of being dropped or fired into a half-built system.
  Reload-safe (re-gates on each setup), drop-oldest overflow at 25, and the
  15-minute audit holds until ready. The buffer logic is a stdlib-only `AlertBuffer`
  so it's unit-tested directly.
- **Root-cause diagnostic trees (`diagnostics/monitor.py`).** When the core network
  switch drops offline, the triage engine now inspects its upstream power monitor
  (`sensor.core_switch_power_watts`) and folds the deduction into the spoken verdict:
  near-zero or unreachable power ⇒ "an upstream power loss on its utility circuit";
  power still present ⇒ "a network or uplink fault rather than a power loss."
- **Air-gapped fallback templates (`intent/templates.py`).** A curated set of
  hardcoded status phrases (grounded in this property's entities — switch, freeze
  sensor, sump pump, garage, storage, …) with keyword matching, for instant
  informational responses when every model link is unreachable. A starting
  scaffold, not 50 invented strings; extend `STATUS_TEMPLATES` as needed.
- **Tests:** +15 (root-cause branches, boot-queue buffer/replay/reload/overflow,
  template lookup/matching) — 132 passing.


- **Follow-up to 6.10.2.** Removing `memory/` from the repo isn't enough if the
  package still lingers in a deployed copy. Extracting a new release over an old
  add-on folder adds files but never deletes ones that were removed, so a stale
  `memory/` can survive in the source tree, ride into the rebuilt image, and get
  copied to `/config/custom_components/jarvis/memory/` on every start — where it
  again shadows `memory.py` and the Memory card reads "unavailable." `run.sh` now
  defensively deletes any `memory/` directory at the destination whenever the
  canonical `memory.py` is present, so a stale package cannot survive a deploy
  regardless of what the source tree carried. No Python change (117 passing).


- **Regression fix.** The `memory/` package added in 6.9.0 shadowed the existing
  top-level `memory.py` (ChromaDB / FTS5 semantic memory). Because Python resolves
  a package before a same-named module, `from .memory import get_memory_stats`
  (and `search_memory`, `store_memory`, `get_conversation_context`) silently
  imported the package — which only exported the fault store — so those calls hit
  their `except` paths: the panel's Memory card read **Backend: unavailable /
  Stored Memories: 0** and the conversation agent lost long-term recall. The
  collision was latent until 6.10.1 (which first actually deployed the
  subpackages) unmasked it.
- **Fix:** removed the `memory/` package and relocated its rolling fault-history
  store to **`diagnostics/fault_log.py`** as `FaultLog` (it was never semantic
  memory — it's an infrastructure fault ledger, and belongs with diagnostics).
  `proactive_audio` now imports `FaultLog` from `.diagnostics`; the audit's
  "this has occurred before" recall is unchanged. The on-disk file moves from
  `/config/jarvis/semantic_memory.json` to `/config/jarvis/fault_history.json`.
  `from .memory import …` once again resolves to the real `memory.py`, restoring
  `get_memory_stats`, `search_memory`, `store_memory`, and conversation context.
  Tests relocated accordingly (117 passing).


- **Critical install fix.** `run.sh` copied only the component's top-level
  `*.py`/`*.json`/`*.yaml` (plus the `frontend/`, `translations/`, `blueprints/`
  asset dirs) and never the Python subpackages. With `audio/`, `diagnostics/`,
  `vision/`, `memory/`, `intent/`, and `automation/` absent from
  `/config/custom_components/jarvis/`, `proactive_audio.py`'s top-level
  `from .audio import ProsodyController` raised `ModuleNotFoundError` and the
  whole integration failed to set up. The installer now copies every source
  subdirectory that is a Python package (selected by `__init__.py`, so future
  subpackages are picked up automatically), clearing previously-installed
  packages first so renamed/removed modules don't linger. Asset dirs have no
  `__init__.py` and are untouched. No Python changed (suite still 117 passing);
  this is purely the install step.


- **`intent/intent_router.py` — `LocalIntentRouter`:** local, cloud-free command
  matching with ordered regex patterns (`secure the garage`, `turn off the
  lights`, pronoun forms like `turn it off` / `close it`). Pronoun context
  resolves to the active entity in the target area — a playing `media_player`
  first, then an `on` `light` — and executes locally. Pure helpers
  `match_intent()` / `is_affirmative()` are stdlib-only and unit-tested; hass-
  touching code is lazily imported so the module loads standalone.
- **Interactive feedback loop:** `jarvis.speak` gains `expect_response` and
  `confirm_intent`; an actionable announcement opens a 10-second, wake-word-free
  confirmation window (fires `jarvis_feedback_window` for the voice satellite
  layer). New **`jarvis.process_intent`** service delivers a captured phrase — an
  affirmative completes the pending action, otherwise the phrase routes as a
  fresh command. One shared router per HA instance preserves the window between
  the two calls.
- **`automation/predictor.py` — `PredictiveHabitMatrix`:** time-bucketed habit
  model over a bounded on-disk log (5000 events). `probability(key, at)` is the
  share of distinct observed days the action recurred in that time bucket;
  `due_preemptions(now, lead_minutes)` surfaces actions whose probability clears
  90% in the window 5–10 minutes ahead. Wired into the 15-minute loop to sample
  occupancy and log candidates — pre-emptive **execution is OFF by default**
  (`PREDICTOR_AUTOEXECUTE`), in keeping with JARVIS earning autonomy.
- **`jarvis.speak` `user_id`:** accepted and threaded through (logged), reserved
  for per-user biometric/profile filtering.
- **Tests:** new `test_intent_router.py` (intent matching, affirmatives, area-
  scoped pronoun resolution) and `test_predictor.py` (probability moving average,
  90% threshold, bucketing, lookahead, persistence, rolling cap, corrupt-file
  tolerance), plus prosody/triage updates — **117 passing, 1 skipped**.


- **`vision/spatial.py` — `SpatialContextEngine`:** fuses three per-area presence
  signals into an occupancy-confidence score (`sensor.{area}_frigate_person_count`
  >0 → +0.60, `binary_sensor.{area}_camera_gaze_detected` → +0.20,
  `binary_sensor.{area}_mmwave_presence` → +0.35, clamped to [0,1]). When gaze AND
  mmWave presence are both established it sets `skip_preamble`, and `jarvis.speak`
  now feeds that into prosody.
- **Prosody `skip_preamble`:** when the listener is demonstrably present and
  attending, the speech rate eases by 0.05 so the terse, preamble-free status
  reads clearly. The telemetry key `media_active` is now accepted (alongside the
  legacy `media_playing`).
- **`memory/vector_store.py` — `LocalSemanticMemory`:** a rolling on-disk JSON
  buffer (last 1000 events) under `/config/jarvis/semantic_memory.json`, with
  `commit_event(text, tags)` and `query_related_faults(keywords)`. The 15-minute
  infrastructure audit now recalls prior occurrences of a fault (matched on the
  triage finding tags), folds a short history clause into the spoken warning, and
  commits each occurrence — all file I/O off the event loop. `InfrastructureTriage`
  verdicts now carry a `tags` list for this recall.
- **Tests:** 22 new unit tests for spatial fusion, the memory store (incl. rolling
  cap, persistence, corrupt-file tolerance), and the new prosody behaviour
  (81 passing total).


- **Target resolution now goes through `audio_routing`** instead of a private
  media_player enumeration. `jarvis.speak` resolves announcement speakers with
  `audio_routing.speakers_in_area` (the same area→speaker routing, including
  device-inherited areas and listen-only-satellite exclusion, used by briefings,
  the sentinel, and doorbell announcements), falling back to the house broadcast
  set (`announcement_speakers` panel override → configured `broadcast_group` →
  all non-satellite speakers) so an announcement is never silently dropped.
- Ambient light/noise telemetry now resolves area membership via the same
  `audio_routing.entity_area` helper, and media-playing state is read from the
  resolved target speakers — one area-resolution path instead of two.
- Ducking now applies to the resolved targets (the speakers actually used),
  restored in a `finally` block as before. Behaviour-equivalent for the common
  case (speakers in the area), but now consistent with the rest of JARVIS and
  correct for Cast-group and broadcast targets.


- **New service `jarvis.speak`** (`message`, `target_area`, `critical`): a
  context-aware spoken announcement. It resolves the target area's entities
  (direct *and* device-inherited), measures ambient light, noise, and media
  activity, and shapes delivery via a new `ProsodyController` — volume, speech
  rate, and a named style (authoritative / whisper / subdued / projected /
  neutral). Active media is ducked for the announcement and restored afterward
  in a `finally` block, so a TTS error never leaves your music turned down.
- **Infrastructure audit** (`InfrastructureTriage`): every 15 minutes JARVIS
  checks root storage (warn >90%, critical >96%), RAM (>92%), and the
  connectivity of the core network switch and basement freeze sensor, then
  synthesises a single natural-language verdict and announces failures to the
  office. Confirmed-offline is critical; unreadable/unavailable is a softer
  visibility warning. A startup probe runs ~60s after load so issues surface
  without waiting a full interval.
- New package layout: `audio/prosody.py` and `diagnostics/monitor.py` (both
  stdlib-only, no Home Assistant dependency), wired in through
  `proactive_audio.py` via two hooks in `async_setup_entry`/`async_unload_entry`.
- **Tests:** 23 new unit tests pin the prosody rule matrix and triage thresholds
  (59 passing total).
- **Config:** set your TTS entity (`proactive_tts_entity` in panel runtime config,
  else `tts.piper`) and the audit's target area (`office` by default).

## [6.7.3] — Safety-loop fix + regression test harness
- **Fix (critical):** since v6.7.1 the cognitive safety tick had been throwing
  `AttributeError` every cycle. `SafetyManager.tick` and `_check_intrusion` call
  `self._residents_away()`, but that method was defined on `LockdownManager`, not
  `SafetyManager` — so freeze, intrusion, and nighttime-lockdown checks were
  silently dying inside the loop's exception handler. `_residents_away` has been
  moved to `SafetyManager` where it is used; `LockdownManager` keeps the
  `_anyone_home` predicate it actually calls. Behaviour of both is unchanged from
  the v6.7.1 intent — they are now simply on the right classes.
- **Tooling:** introduced a Home-Assistant-free **pytest harness** under `tests/`.
  A `conftest.py` installs minimal `homeassistant.*` stubs into `sys.modules`
  before collection and loads integration modules under a synthetic `jc` package;
  hand-rolled fakes (`FakeHass`, `FakeProvider`) exercise `cognitive_core` and
  `reasoning_loop` as near-pure functions. A thin `pytest-homeassistant-custom-
  component` integration layer is scaffolded (skips cleanly until that dep is
  installed). 36 tests now cover the safety predicates, freeze thresholds, and the
  reasoning resilience cascade (cloud failure → breaker open → local floor). This
  is the harness that caught the bug above.

## [6.3.2] — Startup no longer blocked
- **Fix:** the cognitive loop was created with `async_create_task`, which Home
  Assistant tracks as part of config-entry setup — so HA's bootstrap waited the
  full startup timeout on a loop that never returns, logging "Something is
  blocking Home Assistant from wrapping up the start up phase." It now runs as a
  proper **background task** (exempt from the startup wait), with a guarded
  fallback for cores predating the helper.
- The loop also yields before its first tick so startup settles before any
  state-scanning work begins.

## [6.3.1] — Two root-cause fixes
- **Cognition:** `binary_sensor.backups_stale` was escalating as a
  "safety/security trigger" because device_class `problem` was lumped with
  smoke/CO/gas. `problem` now has its own moderate tier, and system-maintenance
  entities (backup, update, snapshot, certificate, HACS, supervisor, firmware…)
  are damped to informational so housekeeping never masquerades as a security
  event. Life-safety classes remain at top salience.
- **Doorbell backlog scanner:** device discovery now mirrors Home Assistant
  core's own Nest enumeration (`async_loaded_entries → runtime_data.device_manager`),
  fetches transcoded thumbnails for clip-preview events and image media for still
  events, and rejects raw MP4 bytes that a vision model can't read. Failure
  reporting is now stage-precise.

## [6.3.0] — Local speech parity
- All local speech now flows through one voice: the Local Mind's composer.
  The learned-cache replay, the legacy templates, and the fallback path no longer
  speak in three different vintages of phrasing.
- Device-aware language (a window "is open," not "is on"; motion "has detected
  motion"), safety-class phrasing ("is detecting smoke", "has cleared"), numeric
  readings (battery "is at 18%"), and named safety alerts ("a smoke alert from
  Kitchen Smoke Detector").

## [6.2.0] — The Local Mind
- An offline reasoning brain that replaces the crude fallback when the cloud is
  unreachable. It replicates a frontier model's decision *procedure*:
  self-awareness (duplicate + flap detection), historical grounding against
  `patterns.db`, case-based memory from past cloud decisions, situational
  judgment (urgency × novelty × presence × security), and persona verbalization.
- Every decision logs its reasoning chain to the dashboard's `LOCAL` log filter.

## [6.1.0] — Loosened reins (capability expansion)
- **Automation suggestions surfaced** in the dashboard with confidence bars,
  YAML reveal, and approve/dismiss — the pattern engine's intelligence is finally
  visible. Thresholds loosened and made runtime-tunable.
- **Visitor learning** — person events feed silent vision learning (training data
  only, never spoken).
- **Rich Reasoning** — optional cloud-first judgment for medium/high events.
- **Ollama groundwork** — a Local LLM URL field flows through every provider path
  for the upcoming GPU server.

## [6.0.0] — Glassmorphism UI
- A deep visual reskin: dark-cyan glassmorphism, Space Grotesk + JetBrains Mono,
  a perspective-grid 3D house with a rotating radar sweep, glass panels, and a
  status badge wired to lockdown state. No structural changes — pure aesthetics.

## [5.9.50] — Doorbell Training UI
- A Settings panel showing the analyzed doorbell dataset, with a backlog-scan
  button and source-tagged event rows (live / event-media / backlog).

## [5.9.49] — Package & mail detection
- Porch-camera watching for packages and mail with a per-camera state machine,
  15-minute sweeps, quiet-hours gating, and an on-demand `jarvis.check_packages`
  service.

## [5.9.48] — Doorbell-only analysis + backlog training
- Camera auto-analysis narrowed to intentional doorbell presses, with a two-pass
  live-clip / recorded-event approach and a JSONL training log.

## [5.9.47] — Automatic camera event analysis
- Doorbell and (optionally) motion events are now auto-analyzed as they happen,
  not just cached.

## [5.9.46] — Appliance profile loading fix
- The appliance monitor now reads its saved profile directly from live runtime
  config instead of falling back to legacy guessing.

## [5.9.45] — Appliance row UI fix
- Restructured appliance cards so delete buttons are no longer covered.

## [5.9.44] — Per-room 3D occupancy glow
- The isometric house lights rooms by occupancy: idle wireframe, occupied cyan
  glow, dominant room pulsing.

## [5.9.43] — Iron Man HUD radial gauges
- Temperature, humidity, and lighting for the dominant room rendered as SVG donut
  gauges.

---

Earlier releases (v5.7–v5.9.42) introduced the observer pipeline, the connectivity
breaker, lockdown management, multi-frame camera vision, constrained appliance
disaggregation, quiet-hours gating, and the reasoning cache.
