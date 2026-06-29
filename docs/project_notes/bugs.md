# Bugs

Log incidents that took real time to diagnose or are likely to recur. Format below. Append-only; if a fix is reversed, write a new entry rather than editing the old one.

## Format

```
### YYYY-MM-DD — short title
**Symptom:** what was observed (error message, broken behavior, surprising state).
**Root cause:** why it happened.
**Fix:** what was changed (file paths, commit hash if available).
**Prevention:** what would catch this earlier next time (test, lint rule, doc, monitoring).
```

## Entries

### 2026-04-29 — Reel stuck in `error` status after retired-model failure
**Symptom:** A reel failed during summarization because of a retired Anthropic model ID. User re-sent the same URL expecting a retry; `POST /api/reel` returned `{"status": "queued"}` but the row never moved out of `error`, and the next digest run skipped it. The bug was only caught because the lingering error count surfaced in a Telegram status reply.
**Root cause:** `db.add_reel(url)` silently no-op'd on every conflict — including error rows. The "queued" response was technically correct (the request was received) but did not reflect what actually happened (nothing was queued; the row stayed stuck).
**Fix:** Made `POST /api/reel` idempotent with action-coded responses. Response now includes `status` ∈ `{queued, requeued, already_pending, already_done}`. Error rows auto-reset to pending on resubmit. Telegram replies render the action verbatim so "already_done" doesn't read as "queued."
**Prevention:** When an endpoint silently no-ops, the action code must surface in the response — never lie about side effects. Pattern A in `~/.claude/rules/telegram-handlers.md` was updated to render the action accurately.

### 2026-04-30 — Medium member-only articles 403'd despite valid `MEDIUM_SID`
**Symptom:** Member-only Medium articles returned 403 from the original HTTP+`MEDIUM_SID`+BeautifulSoup path even with a fresh sid cookie. Custom-domain Medium publications (`ai.plainenglish.io`, etc.) returned only the cookie banner, not the article body.
**Root cause:** Medium needs the full session (uid, xsrf, _cfuvid, …) — a single `sid` cookie is insufficient. Custom-domain publications scope cookies to their own host, so `.medium.com` cookies don't apply. A headed Playwright login script was blocked by Cloudflare even with the standard fingerprint overrides — the headless-shell binary has too many tells.
**Fix:** Routed every URL through headless Chromium via `src/generic_article_fetcher.py`. Medium URLs ride a logged-in session captured by exporting cookies from the user's real browser via Cookie-Editor and converting to Playwright's `storage_state` (`scripts/import_medium_cookies.py`). For non-`medium.com` URLs ending in a 12-char hex post ID, the fetcher additionally tries `https://medium.com/p/<id>` and uses whichever returns >1.5× more body content.
**Prevention:** When a target sits behind Cloudflare, a real-browser session sidesteps detection entirely (data transfer, not automation). The fingerprint problem is upstream of any flag toggling — accept it and move the auth boundary instead.
