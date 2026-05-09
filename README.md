# The Daily AI Brief

An automated newsletter that curates 12 AI articles every morning, generates executive summaries and contrarian analysis using Claude, and delivers the finished brief to subscribers via email.

I built this because I was spending 45 minutes every morning scanning the same 40 sources for AI news. The brief needed to exist. Nobody was going to build it for me. So I wrote it.

## What It Does

Every morning at 5:00 AM CT, a GitHub Actions workflow:

1. **Searches** Tavily across 4 sections, 3 queries per section, pulling from 40+ tier-1 sources
2. **Filters** results through source tiering, domain throttling, deduplication, and blocklists
3. **Generates** a factual summary and contrarian take for each article using Claude
4. **Renders** the results into a formatted HTML email
5. **Sends** to all subscribers via Resend, pulling the list from a managed audience

Total runtime: ~3 minutes. Total cost: under $0.50/day.

## Architecture

```
Tavily (web search)
  ├── 12 queries across 4 sections
  ├── 20 results per query, 7-day window
  └── Filtered to 3 articles per section
        │
        ▼
Claude (content generation)
  ├── Factual summary: 2-3 sentences, no opinions
  └── Contrarian take: 3-5 sentences, first-person analysis
        │
        ▼
Resend (email delivery)
  ├── Subscriber list from Resend Audiences API
  ├── HTML email with inline CSS
  └── Failure alerts on error
```

## Design Decisions Worth Explaining

**Source tiering, not source filtering.** The system does not restrict search to a fixed list. It searches broadly, then ranks. Tier-1 sources (Reuters, Bloomberg, WSJ, TechCrunch, etc.) get priority. Non-tier-1 sources fill remaining slots. This matters because niche trade publications sometimes break stories before the majors pick them up.

**Consultant throttling.** McKinsey, BCG, Gartner, Forrester, and similar domains are capped at one article per run. Without the cap, the brief drifts toward analyst reports and away from actual news. The brief should tell you what happened, not what a consultant thinks about what happened.

**48-hour deduplication.** Every article URL is cached with a timestamp. The cache self-prunes after 48 hours. This prevents the same story from appearing in consecutive briefs when it dominates a news cycle for multiple days.

**Domain blocklist.** Reddit, Medium, Substack, and social platforms are excluded entirely. The signal-to-noise ratio on aggregation platforms is too low for automated curation.

**Contrarian takes, not summaries.** Any AI tool can summarize an article. The contrarian take is the reason this brief exists. It forces the model to find what the mainstream narrative is missing. Some mornings the take is better than the article it responds to.

**Resend Audiences for subscriber management.** Subscribers are managed through Resend's Audiences API, not a hardcoded list. The system pulls the current subscriber list at send time. If the audience is empty or the API fails, it falls back to a default recipient list so the brief still sends.

## Sections

| # | Section | What It Covers |
|---|---------|----------------|
| 1 | AI-Driven Customer Experience Wins | Brands deploying AI that customers actually interact with |
| 2 | LLM & Foundation Model Advancements | New model capabilities, commercial AI applications |
| 3 | AI Efficiency & Productivity | Workforce automation, enterprise productivity gains |
| 4 | AI & The Economy | Jobs, markets, GDP impact, macro trends |

## Setup

### Prerequisites

- Python 3.12+
- [Tavily API key](https://tavily.com) for web search
- [Anthropic API key](https://console.anthropic.com) for Claude
- [Resend API key](https://resend.com) for email delivery
- A verified sending domain in Resend

### Local

```bash
pip install -r automation/requirements.txt
cp .env.example .env
# Fill in your API keys in .env
source .env && python automation/daily_brief.py
```

### GitHub Actions (Automated)

1. Fork this repo
2. Add your API keys as repository secrets: `TAVILY_API_KEY`, `ANTHROPIC_API_KEY`, `RESEND_API_KEY`
3. Optionally add `RESEND_AUDIENCE_ID` to send to a managed subscriber list
4. The workflow runs daily at 10:00 UTC. Adjust the cron in `.github/workflows/daily-brief.yml` for your timezone.
5. Trigger a test run from the Actions tab using "Run workflow"

## Cost

Running daily with Claude Sonnet and Tavily advanced search:

| Service | Daily Cost | Monthly Cost |
|---------|-----------|--------------|
| Tavily (12 advanced searches) | ~$0.12 | ~$3.60 |
| Claude (12 article summaries) | ~$0.08 | ~$2.40 |
| Resend (email delivery) | Free tier | Free tier |
| GitHub Actions | Free tier | Free tier |
| **Total** | **~$0.20** | **~$6.00** |

## Customization

**Change the sections.** Edit the `SECTIONS` list in `daily_brief.py`. Each section has a title and a list of search queries. Three queries per section gives good coverage without redundancy.

**Change the sources.** Edit `TIER_1_SOURCES` to prioritize different publications. Edit `BLOCKED_DOMAINS` to exclude sources. Edit `THROTTLED_DOMAINS` to cap domains that would otherwise dominate.

**Change the voice.** Edit `SYSTEM_PROMPT` to adjust the tone, perspective, and format of generated content. The current prompt produces C-suite-level analysis. Adjust for your audience.

**Change the schedule.** Edit the cron expression in `.github/workflows/daily-brief.yml`.

## License

MIT
