#!/usr/bin/env python3
"""
fetch_fulltext.py — try to obtain the FULL TEXT of each day's selected papers, so the
briefing summaries can be grounded in the actual paper instead of the abstract.

Open-access routes only (no library-proxy automation — publisher licenses prohibit it):
    1. arXiv                → the PDF always exists (abs URL -> /pdf/)
    2. Unpaywall (by DOI)   → best OA location's PDF, else its HTML page
    3. the record's oa_url  → whatever it serves (sniffed: PDF vs HTML)

Downloads land in fulltext/<date>/NN_<slug>.<pdf|html>, and each fetched record in
candidates.json gains a "fulltext_path" + "fulltext_route" so the briefing step knows to
Read the file. Everything is best-effort: a paper that can't be fetched simply keeps its
abstract-only treatment. Pure stdlib.

    python3 fetch_fulltext.py                # annotate candidates.json (default)
    python3 fetch_fulltext.py other.json     # annotate a different candidates file
"""

import json, re, sys, datetime as dt
from pathlib import Path
import urllib.request
from urllib.parse import quote

from harvest import SSL_CTX, MAILTO, get_json   # reuse SSL healing + polite headers

HERE = Path(__file__).parent
MAX_BYTES = 30 * 1024 * 1024          # refuse anything over 30 MB
TIMEOUT = 60
UA = {"User-Agent": f"paper-briefing fulltext fetcher (mailto:{MAILTO})"}


def _download(url):
    """Fetch a URL -> (bytes, kind) where kind is 'pdf' | 'html' | None. Sniffs content
    (magic bytes + Content-Type) rather than trusting the extension."""
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=SSL_CTX) as r:
        data = r.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            return None, None
        ctype = (r.headers.get("Content-Type") or "").lower()
    if data[:5] == b"%PDF-":
        return data, "pdf"
    if "pdf" in ctype:
        return (data, "pdf") if data[:5] == b"%PDF-" else (None, None)
    if "html" in ctype or data[:200].lstrip()[:1] in (b"<",):
        return data, "html"
    return None, None


def _routes(p):
    """Candidate URLs for one paper, best-first: (url, route_label)."""
    out = []
    oa = (p.get("oa_url") or "").strip()
    doi = (p.get("doi") or "").strip()
    m = re.search(r"arxiv\.org/abs/([^\s?#]+)", oa, re.I)
    if not m and doi.lower().startswith("10.48550/"):
        m = re.search(r"arxiv\.(.+)$", doi, re.I)
    if m:
        out.append((f"https://arxiv.org/pdf/{m.group(1)}", "arxiv-pdf"))
    if doi:
        u = get_json(f"https://api.unpaywall.org/v2/{quote(doi)}?email={MAILTO}", tries=1)
        loc = (u or {}).get("best_oa_location") or {}
        if loc.get("url_for_pdf"):
            out.append((loc["url_for_pdf"], "unpaywall-pdf"))
        if loc.get("url") and loc.get("url") != loc.get("url_for_pdf"):
            out.append((loc["url"], "unpaywall-page"))
    if oa and "arxiv.org/abs" not in oa.lower():
        out.append((oa, "oa_url"))
    return out


def _valid_existing(p):
    """True if p already points at a real, non-empty fetched full-text file (so we can
    skip re-downloading — e.g. when the harvest abstract-gate already fetched it)."""
    fp = p.get("fulltext_path")
    if not fp:
        return False
    f = HERE / fp
    return f.exists() and f.stat().st_size > 0


def fetch_one(p, outdir, idx):
    """Try to obtain REAL open-access full text for one paper. On success, set
    fulltext_path/fulltext_route on p and return (True, kb); on failure leave p unset and
    return (False, 0). Idempotent: a paper already backed by a valid fetched file is left
    untouched and reported as success. This is the single source of truth for "do we
    actually have full text?" — the harvest abstract-gate calls it to VERIFY content
    exists before showing an abstract-less paper (not merely that an OA URL is listed)."""
    if _valid_existing(p):
        return True, 0
    p.pop("fulltext_path", None); p.pop("fulltext_route", None)
    for url, route in _routes(p):
        try:
            data, kind = _download(url)
        except Exception:
            continue
        if not data:
            continue
        # an HTML page under ~15 KB is a landing/paywall stub, not an article
        if kind == "html" and len(data) < 15_000:
            continue
        outdir.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"\W+", "_", (p.get("title") or "paper").lower())[:50]
        f = outdir / f"{idx:02d}_{slug}.{kind}"
        f.write_bytes(data)
        p["fulltext_path"] = str(f.relative_to(HERE))
        p["fulltext_route"] = route
        return True, len(data) // 1024
    return False, 0


def today_outdir():
    return HERE / "fulltext" / dt.date.today().isoformat()


def fetch_all(cand_path):
    cand_path = Path(cand_path)
    papers = json.loads(cand_path.read_text())
    outdir = today_outdir()
    got = 0
    for i, p in enumerate(papers, 1):
        ok, kb = fetch_one(p, outdir, i)
        if ok:
            route = p.get("fulltext_route", "cached")
            print(f"  ✓ {i}. [{route:14}] {kb:5} KB  {(p.get('title') or '')[:52]}")
            got += 1
        else:
            print(f"  ✗ {i}. no open-access full text     {(p.get('title') or '')[:52]}")
    cand_path.write_text(json.dumps(papers, indent=2))
    print(f"Full text fetched for {got}/{len(papers)} papers -> {outdir if got else '(none)'}")
    return got


if __name__ == "__main__":
    fetch_all(sys.argv[1] if len(sys.argv) > 1 else HERE / "candidates.json")
