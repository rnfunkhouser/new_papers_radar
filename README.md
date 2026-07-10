# Daily Papers Radar

_[note: I very much 'vibe-coded' this project, and while it's working great for me, you may find issues 
with it or with this (largely) AI generated documentation]_

This helps you build a personal research radar. 

I was sick of relying on really imprecise Goolgle Scholar alerts or the luck of seeing a relevant new 
article get shared by a colleague on social media. I wanted a more rigorous way to ensure I was actually 
seeing the new pubs in my space. Enter the research radar.

Every morning it reads the day's new scholarly papers across several databases, figures out which ones 
*you* would actually care about — learned from a set of papers you already like — and emails you a 
short, well-written briefing of the best few, with a companion web dashboard you can browse and rate.

## 👉 Start here: the [SETUP_GUIDE](SETUP_GUIDE.md)

**[SETUP_GUIDE.md](SETUP_GUIDE.md)** is the step-by-step walkthrough that takes you from zero to
a working radar — that's the document to have open while you set up. This README explains *how
the radar thinks*. To see what a morning briefing actually reads like, skim the
**[sample briefing](sample_briefing_2026-06-29.md)**.

It was originally built for my field as a political-communication researcher, but **nothing about the
machinery is specific to that field** — it learns your taste from your own seed papers. A
few field defaults (which journals to trust, which arXiv sections to scan) ship tuned for
political communication and live in one plain-text file, [`config.toml`](config.toml), but the setup 
guide walks you through how to make this tuned to you.

This is a **template**, designed for University of Idaho faculty: the "smart" AI parts run on
**MindRouter**, the campus LLM gateway, and the daily job runs on a small **campus virtual
machine** that you request from Research Computing & Data Services (RCDS — reachable at
`rcds` at `uidaho` dot `edu`). The setup guide walks through all of that. Though non-UIdaho faculty
could likely adapt this with little trouble so long as you have your own local LLM and server 
hosting.

---

## How it works

### 0. Your taste profile

Everything starts from `seeds.txt`, a list of papers you consider "this is my kind of work."
From those the radar builds a profile: the topic concepts it will search, journals to trust
(any venue holding ≥2 of your seeds), recurring authors to favor, short statements of each
interest cluster, and a cached numeric "meaning fingerprint" (an *embedding*) of every seed.
You can also **link your seeds to a Zotero library** to make the list dynamic — add a paper to
the library and it becomes a seed at the next sync (`python3 harvest.py --sync-zotero`).

### 1. Gather

Each morning it pulls a few thousand recent papers: **OpenAlex** results for each of your top
concepts (paged deep, over a rolling ~2-week window, so papers the databases index late still
get seen), fresh **arXiv** preprints from your categories, and **Semantic Scholar**'s
"papers similar to your seeds." Embeddings are cached, so re-scanning a wide window each day
only pays for what's genuinely new.

### 2. Filter

Obvious non-starters are dropped before ranking: non-articles (book front-matter, corrigenda,
reviews), duplicates, anything you 👎'd, and anything you've already been shown (tracked in a
ledger, `seen.json`).

### 3. Rank

Two passes. First, each candidate is scored by embedding similarity to your *nearest* seed
papers, with a penalty for resembling papers you've disliked. Then a reranker model re-judges
the top candidates against your interest statements in context. That relevance score is
blended with venue/author quality, citation impact, and recency — all the weights live in
`config.toml`.

### 4. Select

The day's slots are filled from the top of the ranking, but only with papers that have real
content — an abstract or fetchable open-access full text. A strong paper without one is held
on a watchlist and re-enters the moment its abstract appears (databases often list a paper
days before adding its abstract).

### 5. Write

For each pick, the campus LLM writes a summary — question, method, concrete results, why it
matters — grounded in the abstract or, when available, the full text, and explicitly
instructed never to invent methods or numbers. Hallucination is still an inherent LLM risk,
which is why every entry links to the real article.

### 6. Deliver

A journal-styled **PDF** lands in your inbox, and the **web dashboard** shows the same
briefing plus a searchable archive and 👍/👎 buttons — those ratings feed straight back into
ranking.

---

## Configuration — one file

Almost everything you'd tune lives in **[`config.toml`](config.toml)**: your email, your
field's journals and arXiv categories, the scoring weights and thresholds. It is heavily
commented; the only value you *must* set is your email. See the
[SETUP_GUIDE](SETUP_GUIDE.md) for the three private files that hold secrets
(`mindrouter.json`, `.briefing_env`, `zotero.json`) — each has a committed `.example`
template you copy and fill in. After changing config or seeds, re-run
`python3 harvest.py --build-profile`.

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

---

*Requires Python 3.11+ (for the standard-library `tomllib`). No third-party Python packages.*
