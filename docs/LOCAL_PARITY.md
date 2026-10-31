# Local-capability parity

> Status: **design principle + current coverage.** States a policy the codebase
> follows and names the modules that back it. Honest about where parity is real,
> where it is partial by deliberate trade, and where it is out of reach.

## The principle

**Local models are first-class. If the configured model lacks a capability a
feature needs, JARVIS provides it — it does not silently degrade or fail for
local users.**

A large share of JARVIS installs run a local model (Ollama — Gemma, Llama,
Qwen, …) for privacy, cost, or latency. The sharpest difference between a local
model and a frontier cloud model is not raw intelligence; it is **function /
tool calling**. Every cloud provider (Groq, OpenAI, Gemini, Anthropic) exposes a
native tool-calling API. Many local models — Gemma in particular — do not. A
feature built only on native tool-calling therefore works for cloud users and
quietly does nothing for a Gemma user: it narrates an action and never performs
it (the "replied with no tool call" signature, issue #413).

The policy says: that gap is JARVIS's to close, not the user's to work around.

## How it is provided — the shared tool-driver

`tool_driver.py` is the mechanism. It drives one agentic loop in either of two
modes behind a single interface:

- **native** — the provider's own function-calling. The model emits tool_calls;
  JARVIS runs them via the handlers and feeds results back as `tool` messages.
- **json** — a tools-free protocol any instruction-following model can satisfy.
  The model emits ONE JSON action per turn, `{"tool":<name>,"args":{…}}`, run via
  the **same** handlers and fed back as a user message, until it replies
  `{"tool":"final","text":"…"}`.

The mode is chosen by a **capability probe**, `tool_driver.supports_tools`:

- every cloud provider's `supports_tools()` returns `True` → native, no probe;
- `OllamaProvider.supports_tools()` returns `None` → the driver probes once (a
  trivial forced tool call) and **caches** the verdict per model, so a
  tool-capable local model (Llama 3.1 / Qwen 3) uses native and Gemma uses JSON,
  each decided once;
- a client exposing no signal runs native with a JSON fallback on no tool call.

A handler returns `Done(value)` to finish the loop or a value to feed back and
continue — the same handlers serve both modes, so the two never drift.

## Where it is applied

| Feature | Module | Parity |
|---|---|---|
| Automation composer (Suggestions tab + chat) | `automation_creator.draft_automation` | **Full** — search → compose → validate → retry, native or JSON |
| Main agent (device control, queries, scenes, web, calendar, …) | `agent.run_agent` → `_run_agent_json` | **Essential set** — a non-tool model drives the curated core tools over JSON; see trade below |
| Delegation (FRIDAY/HOMER + capability sub-agents) | `agent._run_delegated` → `run_agent(allowed_tools=…)` | **Full, transitively** — a delegated sub-agent on a non-tool model takes the JSON path with its scoped tool set |
| Web research grounded fallback | `web_research._llm_grounded_fallback` | **Full** — fires whenever Gemini is configured in ANY provider slot (Main, reasoning, vision, review, classifier) and a key is present, so an Ollama install with a Gemini key (e.g. for vision) gets grounded web answers too |

### The one deliberate trade: the main agent's tool surface

`run_agent`'s JSON path exposes the **curated essential tool set** (the same set
the 413 slim-retry uses: control, state, search, scenes, areas, bulk, home
summary, web, calendar, weather), not the full ~40-tool surface. Two honest
reasons:

1. a 40-tool JSON protocol does not fit a small local model's context window
   alongside the system prompt and a multi-step loop;
2. a small model does not follow a 40-tool JSON menu reliably.

The model reaches everything else through `search_entities` + `control_device`.
This is **parity for the common agentic cases, not the full surface** — called
out so the capability matrix is not overstated. A scoped sub-agent keeps its own
narrow set; HA per-entity tools are dropped from the JSON path.

## Where parity is genuinely out of reach

Some capabilities are inherent to a specific model and cannot be synthesised:

- **Live web grounding by the model itself.** A local model has no web access.
  JARVIS provides web *retrieval* for everyone via DuckDuckGo / SearXNG
  (model-independent), and the model-grounded fallback via Gemini when a Gemini
  key exists — but it cannot make an offline Gemma search the live web on its own.
- **Vision on a text-only local model.** Handled by letting `vision_provider` be
  set independently (commonly Gemini), not by faking vision on a text model.

These are provided by *routing to a capable model for that one job*, which is the
same principle from the other direction: use the right tool, don't degrade.

## For contributors

When you build a feature that needs the model to call a tool, **route it through
`tool_driver.run_tool_loop`** rather than calling `client.chat(tools=…)` and
reading `tool_calls` directly. That one choice is what keeps the feature working
for a local, non-function-calling model. If native tool-calling is genuinely
required and no JSON equivalent is possible, say so explicitly and gate the
feature on `supports_tools`, so a local user gets a clear message instead of a
silent no-op.
