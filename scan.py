#!/usr/bin/env python3
"""IBD Literature Digest — Daily scanner for IBD-relevant publications.

Queries PubMed E-utilities for recent articles from gastroenterology and
high-impact journals matching IBD MeSH/keyword filters, then renders a
static HTML site to docs/index.html for GitHub Pages.
"""

import html as html_lib
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

try:
    import requests
except ImportError:
    print("Missing dependency. Run: pip install -r requirements.txt", file=sys.stderr)
    sys.exit(1)


# ─── Configuration ────────────────────────────────────────────────────────────

NCBI_EMAIL = "bryson.duhon@gmail.com"  # Required by NCBI E-utilities policy
DAYS_BACK = 7
OUTPUT_DIR = Path(__file__).parent / "docs"
PUBMED_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


# ─── Journal Lists ────────────────────────────────────────────────────────────

GI_JOURNALS = [
    '"Gut"[jour]',
    '"Gastroenterology"[jour]',
    '"Am J Gastroenterol"[jour]',
    '"J Crohns Colitis"[jour]',
    '"Inflamm Bowel Dis"[jour]',
    '"Clin Gastroenterol Hepatol"[jour]',
    '"United European Gastroenterol J"[jour]',
    '"Aliment Pharmacol Ther"[jour]',
    '"Dig Dis Sci"[jour]',
    '"J Gastroenterol"[jour]',
    '"Therap Adv Gastroenterol"[jour]',
]

HIGH_IMPACT_JOURNALS = [
    '"N Engl J Med"[jour]',
    '"Lancet"[jour]',
    '"Lancet Gastroenterol Hepatol"[jour]',
    '"JAMA"[jour]',
    '"JAMA Intern Med"[jour]',
    '"Nat Med"[jour]',
    '"BMJ"[jour]',
    '"Ann Intern Med"[jour]',
    '"Cell"[jour]',
    '"Sci Transl Med"[jour]',
]


# ─── IBD Search Query ─────────────────────────────────────────────────────────

IBD_QUERY = (
    '("Inflammatory Bowel Diseases"[MeSH] OR '
    '"Crohn Disease"[MeSH] OR '
    '"Colitis, Ulcerative"[MeSH] OR '
    '"Microscopic Colitis"[MeSH] OR '
    '"Pouchitis"[MeSH] OR '
    '"inflammatory bowel"[tiab] OR '
    '"crohn\'s disease"[tiab] OR '
    'crohn[tiab] OR '
    '"ulcerative colitis"[tiab] OR '
    'pouchitis[tiab] OR '
    '"perianal fistula"[tiab])'
)


# ─── PubMed E-utilities ───────────────────────────────────────────────────────

def date_range_query():
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=DAYS_BACK)
    return f'{start.strftime("%Y/%m/%d")}[pdat]:{end.strftime("%Y/%m/%d")}[pdat]'


def search_pubmed(query, retmax=150):
    params = {
        "db": "pubmed",
        "term": query,
        "retmax": retmax,
        "retmode": "json",
        "sort": "pub_date",
        "tool": "ibd-journal-scanner",
        "email": NCBI_EMAIL,
    }
    r = requests.get(f"{PUBMED_BASE}/esearch.fcgi", params=params, timeout=30)
    r.raise_for_status()
    return r.json().get("esearchresult", {}).get("idlist", [])


def fetch_details(pmids):
    if not pmids:
        return []
    params = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "xml",
        "rettype": "abstract",
        "tool": "ibd-journal-scanner",
        "email": NCBI_EMAIL,
    }
    r = requests.get(f"{PUBMED_BASE}/efetch.fcgi", params=params, timeout=60)
    r.raise_for_status()
    return parse_xml(r.text)


def parse_xml(xml_text):
    articles = []
    root = ET.fromstring(xml_text)
    for node in root.findall(".//PubmedArticle"):
        try:
            art = parse_article(node)
            if art:
                articles.append(art)
        except Exception as e:
            print(f"  warn: failed to parse article: {e}", file=sys.stderr)
    return articles


def parse_article(node):
    pmid_el = node.find(".//PMID")
    if pmid_el is None or not pmid_el.text:
        return None
    pmid = pmid_el.text

    title_el = node.find(".//ArticleTitle")
    title = "".join(title_el.itertext()) if title_el is not None else "No title"
    title = title.strip().rstrip(".")
    if title.startswith("[") and title.endswith("]"):
        title = title[1:-1]

    abstract_els = node.findall(".//Abstract/AbstractText")
    abstract_parts = []
    for el in abstract_els:
        label = (el.get("Label") or "").strip()
        text = "".join(el.itertext()).strip()
        if not text:
            continue
        if label:
            abstract_parts.append(f"{label}: {text}")
        else:
            abstract_parts.append(text)
    abstract = "\n\n".join(abstract_parts)

    iso = node.find(".//Journal/ISOAbbreviation")
    full = node.find(".//Journal/Title")
    journal = (iso.text if iso is not None else (full.text if full is not None else "Unknown"))

    author_nodes = node.findall(".//AuthorList/Author")
    authors = []
    for a in author_nodes[:4]:
        last = a.findtext("LastName", "") or ""
        fore = a.findtext("ForeName", "") or ""
        if last:
            initials = "".join(p[0] for p in fore.split() if p) if fore else ""
            authors.append(f"{last} {initials}".strip())
    author_str = ", ".join(authors)
    if len(author_nodes) > 4:
        author_str += ", et al."

    pub_date = node.find(".//PubDate")
    date_str = ""
    if pub_date is not None:
        year = pub_date.findtext("Year", "") or ""
        month = pub_date.findtext("Month", "") or ""
        day = pub_date.findtext("Day", "") or ""
        date_str = " ".join(x for x in [month, day, year] if x).strip()

    doi = None
    for id_el in node.findall(".//ArticleId"):
        if id_el.get("IdType") == "doi" and id_el.text:
            doi = id_el.text.strip()
            break

    pub_types = [pt.text for pt in node.findall(".//PublicationType") if pt.text]
    mesh = [mh.findtext("DescriptorName", "") for mh in node.findall(".//MeshHeading")]
    mesh = [m for m in mesh if m]

    return {
        "pmid": pmid,
        "title": title,
        "abstract": abstract,
        "journal": journal,
        "authors": author_str,
        "date": date_str,
        "doi": doi,
        "pub_types": pub_types,
        "mesh": mesh,
        "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
    }


# ─── Tagging ──────────────────────────────────────────────────────────────────

BIOLOGIC_CLASSES = {
    "infliximab": "Anti-TNF",
    "adalimumab": "Anti-TNF",
    "certolizumab": "Anti-TNF",
    "golimumab": "Anti-TNF",
    "vedolizumab": "Anti-integrin",
    "natalizumab": "Anti-integrin",
    "ustekinumab": "Anti-IL-12/23",
    "risankizumab": "Anti-IL-23",
    "mirikizumab": "Anti-IL-23",
    "guselkumab": "Anti-IL-23",
    "tofacitinib": "JAK inhibitor",
    "upadacitinib": "JAK inhibitor",
    "filgotinib": "JAK inhibitor",
    "ozanimod": "S1P modulator",
    "etrasimod": "S1P modulator",
}


def tag_article(art):
    text = (art["title"] + " " + art["abstract"] + " " + " ".join(art["mesh"])).lower()
    tags = []

    if any(t in text for t in ["crohn", "fistulizing", "perianal fistula", "stricturing"]):
        tags.append("Crohn's Disease")
    if "ulcerative colitis" in text or "proctitis" in text or "pancolitis" in text:
        tags.append("Ulcerative Colitis")
    if "pouchitis" in text or "ileal pouch" in text or "ipaa" in text:
        tags.append("Pouchitis")
    if "microscopic colitis" in text or "collagenous colitis" in text or "lymphocytic colitis" in text:
        tags.append("Microscopic Colitis")
    if not tags:
        tags.append("IBD")

    pt_lower = " ".join(art["pub_types"]).lower()
    if "randomized controlled trial" in pt_lower:
        tags.append("RCT")
    elif "clinical trial" in pt_lower:
        tags.append("Clinical Trial")
    elif "meta-analysis" in pt_lower:
        tags.append("Meta-Analysis")
    elif "systematic review" in pt_lower:
        tags.append("Systematic Review")
    elif "review" in pt_lower:
        tags.append("Review")
    elif "case reports" in pt_lower:
        tags.append("Case Report")
    elif "guideline" in pt_lower or "practice guideline" in pt_lower:
        tags.append("Guideline")
    else:
        tags.append("Original Research")

    seen_classes = set()
    for drug, drug_class in BIOLOGIC_CLASSES.items():
        if drug in text and drug_class not in seen_classes:
            tags.append(drug_class)
            seen_classes.add(drug_class)

    return tags


# ─── HTML Rendering ───────────────────────────────────────────────────────────

TAG_COLORS = {
    "Crohn's Disease": ("#6D28D9", "#EDE9FE"),
    "Ulcerative Colitis": ("#1D4ED8", "#DBEAFE"),
    "Pouchitis": ("#0E7490", "#CFFAFE"),
    "Microscopic Colitis": ("#047857", "#D1FAE5"),
    "IBD": ("#4338CA", "#E0E7FF"),
    "RCT": ("#B91C1C", "#FEE2E2"),
    "Clinical Trial": ("#B91C1C", "#FEE2E2"),
    "Meta-Analysis": ("#B45309", "#FEF3C7"),
    "Systematic Review": ("#B45309", "#FEF3C7"),
    "Review": ("#92400E", "#FEF3C7"),
    "Guideline": ("#7C2D12", "#FED7AA"),
    "Original Research": ("#374151", "#F1F5F9"),
    "Case Report": ("#6B7280", "#F1F5F9"),
}
DEFAULT_DRUG_COLOR = ("#065F46", "#D1FAE5")


def tag_color(tag):
    return TAG_COLORS.get(tag, DEFAULT_DRUG_COLOR)


def render_tag(tag):
    fg, bg = tag_color(tag)
    return f'<span class="tag" style="color:{fg};background:{bg}">{html_lib.escape(tag)}</span>'


def render_article(art):
    tags_html = "".join(render_tag(t) for t in art["tags"])
    tags_attr = html_lib.escape(json.dumps(art["tags"]))

    abstract_html = ""
    if art["abstract"]:
        paragraphs = art["abstract"].split("\n\n")
        body = "".join(f"<p>{html_lib.escape(p)}</p>" for p in paragraphs)
        abstract_html = f'<details class="abstract"><summary>Show abstract</summary><div class="abstract-body">{body}</div></details>'

    doi_link = ""
    if art["doi"]:
        doi_link = f'<a href="https://doi.org/{html_lib.escape(art["doi"])}" target="_blank" rel="noopener" class="doi-link">Full text →</a>'

    return f"""
    <article class="article-card" data-tags='{tags_attr}'>
      <div class="article-header">
        <span class="journal-badge">{html_lib.escape(art['journal'])}</span>
        <span class="article-date">{html_lib.escape(art['date'])}</span>
      </div>
      <h3 class="article-title">
        <a href="{html_lib.escape(art['url'])}" target="_blank" rel="noopener">{html_lib.escape(art['title'])}</a>
      </h3>
      <p class="article-authors">{html_lib.escape(art['authors'])}</p>
      <div class="tag-row">{tags_html}</div>
      {abstract_html}
      <div class="article-footer">
        <a href="{html_lib.escape(art['url'])}" target="_blank" rel="noopener" class="pubmed-link">PubMed ↗</a>
        {doi_link}
      </div>
    </article>"""


def generate_html(articles, generated_at):
    gi_articles = [a for a in articles if a.get("section") == "gi"]
    hi_articles = [a for a in articles if a.get("section") == "high-impact"]

    all_tags = sorted({t for a in articles for t in a["tags"]})
    filter_buttons = '<button class="filter-btn active" data-filter="all">All</button>'
    for tag in all_tags:
        filter_buttons += f'<button class="filter-btn" data-filter="{html_lib.escape(tag)}">{html_lib.escape(tag)}</button>'

    gi_html = (
        "\n".join(render_article(a) for a in gi_articles)
        if gi_articles
        else '<p class="no-articles">No new IBD articles in this period.</p>'
    )
    hi_html = (
        "\n".join(render_article(a) for a in hi_articles)
        if hi_articles
        else '<p class="no-articles">No new IBD articles in this period.</p>'
    )

    date_display = generated_at.strftime("%B %d, %Y")
    time_display = generated_at.strftime("%H:%M UTC")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>IBD Literature Digest</title>
  <meta name="description" content="Daily scan of inflammatory bowel disease publications from gastroenterology and high-impact journals.">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

    body {{
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: #F8FAFC;
      color: #0F172A;
      line-height: 1.6;
      -webkit-font-smoothing: antialiased;
    }}

    a {{ color: inherit; }}

    .site-header {{
      background: linear-gradient(135deg, #1E3A5F 0%, #2D6A9F 60%, #3B82B5 100%);
      color: white;
      padding: 3rem 1.5rem 2.5rem;
      text-align: center;
    }}
    .site-header h1 {{
      font-size: 2.25rem;
      font-weight: 700;
      letter-spacing: -0.02em;
      margin-bottom: 0.5rem;
    }}
    .site-header .subtitle {{
      font-size: 1.05rem;
      opacity: 0.9;
      font-weight: 400;
      max-width: 540px;
      margin: 0 auto;
    }}
    .header-meta {{
      display: inline-flex;
      gap: 0.75rem;
      align-items: center;
      margin-top: 1.25rem;
      flex-wrap: wrap;
      justify-content: center;
    }}
    .meta-pill {{
      background: rgba(255,255,255,0.18);
      border: 1px solid rgba(255,255,255,0.25);
      border-radius: 999px;
      padding: 0.3rem 0.85rem;
      font-size: 0.82rem;
      font-weight: 500;
    }}

    .container {{
      max-width: 980px;
      margin: 0 auto;
      padding: 2rem 1.5rem 4rem;
    }}

    .filters {{
      background: white;
      border: 1px solid #E2E8F0;
      border-radius: 14px;
      padding: 1rem 1.25rem;
      margin-bottom: 2.5rem;
      box-shadow: 0 1px 3px rgba(15,23,42,0.04);
    }}
    .filters-label {{
      font-size: 0.72rem;
      font-weight: 700;
      color: #64748B;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      margin-bottom: 0.6rem;
      display: block;
    }}
    .filter-group {{
      display: flex;
      flex-wrap: wrap;
      gap: 0.4rem;
    }}
    .filter-btn {{
      padding: 0.35rem 0.9rem;
      border: 1.5px solid #E2E8F0;
      border-radius: 999px;
      background: white;
      color: #475569;
      font-size: 0.82rem;
      font-weight: 500;
      cursor: pointer;
      transition: all 0.15s ease;
      font-family: inherit;
    }}
    .filter-btn:hover {{
      border-color: #2D6A9F;
      color: #2D6A9F;
    }}
    .filter-btn.active {{
      background: #1E3A5F;
      border-color: #1E3A5F;
      color: white;
    }}

    .section-heading {{
      font-size: 1.05rem;
      font-weight: 700;
      color: #0F172A;
      margin-bottom: 1rem;
      padding-bottom: 0.6rem;
      border-bottom: 2px solid #E2E8F0;
      display: flex;
      align-items: center;
      gap: 0.6rem;
      letter-spacing: -0.01em;
    }}
    .section-icon {{
      width: 32px;
      height: 32px;
      border-radius: 8px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      font-size: 0.78rem;
      font-weight: 700;
    }}
    .section-gi .section-icon {{ background: #DBEAFE; color: #1D4ED8; }}
    .section-hi .section-icon {{ background: #FEE2E2; color: #B91C1C; }}
    .section-count {{
      margin-left: auto;
      font-size: 0.8rem;
      font-weight: 500;
      color: #94A3B8;
    }}

    .articles-section {{ margin-bottom: 3rem; }}

    .article-card {{
      background: white;
      border: 1px solid #E2E8F0;
      border-radius: 12px;
      padding: 1.4rem 1.6rem;
      margin-bottom: 1rem;
      transition: box-shadow 0.2s ease, border-color 0.2s ease, transform 0.2s ease;
    }}
    .article-card:hover {{
      box-shadow: 0 4px 16px rgba(15,23,42,0.08);
      border-color: #CBD5E1;
    }}
    .article-card.hidden {{ display: none; }}

    .article-header {{
      display: flex;
      align-items: center;
      gap: 0.75rem;
      margin-bottom: 0.7rem;
    }}
    .journal-badge {{
      background: #F1F5F9;
      color: #475569;
      font-size: 0.76rem;
      font-weight: 600;
      padding: 0.22rem 0.6rem;
      border-radius: 5px;
      border: 1px solid #E2E8F0;
      letter-spacing: 0.01em;
    }}
    .article-date {{
      color: #94A3B8;
      font-size: 0.78rem;
      margin-left: auto;
    }}

    .article-title {{
      font-size: 1.05rem;
      font-weight: 600;
      line-height: 1.4;
      margin-bottom: 0.5rem;
      letter-spacing: -0.01em;
    }}
    .article-title a {{
      color: #0F172A;
      text-decoration: none;
    }}
    .article-title a:hover {{
      color: #2D6A9F;
    }}

    .article-authors {{
      font-size: 0.85rem;
      color: #64748B;
      margin-bottom: 0.75rem;
      font-style: italic;
    }}

    .tag-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 0.35rem;
      margin-bottom: 0.5rem;
    }}
    .tag {{
      font-size: 0.7rem;
      font-weight: 600;
      padding: 0.22rem 0.6rem;
      border-radius: 999px;
      letter-spacing: 0.02em;
      text-transform: uppercase;
    }}

    .abstract {{
      margin-top: 0.85rem;
      border-top: 1px solid #F1F5F9;
      padding-top: 0.75rem;
    }}
    .abstract summary {{
      cursor: pointer;
      font-size: 0.82rem;
      font-weight: 600;
      color: #2D6A9F;
      list-style: none;
      user-select: none;
    }}
    .abstract summary::before {{
      content: '▸ ';
      display: inline-block;
      transition: transform 0.15s ease;
    }}
    .abstract[open] summary::before {{ content: '▾ '; }}
    .abstract-body {{
      margin-top: 0.6rem;
      font-size: 0.88rem;
      color: #334155;
      line-height: 1.65;
    }}
    .abstract-body p {{ margin-bottom: 0.6rem; }}
    .abstract-body p:last-child {{ margin-bottom: 0; }}

    .article-footer {{
      display: flex;
      gap: 1.25rem;
      margin-top: 0.85rem;
      padding-top: 0.75rem;
      border-top: 1px solid #F1F5F9;
    }}
    .pubmed-link, .doi-link {{
      font-size: 0.82rem;
      font-weight: 600;
      text-decoration: none;
      color: #2D6A9F;
    }}
    .pubmed-link:hover, .doi-link:hover {{ text-decoration: underline; }}

    .no-articles {{
      color: #94A3B8;
      font-style: italic;
      padding: 2rem;
      text-align: center;
      background: white;
      border: 1px dashed #E2E8F0;
      border-radius: 12px;
    }}

    .site-footer {{
      text-align: center;
      padding: 2rem 1.5rem 3rem;
      color: #94A3B8;
      font-size: 0.82rem;
      border-top: 1px solid #E2E8F0;
      background: white;
    }}
    .site-footer p {{ margin-bottom: 0.4rem; max-width: 720px; margin-left: auto; margin-right: auto; }}
    .site-footer a {{ color: #475569; text-decoration: underline; }}

    @media (max-width: 640px) {{
      .site-header {{ padding: 2rem 1rem 1.75rem; }}
      .site-header h1 {{ font-size: 1.6rem; }}
      .site-header .subtitle {{ font-size: 0.95rem; }}
      .container {{ padding: 1.5rem 1rem 3rem; }}
      .article-card {{ padding: 1.1rem 1.15rem; }}
      .article-title {{ font-size: 0.98rem; }}
    }}
  </style>
</head>
<body>

<header class="site-header">
  <h1>IBD Literature Digest</h1>
  <p class="subtitle">A daily scan of inflammatory bowel disease publications from gastroenterology and high-impact medical journals.</p>
  <div class="header-meta">
    <span class="meta-pill">{len(articles)} articles · past {DAYS_BACK} days</span>
    <span class="meta-pill">Updated {date_display} · {time_display}</span>
  </div>
</header>

<main class="container">

  <div class="filters">
    <span class="filters-label">Filter</span>
    <div class="filter-group">
      {filter_buttons}
    </div>
  </div>

  <section class="articles-section section-gi">
    <h2 class="section-heading">
      <span class="section-icon">GI</span>
      Gastroenterology Journals
      <span class="section-count">{len(gi_articles)} article{'s' if len(gi_articles) != 1 else ''}</span>
    </h2>
    {gi_html}
  </section>

  <section class="articles-section section-hi">
    <h2 class="section-heading">
      <span class="section-icon">★</span>
      High-Impact Journals
      <span class="section-count">{len(hi_articles)} article{'s' if len(hi_articles) != 1 else ''}</span>
    </h2>
    {hi_html}
  </section>

</main>

<footer class="site-footer">
  <p>Data from <a href="https://pubmed.ncbi.nlm.nih.gov/" target="_blank" rel="noopener">PubMed / NCBI</a> via E-utilities API. Updated daily via GitHub Actions.</p>
  <p style="font-size:0.76rem;color:#CBD5E1">
    Scanning: Gut · Gastroenterology · AJG · J Crohn's Colitis · Inflamm Bowel Dis · CGH · UEGJ · APT · Dig Dis Sci · J Gastroenterol · Therap Adv Gastroenterol ·
    NEJM · Lancet · Lancet GH · JAMA · JAMA IM · Nat Med · BMJ · Ann Intern Med · Cell · Sci Transl Med
  </p>
</footer>

<script>
  const filterBtns = document.querySelectorAll('.filter-btn');
  const cards = document.querySelectorAll('.article-card');

  function applyFilter(filter) {{
    cards.forEach(card => {{
      if (filter === 'all') {{
        card.classList.remove('hidden');
      }} else {{
        const tags = JSON.parse(card.dataset.tags);
        card.classList.toggle('hidden', !tags.includes(filter));
      }}
    }});
    document.querySelectorAll('.articles-section').forEach(section => {{
      const visible = section.querySelectorAll('.article-card:not(.hidden)').length;
      const countEl = section.querySelector('.section-count');
      if (countEl) countEl.textContent = visible + ' article' + (visible !== 1 ? 's' : '');
    }});
  }}

  filterBtns.forEach(btn => {{
    btn.addEventListener('click', () => {{
      filterBtns.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      applyFilter(btn.dataset.filter);
    }});
  }});
</script>

</body>
</html>"""


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print(f"IBD Journal Scanner — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Window: past {DAYS_BACK} days\n")

    date_q = date_range_query()
    all_articles = []
    seen = set()

    print("[1/2] Scanning gastroenterology journals...")
    gi_query = f"({' OR '.join(GI_JOURNALS)}) AND {IBD_QUERY} AND {date_q}"
    gi_pmids = search_pubmed(gi_query, retmax=200)
    print(f"      {len(gi_pmids)} matching IDs")
    time.sleep(0.4)

    if gi_pmids:
        gi_arts = fetch_details(gi_pmids)
        for a in gi_arts:
            if a["pmid"] not in seen:
                a["section"] = "gi"
                a["tags"] = tag_article(a)
                all_articles.append(a)
                seen.add(a["pmid"])
        time.sleep(0.4)

    print("[2/2] Scanning high-impact journals...")
    hi_query = f"({' OR '.join(HIGH_IMPACT_JOURNALS)}) AND {IBD_QUERY} AND {date_q}"
    hi_pmids = search_pubmed(hi_query, retmax=80)
    print(f"      {len(hi_pmids)} matching IDs")
    time.sleep(0.4)

    if hi_pmids:
        hi_arts = fetch_details(hi_pmids)
        for a in hi_arts:
            if a["pmid"] not in seen:
                a["section"] = "high-impact"
                a["tags"] = tag_article(a)
                all_articles.append(a)
                seen.add(a["pmid"])

    print(f"\n{len(all_articles)} IBD-relevant articles collected")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    html = generate_html(all_articles, datetime.now(timezone.utc))
    out_path = OUTPUT_DIR / "index.html"
    out_path.write_text(html, encoding="utf-8")
    print(f"Site written to {out_path}")


if __name__ == "__main__":
    main()
