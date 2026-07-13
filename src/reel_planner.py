"""Generate per-reel implementation plans from each digest result.

Two-phase planning so the cheap classification step doesn't ride on the
synthesis-tier model:

1. **Classify** (executor / Haiku): pick ``matched_project`` and ``topic_slug``
   from a small set of project hints — no active-context dump needed here.
2. **Synthesize** (advisor / Sonnet): given the classification + full
   active-context, write the plan markdown.

Per-reel failure isolation is preserved by ``generate_plan``'s outer
try/except — one bad classification or synthesis call cannot block the rest
of the digest.

This is the reel-side counterpart of the article-side analysis pass spec'd
in ``~/.claude/rules/telegram-handlers.md`` Pattern B. Articles arrive via
Telegram (Claude in-session); reels run unattended at 06:00, so the planning
pass has to run from Python with direct Anthropic SDK calls.
"""

import json
import logging
import re
from datetime import date
from pathlib import Path
from typing import Any

from .model_router import call_advisor, call_executor

logger = logging.getLogger(__name__)

_HOME = Path.home()
_ACTIVE_CONTEXT = _HOME / ".claude" / "memory" / "active-context.md"
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_TODOS_FILE = _PROJECT_ROOT / "data" / "todos.md"
_INBOX = _HOME / "projects" / "_inbox"

_TODOS_SECTION = "## Plans (from articles + reels)"
_TODOS_VERIFY_HEADER = "## How to verify a row is wired up"

# Only plan a reel when the user's attached note asks to build/act on it —
# a saved reel with no note (or a purely descriptive note) is "watch later",
# not a committed project. Added 2026-07-13 after auto-planning every reel
# filled todos.md with a graveyard of plans the user never meant to build.
# ponytail: keyword heuristic, conservative by design (favors false-negatives —
# a missed plan the user can re-request beats an unwanted todo). Swap for a
# tiny classifier call only if it misses genuine intent in practice.
_BUILD_INTENT = (
    "build", "implement", "plan this", "plan it", "let's do", "lets do",
    "let's build", "lets build", "we should build", "add to my list",
    "add to todos", "add this to", "wire this up", "wire it up",
    "set this up", "turn this into a plan", "make this happen", "action this",
)


def _has_build_intent(note: str | None) -> bool:
    if not note:
        return False
    low = note.lower()
    return any(phrase in low for phrase in _BUILD_INTENT)

# Stable project map for the classifier. Keep in sync with the matching
# heuristics documented in ~/.claude/rules/telegram-handlers.md Pattern B.
_PROJECT_HINTS = {
    "brain": "knowledge graph / second brain / wiki / Obsidian / PKM",
    "reels-digest-agent": "Telegram bot / reels / digest / personal automation",
    "varda": "reputation manager / scraping / reviews / Google Maps prospecting (paused)",
    "new": "anything that doesn't fit an existing project — becomes an _inbox/ stub",
}

_CLASSIFY_PROMPT = """\
Classify a short-form video (Instagram reel or carousel) into ONE of these projects:

{project_hints}

Reel:
- Title: {title}
- Takeaways:
{takeaways}

Summary excerpt:
{summary_excerpt}

Respond with ONLY a JSON object (no markdown, no prose, no fences):
- "matched_project": one of "brain", "reels-digest-agent", "varda", "new"
- "topic_slug": kebab-case slug for the filename, max 50 chars, lowercase letters / digits / hyphens only, derived from the reel topic
"""

_SYNTHESIZE_PROMPT = """\
You are a planning assistant for a solo builder / full-stack developer. They processed a short video (Instagram reel or carousel post) into a digest and you need to turn its content into a concrete implementation plan tailored to their existing projects.

Their active projects (from ~/.claude/memory/active-context.md):
<active_context>
{active_context}
</active_context>

The reel they just processed:
- URL: {url}
- Title: {title}
- Takeaways:
{takeaways}
- Detailed summary:
{summary}

This reel has already been classified for project: **{matched_project}**

Output ONLY the plan as markdown (no JSON wrapper, no fences), using exactly this structure:

# <topic line — your wording, not the reel's title>

**Source:** {url}
**Project:** <matched project name, or "new initiative" if matched_project is "new">
**Created:** {today}

## What you already have
<3-5 bullets on existing state, drawn from active_context. Be specific — reference actual files / scripts / deployed components.>

## What the reel proposes
<2-4 bullets distilling the reel's core idea>

## Gap (where this extends what you have)
<2-4 sentences on the delta>

## Plan
1. <concrete first step tailored to the existing stack — name files, commands, directories>
2. <step>
3. <step>
"""


def _slugify(text: str, max_len: int = 50) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:max_len].rstrip("-") or "untitled"


def _read_active_context() -> str:
    try:
        return _ACTIVE_CONTEXT.read_text()
    except FileNotFoundError:
        return "(active-context.md not found)"


def _resolve_plan_path(matched_project: str, topic_slug: str) -> Path:
    today_iso = date.today().isoformat()
    fname = f"{today_iso}-{topic_slug}.md"
    if matched_project and matched_project.lower() != "new":
        proj_dir = _HOME / "projects" / matched_project
        if proj_dir.is_dir():
            plans_dir = proj_dir / "docs" / "plans"
            plans_dir.mkdir(parents=True, exist_ok=True)
            return _avoid_collision(plans_dir / fname)
    inbox_dir = _INBOX / f"{today_iso}-{topic_slug}"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    return _avoid_collision(inbox_dir / "PLAN.md")


def _avoid_collision(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix, parent = path.stem, path.suffix, path.parent
    n = 2
    while True:
        candidate = parent / f"{stem}-{n}{suffix}"
        if not candidate.exists():
            return candidate
        n += 1


def _append_to_todos(plan_path: Path, topic: str) -> None:
    if not _TODOS_FILE.exists():
        return
    entry = f"- [ ] Plan: {topic} — `{plan_path}`"
    todos = _TODOS_FILE.read_text()
    if _TODOS_SECTION in todos:
        # Append under existing section, before the next ## or EOF.
        lines = todos.splitlines(keepends=True)
        out: list[str] = []
        in_section = False
        inserted = False
        for line in lines:
            if line.rstrip() == _TODOS_SECTION:
                in_section = True
                out.append(line)
                continue
            if in_section and line.startswith("## ") and not inserted:
                out.append(entry + "\n\n")
                inserted = True
                in_section = False
            out.append(line)
        if in_section and not inserted:
            out.append(entry + "\n")
        todos = "".join(out)
    else:
        # Insert section just above "## How to verify..." so recaps surface it.
        if _TODOS_VERIFY_HEADER in todos:
            todos = todos.replace(
                _TODOS_VERIFY_HEADER,
                f"{_TODOS_SECTION}\n\n{entry}\n\n{_TODOS_VERIFY_HEADER}",
                1,
            )
        else:
            todos = todos.rstrip() + f"\n\n{_TODOS_SECTION}\n\n{entry}\n"
    _TODOS_FILE.write_text(todos)


def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*\n", "", text)
    text = re.sub(r"\n```\s*$", "", text)
    return text


def _classify(title: str, takeaways: list[str], summary: str) -> dict[str, str]:
    """Phase 1 — Haiku classifies the reel into a project + slug."""
    hints = "\n".join(f"  - {k}: {v}" for k, v in _PROJECT_HINTS.items())
    takeaways_str = "\n".join(f"  - {t}" for t in takeaways) if takeaways else "  (none)"
    # Cap summary length for the cheap classifier — full body isn't needed
    summary_excerpt = (summary or "")[:600]
    prompt = _CLASSIFY_PROMPT.format(
        project_hints=hints,
        title=title,
        takeaways=takeaways_str,
        summary_excerpt=summary_excerpt,
    )
    text = call_executor(prompt, max_tokens=200, label="reel_planner.classify")
    return json.loads(_strip_fences(text))


def _synthesize(matched: str, url: str, title: str, takeaways: list[str], summary: str) -> str:
    """Phase 2 — Sonnet writes the full plan markdown given the classification."""
    takeaways_str = "\n".join(f"  - {t}" for t in takeaways) if takeaways else "  (none)"
    prompt = _SYNTHESIZE_PROMPT.format(
        active_context=_read_active_context(),
        url=url,
        title=title,
        takeaways=takeaways_str,
        summary=summary,
        matched_project=matched,
        today=date.today().isoformat(),
    )
    text = call_advisor(prompt, max_tokens=2000, label="reel_planner.synthesize")
    return _strip_fences(text)


def generate_plan(reel_result: dict[str, Any]) -> Path | None:
    """Generate a plan markdown file for one reel result. Returns the plan path on success, None on failure."""
    try:
        if not _has_build_intent(reel_result.get("note")):
            logger.info(
                "Reel %s: no build-intent in note, skipping plan.",
                reel_result.get("id"),
            )
            return None

        summary = reel_result.get("summary") or {}
        title = summary.get("title", "Untitled reel")
        takeaways = summary.get("takeaways", [])
        body = summary.get("summary") or summary.get("body") or ""
        url = reel_result.get("url", "")

        classification = _classify(title, takeaways, body)
        matched = str(classification.get("matched_project", "new"))
        slug = _slugify(str(classification.get("topic_slug") or title))

        plan_md = _synthesize(matched, url, title, takeaways, body)

        plan_path = _resolve_plan_path(matched, slug)
        plan_path.write_text(plan_md)
        _append_to_todos(plan_path, title)
        logger.info("Reel plan written to %s (matched: %s)", plan_path, matched)
        return plan_path
    except Exception as exc:
        logger.warning("Failed to generate reel plan for %s: %s", reel_result.get("url"), exc)
        return None
