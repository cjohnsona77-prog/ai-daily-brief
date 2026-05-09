"""
Daily AI Brief
Searches Tavily for 12 articles across 4 sections, generates summaries and
contrarian takes via Claude, formats as HTML email, and sends via Resend.
"""

import os
import json
import re
import time
from html import escape as he
from datetime import datetime, timedelta, timezone

import resend
import anthropic
from tavily import TavilyClient

# ── Configuration ─────────────────────────────────────────────────────────────

TAVILY_API_KEY     = os.environ["TAVILY_API_KEY"]
ANTHROPIC_API_KEY  = os.environ["ANTHROPIC_API_KEY"]
RESEND_API_KEY     = os.environ["RESEND_API_KEY"]
RESEND_AUDIENCE_ID = os.environ.get("RESEND_AUDIENCE_ID", "")

FROM_EMAIL  = os.environ.get("FROM_EMAIL", "AI Brief <brief@yourdomain.com>")
ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "")
FALLBACK_RECIPIENTS = [e.strip() for e in os.environ.get("FALLBACK_RECIPIENTS", "").split(",") if e.strip()]


def get_recipients() -> list[str]:
    """Pull subscriber list from Resend Audience. Fall back to env var list."""
    if not RESEND_AUDIENCE_ID:
        return FALLBACK_RECIPIENTS
    try:
        resend.api_key = RESEND_API_KEY
        contacts = resend.Contacts.list({"audience_id": RESEND_AUDIENCE_ID})
        emails = [c["email"] for c in contacts.data if not c.get("unsubscribed", False)]
        print(f"Loaded {len(emails)} subscriber(s) from Resend Audience")
        return emails if emails else FALLBACK_RECIPIENTS
    except Exception as e:
        print(f"Failed to load audience ({e}), falling back to default recipients")
        return FALLBACK_RECIPIENTS


# ── Sections ──────────────────────────────────────────────────────────────────
# Each section gets 3 articles. 4 sections = 12 articles per brief.
# Multiple queries per section increase recall across different angles.

SECTIONS = [
    {
        "id": 1,
        "title": "AI-Driven Customer Experience Wins",
        "icon": "🏆",
        "queries": [
            "brand AI chatbot agent customer experience deployed live",
            "retail bank airline hotel telecom AI customer service live deployment",
            "company AI agentic shopping assistant personalization launched",
        ],
    },
    {
        "id": 2,
        "title": "LLM & Foundation Model Advancements",
        "icon": "🧠",
        "queries": [
            "Claude Anthropic GPT OpenAI Gemini Google AI model commerce transactions",
            "AI chatbot purchase booking financial services conversational commerce",
            "Meta AI Grok xAI Mistral retail transactions real world tasks",
        ],
    },
    {
        "id": 3,
        "title": "AI Efficiency & Productivity",
        "icon": "⚡",
        "queries": [
            "AI workforce automation productivity knowledge workers",
            "AI agents tools replacing manual workflows enterprise efficiency",
            "artificial intelligence individual productivity gains how companies doing more with less",
        ],
    },
    {
        "id": 4,
        "title": "AI & The Economy",
        "icon": "📊",
        "queries": [
            "AI jobs employment displacement creation workforce",
            "AI stock market earnings corporate impact macroeconomic",
            "artificial intelligence unemployment economy GDP impact",
        ],
    },
]

# ── Source Tiering ────────────────────────────────────────────────────────────
# Tier 1 sources get priority placement. Fallback sources fill remaining slots.
# This keeps the brief credible without losing coverage on niche topics.

TIER_1_SOURCES = [
    "reuters.com", "bloomberg.com", "wsj.com", "forbes.com", "fortune.com",
    "techcrunch.com", "wired.com", "theverge.com", "cnbc.com",
    "businessinsider.com", "retaildive.com", "modernretail.co",
    "grocerydive.com", "supplychaindive.com", "cxdive.com", "venturebeat.com",
    "arstechnica.com", "technologyreview.com", "zdnet.com", "cio.com",
    "infoworld.com", "computerworld.com", "artificialintelligence-news.com",
    "aimagazine.com", "theregister.com", "engadget.com", "fastcompany.com",
    "inc.com", "hbr.org", "mckinsey.com", "gartner.com",
    "pymnts.com", "digitalcommerce360.com", "chainstoreage.com",
    "supermarketnews.com", "progressivegrocer.com", "nrf.com",
    "ft.com", "economist.com", "apnews.com", "axios.com",
]

BLOCKED_DOMAINS = [
    "reddit.com", "quora.com", "pinterest.com", "facebook.com",
    "twitter.com", "x.com", "tiktok.com", "instagram.com",
    "medium.com", "substack.com", "wordpress.com", "blogspot.com",
]

# Consulting/analyst domains: max 1 article per run to prevent brief
# from reading like a Gartner reprint.
THROTTLED_DOMAINS = [
    "mckinsey.com", "bcg.com", "bain.com", "deloitte.com",
    "accenture.com", "gartner.com", "forrester.com", "idc.com",
]

SEEN_URLS_FILE = "automation/seen_urls.json"


# ── Deduplication ─────────────────────────────────────────────────────────────
# Track URLs for 48 hours to prevent repeats across consecutive runs.

def load_seen_urls() -> dict:
    if os.path.exists(SEEN_URLS_FILE):
        with open(SEEN_URLS_FILE) as f:
            return json.load(f)
    return {}


def save_seen_urls(seen: dict):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    pruned = {url: ts for url, ts in seen.items() if ts > cutoff}
    with open(SEEN_URLS_FILE, "w") as f:
        json.dump(pruned, f, indent=2)


def get_domain(url: str) -> str:
    try:
        from urllib.parse import urlparse
        return urlparse(url).netloc.lower().lstrip("www.")
    except Exception:
        return ""


def is_tier1(url: str) -> bool:
    domain = get_domain(url)
    return any(domain == source or domain.endswith("." + source)
               for source in TIER_1_SOURCES)


def is_throttled(url: str) -> bool:
    domain = get_domain(url)
    return any(domain == t or domain.endswith("." + t) for t in THROTTLED_DOMAINS)


def is_blocked(url: str) -> bool:
    domain = get_domain(url)
    return any(domain == b or domain.endswith("." + b) for b in BLOCKED_DOMAINS)


# ── Article Search ────────────────────────────────────────────────────────────

def search_section(tavily: TavilyClient, section: dict, seen_urls: dict,
                   throttled_used: set) -> list[dict]:
    tier1_candidates: list[dict] = []
    fallback_candidates: list[dict] = []

    for query in section["queries"]:
        try:
            print(f"  Searching: {query}")
            from datetime import date
            current_month_year = date.today().strftime("%B %Y")
            dated_query = f"{query} {current_month_year}"
            results = tavily.search(
                query=dated_query,
                search_depth="advanced",
                max_results=20,
                include_raw_content=False,
                days=7,
            )
            raw_count = len(results.get("results", []))
            print(f"  -> {raw_count} raw results returned")

            for r in results.get("results", []):
                url = r.get("url", "")
                already_seen = (
                    url in seen_urls
                    or url in [c["url"] for c in tier1_candidates]
                    or url in [c["url"] for c in fallback_candidates]
                )
                if already_seen or not r.get("content") or is_blocked(url):
                    continue

                domain = get_domain(url)
                if is_throttled(url):
                    if domain in throttled_used:
                        continue
                    throttled_used.add(domain)

                article = {
                    "url": url,
                    "title": r.get("title", "Untitled"),
                    "source": r.get("source", url.split("/")[2]),
                    "content": r.get("content", ""),
                }
                if is_tier1(url):
                    tier1_candidates.append(article)
                else:
                    fallback_candidates.append(article)

            time.sleep(1)

        except Exception as e:
            print(f"  ERROR on query '{query}': {e}")

    print(f"  Section '{section['title']}': {len(tier1_candidates)} tier-1, {len(fallback_candidates)} fallback")

    combined = tier1_candidates[:3]
    if len(combined) < 3:
        needed = 3 - len(combined)
        combined += fallback_candidates[:needed]
        if fallback_candidates[:needed]:
            print(f"  -> Used {needed} fallback source(s) to reach 3 articles")

    return combined


# ── Content Generation ────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are writing a daily AI newsletter for a C-suite audience.
The readers are executives who need to stay current on AI without wading through
vendor hype. The brief is designed to be read in 5 minutes.

Write two blocks for each article:

1. FACTUAL SUMMARY (2-3 sentences max): What happened, who was involved, one key
   number or outcome. Objective, no opinions. Be specific: numbers, company names,
   outcomes.

2. CONTRARIAN TAKE (3-5 sentences): Pick the single strongest angle: what the
   mainstream narrative is missing, where the risk actually lives, what you have
   seen fail in practice. Write as a peer talking to peers.

Voice rules for the Contrarian Take:
- Direct, credible, no arrogance.
- No em dashes. Vary sentence length naturally.
- Lead with the risk or gap, not agreement.
- Specific over vague. Name the problem, name the consequence.
- Short sentences. Active voice.

Return valid JSON only:
{
  "summary": "...",
  "contrarian": "..."
}"""


def generate_article_content(client: anthropic.Anthropic, article: dict) -> dict:
    prompt = f"""Article title: {article['title']}
Source: {article['source']}
URL: {article['url']}

Article content:
{article['content'][:3000]}

Write the FACTUAL SUMMARY and CONTRARIAN TAKE as specified."""

    try:
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=512,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        return json.loads(raw)
    except Exception as e:
        print(f"Claude generation error for '{article['title']}': {e}")
        return {
            "summary": "Content generation failed for this article.",
            "contrarian": "",
        }


# ── HTML Generation ───────────────────────────────────────────────────────────

def render_article_card(num: int, article: dict, generated: dict) -> str:
    num_str = str(num).zfill(2)
    contrarian_block = ""
    if generated.get("contrarian"):
        contrarian_block = f"""
      <div style="border-left:3px solid #c53030;padding:12px 16px;margin-top:12px;background:#fff8f8;">
        <div style="font-size:10px;font-weight:700;color:#c53030;letter-spacing:0.12em;
                    text-transform:uppercase;margin-bottom:6px;">Contrarian Take</div>
        <div style="font-size:14px;color:#333333;line-height:1.65;">
          {generated['contrarian']}
        </div>
      </div>"""

    return f"""
    <div style="background:#ffffff;border:1px solid #e8e8e8;border-radius:8px;
                padding:22px 24px;margin-bottom:14px;">
      <div style="font-size:10px;font-weight:700;color:#999999;letter-spacing:0.12em;
                  text-transform:uppercase;margin-bottom:8px;">
        {num_str} &nbsp;&middot;&nbsp;
        <a href="{he(article['url'])}" style="color:#999999;text-decoration:none;">{he(article['source'])}</a>
      </div>
      <a href="{he(article['url'])}" style="font-size:16px;font-weight:700;color:#1a1a2e;
                text-decoration:none;line-height:1.4;display:block;margin-bottom:14px;">
        {he(article['title'])}
      </a>
      <div style="border-left:3px solid #2d6a4f;padding:12px 16px;background:#f6fbf8;">
        <div style="font-size:10px;font-weight:700;color:#2d6a4f;letter-spacing:0.12em;
                    text-transform:uppercase;margin-bottom:6px;">Factual Summary</div>
        <div style="font-size:14px;color:#333333;line-height:1.65;">
          {generated['summary']}
        </div>
      </div>
      {contrarian_block}
    </div>"""


def render_section(section: dict, articles: list[dict], start_num: int,
                   generated_list: list[dict], error: str = "") -> str:
    if error:
        body = f"""<div style="background:#fff5f5;border:1px solid #feb2b2;border-radius:8px;
                               padding:20px;color:#c53030;">
                     <strong>Section failed to generate.</strong> {error}
                   </div>"""
    elif not articles:
        body = """<div style="background:#fffbeb;border:1px solid #f6e05e;border-radius:8px;
                              padding:20px;color:#744210;">
                    No qualifying articles found for this section today.
                  </div>"""
    else:
        cards = ""
        for i, (article, generated) in enumerate(zip(articles, generated_list)):
            cards += render_article_card(start_num + i, article, generated)
        body = cards

    anchor_id = f"section{section['id']}"
    return f"""
    <div style="margin-bottom:40px;">
      <a name="{anchor_id}" id="{anchor_id}"></a>
      <div style="background:#1a1a2e;color:#ffffff;padding:16px 24px;border-radius:8px;
                  margin-bottom:16px;">
        <div style="font-size:10px;font-weight:600;letter-spacing:0.14em;text-transform:uppercase;
                    opacity:0.5;margin-bottom:4px;">Section {section['id']}</div>
        <div style="font-size:18px;font-weight:800;letter-spacing:-0.01em;">{section['title']}</div>
      </div>
      {body}
    </div>"""


def build_nav_links() -> str:
    parts = []
    for s in SECTIONS:
        anchor = "section" + str(s["id"])
        parts.append(
            '<a href="#' + anchor + '" style="color:#ffffff;opacity:0.65;text-decoration:none;'
            'font-size:12px;font-weight:600;letter-spacing:0.04em;">' + s["title"] + '</a>'
        )
    links = ' &nbsp;&middot;&nbsp; '.join(parts)
    return (
        '<div style="text-align:center;padding:14px 0 0;font-size:12px;line-height:1.9;">'
        + links + '</div>'
    )


def build_email_html(sections_html: str, date_str: str) -> str:
    nav = build_nav_links()
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>The Daily AI Brief &mdash; {date_str}</title>
</head>
<body style="margin:0;padding:0;background:#f4f4f7;font-family:-apple-system,BlinkMacSystemFont,
             'Segoe UI',Roboto,sans-serif;">
  <div style="max-width:660px;margin:0 auto;padding:24px 16px;">

    <!-- Header -->
    <div style="background:#1a1a2e;color:#ffffff;border-radius:10px;
                padding:36px 40px;margin-bottom:24px;text-align:center;">
      <div style="font-size:11px;font-weight:600;letter-spacing:0.14em;text-transform:uppercase;
                  opacity:0.5;margin-bottom:12px;">The Daily AI Brief</div>
      <div style="font-size:30px;font-weight:800;letter-spacing:-0.02em;margin-bottom:14px;">
        The Daily AI Brief
      </div>
      <div style="font-size:13px;opacity:0.65;line-height:1.7;">
        <div>12 Curated Articles &nbsp;&middot;&nbsp; Factual Summary + Contrarian Perspective</div>
        <div>{date_str}</div>
      </div>
      {nav}
    </div>

    <!-- Sections -->
    {sections_html}

    <!-- Footer -->
    <div style="text-align:center;padding:24px 0 16px;border-top:1px solid #e0e0e0;
                margin-top:8px;">
      <div style="font-size:13px;font-weight:700;color:#1a1a2e;margin-bottom:6px;">
        The Daily AI Brief
      </div>
      <div style="font-size:12px;color:#999999;line-height:1.8;">
        AI-curated. Human-edited.
      </div>
    </div>

  </div>
</body>
</html>"""


# ── Email Send ────────────────────────────────────────────────────────────────

def send_email(html_body: str, date_str: str):
    resend.api_key = RESEND_API_KEY
    subject    = f"The Daily AI Brief | {date_str}"
    recipients = get_recipients()
    print(f"Sending to {len(recipients)} recipient(s)")
    response = resend.Emails.send({
        "from":    FROM_EMAIL,
        "to":      recipients,
        "subject": subject,
        "html":    html_body,
    })
    print(f"Sent successfully. ID: {response['id']}")


def send_failure_alert(error: str):
    if not ALERT_EMAIL:
        return
    resend.api_key = RESEND_API_KEY
    resend.Emails.send({
        "from": FROM_EMAIL,
        "to": [ALERT_EMAIL],
        "subject": "ALERT: Daily AI Brief failed to generate",
        "html": f"<p>The Daily AI Brief failed.</p><pre>{error}</pre>",
    })


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    date_str = datetime.now(timezone.utc).strftime("%B %d, %Y")
    tavily  = TavilyClient(api_key=TAVILY_API_KEY)
    claude  = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    seen_urls = load_seen_urls()
    sections_html  = ""
    article_num    = 1
    newly_seen     = {}
    throttled_used: set = set()

    for section in SECTIONS:
        try:
            articles = search_section(tavily, section, seen_urls, throttled_used)
            generated_list = []
            for article in articles:
                generated = generate_article_content(claude, article)
                generated_list.append(generated)
                newly_seen[article["url"]] = datetime.now(timezone.utc).isoformat()

            sections_html += render_section(section, articles, article_num, generated_list)
            article_num += len(articles)

        except Exception as e:
            error_msg = str(e)
            print(f"Section {section['id']} failed: {error_msg}")
            sections_html += render_section(section, [], article_num, [], error=error_msg)
            article_num += 3

    seen_urls.update(newly_seen)
    save_seen_urls(seen_urls)

    html = build_email_html(sections_html, date_str)
    send_email(html, date_str)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Fatal error: {e}")
        try:
            send_failure_alert(str(e))
        except Exception as alert_err:
            print(f"Could not send failure alert: {alert_err}")
        raise
