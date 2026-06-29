# Dual-model Claude Code setup: cheap executor + smart advisor for long overnight builds

**Source:** https://www.instagram.com/reel/DXW5uceDieQ/?igsh=bHJ1M2ZuMTE1bWRt
**Project:** reels-digest-agent
**Created:** 2026-05-04

## What you already have
- `src/reel_planner.py` uses `claude-sonnet-4-6` for every reel planning pass — a single-model, single-call pattern with no cost tiering
- `src/daily_job.py` runs `_process_reel` per reel and invokes `reel_planner.py` after `publish_digest`; failures are caught per-reel so one bad plan can't block the digest
- `src/transcriber.py` holds a shared Whisper singleton already illustrating a "warm shared resource" pattern reusable for a shared Opus advisor client
- `~/.claude/rules/context-hygiene.md` and the 70% context soft ceiling show an existing cost-awareness discipline that aligns with tiered model usage
- The Jarvis / Telegram MCP pipeline involves long-running Claude Code sessions (tmux, Task Scheduler) where overnight build costs would be most impactful

## What the reel proposes
- Route bulk/mechanical work (file edits, command execution, boilerplate generation) to a fast cheap model (Haiku) as the default executor
- Reserve a smarter model (Opus) as a strategic advisor called only at decision points — architecture choices, ambiguous requirements, plan validation
- The two models communicate via a shared scratchpad or structured prompt handoff rather than running fully independently
- Claimed outcome: 60–70% cost reduction on complex multi-step builds with no meaningful quality drop

## Gap (where this extends what you have)
Every current LLM call in the stack — `reel_planner.py`, `src/server.py` article handler, and any ad-hoc Claude Code session — uses a single model for the entire task regardless of whether the step is mechanical or strategic. There is no routing layer that classifies subtask complexity before dispatching. Implementing even a lightweight two-tier pattern in `reel_planner.py` (Haiku for extraction/formatting, Sonnet/Opus only for gap analysis and plan synthesis) would cut per-digest API spend and provide a tested template to carry into longer overnight Claude Code sessions via a wrapper script or CLAUDE.md-level instruction.

## Plan
1. **Audit current model spend:** add a cost-logging hook in `src/reel_planner.py` and `src/generic_article_fetcher.py` — log `input_tokens`, `output_tokens`, and model name to `data/cost_log.jsonl` after each Anthropic SDK call, mirroring the pattern already used in `~/projects/brain/graphify-out/cost.json`.
2. **Split `reel_planner.py` into two phases:** Phase 1 (Haiku) — extract structured fields (title, takeaways, action items, project match) from the transcript. Phase 2 (Sonnet or Opus) — receive Phase 1 output as context and write only the `## Gap` and `## Plan` sections, which require cross-referencing `active-context.md` and existing plans.
3. **Add a shared advisor client in `src/transcriber.py` style:** instantiate a single `anthropic.Anthropic()` client at module level in a new `src/model_router.py` with two thin wrappers — `call_executor(prompt)` defaulting to `claude-haiku-4-5` and `call_advisor(prompt)` defaulting to `claude-opus-4-5` — so the model names are configured once and swappable via `.env` vars `EXECUTOR_MODEL` / `ADVISOR_MODEL`.
4. **Wire the router into `daily_job.py`:** replace the direct `reel_planner` import call with `model_router.call_executor` + `model_router.call_advisor` in sequence; confirm per-reel failure isolation still holds by running a single-reel smoke test (`python -m src.daily_job --reel-only --limit 1`).
5. **Document the pattern in `~/.claude/CLAUDE.md`:** add a short "Model tiering" note so future Claude Code sessions default to spawning Haiku for file-editing sub-tasks and escalating to the advisor model only when asked to make an architectural decision — this is the overnight-build payoff the reel describes.