# JARVIS traction report — 2026-10-31

Generated daily by `.github/workflows/traffic.yml`. What to do with these
numbers lives in `docs/GROWTH.md` on the default branch. Clone counts
include CI checkouts, so treat views and referrers as the audience signal.

## 7 days to 2026-10-30 vs the 7 before

| Metric | Last 7d | Prior 7d | Change |
|---|---:|---:|---:|
| Views | 650 | 217 | +200% |
| Unique visitors (sum of daily) | 239 | 114 | +110% |
| Clones | 1120 | 501 | +124% |
| Unique cloners (sum of daily) | 287 | 191 | +50% |

## Totals

| Metric | Now | 7d change | 30d change |
|---|---:|---:|---:|
| Stars | 33 | +3 | +13 |
| Forks | 8 | +2 | +5 |
| Watchers | 1 | +0 | -1 |
| HA analytics installs | 127 | +19 | — |
| Release asset downloads | 0 | +0 | +0 |

## HA forum thread

[Jarvis-AIO](https://community.home-assistant.io/t/jarvis-aio/1027117): **1449** views (+532 this week), **12** replies (+8 this week).

New posts in the last 7 days:
- #5 @sam3gp8 (2026-10-24): @williammorran thanks for reporting this. It turned up a real bug. JARVIS was reading secrets.yaml more strictly than Home Assistant does. If the file had a Home Assistant-only tag anywhere in it, li…
- #6 @williammorran (2026-10-24): Thanks ill take a look when they restore internet to the area, cloud routers suck so I cant even access my internal network as the cloud based settings control even dhcp. But thanks for the change il…
- #7 @biancapascal (2026-10-26): I installed Yarvis, also i have credits on openrouter.ai but for some reasons he dos’nt speak. in settalite speakers i also have no speaker what i try Can anybody help me please?
- #8 @sam3gp8 (2026-10-27): Hi! Two separate things are probably going on here — let’s split them, because one is the “brain” (OpenRouter) and one is the “voice” (speaking). The quickest way to tell which: open the JARVIS panel…
- #9 @biancapascal (2026-10-27): a.> JARVIS AI Assistant HA OS b.> allot of speakers i have, sonos, tv , chrome cast. c.> Jarvis replies very good in de chat, if i try in my setting off HA the tts pipe i hear it speak. I tryed many…
- #10 @sam3gp8 (2026-10-28): @biancapascal thanks, that narrows it down a lot. Chat works and HA’s own voice works, so your speakers and TTS are fine. The problem is the voice JARVIS asks for. Please try this: In the JARVIS pane…
- #11 @biancapascal (2026-10-28): I have from everything the latest version. This says my log.: 19:09:59 AGENT replied with no tool call — served by custom/google/gemini-2.5-flash, 50 tool(s) available 19:09:57 AGENT LLM needed (comp…
- #12 @sam3gp8 (2026-10-29): @biancapascal thanks for the log, that explains it! device=None means the message was typed (in the JARVIS chat or the Assist text box). JARVIS answers typed messages in text only. It speaks only whe…
- #14 @sam3gp8 (2026-10-29): @neena022gupta this was fixed in 9.30.2 . Update JARVIS in HACS and restart, then follow the steps in my reply to williammorran above (post #5 ). If it still doesn’t work, post the exact error JARVIS…

## Top referrers (rolling 14 days)

| Source | Views | Uniques |
|---|---:|---:|
| community.home-assistant.io | 80 | 50 |
| Google | 37 | 22 |
| github.com | 30 | 20 |
| aocr.org | 9 | 9 |
| Bing | 2 | 2 |
| DuckDuckGo | 1 | 1 |

## Most-visited pages (rolling 14 days)

| Path | Views | Uniques |
|---|---:|---:|
| `/sam3gp8/jarvis-aio` | 426 | 232 |
| `/sam3gp8/jarvis-aio/issues` | 97 | 38 |
| `/sam3gp8/jarvis-aio/pulls` | 39 | 8 |
| `/sam3gp8/jarvis-aio/blob/main/docs/media/residence-3d.png` | 19 | 17 |
| `/sam3gp8/jarvis-aio/blob/main/docs/media/areas.png` | 18 | 14 |
| `/sam3gp8/jarvis-aio/issues/334` | 8 | 8 |
| `/sam3gp8/jarvis-aio/releases` | 7 | 7 |
| `/sam3gp8/jarvis-aio/blob/main/docs/media/activity-feed.png` | 7 | 6 |
| `/sam3gp8/jarvis-aio/discussions` | 7 | 5 |
| `/sam3gp8/jarvis-aio/issues/413` | 7 | 4 |

## Daily views & clones (last 30 days)

| Date | Views | Uniq | Clones | Uniq |
|---|---:|---:|---:|---:|
| 2026-10-30 | 138 | 44 | 346 | 87 |
| 2026-10-29 | 206 | 63 | 249 | 46 |
| 2026-10-28 | 68 | 30 | 101 | 29 |
| 2026-10-27 | 49 | 27 | 37 | 24 |
| 2026-10-26 | 59 | 31 | 50 | 18 |
| 2026-10-25 | 28 | 18 | 93 | 27 |
| 2026-10-24 | 102 | 26 | 244 | 56 |
| 2026-10-23 | 21 | 14 | 71 | 33 |
| 2026-10-22 | 17 | 9 | 189 | 47 |
| 2026-10-21 | 8 | 8 | 115 | 36 |
| 2026-10-20 | 24 | 11 | 21 | 17 |
| 2026-10-19 | 36 | 16 | 20 | 13 |
| 2026-10-18 | 50 | 30 | 42 | 23 |
| 2026-10-17 | 61 | 26 | 43 | 22 |
| 2026-10-16 | 91 | 38 | 154 | 54 |
| 2026-10-15 | 26 | 14 | 189 | 45 |
| 2026-10-14 | 35 | 12 | 149 | 44 |
| 2026-10-13 | 41 | 13 | 84 | 41 |
| 2026-10-12 | 102 | 32 | 442 | 114 |
| 2026-10-11 | 64 | 31 | 1016 | 281 |
| 2026-10-10 | 85 | 26 | 912 | 245 |
| 2026-10-09 | 155 | 30 | 1561 | 291 |
| 2026-10-08 | 132 | 25 | 593 | 149 |
| 2026-10-07 | 169 | 22 | 293 | 82 |
| 2026-10-06 | 183 | 30 | 478 | 114 |
| 2026-10-05 | 103 | 34 | 677 | 170 |
| 2026-10-04 | 77 | 28 | 1258 | 270 |
| 2026-10-03 | 92 | 20 | 384 | 125 |
| 2026-10-02 | 128 | 37 | 1077 | 233 |
