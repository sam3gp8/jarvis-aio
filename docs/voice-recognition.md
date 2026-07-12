# JARVIS Voice Recognition — Setup

JARVIS can know who it's talking to by **voice**, fuse that with who's home and
who's on camera, and **learn people's voices over time** from ordinary
conversation. This is the setup guide.

## How it's put together

JARVIS does **not** run a speaker-embedding model inside Home Assistant — that's
heavy, dependency-laden, and needs the raw utterance audio. Instead a dedicated
speaker-recognition service does the embedding + enrollment and publishes *who is
speaking* to Home Assistant; JARVIS **consumes** that signal and feeds it into
its identity resolver's voice tier (the strongest tier).

```
  Voice satellite / Assist audio
            │
            ▼
  Speaker-recognition service          ← does the ML (embeddings, enrollment)
  (VoiceBM · speaker-recognition · …)
            │  publishes "current speaker" to HA
            ▼
  binary_sensor.<person>_voice  /  sensor.current_speaker
            │
            ▼
  JARVIS identity resolver  ──►  voice + presence + face  ──►  who you are
```

You supply the service; JARVIS supplies the fusion and the personalization
(per-person memory, routines, command attribution) it already has.

## Step 1 — Run a speaker-recognition service

Any backend works as long as it surfaces the current speaker as an HA entity.
Two good options:

- **VoiceBM** — `github.com/cybericebyte/VoiceBM`. Sherpa-ONNX, **CPU-only**,
  publishes over MQTT with HA discovery. Gives you a per-person
  `binary_sensor.<person>_voice` (ON while they speak) and a current-speaker
  sensor, plus a built-in enrollment/review flow. Closest fit.
- **speaker-recognition** — `github.com/EuleMitKeule/speaker-recognition`.
  Resemblyzer, runs as a Home Assistant **add-on** + integration exposing a
  current-speaker sensor, with `/train` and `/recognize` REST endpoints.

Install and enroll a couple of voices per their instructions, then confirm the
entity exists in **Developer Tools → States** (e.g. `binary_sensor.sam_voice`
flips ON when Sam speaks, or `sensor.current_speaker` shows a name).

> GPU note: neither service *requires* a GPU (both default to CPU/ONNX). Your
> Ollama GPU is free to keep serving the LLM. If you later want faster or
> higher-accuracy embeddings, point the service's model at the GPU — that's a
> service-side change, independent of JARVIS.

## Step 2 — Point JARVIS at it

In **Settings → Devices & Services → JARVIS → Configure → Identity**:

- **Enable the voice tier** — turn on *voice fingerprint* (`identity_voice_fingerprint`).
- **Voice recognition source** (`voice_recognition_source`) — one of:
  - a glob for per-person sensors: `binary_sensor.*_voice` (VoiceBM style), or
  - a single current-speaker sensor: `sensor.current_speaker`.

That's it — JARVIS now weights voice most heavily, and falls back to who's home /
who's on camera when the voice is uncertain, so it degrades gracefully.

**Confidence:** if your source publishes a confidence/score attribute JARVIS uses
it (accepts 0–1 or 0–100); otherwise it uses `voice_recognition_confidence`
(default 0.85).

## Step 3 — Learn voices over time (hands-free enrollment)

The service does the enrolling, but the hard part is the **label** — *who* is this
unknown voice? JARVIS already knows the answer when you're the only one home or a
camera just recognized your face. So when JARVIS is confident who's speaking from
those signals but the voice service doesn't recognize the voice yet, it fires:

```
event: jarvis_voice_enroll_candidate
data:  { person: "sam", device_id: "…" }
```

Wire that to your service's enrollment so profiles build themselves from normal
conversation. Example (VoiceBM, enrolling the pending sample under the named
person):

```yaml
automation:
  - alias: "JARVIS · auto-enroll voice"
    trigger:
      - platform: event
        event_type: jarvis_voice_enroll_candidate
    action:
      # Enroll VoiceBM's pending utterance under the person JARVIS identified.
      # (Use your service's actual enroll service / MQTT command.)
      - service: mqtt.publish
        data:
          topic: "voicebm/enroll"
          payload: "{{ trigger.event.data.person }}"
```

For `speaker-recognition`, call its `/train` with the pending sample tagged with
`trigger.event.data.person` instead.

Auto-flagging is rate-limited (once per person per 5 minutes) and can be turned
off with **Auto-flag voices to enroll** (`voice_recognition_auto_enroll`).

## Config reference

| Key | Default | Meaning |
|---|---|---|
| `identity_voice_fingerprint` | `false` | Master enable for the voice tier |
| `voice_recognition_source` | — | Current-speaker sensor id, or a glob like `binary_sensor.*_voice` |
| `voice_recognition_confidence` | `0.85` | Fallback score when the source carries none |
| `voice_recognition_auto_enroll` | `true` | Fire `jarvis_voice_enroll_candidate` for hands-free learning |
| `identity_min_confidence` | `0.45` | Below this, JARVIS says "unknown" rather than guess |

## How JARVIS uses the result

Once JARVIS knows who's speaking, everything per-person it already does kicks in:
commands are attributed to the right person, *their* preferences and facts surface
(and stay private from other residents), and the routines it learns are filed
under the right person. Voice just makes that attribution far more reliable than
presence + face alone.
