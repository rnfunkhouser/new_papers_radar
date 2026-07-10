#!/usr/bin/env python3
"""
dashboard.py — a tiny local web dashboard for the Daily Articles Briefing.

Runs a localhost-only HTTP server (pure stdlib) so you can, at this Mac:
  - read each day's papers as cards (title, link, abstract, score),
  - 👍/👎 each paper — votes are written to feedback.json, which both steers future
    scoring and becomes a labeled evaluation set for tuning,
  - browse and search the archive of past briefings.

The daily run writes a structured snapshot to briefings/data_<date>.json (the same papers
the PDF covers); this server reads those. Bind is 127.0.0.1 only — nothing is exposed.

    python3 dashboard.py            # serve at http://localhost:8765
"""

import json, html, os, re, sys, datetime as dt
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, quote

HERE = Path(__file__).parent
BRIEFINGS = HERE / "briefings"
FEEDBACK = HERE / "feedback.json"
# Bind/port/password from env so the same code serves localhost (Mac) or a public VM.
HOST = os.environ.get("DASH_HOST", "127.0.0.1")
PORT = int(os.environ.get("DASH_PORT", "8765"))
# Bot-deterrence gate on model-steering writes (votes + topic weights). Reading is public.
# Not real security — the password rides in request bodies; matches the stated intent.
WRITE_PASSWORD = os.environ.get("DASH_PASSWORD", "changeme")

# ----------------------------------------------------------------------------
# data helpers
# ----------------------------------------------------------------------------

def _days():
    """All briefing dates that have a structured data file, newest first."""
    return sorted((p.stem.replace("data_", "")
                   for p in BRIEFINGS.glob("data_*.json")), reverse=True)

def _papers(date):
    f = BRIEFINGS / f"data_{date}.json"
    if not f.exists():
        return []
    try:
        return json.loads(f.read_text())
    except Exception:
        return []

def _load_feedback():
    if FEEDBACK.exists():
        try:
            return json.loads(FEEDBACK.read_text())
        except Exception:
            return {}
    return {}

def _save_feedback(fb):
    FEEDBACK.write_text(json.dumps(fb, indent=2))

def _norm(t):
    return re.sub(r"\W+", "", (t or "").lower())

def _first_sentences(text, n=2, maxchars=340):
    """A short preview of an abstract — used as the card summary when there's no blurb."""
    text = (text or "").strip()
    if not text:
        return ""
    parts = re.split(r"(?<=[.!?])\s+", text)
    out = " ".join(parts[:n]).strip()
    if len(out) > maxchars:
        out = out[:maxchars].rsplit(" ", 1)[0] + "…"
    elif len(parts) > n:
        out += " …"
    return out

def _paper_key(date, doi, title):
    return f"{date}|{(doi or '').strip() or _norm(title)[:60]}"

def _source_url(p):
    """Always returns a clickable destination: DOI page > open-access URL > a scholarly
    search for the title (so a paper with no DOI still has a 'find it' link)."""
    if (p.get("doi") or "").strip():
        return "https://doi.org/" + p["doi"].strip()
    if (p.get("oa_url") or "").strip():
        return p["oa_url"].strip()
    t = (p.get("title") or "").strip()
    return "https://scholar.google.com/scholar?q=" + quote(t) if t else ""

def _link_label(p, url):
    if "doi.org/" in url:
        return "doi.org/" + p["doi"].strip()
    if "scholar.google" in url:
        return "search for this paper ↗"
    return (urlparse(url).netloc or url) + " ↗"

def build_day_data(date):
    """Build briefings/data_<date>.json = the papers Claude KEPT for the briefing (from
    cards_<date>.json), each merged with its structured fields + official abstract from
    candidates.json, and Claude's short summary as 'blurb'. Falls back to the full
    candidates.json if the cards file is missing — so the dashboard always shows something."""
    cf = HERE / "candidates.json"
    cands = json.loads(cf.read_text()) if cf.exists() else []
    by_title = {_norm(c.get("title")): c for c in cands}
    cards_f = BRIEFINGS / f"cards_{date}.json"
    out = None
    if cards_f.exists():
        try:
            cards = json.loads(cards_f.read_text())
            merged = []
            for card in cards:
                t = card.get("title") if isinstance(card, dict) else card
                nt = _norm(t)
                c = by_title.get(nt)
                if not c and nt:                       # fuzzy contains-match
                    c = next((v for k, v in by_title.items() if nt in k or k in nt), None)
                if c:
                    rec = dict(c)
                    if isinstance(card, dict) and card.get("blurb"):
                        rec["blurb"] = card["blurb"]
                    merged.append(rec)
            if merged:
                out = merged
        except Exception:
            out = None
    if out is None:
        out = cands
    BRIEFINGS.mkdir(exist_ok=True)
    (BRIEFINGS / f"data_{date}.json").write_text(json.dumps(out, indent=2))
    return len(out)

# ----------------------------------------------------------------------------
# HTML rendering
# ----------------------------------------------------------------------------

FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
         '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
         '<link href="https://fonts.googleapis.com/css2?family=Source+Serif+4:opsz,wght@8..60,500;8..60,600'
         '&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">')

# "Warm Editorial" design system — the default dashboard aesthetic.
CSS = """
:root{--bg:#f6f1e7;--panel:#fffefa;--panel2:#efe8da;--line:#e2d8c6;
 --txt:#2c2620;--muted:#9a8d78;--accent:#a8734d;--teal:#2c2620;
 --b:#a79781;--warmbody:#5c5346}
*{box-sizing:border-box}
body{margin:0;padding:0 0 64px;background:var(--bg);color:var(--txt);
 font-family:"Inter",-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
 font-size:13px;line-height:1.5}
h1,.card h2,.tp-name{font-family:"Source Serif 4",Georgia,"Times New Roman",serif;font-weight:600}
header{position:sticky;top:0;z-index:5;background:var(--panel);border-bottom:2px solid var(--teal);
 padding:14px 26px;display:flex;gap:22px;align-items:baseline;flex-wrap:wrap}
header h1{font-size:22px;margin:0;color:var(--teal)}
header nav{display:flex;gap:16px}
header nav a{color:var(--accent);text-decoration:none;font-size:13px;font-weight:600;
 text-transform:uppercase;letter-spacing:1.2px}
header form{margin-left:auto}
input[type=search]{font:13px Inter,sans-serif;padding:7px 12px;border:1px solid var(--line);
 border-radius:8px;width:230px;background:var(--panel);color:var(--txt);outline:none}
input[type=search]:focus{border-color:var(--accent)}
main{max-width:840px;margin:0 auto;padding:26px 28px}
.sub{color:var(--muted);font-size:15px;margin:-4px 0 22px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;
 box-shadow:0 1px 2px rgba(30,25,20,.05);padding:20px 22px;margin:0 0 16px}
.card h2{font-size:16.5px;margin:0 0 6px;line-height:1.4;color:var(--teal)}
.card h2 a{color:var(--teal);text-decoration:none}
.card h2 a:hover{color:var(--accent)}
.meta{color:var(--muted);font-size:12px;margin:0 0 10px}
.score{display:inline-block;font-size:11px;font-variant-numeric:tabular-nums;font-weight:600;
 color:var(--muted);background:var(--panel2);border-radius:20px;padding:3px 9px;margin-left:8px}
.blurb{font-size:13.5px;color:var(--warmbody);margin:10px 0 4px;line-height:1.62}
.blurb p{margin:0 0 10px}.blurb p:last-child{margin-bottom:0}
.absbox{margin:10px 0 0;border-top:1px solid var(--line);padding-top:8px}
.absbox summary{font-size:12px;color:var(--accent);font-weight:700;cursor:pointer;list-style:none}
.absbox summary::-webkit-details-marker{display:none}
.absbox summary:before{content:'▸ ';color:var(--muted)}
.absbox[open] summary:before{content:'▾ '}
.abs{font-size:12.5px;color:var(--warmbody);margin:8px 0 0}
.noabs{font-size:11.5px;color:var(--muted);font-style:italic;margin:8px 0 0}
.topic{font-size:11.5px;text-transform:uppercase;letter-spacing:1.2px;font-weight:700;
 color:var(--muted);margin:28px 0 12px;border-bottom:1px solid var(--line);padding-bottom:6px}
.votes{margin-top:14px;display:flex;gap:8px;align-items:center}
.vote{font:12.5px Inter,sans-serif;font-weight:600;cursor:pointer;border:1px solid var(--line);
 background:var(--panel);color:var(--muted);border-radius:20px;padding:6px 15px}
.vote:hover{background:var(--panel2)}
.vote.up.on{background:var(--accent);color:#fff;border-color:var(--accent)}
.vote.down.on{background:var(--b);color:#fff;border-color:var(--b)}
.src{font-size:12px}.src a{color:var(--accent);text-decoration:none;font-weight:600}
.src a:hover{text-decoration:underline}
.empty{color:var(--muted);font-style:italic}
/* archive */
.month{background:var(--panel);border:1px solid var(--line);border-radius:8px;margin:0 0 12px;
 box-shadow:0 1px 2px rgba(30,25,20,.05)}
.month>summary{padding:14px 20px;cursor:pointer;list-style:none;display:flex;align-items:baseline;gap:10px}
.month>summary::-webkit-details-marker{display:none}
.month>summary .mname{font-family:"Source Serif 4",Georgia,serif;font-weight:600;font-size:15px;color:var(--teal)}
.month>summary .mcount{font-size:11px;color:var(--muted);font-variant-numeric:tabular-nums}
.month>summary:before{content:'▸';color:var(--accent);font-size:12px}
.month[open]>summary:before{content:'▾'}
.arow{padding:9px 20px;border-top:1px solid var(--line);display:flex;gap:12px;align-items:baseline}
.arow a{color:var(--accent);text-decoration:none;font-weight:600;font-variant-numeric:tabular-nums}
.arow .awk{color:var(--muted);font-size:12px}
/* topics page */
.tp{background:var(--panel);border:1px solid var(--line);border-left:4px solid var(--accent);
 border-radius:8px;box-shadow:0 1px 2px rgba(30,25,20,.05);padding:18px 22px;margin:0 0 14px}
.tp-head{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.tp-name{font-size:15px;color:var(--teal);text-transform:capitalize}
.tp-n{font-size:11px;color:var(--muted);font-variant-numeric:tabular-nums}
.tp-desc{font-size:13px;color:var(--warmbody);margin:8px 0 0;line-height:1.6;max-width:680px}
.wctl{margin-left:auto;display:flex;align-items:center;gap:8px}
.wbtn{width:28px;height:28px;border-radius:20px;border:1px solid var(--line);background:var(--panel);
 color:var(--accent);font-size:15px;font-weight:700;cursor:pointer;line-height:1}
.wbtn:hover{background:var(--accent);color:#fff;border-color:var(--accent)}
.wval{font-size:12px;font-weight:700;color:var(--accent);font-variant-numeric:tabular-nums;min-width:44px;text-align:center}
.tp .absbox{border-top:1px solid var(--line)}
.tprow{padding:8px 0 2px;font-size:12.5px}
.tprow a{color:var(--accent);text-decoration:none;font-weight:600}
.tprow .tmeta{color:var(--muted);font-size:11.5px;font-variant-numeric:tabular-nums}
.foot{margin-top:28px;padding:14px 18px;background:var(--panel);border:1px solid var(--line);
 border-radius:8px;font-size:11.5px;color:var(--muted);line-height:1.6}
.foot a{color:var(--accent);text-decoration:none;font-weight:600}
"""

JS = """
// Password for model-steering writes; asked once per browser session, cached.
function pw(){
  let p=sessionStorage.getItem('dashpw');
  if(!p){ p=prompt('Password to rate/steer (reading is open).\\nHint: what is this app finding for you?')||''; if(p) sessionStorage.setItem('dashpw',p); }
  return p;
}
function postWrite(url, payload){
  payload.password=pw();
  return fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)})
    .then(r=>{ if(r.status==401){ sessionStorage.removeItem('dashpw'); alert('Wrong password — try again.'); throw 'auth'; } return r.json(); });
}
function vote(btn, key, v){
  const card=btn.closest('.card');
  const nv=(card.dataset.vote==v)?'':v;            // click same = toggle off
  postWrite('/vote',{key:key, vote:nv, title:card.dataset.title, date:card.dataset.date})
   .then(()=>{
     card.dataset.vote=nv;
     card.querySelector('.vote.up').classList.toggle('on', nv=='up');
     card.querySelector('.vote.down').classList.toggle('on', nv=='down');
   }).catch(()=>{});
}
function toggleAbs(b){const a=b.previousElementSibling;a.classList.toggle('open');
  b.textContent=a.classList.contains('open')?'show less':'show more';}
function nudge(btn, label, delta){
  postWrite('/topic_weight',{label:label, delta:delta})
   .then(d=>{
     const row=btn.closest('.tp');
     row.querySelector('.wval').textContent='×'+d.weight.toFixed(2);
   }).catch(()=>{});
}
"""

def _page(title, body):
    return ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{html.escape(title)}</title>{FONTS}<style>{CSS}</style>"
            "<header><h1>Daily Articles Briefing</h1>"
            "<nav><a href='/'>Today</a><a href='/archive'>Archive</a><a href='/topics'>Topics</a></nav>"
            "<form action='/search'><input type=search name=q placeholder='Search all briefings…'></form>"
            f"</header><main>{body}</main><script>{JS}</script>")

def _topic_order():
    """{label: 1-based rank} using the same size-descending order as the Topics page,
    so 'Topic 2' on a card and on /topics mean the same thing."""
    try:
        clusters = json.loads((HERE / "clusters.json").read_text()).get("clusters", [])
    except Exception:
        return {}
    ordered = sorted(clusters, key=lambda c: -c.get("size", 0))[:10]
    return {c["label"]: i + 1 for i, c in enumerate(ordered)}

def _topic_no(label, order):
    """Topic number for a label; legacy labels from before label-inheritance fall back
    to a word-overlap match (>=2 shared words) so old archives still get numbered."""
    if not label:
        return None
    if label in order:
        return order[label]
    words = set(w for w in re.split(r"\W+", label) if len(w) > 3)
    best, bestov = None, 1
    for k, n in order.items():
        ov = len(words & set(w for w in re.split(r"\W+", k) if len(w) > 3))
        if ov > bestov:
            best, bestov = n, ov
    return best

def _display_date(p):
    """The paper's real-world date. Advance-access records carry a FUTURE print date
    (e.g. an August issue harvested in July) — for those, show the online/record date
    instead, marked in press; never display a not-yet-happened date as-is."""
    pub = (p.get("date") or "").strip()
    created = (p.get("created") or "").strip()
    today = dt.date.today().isoformat()
    if pub and pub <= today:
        return pub
    if created and created <= today:
        return f"online {created} · in press"
    if pub:
        return f"in press ({pub[:7]})"
    return ""

def _card(p, date, fb, topic_no=None):
    key = _paper_key(date, p.get("doi"), p.get("title"))
    vote = (fb.get(key) or {}).get("vote", "")
    url = _source_url(p)
    title = html.escape(p.get("title") or "(untitled)")
    titleh = f"<a href='{html.escape(url)}'>{title}</a>" if url else title
    authors = ", ".join(p.get("authors", [])[:6])
    sc = p.get("_scores", {})
    meta = " · ".join(x for x in [html.escape(authors), html.escape(p.get("venue") or ""),
                                  html.escape(_display_date(p))] if x)
    chip = ""
    if sc:
        tpart = f"Topic {topic_no} · " if topic_no else ""
        chip = f"<span class=score>{tpart}{p.get('score', 0):.2f}</span>"
    score = chip
    # Always show a source link (DOI / OA / scholarly search).
    src = (f"<div class=src>🔗 <a href='{html.escape(url)}'>{html.escape(_link_label(p, url))}</a></div>"
           if url else "")
    # Summary always shown: Claude's blurb if we have it, else a short preview of the
    # official abstract. The full official abstract stays available behind the toggle.
    abs_raw = p.get("abstract") or ""
    summary = p.get("blurb") or _first_sentences(abs_raw)
    # summary is now full-depth prose (matches the PDF) — render paragraph breaks and the
    # occasional inline **bold** / *italic* markdown instead of showing raw asterisks.
    def _inline_md(s):
        s = html.escape(s)
        s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"(?<!\*)\*(?!\*)([^*]+)\*(?!\*)", r"<em>\1</em>", s)
        return s
    paras = [p.strip() for p in re.split(r"\n\s*\n|\r\n\r\n", summary) if p.strip()] or [summary]
    blurb_html = ("<div class=blurb>"
                  + "".join(f"<p>{_inline_md(pp)}</p>" for pp in paras)
                  + "</div>") if summary else ""
    abs_ = html.escape(abs_raw)
    abs_html = (f"<details class=absbox><summary>Official abstract</summary>"
                f"<div class=abs>{abs_}</div></details>") if abs_ else \
               "<div class=noabs>No official abstract on record.</div>"
    return (f"<div class=card data-vote='{vote}' data-title=\"{html.escape((p.get('title') or '')[:200])}\" data-date='{date}'>"
            f"<h2>{titleh}</h2><div class=meta>{meta}{score}</div>{src}{blurb_html}{abs_html}"
            + "<div class=votes>"
              f"<button class='vote up{' on' if vote=='up' else ''}' onclick=\"vote(this,'{key}','up')\">👍 interesting</button>"
              f"<button class='vote down{' on' if vote=='down' else ''}' onclick=\"vote(this,'{key}','down')\">👎 not for me</button>"
              "</div></div>")

def render_day(date):
    papers = _papers(date)
    fb = _load_feedback()
    if not papers:
        return _page("Today", "<p class=empty>No briefing data yet. The daily run writes it each morning.</p>")
    try:
        nice = dt.date.fromisoformat(date).strftime("%A, %B %-d, %Y")
    except Exception:
        nice = date
    # Group by topic cluster if the papers carry topic labels; else a single flat list.
    order = _topic_order()
    groups = {}
    for p in papers:
        groups.setdefault(p.get("topic") or "", []).append(p)
    if len(groups) <= 1:
        cards = "".join(_card(p, date, fb, _topic_no(p.get("topic"), order)) for p in papers)
    else:
        cards = "".join(
            (f"<div class=topic>{('Topic ' + str(_topic_no(topic, order)) + ' — ') if _topic_no(topic, order) else ''}"
             f"{html.escape(topic)}</div>" if topic else "")
            + "".join(_card(p, date, fb, _topic_no(topic, order)) for p in ps)
            for topic, ps in sorted(groups.items(), key=lambda kv: -len(kv[1])))
    foot = ("<div class=foot>ℹ️ <b>Score</b> is the paper's overall ranking value, roughly 0–1: "
            "50% topical alignment with your seed papers (semantic match to your interests), "
            "35% venue quality (your trusted journals, prestige outlets, and measured citation "
            "impact), 15% recency — minus penalties for non-journal formats and non-US/European "
            "author bases. On a typical day, ~0.6+ is a strong match. <b>Topic N</b> is the "
            "interest area the paper sits closest to — see the <a href='/topics'>Topics</a> page.</div>")
    return _page(f"Briefing {date}", f"<div class=sub>{nice}</div>{cards}{foot}")

def render_archive():
    """Past editions grouped under a collapsible toggle per month (newest first,
    current month open)."""
    by_month = {}
    for d in _days():
        by_month.setdefault(d[:7], []).append(d)
    blocks = []
    for i, (month, days) in enumerate(sorted(by_month.items(), reverse=True)):
        try:
            mname = dt.date.fromisoformat(month + "-01").strftime("%B %Y")
        except Exception:
            mname = month
        rows = []
        for d in days:
            n = len(_papers(d))
            try:
                wk = dt.date.fromisoformat(d).strftime("%A")
            except Exception:
                wk = ""
            rows.append(f"<div class=arow><a href='/day/{d}'>{d}</a>"
                        f"<span class=awk>{wk} · {n} paper{'s' if n != 1 else ''}</span></div>")
        blocks.append(f"<details class=month{' open' if i == 0 else ''}>"
                      f"<summary><span class=mname>{mname}</span>"
                      f"<span class=mcount>{len(days)} edition{'s' if len(days) != 1 else ''}</span></summary>"
                      + "".join(rows) + "</details>")
    body = "".join(blocks) or "<p class=empty>No briefings archived yet.</p>"
    return _page("Archive", body)

def render_topics():
    """The model's deduced interest areas: label, synthesized description, a ± weight
    control, and (per topic) the top-3 briefing papers from the rolling 45-day window."""
    interests = []
    if (HERE / "interests.json").exists():
        try:
            interests = json.loads((HERE / "interests.json").read_text()).get("interests", [])
        except Exception:
            pass
    sizes = {}
    if (HERE / "clusters.json").exists():
        try:
            sizes = {c["label"]: c.get("size", 0)
                     for c in json.loads((HERE / "clusters.json").read_text()).get("clusters", [])}
        except Exception:
            pass
    weights = {}
    wf = HERE / "topic_weights.json"
    if wf.exists():
        try:
            weights = json.loads(wf.read_text())
        except Exception:
            pass
    # top-3 briefing papers per topic over the last 45 days, by score
    cutoff = (dt.date.today() - dt.timedelta(days=45)).isoformat()
    best = {}
    for d in _days():
        if d < cutoff:
            continue
        for p in _papers(d):
            t = p.get("topic")
            if t:
                best.setdefault(t, []).append((p.get("score", 0), d, p))
    if not interests:
        return _page("Topics", "<p class=empty>No interest profile yet — run "
                               "<code>python3 harvest.py --build-profile</code> on the VPN.</p>")
    blocks = ["<div class=sub>The interest areas the model has deduced from your seed papers. "
              "Use +/− to gently raise or lower a topic's priority in the daily ranking "
              "(each click ±0.05, capped ×0.75–×1.30).</div>"]
    interests = sorted(interests, key=lambda i: -sizes.get(i["label"], 0))[:10]
    for ti, it in enumerate(interests, 1):
        label = it["label"]
        w = float(weights.get(label, 1.0))
        n = sizes.get(label, 0)
        esc = html.escape(label).replace("'", "&#39;")
        top3 = sorted(best.get(label, []), key=lambda x: -x[0])[:3]
        rows = ""
        if top3:
            rows = "".join(
                f"<div class=tprow><a href='/day/{d}'>{html.escape((p.get('title') or '')[:110])}</a><br>"
                f"<span class=tmeta>score {s:.2f} · {html.escape(p.get('venue') or '')} · {d}</span></div>"
                for s, d, p in top3)
            rows = (f"<details class=absbox><summary>Top papers, last 45 days</summary>{rows}</details>")
        else:
            rows = "<div class=noabs>No briefing papers matched this topic in the last 45 days.</div>"
        blocks.append(
            f"<div class=tp><div class=tp-head>"
            f"<span class=tp-name>Topic {ti} — {html.escape(label)}</span><span class=tp-n>{n} seed papers</span>"
            f"<span class=wctl><button class=wbtn onclick=\"nudge(this,'{esc}',-0.05)\">−</button>"
            f"<span class=wval>×{w:.2f}</span>"
            f"<button class=wbtn onclick=\"nudge(this,'{esc}',0.05)\">+</button></span></div>"
            f"<div class=tp-desc>{html.escape(it.get('statement') or '')}</div>{rows}</div>")
    return _page("Topics", "".join(blocks))

def render_search(q):
    q = (q or "").strip()
    if not q:
        return _page("Search", "<p class=empty>Type a query in the search box.</p>")
    ql = q.lower()
    hits = []
    fb = _load_feedback()
    order = _topic_order()
    for d in _days():
        for p in _papers(d):
            blob = (p.get("title", "") + " " + p.get("abstract", "") + " " + p.get("venue", "")).lower()
            if ql in blob:
                hits.append(_card(p, d, fb, _topic_no(p.get("topic"), order)))
    body = f"<div class=sub>{len(hits)} result(s) for “{html.escape(q)}”</div>" + ("".join(hits) or "<p class=empty>No matches.</p>")
    return _page(f"Search: {q}", body)

# ----------------------------------------------------------------------------
# server
# ----------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def _send(self, body, ctype="text/html; charset=utf-8", code=200):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data))); self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path); path = u.path
        if path == "/" or path == "":
            days = _days()
            self._send(render_day(days[0]) if days else render_day(dt.date.today().isoformat()))
        elif path.startswith("/day/"):
            self._send(render_day(path.split("/day/", 1)[1]))
        elif path == "/archive":
            self._send(render_archive())
        elif path == "/topics":
            self._send(render_topics())
        elif path == "/search":
            self._send(render_search((parse_qs(u.query).get("q") or [""])[0]))
        else:
            self._send("<p>Not found. <a href='/'>Home</a></p>", code=404)

    def do_POST(self):
        p = urlparse(self.path).path
        n = int(self.headers.get("Content-Length", 0))
        try:
            req = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            req = {}
        # Both write endpoints steer the model — gate them on the shared password.
        if p in ("/topic_weight", "/vote"):
            supplied = req.get("password") or self.headers.get("X-Auth", "")
            if supplied != WRITE_PASSWORD:
                return self._send(json.dumps({"ok": False, "error": "auth"}),
                                  "application/json", 401)
        if p == "/topic_weight":
            # gentle ± nudges to a topic's priority; clamped so it can't overcorrect
            wf = HERE / "topic_weights.json"
            weights = {}
            if wf.exists():
                try:
                    weights = json.loads(wf.read_text())
                except Exception:
                    pass
            label = (req.get("label") or "").strip()
            if label:
                w = float(weights.get(label, 1.0)) + float(req.get("delta") or 0)
                w = max(0.75, min(1.30, round(w, 2)))
                if abs(w - 1.0) < 1e-9:
                    weights.pop(label, None)
                else:
                    weights[label] = w
                wf.write_text(json.dumps(weights, indent=2))
                return self._send(json.dumps({"ok": True, "weight": w}), "application/json")
            return self._send(json.dumps({"ok": False}), "application/json", 400)
        if p != "/vote":
            return self._send("{}", "application/json", 404)
        fb = _load_feedback()
        key = req.get("key")
        if key:
            if req.get("vote"):
                fb[key] = {"vote": req["vote"], "title": req.get("title", ""),
                           "date": req.get("date", ""), "ts": dt.datetime.now().isoformat(timespec="seconds")}
            else:
                fb.pop(key, None)          # toggled off
            _save_feedback(fb)
        self._send(json.dumps({"ok": True}), "application/json")

    def log_message(self, *a):            # quiet
        pass

def main():
    # `python3 dashboard.py build <date>` — rebuild that day's data from the briefing.
    if len(sys.argv) >= 3 and sys.argv[1] == "build":
        n = build_day_data(sys.argv[2])
        print(f"built briefings/data_{sys.argv[2]}.json with {n} paper(s)")
        return
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Daily Articles Briefing dashboard → http://{HOST}:{PORT}  (Ctrl-C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
