# Changelog

All notable changes to JARVIS are documented here. This project uses semantic-ish
versioning (`MAJOR.MINOR.PATCH`); UI reskins and capability expansions bump MINOR,
bug fixes bump PATCH.

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
