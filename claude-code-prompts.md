# Claude Code Prompts — Reels Digest Implementation

Three prompts you can paste into Claude Code, one at a time. Run each in a **fresh Claude Code session** so context stays clean.

**Target paths:** everything lives under `~/projects/` in WSL. If that directory doesn't exist yet, create it: `mkdir -p ~/projects`.

**Order matters:** do Prompt 1 first — the plugins improve how Claude Code handles Prompts 2 and 3.

---

## Prompt 1 — Install the 3 Claude Code plugins

**Where to run:** anywhere. Plugins are installed to your user scope, so the working directory doesn't matter. I'd suggest `cd ~` to keep it clean.

**How to run:** open Claude Code (`claude` in the terminal), then paste the prompt below. Note that some steps use `/plugin` slash commands which you type directly — Claude Code itself won't run those for you. I've marked which parts are "paste into Claude" vs "type yourself."

---

### PASTE INTO CLAUDE CODE:

```
I want to install three Claude Code plugins/MCP servers that improve your reasoning, development workflow, and library documentation lookup. Walk me through each one, verify it installed correctly, and then update my global CLAUDE.md at ~/.claude/CLAUDE.md to document them and add invocation hints.

The three tools are:

1. **Sequential Thinking** — Anthropic's official MCP server for structured step-by-step reasoning. Install via:
   claude mcp add --scope user sequential-thinking -- npx -y @modelcontextprotocol/server-sequential-thinking

2. **Superpowers** (by obra) — a skills framework for TDD, debugging, brainstorming, and subagent-driven development. I will install this myself via the /plugin slash commands, because you (Claude Code) cannot run slash commands on my behalf. After I confirm it's installed, verify it by checking that /help shows new commands like /superpowers:brainstorm. The commands I'll run are:
   /plugin marketplace add obra/superpowers-marketplace
   /plugin install superpowers@superpowers-marketplace
   (then restart Claude Code)

3. **Context7** (by Upstash) — live, version-specific library docs injected into context. This prevents hallucinated API calls against fast-moving libraries like Next.js, React, Prisma, etc. There are two install options:
   (a) MCP-only: claude mcp add --scope user context7 -- npx -y @upstash/context7-mcp
   (b) Full plugin with skills/agents/commands from the official marketplace: /plugin install context7@claude-plugins-official
   I prefer option (b) because it includes auto-triggering skills and a /context7:docs command, which matches my existing Phase 3 plan for Agent Skills. I'll run the slash command myself.

Your tasks:
- Run the MCP install command for Sequential Thinking (step 1) and verify it appears in `claude mcp list`.
- Tell me the exact slash commands to type for Superpowers (step 2) and Context7 (step 3), and wait for me to confirm each is installed before moving on.
- After all three are verified, update ~/.claude/CLAUDE.md to:
  * Add a new section "## Installed Plugins & MCP Servers" listing all three with a one-line description each
  * Add to my model routing / tool preferences section: "For any code involving Next.js, React, Node, Python libraries, or fast-moving frameworks, proactively invoke Context7 before writing code."
  * Add: "For multi-step debugging, architecture decisions, or complex refactors, use the sequential-thinking tool to structure reasoning."
  * Add: "For any greenfield feature or non-trivial change, use Superpowers' brainstorm → plan → execute workflow unless I explicitly say 'quick change'."
- Show me a diff of the CLAUDE.md changes before saving.
- Do NOT add Context7 API keys — I'll use the free anonymous tier for now. If I later want higher rate limits, I'll add a key manually.

Before starting, read my existing ~/.claude/CLAUDE.md so you understand the structure and don't break the formatting I already have.
```

---

## Prompt 2 — Set up the AI Website Cloner template

**Where to run:** `cd ~/projects` before opening Claude Code, then run `claude`.

**Heads up on WSL:** The cloner uses `claude --chrome`, which launches Chrome DevTools MCP for visual reconnaissance. On WSL this can be finicky. Two options:
- **Option A (simplest):** install the cloner inside WSL but run the `claude --chrome` step from Windows-side Chrome via a bridged port. The prompt below asks Claude Code to detect your setup and tell you which approach to use.
- **Option B (most reliable):** install Chromium inside WSL directly. More setup but everything stays in Linux.

The prompt handles both — just run it and answer the diagnostic questions.

---

### PASTE INTO CLAUDE CODE (after `cd ~/projects`):

```
I want to install and configure the ai-website-cloner-template by JCodesMore so I can use it to reverse-engineer any website into a clean Next.js codebase. The repo is https://github.com/JCodesMore/ai-website-cloner-template.

My environment: Windows + WSL (Ubuntu), VS Code with the Claude Code extension. Node/npm are installed inside WSL. This is important because the template uses `claude --chrome` which needs Chrome accessible to the WSL process.

Do the following, in order, and STOP after each major step to confirm before continuing:

Step 1 — Clone and install
- cd to ~/projects (create it if it doesn't exist)
- git clone https://github.com/JCodesMore/ai-website-cloner-template.git
- cd ai-website-cloner-template
- Check the .nvmrc to confirm the required Node version. If my current Node doesn't match, tell me (don't try to change it yourself — I'll handle nvm).
- Run npm install
- Run `npm run check` (lint + typecheck + build) to confirm the base template is healthy. Report any errors.

Step 2 — WSL + Chrome compatibility check
Before I try `claude --chrome`, diagnose my setup:
- Check if Chromium or Chrome is installed inside WSL: `which chromium-browser google-chrome chrome 2>/dev/null`
- Check if I'm running WSL2 (`wsl.exe -l -v` from inside WSL won't work; instead check `uname -r` for "WSL2" or "microsoft-standard")
- Check if DISPLAY or WAYLAND_DISPLAY is set
- Based on what you find, tell me which of these I need to do:
  (a) Install Chromium in WSL: `sudo apt update && sudo apt install -y chromium-browser`
  (b) Use the Windows-side Chrome via WSLg (WSL2 on Windows 11 usually just works)
  (c) Something else if my setup is unusual
- Do NOT run sudo commands yourself. Just tell me what to run and I'll do it.

Step 3 — Read the template's instructions
- Read AGENTS.md and CLAUDE.md in the cloned repo so you know exactly how the /clone-website skill expects to be invoked
- Summarize for me: (1) what the /clone-website command does end-to-end, (2) what files it creates where, and (3) what I should expect the first run to output. Keep it to ~10 lines.

Step 4 — Document the setup for me
- Create a file `SETUP_NOTES.md` at the repo root (this is my own notes, not tracked by the template) containing:
  * The exact steps I ran
  * The WSL-specific decisions we made in Step 2
  * A "first run" checklist: what command to run, what URL to test with (suggest something simple like https://example.com for the first run, not Apple's homepage)
  * Known gotchas: disk space for downloaded assets, how long a clone typically takes, that I should never run this against sites I don't have permission to clone
- Add SETUP_NOTES.md to .gitignore so I don't accidentally commit it.

DO NOT actually run `/clone-website` yet. I want to verify everything is set up cleanly first and then run my first clone manually so I can watch what happens.

Important constraints:
- Do not modify the template's own files (AGENTS.md, CLAUDE.md, package.json, etc.) — those are upstream and I want to be able to pull updates.
- If anything fails, stop and tell me. Don't try to "fix" it with creative workarounds.
```

---

## Prompt 3 — Obsidian Second Brain Workflow

**Before you run this prompt, you need to manually do two things Claude Code can't do for you:**

1. **Install Obsidian** — download from https://obsidian.md and install it on Windows (not WSL; Obsidian is a GUI app). Create a new vault called `brain` somewhere accessible from both Windows and WSL — I'd put it at `~/projects/brain` inside WSL, which maps to `\\wsl.localhost\Ubuntu\home\<you>\projects\brain` on Windows, and then open that folder as a vault in Obsidian.

2. **Export your Claude data** — go to https://claude.ai/settings/data-privacy-controls and click "Export data." You'll get an email with a ZIP file within a few minutes to a few hours. Download it, unzip it, and move the entire unzipped folder into `~/projects/brain/` inside WSL.

Once both are done, `cd ~/projects/brain` and run `claude`, then paste the prompt.

---

### PASTE INTO CLAUDE CODE (after the two manual steps above):

```
I'm building a personal knowledge graph in Obsidian from my exported Claude conversation history. This vault lives at ~/projects/brain and I've already dropped my unzipped Claude data export into it. The goal is a navigable "second brain" where main topics have landing pages (Maps of Content / MOCs) that link out to the individual conversations, and conversations are interconnected via smart tags.

Before you do anything, STOP and read these three files completely:
1. ~/.claude/CLAUDE.md — my global preferences, especially the part about data routing and security tiers
2. The top-level structure of ~/projects/brain (ls -la and one level deep) — I want to know what the Claude export looks like
3. One or two sample conversation files from the export so you understand the format (JSON? Markdown? How are they named?)

Then report back to me with:
- The export format and structure
- Roughly how many conversations are in there
- A rough estimate of how many tokens it would take to read every conversation once (ballpark — I just want to know if we're talking about 10k or 10M)
- Any obvious privacy concerns (API keys, credentials, personal info I should redact before tagging)

STOP THERE and wait for my go-ahead before doing anything else.

Once I approve, your plan should be:

Phase A — Prep
- Create a standard Obsidian-friendly folder structure in the vault:
  /conversations — one markdown file per Claude chat, named with date + slug
  /MOCs — Maps of Content landing pages (one per major topic)
  /tags — optional index pages per tag
  /.obsidian — leave alone if it exists
- Convert the raw export into individual markdown files in /conversations, with YAML frontmatter including: date, title, tags (empty for now), source (Claude), original_id
- Do NOT delete or modify the original export folder — move it to /raw-export/ inside the vault as a backup

Phase B — Tagging (this is where the real work is)
- Spawn subagents to process conversations in batches (groups of ~20 at a time to stay within context windows)
- Each subagent reads its batch and assigns semantic tags based on topic. Use my actual domains as anchor topics: software development, solo founder / building, MBA coursework, Telegram bot infrastructure, Claude Code, AI tooling, automation, personal productivity. Create new tags only when something genuinely doesn't fit.
- Tags go into the YAML frontmatter as a list. Use lowercase-with-hyphens.
- Subagents also extract 1-3 "key connections" per conversation — i.e. "this conversation builds on X" or "this is a prerequisite for Y" — as Obsidian wiki-links [[like this]] at the bottom of each file.

Phase C — MOCs
- After tagging, identify the top 8-12 recurring topics (by tag frequency)
- For each, create a MOC file in /MOCs with:
  * A short intro paragraph of what the topic covers in MY life, not generic
  * A list of linked conversations grouped by sub-theme
  * A "related MOCs" section linking to other MOCs
- The MOCs should be the "big nodes" in the Obsidian graph view

Phase D — Index
- Create a single Home.md at the vault root that links to all MOCs and acts as the entry point

Constraints:
- DO NOT call the Claude API to summarize conversations unless I explicitly approve it — your own context window is enough for tagging, and I don't want a surprise API bill. If you think a conversation is too long to read in full, just skim the first and last 1000 chars and tag from that.
- Use sequential-thinking for Phase B planning since it's the most complex step.
- Checkpoint after each phase. Show me what you did and wait for approval before the next phase. This is too much work to redo if we get the structure wrong.
- If you find credentials, API keys, or anything that looks sensitive in a conversation, REDACT it in the markdown file (replace with [REDACTED]) and flag it in a /SENSITIVE_FOUND.md file for me to review.

Start by reading the three files I listed and reporting back.
```

---

## After all three are done

Open Obsidian, switch to graph view, and you'll see the interconnected network. For the cloner, try your first run against `https://example.com` to verify the pipeline works before pointing it at anything complex.

If any prompt produces garbage output or Claude Code gets stuck, stop it (Ctrl+C), tell me what happened, and I'll revise the prompt. These are a starting point, not gospel — the first run of any of them may reveal something I didn't anticipate about your exact setup.
