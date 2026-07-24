# Design decisions

How the radar actually thinks, and why each piece is built the way it is — in plain English,
for anyone who wants to understand or change the machinery. Nothing here depends on any
particular infrastructure: the "smart" parts need an **embedding model** and a **chat LLM**
from any OpenAI-compatible API; everything else is free public scholarly APIs (OpenAlex,
Crossref, arXiv, OSF preprint servers, Semantic Scholar, Unpaywall, Zotero).

## The two-stage vocabulary: Gathering and Selection

The whole system reduces to two stages with deliberately different owners:

- **Gathering** casts the net. It is steered by your **seed papers** — a flat list of DOIs —
  and everything about it is *learned automatically*: which areas to search, which venues to
  trust, what your taste "looks like" numerically. You tune it by adding papers, never by
  maintaining keywords.
- **Selection** makes the call. It is steered by your **Selection Criteria**
  (`interest_profile.json`) — a page of *plain English you write yourself*: who you are,
  your interest "flavors," what you're explicitly not interested in, example papers you'd
  love or reject. An LLM judge reads every shortlisted paper against this page.

This split is the project's core design bet: numeric taste models are great at *recall*
(don't miss anything plausibly relevant) and terrible at *precision on intent* ("mentions
politics" vs. "is about politics"); a reading judge is the reverse. So the seeds do recall,
the judge does precision, and each is tuned in the medium it understands — papers for one,
prose for the other.

## Seeds (the Gathering profile)

- Taste is defined entirely by a flat list of DOIs (`seeds.txt`), all at equal weight.
  Seeds can be raw DOIs or pasted citations (resolved via Crossref; low-confidence matches
  are flagged). Lines starting with `#` are comments.
- A Zotero library can act as a live seed source. The sync is **append-only with a ledger**:
  adding to Zotero adds a seed; removing from Zotero never removes one; manual deletions
  from `seeds.txt` are never undone. Only scholarly item types sync; DOI-less items are
  resolved via Crossref only at match score ≥ 70.

From the seeds, a fully rebuildable profile is derived (nothing hand-maintained):

- **Concept tags**: each seed's OpenAlex concepts with score ≥ 0.3, excluding level 0–1 root
  concepts ("Political science", "Artificial intelligence" — they match everything). Keep
  the ~40 globally strongest, then union in each seed's top 2 so a small, newly added topic
  is guaranteed to register.
- **Auto-trusted venues**: any journal holding ≥ 2 seeds is trusted automatically, on top of
  a configurable starting list.
- **Seed authors** are recorded; their new papers get a quality bonus.
- **Seed embeddings**: each seed's title + abstract embedded once and cached.
- **Topic clusters**: seed embeddings grouped by spherical k-means into ~10 clusters (fixed
  random seed). Labels come from common title words; a rebuilt cluster inherits its old
  label when member overlap is high, so dashboard topic weights survive rebuilds. These
  clusters are what the dashboard's **Gathering** page shows.
- **Retrieval concepts**: the harvest list guarantees one distinct concept per cluster
  before filling remaining slots by global weight — otherwise small topics were never even
  fetched, and no amount of scoring could surface them.

## Gathering (the daily harvest)

- Complementary sources each morning: **OpenAlex** (one query per retrieval concept,
  newest-first, **cursor-paged up to 3,500 works per concept** — deep paging matters because
  broad concepts hold thousands of papers in a two-week window and late-indexed papers sit
  far down the list), **arXiv** (newest preprints in your configured categories), **OSF
  preprint servers** (e.g. SocArXiv, PsyArXiv), and **Semantic Scholar recommendations**
  ("papers similar to" the seed set).
- **Rolling 14-day window, re-scanned every day.** Databases index papers 2–5 days late; a
  wide window re-scanned daily catches them, and the no-repeat ledger makes overlap
  harmless. A strong paper that misses the cut on a busy day keeps re-competing.
- Affordable because **every paper is embedded exactly once, ever** (cached with a 35-day
  TTL, half-precision packed). Each cache entry also records whether the paper had an
  abstract when embedded: a paper first seen title-only is **re-embedded once its abstract
  arrives**, so stale title-only vectors never linger.
- API etiquette: a `mailto` identifier for OpenAlex/Crossref/Unpaywall's "polite pool,"
  sleeps between requests, retries with backoff. Every network step is best-effort — a
  source failing degrades the run, never kills it.

## Filtering (before ranking)

- Keep only article/preprint/posted-content types; drop front-matter, corrigenda,
  editorials, book reviews, and titles under 4 words; drop records dated more than 90 days
  in the future (bad metadata) while keeping modest advance-access dates.
- **Dedupe** on DOI (falling back to a title slug), preferring the record with an abstract.
- **Abstracts are never truncated mid-sentence**: sources are capped only at a pathological
  bound (8,000 chars, cut at a sentence end) because some records ship entire papers as the
  "abstract" field.
- **No-repeat ledger** (`seen.json`, 180-day TTL): every paper ever shown is excluded from
  future runs. This is what makes the wide rolling window safe.
- Anything you've 👎'd is hard-dropped before scoring.

## Ranking stage 1 — embedding relevance (the prefilter)

Every candidate is compared against the positive examples (seeds + 👍'd papers):

- Similarity to the **nearest k=3** positives, not the centroid — a paper close to one small
  seed cluster scores high even if it's far from everything else.
- **Contrastive correction**: subtract 0.3 × the candidate's similarity to *the day's
  average paper*. "Generic" should mean "generic," not "central to your interests" — an
  earlier version subtracted mean similarity to all seeds, which taxed papers at the very
  center of the taste profile; measured against blind-rated papers, the pool baseline put
  every top-rated paper comfortably inside the judge's queue while the seeds baseline
  dropped some out.
- **Dislike penalty**: a candidate closer to a 👎'd paper than to the likes is pushed down.
- Normalized across the day's pool (10th percentile → 0, max → 1) so the top papers keep
  distinct scores.
- Fallbacks: concept-tag overlap when no embedding is available; a Semantic Scholar
  recommendation gets a small additive bonus (an earlier hard floor could override the
  semantic verdict — floors that bypass a veto are a recurring bug class).

**This pass's only job is recall.** Its score decides who the judge reads, and it remains
the value the quality gates test — but for judged papers it no longer decides the ranking.

## Ranking stage 2 — the LLM judge (Selection)

The top ~1,000 abstract-bearing candidates by embedding relevance — plus two side channels:
the top few hundred by provisional overall score (so a paper riding venue quality still gets
read) and the nearest ~100 to *each flavor's description* (so a flavor your seeds cover
thinly still competes) — are each **read** by the chat LLM against your Selection Criteria.

- The prompt contains your core statement, your flavors, your not-interested list, your
  example titles, and your **most recent 👍/👎 votes** as boundary examples. The judge
  returns strict JSON: which flavors the paper substantively engages, a fit score 0–10, and
  a one-sentence reason. Temperature 0.
- The rubric treats **each flavor as already an intersection** ("LLMs for persuasion
  online," not "LLMs" + "persuasion" + "online"): a paper squarely inside ONE flavor is a
  bullseye (9–10); engaging a second flavor adds the tenth point; competent work on a mere
  *component* of a flavor caps around 4–6. This matters: rubrics that count matched topics
  let a mediocre three-topic paper outrank a brilliant two-topic one.
- For judged papers, **relevance = fit/10**, and the verdict (score, flavors, reason) is
  attached to the paper — it becomes the chip on the dashboard card and the *Fit* line in
  the briefing, so every pick is auditable.
- **Verdicts are cached per paper per profile version.** Each paper is judged once, ever;
  the daily cost is only what's new. Editing your criteria bumps the profile version
  (a date + content hash), which invalidates the cache — the next run re-judges the whole
  window under your new wording. That's the tuning loop: edit a paragraph, see the
  boundary move tomorrow.

*Why a judge and not a cross-encoder reranker?* The earlier design scored the shortlist
with a cross-encoder against auto-written interest statements. Measured on a 15,000-paper
pool, its scores turned out to be nearly **query-invariant** — a robotics paper scored 0.96
against "political discourse" — so it discriminated documents, not fit. In a blind rating
test, the judge's top-5 scored perfectly with the user while the cross-encoder pipeline's
did not. The judge costs a few hundred small chat calls a day (cached), replaces the
reranker entirely, and its one-sentence rationales double as the interface. (The legacy
reranker code remains only for the offline diagnostic scripts.)

## Quality, recency, gates, final score

- **Quality (0–1)**: prestige venues (Nature/Science-class, matched by name or prefix,
  never substring) = 0.9; your trusted venues = 0.7; on top of the tiers, OpenAlex's free
  per-journal citation stats slide any strong journal up a log scale (IF≈2 → ~0.31,
  10 → ~0.67, 20+ → 0.85); a returning seed author adds +0.3; capped at 1.0.
- **Recency: weight 0 by design.** The 14-day window already enforces freshness; a scored
  recency term mostly penalized good papers the databases indexed late.
- **Final score = 0.73 × relevance + 0.27 × quality** (configurable), then multiplicative
  **gates**: non-journal formats and (optionally) papers from outside a preferred-country
  list need strong topical fit to escape a heavy penalty. The gates test the *pre-judge
  embedding relevance* — a stable scale that doesn't shift when the judge or its profile
  changes. Dashboard per-topic ± weights apply last, as deliberately mild nudges.

## Selection of the day's briefing (the abstract gate + watchlist)

- Surface the top 5 per day (configurable), but **only papers with an abstract** — the only
  real content available at scoring time. A title-only record inflates every scoring method
  (measured: a cross-encoder once scored an astrophysics paper ~0.93 on every topic from
  its title alone), so title-only papers are never shown on a guess.
- A strong candidate with no abstract is **held on a watchlist** instead of shown or lost —
  advance-access papers are often listed days before their abstract is deposited. Held
  papers are re-checked daily and re-enter competition the moment the abstract appears
  (their cached title-only embedding is evicted so they're re-embedded with real content).
  The display scans past held papers so an abstract-less paper never costs you a slot.

## Writing (the briefing itself)

- Summaries are pitched at a one-sentence **audience statement** kept in config.
- **Two length regimes, chosen by what the model actually read.** If only the abstract is
  available, the entry is a plain-English restatement at roughly the abstract's own length
  (~100–180 words) — summarizing an abstract at 400 words is inflation, not information.
  Only when open-access full text was actually fetched and read does the entry run full
  depth (300–450 words: question → method → concrete results → why it matters). Full text
  is retrievable for roughly a third to a half of picks (arXiv PDF → Unpaywall → the
  record's own OA link; landing-page stubs are sniffed out and rejected).
- **Grounding rules are explicit in the prompt**: never invent methods, Ns, numbers, or
  effect sizes; when the abstract omits them, say so plainly. A shorter honest entry beats
  a longer speculative one.
- Each entry carries its judge verdict (*Fit: 9/10 · the flavors it engages*), the real
  link (DOI or OA URL), and — under the summary — the paper's actual abstract, whole.
- **Date honesty**: advance-access records are cited as "in press" with the online date.
- The dashboard cards reuse the briefing's summaries verbatim, so PDF and dashboard never
  disagree.

## Feedback loops (three of them)

- **Votes.** 👍 adds a positive example to the embedding pass AND appears in the judge's
  prompt as a recent example; 👎 hard-drops that paper forever, nudges down its embedding
  neighborhood, and appears in the judge's prompt as a rejection. Votes are deliberately
  the cheapest feedback: one click teaches both stages.
- **Downvote-cluster alert.** If several recent 👎s land nearest the *same* flavor, the
  radar says so — a banner on the dashboard and a note in the briefing suggesting a
  criteria edit, with an LLM one-liner naming what the rejected papers have in common. It
  re-fires daily while the pattern holds and clears itself when the evidence ages out.
- **Coverage audit (Gathering ↔ Selection drift).** The two stages can silently diverge:
  add seeds for a new project and Gathering adapts automatically, but the judge will veto
  the new area until your criteria mention it. So after each profile rebuild, every seed
  cluster's centroid is compared against the flavor descriptions; a sizable cluster no
  flavor covers triggers a **proposal** — an LLM-drafted flavor built from that cluster's
  papers, shown on the dashboard (accept prefills the form; you still review and save;
  dismiss is remembered) and noted in the briefing email until resolved. Proposals persist
  until acted on; they cannot be missed by being away for a few days.

## Timing & operational mechanics

- One cron run early each morning. Exact time is uncritical **by design**: the rolling
  window + no-repeat ledger decouple correctness from when the job fires — a skipped day's
  papers are still in-window tomorrow.
- Fixed run order: Zotero seed sync → harvest/rank/judge/select → full-text fetch → LLM
  writes the briefing → dashboard data build → PDF + email.
- Every stage is **best-effort with graceful degradation**: embeddings down → concept-tag
  relevance; judge unreachable → embedding relevance stands; full text unfetchable →
  short abstract-grounded summary; Zotero unconfigured → skipped. The briefing still goes
  out.
- All state (profile, clusters, caches, ledgers, watchlist, judge verdicts) is
  **rebuildable from `seeds.txt` + `interest_profile.json` + one command** — state files
  are disposable, never precious.
- All tunables live in one commented `config.toml`; your criteria live in
  `interest_profile.json` (or the dashboard's Selection Criteria page, same thing).
