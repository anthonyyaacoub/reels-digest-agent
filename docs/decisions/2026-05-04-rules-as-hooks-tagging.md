# Audit: which rules belong in hooks vs prose

**Date:** 2026-05-07 (planned 2026-05-04, written today)
**Plan:** `~/projects/reels-digest-agent/docs/plans/2026-05-04-agent-harness-hooks-eval-layer.md` step 1
**Reviewer question:** *if this rule failed silently, would the user notice?* If yes → `[hook]`. If no → `[prose]`.

A `[hook]` candidate is durable, deterministic, and the failure mode is concrete (a destructive command runs, a state file silently doesn't get updated). A `[prose]` rule is judgement-heavy or stylistic where the model's reading the rule is the right enforcement.

Not every `[hook]` candidate gets implemented. The first three hooks (per the plan's step 3) are picked from this list. The rest stay tagged so future-me knows what's reasonable to add when the pain is real.

---

## `~/.claude/rules/security.md`

### PRODUCT DATA tier
- "No logging scraped content or user data to stdout or files" — `[hook]` (PostToolUse on Write/Edit, grep for known PII patterns). Not in v1; flagged for future since the cost of a silent leak is high.
- "No committing test fixtures containing real email addresses or real reviews" — `[hook]` (PreToolUse on Bash matching `git add` / `git commit`, grep staged diff for email patterns). Not in v1.
- "No 'add auth later' on anything user-facing" — `[prose]`. Architectural intent, not a runtime signal.
- "Azure credentials must use managed identity or Key Vault — never hardcoded" — `[hook]` candidate (PostToolUse on Edit/Write, grep for connection-string-shaped strings). Not in v1.
- "Scraped review content is untrusted input — sanitize before storage or display" — `[prose]`. Design rule, no single point of enforcement.

### NEVER (absolute)
- "Expose API keys or secrets in code, commits, logs, or error messages" — `[hook]` (PostToolUse on Edit/Write, regex for `sk-`, `xoxp-`, hex>=32 chars). Not in v1; complement to credential rule above.
- "Commit `.env` files" — `[hook]` (PreToolUse on Bash matching `git add`, block `.env`). Not in v1.
- **"Run destructive commands without explicit confirmation: `rm -rf`, `DROP TABLE`, force-push to shared branches"** — **`[hook]` v1**. This is the cleanest deterministic-enforcement candidate in the whole rule set. Implemented as PreToolUse Bash danger-pattern grep.
- "Skip input validation at system boundaries" — `[prose]`. Code-design intent.

### CAREFUL
- All four "pause and think" rules — `[prose]` by design (the rule is "pause and think," which is judgement, not action).

### SAFE
- All four — `[prose]`. They're opt-in defaults, not constraints.

---

## `~/.claude/rules/git.md`

- "Commit messages: present tense, capital letter" — `[prose]`. Style.
- "Branch naming: feature/fix/" — `[prose]`. Style.
- **"Never commit unless explicitly asked"** — `[prose]`. The model has to read intent from the conversation; a hook on `git commit` would over-fire.
- **"Never force-push to main/master"** — `[hook]` v1. Already covered by the danger-pattern grep in security.md (`git push.*--force(?!-with-lease)` + branch detection). Folded into the same hook.

---

## `~/.claude/rules/jarvis-persona.md`

The voice/length/anticipatory-framing block is entirely `[prose]` — that's the spec for *how* the model speaks, and a hook can't enforce tone.

The memory protocol is more interesting:

- "On entry to any Telegram-channel turn: read `~/.claude/jarvis/state.md`" — `[hook]` candidate (UserPromptSubmit on Telegram-channel turns, inject state.md content), but the current Jarvis flow already reads it via the persona-rule, so the prose enforcement is good enough.
- **"On exit: update only what changed"** — `[hook]` v1 candidate but DOWNGRADED to "log changes, don't enforce." Implemented as PostToolUse Edit|Write *on `~/.claude/jarvis/state.md` itself* — when state.md does change, log the change so we can audit drift later. We do NOT block turns that fail to update state.md, because that's a judgement call (some turns shouldn't write).
- "Don't restructure the file. Don't auto-prune." — `[prose]`. Style/intent rule.

---

## `~/.claude/rules/telegram-handlers.md`

This file is a routing table — Patterns A–J map intents to backend actions. The prose is dense but most of it is "if the message looks like X, do Y" which is exactly what the model should be doing.

What's deterministic and worth surfacing as a `harness_events` row (not blocking):

- **Every Telegram outbound reply** — `[hook]` v1. PostToolUse on `mcp__plugin_telegram_telegram__reply` → POST a `telegram.reply` event to `/api/harness-event`. Single source of truth for what the bot actually said. Doesn't enforce anything; it observes.
- Pattern A (reel URL) → POST /api/reel → already logs via `src/server.py logger.info("POST /api/reel → ...")`. Already covered by the FastAPI-side backfill (plan step 5). No hook needed; the FastAPI handler writes the harness event.
- Pattern B (article URL) → /api/article → same as above. Backfilled FastAPI-side.
- Pattern B step 9 ("merge or create" decision) — `[hook]` v1 candidate via PostToolUse Write on `**/docs/plans/*.md` and `**/_inbox/**/PLAN.md`. We log when a plan file is written; the LLM-grader prompt (Parreño's "advanced section") is deferred. The user manually reviews if they suspect a fragmentation/merge violation.
- Pattern C (capture intent) → `data/todos.md` — `[prose]`. Judgement: did the model classify this as a capture vs a list lookup correctly? No good deterministic signal.
- Pattern D (list lookup) — `[prose]`. Read-only; nothing to enforce.
- Pattern E (status query) — `[prose]`. Read-only.
- Pattern F (GitHub queries) — `[prose]`. The mcp tool calls are themselves the action; logging the tool call would be redundant with Anthropic's own tool-call records.
- Pattern G (free-form chat) — `[prose]`. Catch-all.
- Pattern H (voice attachment) → /api/transcribe → already FastAPI-logged. Backfilled.
- Patterns I, J (date-spot capture / lookup) — same as C/D, prose.

"Don't run `src/telegram_bot.py`" — `[hook]` candidate (PreToolUse Bash grep for `python.*telegram_bot.py`). Low priority — this hasn't been an actual failure mode, the long-polling bot is just sitting there as documentation.

---

## v1 hooks summary

Three hooks, one for each top "silent-failure-with-real-cost" candidate:

1. **PreToolUse Bash → bash-danger-check.sh** — covers security.md "destructive commands" and git.md "never force-push to main/master". Exit 2 with `permissionDecision: ask` for matched patterns; exit 0 otherwise.
2. **PostToolUse Edit|Write on `~/.claude/jarvis/state.md` → jarvis-state-log.sh** — observe-only, log diffs. Doesn't enforce.
3. **PostToolUse on `mcp__plugin_telegram_telegram__reply` → telegram-reply-event.sh** — POST one event line to `/api/harness-event`. Doesn't enforce.

A simplified step-7 hook is added in v1 too:

4. **PostToolUse Write on plan-file paths → plan-write-event.sh** — log when a plan file is written. The LLM-grader eval prompt (Parreño's prompt-type hook) is deferred until the harness has produced enough rows to prove the manual review is the bottleneck.

## Re-audit cadence

The plan calls for re-running this audit quarterly. Next review: 2026-08-07. Trigger conditions for sooner: a silent rule violation that costs real time, or a new rule added to `~/.claude/rules/` that itself looks like a hook candidate.
