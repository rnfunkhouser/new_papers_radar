# Design decisions

The pipeline's design decisions, independent of any particular infrastructure. Everything
below works the same whether the "smart" parts run on a campus LLM gateway, a locally hosted
model, or a commercial API — you need an **embedding model**, a **cross-encoder reranker**,
and a **chat LLM** (we use Qwen3-Embedding-8B, Qwen3-Reranker-8B, and whatever large chat
model the gateway offers; nothing depends on those specific models). Everything else is free
public scholarly APIs: OpenAlex, Crossref, arXiv, Semantic Scholar, Unpaywall, Zotero.

## Seeds (the taste profile)

- The user's taste is defined entirely by a flat list of DOIs (`seeds.txt`), all at equal
  weight. No hand-written keyword lists or topic descriptions.
- Seeds can be added as raw DOIs or as APA-style citations — citations are resolved via
  Crossref bibliographic search; matches under a confidence score of 60 are flagged for
  manual verification.
- A public Zotero library can act as a live seed source. The sync is **append-only with a
  ledger** of every Zotero item key and DOI ever processed: adding to Zotero adds a seed;
  removing from Zotero never removes a seed; manually deleting from `seeds.txt` is never
  undone by a re-sync. Only scholarly item types are synced (articles, chapters, preprints,
  reports, theses — not notes, attachments, web pages). DOI-less Zotero items are resolved
  via Crossref only when the match score is ≥ 70; otherwise skipped and logged.

From the seeds, a rebuildable profile is derived (nothing hand-maintained):

- **Concept tags**: each seed's OpenAlex concepts with score ≥ 0.3, **excluding level 0–1
  root concepts** ("Political science", "Artificial intelligence") — they match everything
  and drown out what actually distinguishes the interests. Keep the ~40 globally strongest
  concepts, then union in each seed's top 2 concepts so a small, newly added topic is
  guaranteed to register.
- **Auto-trusted venues**: any journal holding ≥ 2 seeds is trusted automatically, on top of
  a hand-curated starting list. The list grows itself as seeds grow.
- **Seed authors** are recorded; their new papers get a quality bonus.
- **Seed embeddings**: each seed's title + abstract (truncated to 2,000 chars) is embedded
  once and cached.
- **Topic clusters**: seed embeddings are grouped by spherical k-means into ~10 clusters
  (fixed random seed). Labels come from the most common content words in member titles, and
  a rebuilt cluster **inherits its old label** when member overlap (Jaccard) ≥ 0.4 — so
  topic weights and archive tags survive profile rebuilds. Clusters larger than ~13 seeds
  are subdivided into 2–3 sub-angles so a big topic is represented by its specific
  sub-interests, not its vague common theme.
- **Interest statements**: for each cluster (and each sub-angle), the LLM reads the member
  seeds and writes **2–3 sentences naming the mechanism/purpose that unites them** — not the
  surface topic ("narrative as a mechanism of persuasion", not "narrative") — plus the
  recurring methods, populations, or settings. These are regenerated on every profile
  rebuild and become the reranker's queries, so they are the one place the seeds' taste is
  verbalized; the extra sentences exist to carry more of that specificity.
- **Retrieval concepts**: the concept list used for harvesting guarantees **one distinct
  concept per cluster (and per sub-angle)** before filling remaining slots by global
  weight — otherwise small topics were never even fetched, and no amount of scoring could
  surface them.

## Gathering

- Three complementary sources each morning:
  - **OpenAlex**: one query per retrieval concept (22 concepts), `type:article`, sorted
    newest-first, **cursor-paged up to 3,500 works per concept** (200 per page). Deep paging
    matters: broad concepts hold thousands of papers in a 14-day window ("Narrative" ~5k,
    "Social media" ~2.7k when measured), and a paper OpenAlex indexes several days after its
    print date sits far down the newest-first list — a shallow cap silently truncates
    exactly the late-indexed papers the wide window exists to catch.
  - **arXiv**: the 120 newest preprints across the configured categories.
  - **Semantic Scholar recommendations**: "papers similar to" the seed set, using **all**
    seeds as positive examples (an even spread of 100 when there are more — using only the
    first N starved recently added seeds).
- **Rolling 14-day window, re-scanned every day.** Databases index papers 2–5 days after
  their publication date; a wide window re-scanned daily catches late-indexed papers, and
  the no-repeat ledger (below) makes the overlap harmless. It also lets a strong paper that
  missed the cut on a busy day keep re-competing.
- The wide window is affordable because **candidate embeddings are cached per paper**
  (35-day TTL, half-precision packed) — each paper is embedded exactly once, ever, so a day's
  marginal cost is only the genuinely new papers.
- API etiquette: a `mailto` identifier on OpenAlex/Crossref/Unpaywall calls (their "polite
  pool"), short sleeps between requests, and retries with backoff. Every network step is
  best-effort — a source failing degrades the run, never kills it.

## Filtering (before ranking)

- Keep only OpenAlex types `article` / `preprint` / `posted-content`.
- Drop noise by title: front-matter, corrigenda, errata, editorials, "review for…", indexes,
  and any title under 4 words.
- Drop records dated more than 90 days in the future (bad metadata); a modest future print
  date is legitimate advance access and is kept.
- **Dedupe** on DOI, falling back to a normalized title slug; when duplicates carry
  different metadata, prefer the record that has an abstract.
- **No-repeat ledger** (`seen.json`): every paper ever shown is recorded (180-day TTL) and
  excluded from future runs. This is what makes the wide rolling window safe, and it makes
  "show me more" trivial — a rerun naturally surfaces the next-best papers.
- Anything the user has 👎'd is hard-dropped by DOI/title before scoring.

## Ranking

Relevance is judged in two passes, then blended with quality and recency. The split exists
because the two techniques fail in opposite ways. Pass 1 compares embedding vectors — cheap
enough to score the entire pool (thousands of papers) and lossless about the seeds (each
candidate is compared against the actual seed vectors, never a verbal summary), but a
bi-encoder can only say "about the same stuff"; it can't tell *narrative used to persuade*
from *narrative studied in nursing education*. Pass 2 is a cross-encoder that reads a
candidate **together with** a statement of the interest and judges contextual fit — exactly
the angle/mechanism distinction pass 1 misses — but it's too expensive for the whole pool
and needs text queries. So: pass 1 does recall over everything and nominates a shortlist;
pass 2 does precision on the shortlist; the final relevance averages the two so neither
judgment alone can bury or crown a paper.

**Pass 1 — embedding relevance** (each candidate vs. the positive examples = seeds + 👍'd
papers):

- Similarity to the **nearest k=3** positives, not the centroid — so a paper close to one
  small seed cluster scores high even if it's far from everything else (cluster-aware by
  construction).
- **Contrastive correction**: subtract 0.3 × the mean similarity to *all* positives. A paper
  that is only generically close to everything scores below one specifically close to a few
  seeds.
- **Dislike penalty**: if a candidate is closer to a 👎'd paper than to the likes, subtract
  0.5 × the excess.
- Normalize across the day's pool: 10th percentile → 0, actual maximum → 1, so the top few
  papers keep distinct scores instead of all pinning at 1.0.
- Fallback when no embedding is available (no abstract, or the embedding service is down):
  weighted concept-tag overlap, saturating at ~3 strong concept matches.
- A Semantic Scholar recommendation gets a relevance floor of 0.7 (it was already judged
  similar to the seeds).

**Pass 2 — cross-encoder rerank** of the top 200 by embedding relevance:

- Each shortlisted candidate is judged against every interest statement (the 2–3 sentence
  cluster distillations); take the **max** over statements — a paper only needs to fit one
  interest, not all of them. For subdivided topics, only the specific sub-angle statements
  are used — the vague umbrella statement is dropped, so a generically on-topic paper can't
  ride in on the broad theme.
- Final relevance = **0.5 × embedding score + 0.5 × reranker score** (an even blend, so the
  direct-to-seeds vector judgment and the statement-mediated fit judgment count equally).
  Reranking the top 200 (not just the final top-N) means a paper that would sneak in via
  recency/quality still gets the "is this actually on-topic?" check.

**Quality** (0–1):

- Tiers: prestige venues (Nature/Science/APSR-class; matched by name or name-prefix so the
  whole Nature family counts) = 0.9; the user's trusted venues = 0.7. Venue matching is
  **equality or "name: subtitle"** after stripping a leading "The " — never substring, which
  produced false matches.
- **Sliding impact scale** on top of the tiers: OpenAlex's free per-journal
  `2yr_mean_citedness` (≈ impact factor) is mapped log-scale onto 0–0.85
  (IF≈2 → ~0.31, 5 → ~0.50, 10 → ~0.67, 20+ → 0.85); final venue quality =
  max(tier, impact). A strong journal in neither list still gets credit; the personal lists
  remain a floor. Stats are batch-fetched and cached 90 days.
- A returning seed author adds +0.3. Total capped at 1.0.

**Recency**: linear decay over 14 days; a future (advance-access) date clamps to
maximally fresh, never above 1.0.

**Blend**: score = 0.50 × relevance + 0.35 × quality + 0.15 × recency (configurable).

**Gates** (multiplicative penalties escaped only by strong topical fit):

- **Type gate**: journal articles and preprints (arXiv exempt from penalty) are the default
  diet. Proceedings/conference papers, repository working papers, and book reviews take a
  ×0.75 haircut when relevance ≥ 0.85 and ×0.40 below it — effectively vanishing unless
  really well aligned.
- **Geo gate**: papers whose author affiliations are *entirely* outside a preferred-country
  list need relevance ≥ 0.90 or take ×0.55. Papers with unknown affiliations (arXiv, S2
  recs) pass untouched. Set the list empty to disable.
- **Topic weights**: per-topic ± multipliers from the dashboard, deliberately mild nudges
  applied to the final score.

## Selection (the abstract gate + watchlist)

- Surface the top 5 per day (configurable), but **only papers with real content**: an
  abstract, or open-access full text that *actually downloads* — verified by performing the
  real fetch, not by trusting that an OA URL is listed (listed-but-dead links were showing
  empty stubs).
- Scan down the ranking (up to 40 deep) until the slots are filled.
- A strong candidate with no abstract and no fetchable full text is **held on a watchlist**
  instead of shown or lost — advance-access papers are often listed days before their
  abstract is deposited. Held papers are re-checked daily (capped at 60 lookups/run,
  30-day TTL) and **re-injected to compete the moment the abstract appears**. Status
  tracking (held/ready/done/expired) gives real backfill-rate stats.
- Shown papers are recorded in the ledger at selection time.

## Writing (LLM-agnostic prompt decisions)

- Summaries are pitched at a one-sentence **audience statement** kept in config.
- Fixed structure per paper: background/question → method → concrete results → why it
  matters, as flowing prose, ~300–450 words.
- **Grounding rules are explicit in the prompt**: never invent methods, Ns, numbers, or
  effect sizes; when the abstract omits them, say so plainly; a paper with no abstract gets
  at most a short, labeled 1–2 paragraph entry. A shorter honest entry beats a longer
  speculative one.
- **Full text when available**: before writing, open-access full text is fetched (routes in
  order: arXiv PDF → Unpaywall best OA location → the record's own OA URL; content sniffed
  by magic bytes, HTML under 15 KB rejected as a landing/paywall stub, 30 MB cap). Entries
  grounded in full text may run up to ~75% longer, but only when the paper genuinely
  contains extra insight — never padded.
- **Date honesty**: advance-access records carry future print dates; those are cited as
  "in press" with the online date, never presented as published-on-a-future-date.
- Every entry must link to the real article (doi.org, else the OA URL) — the standing
  defense against summary hallucination.
- Papers are ordered best-first; if fewer than the quota clear the bar, fewer are shown.
- The dashboard cards reuse the briefing's summaries **verbatim** so PDF and dashboard never
  disagree.

## Feedback loop

- Deliberately **asymmetric**: 👍 adds a positive example (acts as a bonus seed in the
  embedding relevance pass); 👎 hard-drops that paper forever and only gently nudges down
  its embedding neighborhood. Upvotes add signal; downvotes mostly just remove.
- Voted papers' embeddings are computed once and cached.

## Timing & operational mechanics

- One cron run early each morning (before the workday). Exact time is uncritical **by
  design**: the rolling window + no-repeat ledger decouple correctness from when the job
  fires or whether a day is missed — a skipped day's papers are still in-window tomorrow.
- Fixed run order: Zotero seed sync → harvest/rank/select → full-text fetch → LLM writes
  the briefing → dashboard data build → PDF + email.
- Every stage after harvest is **best-effort with graceful degradation**: embeddings down →
  concept-tag relevance; reranker down → embedding relevance stands; full text unfetchable →
  abstract-grounded summary; Zotero unconfigured → skipped. The briefing still goes out.
- All state (profile, clusters, interest statements, caches, ledgers, watchlist) is
  **rebuildable from `seeds.txt` + one command**, so state files are disposable and never
  precious.
- All tunables live in one commented config file; scripts read them from there rather than
  hardcoding.
