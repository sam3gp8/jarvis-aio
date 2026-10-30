# How close can JARVIS-AIO get to the MCU JARVIS?

> Status: **advisory / design.** A grounded capability-gap analysis, not a
> promise and not a release. It maps what the JARVIS of the films *does* onto what
> this codebase actually does today, says honestly what is close, what is reachable
> with real work, and what is out of reach inside Home Assistant — and ends with a
> value-first shortlist of what to build next. It changes no behaviour and flips no
> matrix cell. Every "we already have X" below names the module that backs it.

The companion documents look **inward** at the architecture: `KERNEL_PLAN.md` is
the migration spine (shadow → parity → enforce, phases H→Ω) and `AUDIT_2026-10.md`
grades that spine against itself. This document looks **outward** at the
*experience* — the thing a person means when they say "I want JARVIS." The two are
complementary: the kernel plan answers *"is the architecture coherent?"*; this
answers *"does it feel like JARVIS, and how much closer can it honestly get?"*

The one-line answer, stated up front so the rest can be read as evidence for it:

> **The gap to MCU JARVIS is not intelligence and not architecture. It is
> embodiment (a body, fabrication, holograms — out of reach, and correctly so) and
> reflex fluency (ambient presence, anticipation, earned autonomy — reachable, and
> already on the ladder). Within Home Assistant, "as close as possible" is a
> fluent, anticipating, always-present butler that controls the whole home,
> delegates safely, remembers across restarts, and has real character — most of the
> *experience* of JARVIS minus the sci-fi hardware. In one dimension JARVIS-AIO is
> already ahead of the films: principled, auditable governance.**

---

## 1. What the MCU JARVIS actually is

Stripping the spectacle, the JARVIS of the films is eight things at once:

1. **Ambient conversational presence.** No wake word, no turn-taking friction. He
   is simply *there*, in the room, and Tony talks to him like a person. Dry
   British wit, unfailingly deferential ("sir"), but willing to push back.
2. **Whole-environment control.** The workshop, the mansion, lights, security,
   media — and, famously, the fabrication bays and the suits (the Iron Legion, the
   "House Party Protocol").
3. **Deep real-time perception.** Sees everything through cameras and sensors,
   reads blueprints at a glance, analyses materials, renders and manipulates
   holographic models.
4. **Proactive anticipation.** Surfaces what Tony needs *before* he asks — runs
   diagnostics unprompted, flags the anomaly, has the simulation ready.
5. **Autonomous multi-step execution and delegation.** Given an objective he plans,
   acts, verifies, and spins up sub-agents (pilots a legion of suits) to do it.
6. **Persistent memory and learning.** Remembers everything, learns Tony's
   preferences and patterns, and gets better over time.
7. **Judgment and personality.** Moral reasoning, loyalty, humour, genuine
   character — not a command parser with a voice skin.
8. **(Eventually) embodiment** — JARVIS becomes Vision. This is sci-fi and sits
   outside any honest scope discussion; it is named here only to retire it.

Those are the eight axes the rest of this document grades against.

---

## 2. What JARVIS-AIO already has (grounded in the code)

Each MCU axis, mapped to the module(s) that actually implement it. This is the
"so we wrap, don't rebuild" census from `KERNEL_PLAN.md`, re-cut by *felt
capability* instead of by kernel primitive.

| MCU axis | Backing code today | Honest stage |
| --- | --- | --- |
| Conversational presence | `conversation.py` (HA Assist agent; provider-agnostic agentic tool loop, `MAX_ITERS=8`; multi-wake dedup across satellites; presence- and recognition-aware context) | Real, per-turn (wake-word bound) |
| Personality | `persona.py` (registers `light/neutral/urgent/grave`; banter levels `0/1/2` where 2 = "Stark's JARVIS"; honorific "sir"; *"does not quip during a smoke alarm"*) | **Genuinely close** |
| Whole-environment control | 43 agent tools in `agent.py` (`control_device`, `bulk_control`, `run_scene_script`, `create_automation`, `set_mode`, `manage_autonomy`, …), all converging on the authority-gated actuator seam | Close, within HA's entity graph |
| Perception | `vision/` (scene memory, camera analysis), `recognition.py` (who-is-where / faces), `presence.py` (multi-source fusion), `cognition.py` (sees every state change, per-entity rhythm model) | Partial, HA-sensor bound |
| Proactive anticipation | `cognition.py` (salience + anomaly scoring, "this usually happens by now"), `pattern_analyzer.py`, `proactive_audio.py`, `proactive_briefing.py` (arrival / security / briefing / scheduled); `kernel/causal.py` prediction | Partial (heuristic; prediction at parity) |
| Autonomous multi-step + delegation | `goals.py` + `execute_plan` (planner → executor → verifier), `agent.py` FRIDAY/HOMER delegation via `kernel/agency.py` (children can only *narrow* capability — **enforce**), `kernel/budget.py` | **Real, governed** |
| Memory | `knowledge.py` (facts + typed relations graph — **enforce**), `continuity.py` + `kernel/agency_state.py` (resume-after-restart seam), reasoning cache | Memory close |
| Learning | `kernel/learning.py` (structured `Outcome` → bounded, reversible belief updates) | Deliberately held at **parity** |
| Judgment / governance | `docs/JARVIS_CONSTITUTION.md` precedence ladder (life-safety > security > property > household > convenience > personality), `kernel/authority.py` (**enforce**), the *"more capability never means less governance"* invariant | **Ahead of the films** |
| Resilience (offline) | `local_mind.py` (offline reasoning brain), `local_engine.py`, connectivity breaker | A story the films never needed |

Two things stand out from this census:

- The **cognitive spine is already here and already coherent.** Per
  `AUDIT_2026-10.md`, 18 of 47 kernel primitives are at **enforce** (authority,
  agency, actuator, situation, world-model, graph, learning-path, autonomy-path,
  …). The architecture is not a sketch; it runs.
- The **character is real, not a skin.** `persona.py` is a register machine with a
  moral rule baked in (plainer speech as the situation grows graver; no wit during
  an alarm). That is closer to the films' JARVIS than most assistants ever get,
  and it is fifteen minutes of config away from full Stark-register wit
  (`banter_level: 2`).

---

## 3. The honest gap — three buckets

### Bucket A — Already close (the butler is real)

These need polish and fluency, not invention.

- **Conversational voice presence with genuine character.** `conversation.py` +
  `persona.py` deliver a deferential, dry, context-aware JARVIS today. The
  remaining inches are *fluency*, not capability (see Bucket B: ambient presence,
  reflex latency).
- **Whole-home control within the HA entity graph.** 43 tools, authority-gated,
  verify-after-act. Everything HA can represent, JARVIS can drive.
- **Multi-agent delegation that provably cannot self-escalate.** `kernel/agency.py`
  derives a strictly *narrower* child token; the no-escalation rule is the same one
  `authority.py` enforces. This is the films' "spin up helpers" — safely.
- **Durable memory and continuity-of-self across restarts.** `knowledge.py`
  (graph, enforce) + `continuity.py` / `agency_state.py` mean JARVIS resumes rather
  than waking blank.
- **Executable governance — arguably *beyond* the films.** The MCU JARVIS has no
  visible constitution; he simply obeys Tony. JARVIS-AIO has a written precedence
  ladder, an authority engine, and four CI gates that forbid an actuator from
  bypassing authority. For a system that lives in a real house, that is not a
  shortfall — it is the feature that makes the rest *trustworthy*.

### Bucket B — Reachable with real work (on the ladder, honestly staged)

Each of these already has kernel substrate at pure/shadow/parity in
`KERNEL_PLAN.md`; the open work is *authoritative dependence*, not building.

- **Ambient presence (lowest wake-word friction, cross-surface continuity).** The
  single biggest lever on *felt* MCU-ness. `conversation.py` already dedups
  simultaneous wakes; Phase AA (`kernel/surfaces.py`, staged-ahead) carries the
  `interaction_id` design for "the same conversation follows you room to room."
  Making presence feel *ambient* rather than *summoned* is mostly product + latency
  work on an existing base.
- **Proactive fluency (model-driven, not heuristic).** Today anticipation is
  salience heuristics (`cognition.py`) plus prediction at **parity** (`kernel/causal.py`,
  Phase L). Walking L → enforce and landing Phase P (household intelligence) turns
  "it noticed an anomaly" into "it had the answer ready" — the films' signature.
- **Graduated / contextual autonomy (earned per-capability trust).** Phase N/AC,
  owner-gated. The substrate is shipped at parity (`kernel/autonomy.py`: a trust
  level that is a pure function of verified-outcome rate × risk class, security
  pinned at confirm). This is the biggest *honest* "acts on its own like the films"
  lever: JARVIS does the safe, proven things without asking and confirms the rest.
- **Deeper world/space-time reasoning.** Knowledge graph is already **enforce**
  (Phase T); space/time is at parity (Phase Q). More of the home's structure
  becomes first-class as these climb.
- **A closed learning loop.** `kernel/learning.py` is deliberately parked at
  parity: it reads structured `Outcome`s, never raw logs, and — by invariant — may
  never relax a safety threshold. Closing it (Phase M, owner-gated) is reachable;
  holding it until the Outcome model is solid is the correct call, not a gap.
- **Faster local reflex.** The README names this as *the* frontier: "where the
  frontier is reflex speed, not intelligence." `cognition.py` and `local_mind.py`
  are explicitly built to swap a local GPU/Ollama model behind `decide()` with no
  pipeline change. This is the clearest "same JARVIS, feels faster" win.

### Bucket C — Genuinely out of reach inside Home Assistant (and why)

Named plainly so the roadmap stays honest. None of these is a failure; most are
correct scope decisions.

- **A physical body / robotics / fabrication.** The MCU JARVIS builds suits, pilots
  the Iron Legion, and becomes Vision. Home Assistant controls *entities*, not
  actuated robotics at that fidelity. This is the README's "deliberately
  HA-bounded body," and it is the hard ceiling.
- **Holographic / AR workshop UI.** No equivalent surface exists; HA offers
  dashboards, voice, and a panel. The *interaction* can be excellent; the
  *hologram* cannot.
- **Stark-grade compute and open-ended AGI judgment.** The films' JARVIS reasons
  about novel physics and invents. JARVIS-AIO's judgment is frontier-LLM-bounded
  online and, by its own honest admission (`local_mind.py`), heuristic offline — it
  "does not replicate open-ended understanding of genuinely novel situations."
- **Film-fidelity perception.** Reading a blueprint or analysing an alloy from a
  glance is beyond HA's camera/sensor reality. Perception is real but bounded.
- **World-affecting agency beyond the home.** Piloting vehicles, commandeering
  satellites. The governance model refuses this *by design* — and should. A JARVIS
  you trust in your house is one that cannot reach past it.

---

## 4. The reframe

Put the three buckets together and the shape is clear. Popular intuition says the
distance to JARVIS is *intelligence* — a smarter brain. The code says otherwise.
The brain (a coherent kernel, a real character, governed autonomy, durable memory)
is substantially here. What separates JARVIS-AIO from the *felt* experience of the
films is two different things:

1. **Embodiment** — the body, the fabrication, the holograms. Out of reach inside
   HA, and the system is honest and correct about that.
2. **Reflex fluency** — ambient always-there presence, anticipation that arrives
   before the question, and autonomy that acts on the proven-safe without asking.
   *This* is reachable, and it is exactly what the post-H roadmap is walking up the
   shadow → parity → enforce ladder.

So "as close as possible within Home Assistant" is not a smaller JARVIS. It is a
butler who happens not to have a body: present, anticipating, controlling the whole
home, delegating safely, remembering, and genuinely characterful — governed by an
auditable constitution the films never bothered to write.

---

## 5. Value-first shortlist (what to actually do next)

Ranked by *felt MCU-ness per unit of honest work*, not by matrix coverage. Each
rides an existing seam; none invents a parallel system; all keep governance ahead
of capability.

| # | Move | Why it's the highest-leverage honest win | Seam it rides |
| --- | --- | --- | --- |
| 1 | **Ambient presence** — cut wake-word/turn-taking friction; carry one conversation across surfaces | The single biggest change in how *present* JARVIS feels; base already dedups wakes | `conversation.py` + Phase AA `surfaces` |
| 2 | **Graduated autonomy** — per-capability earned trust, owner-gated, safety pinned | The most honest "acts like the films" lever: act on proven-safe, confirm the rest | `kernel/autonomy.py` (parity → enforce, Phase N) |
| 3 | **Proactive fluency** — model-driven anticipation, answer-ready | Turns "noticed" into "anticipated" — the films' signature | `kernel/causal.py` (L) + Phase P |
| 4 | **Reflex speed** — local GPU/Ollama behind `decide()` | README's stated frontier; same JARVIS, faster; no pipeline change | `cognition.py` / `local_mind.py` |
| 5 | **Keep governance ahead of capability** | The invariant that makes a house-resident JARVIS *trustworthy* — do not trade it for any of 1–4 | Constitution + 4 CI gates |

A sensible first increment, if one is wanted, is **#1 or #2** — both are mostly
wiring on substrate that already exists at parity/shadow, both are owner-gated, and
both move the *experience* far more than another enforce-flip on the coverage
matrix would. Neither should be started without the owner choosing it: #2 in
particular **PAUSES for owner before enforce** by the roadmap's own rule, because
it changes who/what may act without asking.

---

## 6. What this document deliberately does *not* claim

- It does not claim a coverage number. Felt capability and matrix coverage are
  different axes; conflating them is exactly the inflation the Constitution forbids.
- It does not propose flipping any cell. Every item in Bucket B is already staged in
  `KERNEL_PLAN.md` with its own kill-switch and owner gate; this document only ranks
  them by experienced value.
- It does not promise the body. Bucket C is the ceiling, stated once, honestly.
