# Daily Papers Radar

[note: I very much 'vibe-coded' this project, and while it's working great for me, you may find issues 
with it or with this (largely) AI generated documentation]

This helps you build a personal research radar. Every morning it reads the day's new scholarly 
papers across several databases, figures out which ones *you* would actually care about — learned 
from a set of papers you already like — and emails you a short, well-written briefing of the best
few, with a companion web dashboard you can browse and rate.

It was originally built for my field as a political-communication researcher, but **nothing about the
machinery is specific to that field** — it learns your taste from your own seed papers. A
few field defaults (which journals to trust, which arXiv sections to scan) ship tuned for
political communication and live in one plain-text file, [`config.toml`](config.toml), for
you to change to your field or ignore.

> **New here?** Start with **[SETUP_GUIDE.md](SETUP_GUIDE.md)** — a step-by-step,
> plain-language walkthrough written for faculty, no computer-science background assumed.
> This README explains *how the radar thinks*; the guide explains *how to stand it up*.
> To see what a morning briefing actually reads like, skim the
> **[sample briefing](sample_briefing_2026-06-29.md)**.

This is a **template**. It is designed for University of Idaho faculty: the "smart" AI parts
run on **MindRouter**, the campus LLM gateway, and the daily job runs on a small **campus
virtual machine** that you request from Research Computing & Data Services (`rcds@uidaho.edu`).
The setup guide walks through all of that.

---

## The one-paragraph version

You give it a **seed set** of papers you love (it can pull these straight from a Zotero
library). It turns those into a numerical "taste profile." Each morning it **gathers** a few
thousand recent papers from OpenAlex, arXiv, and Semantic Scholar; **filters** out the junk;
**ranks** what's left by how close each paper sits to your taste; **selects** the top handful
that clear a quality bar; **writes** each one up in a consistent house style using a campus
LLM; and **delivers** a PDF by email plus a searchable dashboard. It remembers what it has
already shown you, and it learns from your 👍/👎 ratings.

---

## How it works, stage by stage

### 0. Your taste profile (built once, refreshed when seeds change)

Everything starts from `seeds.txt`, a list of papers you consider "this is my kind of work."
From the seeds the system builds `seeds_profile.json`, which contains:

- **Concepts** — the OpenAlex topic tags your seeds carry most often. These become the
  *search terms* used to gather.
- **Trusted venues** — any journal where ≥2 of your seeds appeared is auto-added to a
  "quality on sight" list, so the list self-updates as you add seeds.
- **Seed authors** — authors who recur in your seeds get a small relevance bump.
- **Interest statements** — short natural-language descriptions of each topic cluster, written
  by an LLM, used later to re-judge relevance in context (see Rank).
- **Seed embeddings** — each seed paper is turned into a numeric "meaning fingerprint"
  (an *embedding*) and cached, so we only ever compute it once.

Big topics get **sub-angles**: if a cluster has many seeds it tends to blur into a generic
theme, so it's split into 2–3 more specific sub-statements that judge papers on your
*specific* sub-interests rather than the vague common theme.

### 1. Gather — cast a wide net

For each of your top concepts, the system asks **OpenAlex** for recent papers tagged with
that concept, newest first. It also pulls recent **arXiv** preprints (from the categories you
list in `config.toml`) and asks **Semantic Scholar** to "recommend papers similar to my seeds."

The cost of gathering more is kept low by **caching every paper's embedding**, so a wide
window re-scanned daily only pays to embed the papers that are *genuinely new since yesterday*.

### 2. Filter — throw out what you'd never want

Before spending any effort ranking, obvious non-starters are dropped: non-articles (book
front-matter, corrigenda, reviews), duplicates (merged, keeping whichever copy has an
abstract), anything you 👎'd, and anything already shown (a ledger, `seen.json`, records
everything you've been sent; entries expire after 180 days).

### 3. Rank — how close is this to *your* taste?

This is the heart of it, and it works in two passes:

1. **Embedding relevance.** Every surviving candidate is compared (cosine similarity) to your
   *nearest* seed papers. A **contrastive** tweak favors papers that are *specifically* close
   to a few seeds over ones that are only *generically* close; a **downvote penalty** pushes
   down papers that resemble things you disliked.
2. **Cross-encoder rerank.** The top candidates are re-judged by a reranker model that reads
   each paper *together with* your interest statements and scores contextual fit.

The relevance number is combined with **quality** (prestige venues, your trusted journals,
measured citation impact, a returning-author bump), **recency**, and **gates** (a paper from
outside your preferred regions, or of a non-article type, must be exceptionally on-topic to
escape a scoring penalty). All of these weights and thresholds live in `config.toml`.

### 4. Select — only show papers with real content

Going down the ranked list, the system fills the day's slots — but it will only *show* a paper
it has real content for: an abstract, or fetchable open-access full text. A strong paper with
neither is **held on a watchlist** and re-checked daily; the moment its abstract appears, it
re-enters and competes (since databases often list before an abstract is added, then update 
with the abstact or full paper later.

### 5. Write — a consistent house style

For each selected paper, the campus LLM (via MindRouter) writes a full-depth summary — the
question, the method, the concrete results, and why it matters — grounded in the abstract or,
when available, the actual open-access full text. It is explicitly instructed **never to
invent** methods or numbers the source doesn't state, though that may still be a risk with 
LLMs, something that's combatted by giving you the link to the real article.

### 6. Deliver — email + dashboard

A clean, journal-styled **PDF** is built (pure standard library) and **emailed**. A **web
dashboard** shows the same briefing, lets you search the archive, and lets you rate papers
👍/👎 — those ratings feed straight back into ranking.

---

## Configuration — one file

Almost everything you'd tune lives in **[`config.toml`](config.toml)**: your email, your
field's journals and arXiv categories, the scoring weights, and the gate thresholds. It is
heavily commented; the only value you *must* set is your email. See the
[SETUP_GUIDE](SETUP_GUIDE.md) for the three private files that hold secrets
(`mindrouter.json`, `.briefing_env`, `zotero.json`) — each has a committed `.example`
template you copy and fill in.

## Files

| File | Role |
|---|---|
| `config.toml` | **Your settings** — email, field journals, arXiv cats, scoring knobs (edit this) |
| `config.py` | Loads `config.toml` (pure stdlib) and exposes the values to the scripts |
| `seeds.txt.example` | Template for your seed list — copy to `seeds.txt` and fill in |
| `harvest.py` | The core: gather, filter, rank, score, select; profile building; Zotero sync |
| `embeddings.py` | MindRouter client — embeddings, chat, rerank; embedding caches |
| `write_briefing.py` | Writes the briefing prose via MindRouter |
| `deliver.py` | Markdown → PDF → email |
| `pdfgen.py` | Pure-stdlib Markdown→PDF (journal look) |
| `dashboard.py` | The web dashboard (stdlib server), with 👍/👎 rating |
| `fetch_fulltext.py` | Open-access full-text fetcher (arXiv / Unpaywall / OA url) |
| `run_daily.sh` | The morning routine, in order |
| `rank_report.py`, `diagnose_paper.py` | Read-only diagnostics: where/why a paper ranks |
| `Dockerfile`, `docker-compose.yml`, `entrypoint.sh`, `crontab` | Container: cron + dashboard |
| `deploy.sh` | Push code to the VM and rebuild the container (never touches saved state) |
| `vm_recon.sh` | Read-only preflight to check a new VM can reach the needed services |
| `com.example.papersradar*.plist` | launchd jobs for the "always-on Mac" alternative to the VM |
| `*.example` | Templates for the private config files (copy and fill in) |
| `sample_briefing_2026-06-29.md` | A real sample of what a morning briefing reads like |

State files (git-ignored, rebuildable): `seeds_profile.json`, `clusters.json`, `interests.json`,
`*_embeddings.json`, `seen.json`, `watchlist.json`, `venue_stats.json`, `feedback.json`,
`briefings/`, `fulltext/`, `logs/`.

## Running it

```bash
python3 harvest.py --build-profile        # build the taste profile from seeds.txt (once)
python3 harvest.py                         # a manual harvest (uses config.toml defaults)
bash run_daily.sh                          # the whole morning routine
./deploy.sh                                # push code to the VM + rebuild the container
```

## The knobs worth tuning

All live in [`config.toml`](config.toml), fully commented — `days_window`, `top_n`,
`openalex_concepts`, `openalex_max_per_concept`, the scoring weights, and the geo/type gate
thresholds. Change a value, save, and re-run `python3 harvest.py --build-profile`.

---

*Requires Python 3.11+ (for the standard-library `tomllib`). No third-party Python packages.*
