#!/usr/bin/env python3
"""
write_briefing.py — generate the daily briefing prose via the configured LLM API
(llm_api.json — any OpenAI-compatible endpoint; see embeddings.py).

For each selected paper in candidates.json it makes ONE chat call (per-paper is
steadier than juggling five papers + JSON in a single call), grounded in the abstract and —
when fetch_fulltext.py obtained it — the actual full text. It then assembles, with
guaranteed PDF/dashboard parity:
    briefings/briefing_<date>.md    (the PDF source: hook headline + citation + full prose)
    briefings/cards_<date>.json     (the dashboard: {title, blurb} — blurb == the same prose)

The house-style, length, and anti-fabrication rules mirror the prompt that used to live in
run_daily.sh. Model is auto-resolved from the provider's live model catalog (see embeddings.py).

    python3 write_briefing.py <YYYY-MM-DD>
"""

import json, re, sys, datetime as dt
from pathlib import Path

import config
import embeddings

HERE = Path(__file__).parent
FULLTEXT_MAX = 48000          # chars of full text fed to the model (well within 131k ctx)

_SYSTEM_COMMON = f"""You write ONE entry for a daily research briefing for {config.BRIEFING_AUDIENCE}. \
Tone: tight, academic/journalistic summary. Do NOT editorialize or add opinions; you may \
translate the practical import in plain language only in a closing 'why it matters' turn.

GROUNDING — this is critical. Use ONLY what is in the material provided (title, metadata, \
abstract, and full text if given). NEVER invent methods, sample sizes, numbers, effect \
sizes, or findings that are not present. If the abstract omits specifics, say so plainly \
('the abstract does not report effect sizes'). If only a title is available, write a short \
1-2 paragraph entry grounded strictly in title/venue and say so — a shorter honest entry \
always beats a longer speculative one.

OUTPUT: only the summary body — flowing prose, paragraphs separated by a blank line. Do \
NOT write a headline, a title, a markdown heading, or any preamble; the paper's real title \
is added separately. Start directly with the first sentence of the summary."""

# Two length regimes (2026-07-24): an abstract-only entry has exactly one source — the
# abstract — so a 300-450 word "summary" of it is inflation, not information. Short mode
# restates the abstract in plain English at roughly the abstract's own length. The full
# treatment is reserved for entries genuinely grounded in retrieved full text.
SYSTEM_SHORT = _SYSTEM_COMMON + """

LENGTH: you have ONLY the abstract. Write a plain-English restatement of it — question, \
design, findings, and one 'why it matters' sentence — at roughly the abstract's own \
length (one or two paragraphs, ~100-180 words). Do NOT stretch it: every sentence must \
carry information the abstract actually states."""

SYSTEM_FULL = _SYSTEM_COMMON + """

LENGTH: about 300-450 words of flowing prose (not headed subsections), covering background \
(the question and why it arose), method (design, sample, measures as specifically as the \
record allows), results (concrete findings/numbers where present), and discussion (what the \
authors conclude and why it matters) — grounded in the FULL TEXT provided. Only when the \
full text genuinely contains additional meaningful insight (important moderators, \
robustness checks, notable limitations, striking secondary findings) may you run up to \
~780 words. Never pad to reach a length."""


def read_fulltext(rec):
    """Return extracted full text for a record that has a fulltext_path, else ''. HTML is
    de-tagged; PDF uses pypdf if available (installed in the container). Best-effort."""
    rel = rec.get("fulltext_path")
    if not rel:
        return ""
    p = HERE / rel
    if not p.exists():
        return ""
    try:
        if p.suffix.lower() == ".pdf":
            try:
                import pypdf
            except Exception:
                return ""
            txt = []
            for page in pypdf.PdfReader(str(p)).pages:
                txt.append(page.extract_text() or "")
                if sum(len(t) for t in txt) > FULLTEXT_MAX:
                    break
            text = "\n".join(txt)
        else:  # html / other
            raw = p.read_text(errors="replace")
            raw = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
            text = re.sub(r"(?s)<[^>]+>", " ", raw)
            text = re.sub(r"\s+", " ", text)
        text = text.strip()
        return text[:FULLTEXT_MAX] if len(text) >= 500 else ""   # too short = extraction failed
    except Exception:
        return ""


def write_entry(rec):
    """One chat call -> summary prose (the real paper title is used for the heading
    elsewhere, not generated here). Falls back to the abstract on failure so one bad paper
    never sinks the whole briefing."""
    parts = [f"Title: {rec.get('title','')}",
             f"Authors: {', '.join(rec.get('authors', [])[:12])}",
             f"Venue: {rec.get('venue','')}",
             f"Date: {rec.get('date','')}"]
    abstract = (rec.get("abstract") or "").strip()
    parts.append(f"Abstract: {abstract}" if abstract else "Abstract: (none in the record)")
    ft = read_fulltext(rec)
    if ft:
        parts.append("\nFULL TEXT (extracted from the open-access source — ground the entry "
                     "in this; cite only what it actually says):\n" + ft)
    user = "\n".join(parts)
    try:
        out = embeddings.chat(SYSTEM_FULL if ft else SYSTEM_SHORT, user)
    except Exception as e:
        print(f"  ! chat failed for {rec.get('title','')[:40]} ({str(e)[:50]})", file=sys.stderr)
        return abstract or "No abstract or full text was available for this record."
    # strip any stray HEADLINE:/=== the model may still emit despite the instruction
    out = re.sub(r"^\s*HEADLINE:.*?(\n|$)", "", out, flags=re.I).replace("===", "").strip()
    return out


def citation_line(rec):
    authors = ", ".join(rec.get("authors", [])[:8]) or "Unknown"
    venue = rec.get("venue") or ""
    # honest date: never show a future print date as-is
    pub, created = (rec.get("date") or "").strip(), (rec.get("created") or "").strip()
    today = dt.date.today().isoformat()
    if pub and pub <= today:
        date = pub
    elif created and created <= today:
        date = f"online {created} · in press"
    elif pub:
        date = f"in press ({pub[:7]})"
    else:
        date = ""
    cite = f"**{authors}** · *{venue}* · {date}".rstrip(" ·")
    doi = (rec.get("doi") or "").strip()
    url = f"https://doi.org/{doi}" if doi else (rec.get("oa_url") or "")
    link = f"[{('doi.org/' + doi) if doi else url}]({url})" if url else "(no link available)"
    return cite + "\n" + link


def main(date):
    cands = json.loads((HERE / "candidates.json").read_text())
    model = embeddings.resolve_chat_model()
    print(f"  writing {len(cands)} entries via chat model: {model}")
    try:
        nice = dt.date.fromisoformat(date).strftime("%A, %B %-d, %Y")
    except Exception:
        nice = date
    md = [f"# Daily Papers Radar", f"### {nice}", "", "---", ""]
    cards = []
    for i, rec in enumerate(cands, 1):
        title = rec.get("title", "Untitled")
        summary = write_entry(rec)
        md += [f"## {i}. {title}", "", citation_line(rec), ""]
        if rec.get("judge_fit") is not None:   # mirror the dashboard chip: fit + flavors
            flav = ", ".join(f.replace("_", " ") for f in (rec.get("judge_flavors") or []))
            md += [f"*Fit: {rec['judge_fit']:g}/10{' · ' + flav if flav else ''}*", ""]
        md += [summary, ""]
        abstract = (rec.get("abstract") or "").strip()
        if abstract:                        # the real abstract, under a small heading, at the end
            md += ["**Abstract**", "", abstract, ""]
        md += ["---", ""]
        cards.append({"title": title, "blurb": summary})
        print(f"    {i}. {len(summary.split())}w  {title[:60]}")
    md += ["### How these were chosen", "",
           "Two stages. **Gathering** casts the net: research areas learned from your seed "
           "papers steer each morning's search, and candidates are shortlisted by semantic "
           "closeness to those seeds. **Selection** decides: an LLM judge reads every "
           "shortlisted abstract against your editable Selection Criteria and scores its "
           "fit — each entry's *Fit* line above (73% of the final score; venue quality is "
           "the other 27%). Only papers with an abstract are shown; strong papers still "
           "awaiting one are held and re-checked daily. Tune the picks by voting on the "
           "dashboard or editing the Selection Criteria page."]
    notes = []
    try:
        alert = json.loads((HERE / "alerts.json").read_text())
        if alert.get("date") == dt.date.today().isoformat():
            notes.append(f"⚠️ **Tuning note:** {alert.get('suggestion', '')}")
    except Exception:
        pass
    try:                                # coverage-audit proposals (option B) — mirror the banner
        props = json.loads((HERE / "proposals.json").read_text())
        for p in props.values():
            if p.get("status") == "pending":
                fl = p.get("flavor") or {}
                notes.append(f"💡 **Proposed new interest:** your seed papers now include a "
                             f"cluster (\"{p.get('cluster', '?')}\", {p.get('size', '?')} papers) "
                             f"that your Selection Criteria don't cover — the judge is likely "
                             f"vetoing this area. A drafted flavor "
                             f"(*{fl.get('key', '?')}*) is ready to review on the dashboard's "
                             f"Selection Criteria page.")
    except Exception:
        pass
    if notes:
        md += [""] + [n for note in notes for n in (note, "")]
    (HERE / "briefings").mkdir(exist_ok=True)
    (HERE / "briefings" / f"briefing_{date}.md").write_text("\n".join(md))
    (HERE / "briefings" / f"cards_{date}.json").write_text(json.dumps(cards, indent=2))
    print(f"  wrote briefing_{date}.md + cards_{date}.json")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else dt.date.today().isoformat())
