# Optimize the article fetch path: faster sync tier + lower-token agentic browser

**Sources:**
- https://www.instagram.com/reel/DX2orbYgPVI/ — Scrapling: drop-in HTML fetcher, claims 774× faster than BeautifulSoup with Cloudflare bypass and selector-free adaptive extraction.
- https://www.instagram.com/reel/DXzhKhvzKTZ/ — Vercel Agent Browser: snapshot `@ref` system instead of full screenshots, ~93% token reduction for agentic browser sessions.

**Project:** reels-digest-agent
**Created:** 2026-05-04 (merged 2026-05-06 — was two separate plans, Scrapling + Vercel Agent Browser)

## What you already have
- `src/generic_article_fetcher.py` — headless Chromium via Playwright is the primary fetch path; full browser launch per request, biggest latency contributor in `/api/article`.
- `src/article_fetcher.py` — legacy HTTP path, kept for `_extract_article` / `_HEADERS` / `_best_src` / `_best_srcset` helpers; `fetch_article` no longer called.
- `/api/article` synchronous, falls back to readability-lxml when `<article>` body < 1500 chars; Medium cookie injection + custom-domain detection baked in.
- Playwright is also implicit in the Telegram MCP browser sessions where screenshots flow to the model.
- No metrics today on tokens-per-fetch or p50/p95 latency for `/api/article`.

## What the candidates propose
The two are NOT substitutes — they target different code paths.

- **Scrapling** targets the synchronous FastAPI fetch path: a fast HTTP-level tier with Cloudflare bypass that runs *before* launching Chromium. Selector-free adaptive extraction also reduces maintenance on `_extract_article`. Drop-in `pip install`.
- **Vercel Agent Browser** targets the agentic/MCP path: instead of feeding screenshots to the model, it shares contextually relevant DOM snippets via `@ref`. The 93% claim is specifically about screenshot-heavy Playwright MCP usage, so the savings only land in interactive Claude Code sessions, not in `/api/article`.

## Gap (where this extends what you have)
Today every `/api/article` call pays a full Playwright browser-launch cost on the first attempt, and every MCP browser turn ships full screenshots to the model. Both are simple-but-expensive defaults. A two-tier synchronous fetcher (Scrapling fast tier → Playwright fallback) would cut median fetch time on most non-Medium URLs. Swapping the MCP browser layer for Vercel AB would reduce context-window growth in long Jarvis/planning sessions.

## Plan

### Phase 0 — Baseline (both candidates need this)
1. Add a fetch logger in `src/generic_article_fetcher.py`: log `url`, `host`, `body_chars`, `fetch_duration_ms`, and (for the planning path) `prompt_tokens`/`completion_tokens` from the Anthropic SDK response into `data/fetch_log.jsonl`.
2. Pick 10 representative URLs from `data/todos.md` plans — mix of Medium, custom-domain Medium, Substack, plain blogs, Cloudflare-protected. This is the benchmark suite.
3. Run the suite once on the current Playwright-only path; this is the comparison anchor.

### Phase 1 — Scrapling as a fast first-attempt tier (synchronous path)
4. `pip install scrapling` in the reels-digest-agent venv. Write `src/scrapling_fetcher.py` exposing the same `(title, content_md)` tuple shape as `generic_article_fetcher.py`. Try `Fetcher().get(url)` first; escalate to `StealthyFetcher` only when the response looks like a Cloudflare interstitial.
5. Run the same 10-URL suite through it; compare body length, fetch time, success rate. Medium URLs are exempt — they always go straight to Playwright (cookies can't be injected into Scrapling without extra work).
6. If ≥70% of non-Medium URLs return body ≥ 1500 chars in <2s, wire Scrapling as a first-attempt tier in `generic_article_fetcher.py` before Playwright launch. Keep Playwright as guaranteed fallback.
7. After a 48 h soak, grep FastAPI logs for `/api/article` p50/p95 response times; record before/after in `docs/project_notes/key_facts.md`. If Cloudflare bypass proves unreliable in practice, demote Scrapling to extraction-only (use as a third fallback after `<article>`-tag and before readability-lxml) and revert step 6.
8. Add `scrapling` to `requirements.txt`; record decision in `docs/project_notes/decisions.md`.

### Phase 2 — Vercel Agent Browser for the MCP path
9. Install Vercel Agent Browser CLI in a separate sandbox (`~/projects/_inbox/agent-browser-eval/`) — don't pollute the reels-digest-agent venv. Confirm whether it exposes an MCP server interface or only a CLI.
10. If MCP: configure it in `~/.claude/settings.json` alongside (not replacing) the Playwright MCP entry. Test with a live Jarvis session doing a web lookup the bot hasn't seen. Compare context-window growth vs the Playwright MCP baseline.
11. If token savings exceed 40% on the same task, replace the Playwright MCP entry with Vercel Agent Browser and update `~/.claude/rules/tools.md` to reflect the new browser layer. Otherwise record findings in `docs/project_notes/decisions.md` and close.

### Stop conditions
- If Phase 1 step 5 shows Scrapling fails >50% of the suite (Cloudflare bypass weaker than claimed, extraction quality poor), skip steps 6–8 entirely.
- If Phase 2 step 9 shows Vercel AB has no MCP interface, defer Phase 2 — the integration cost climbs significantly without one.
- The two phases are independent. If Phase 1 wins and Phase 2 loses, ship Phase 1 alone.
