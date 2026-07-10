#!/usr/bin/env python3
"""
harvest.py — Daily personal research-paper radar.

Pulls recent papers across OpenAlex, Crossref, arXiv, and Semantic Scholar,
scores them against an interest profile derived from your seed set, filters out
noise (book front-matter, non-articles, corrigenda), and writes a ranked
candidate list that write_briefing.py turns into the daily briefing.

Needs direct outbound access to those scholarly APIs (any normal terminal,
the campus VM, or the Docker container all work).

Usage:
    python harvest.py --build-profile      # one-time: build interest profile from seeds.txt
    python harvest.py --days 3 --top 5      # daily: harvest, score, write candidates.json + .md
"""

import argparse, json, math, re, ssl, sys, time, datetime as dt
from pathlib import Path
from urllib.parse import quote
import urllib.request

import config


def _ssl_context():
    """Stock macOS python.org builds often ship without a CA bundle (every HTTPS call
    then fails with CERTIFICATE_VERIFY_FAILED) unless 'Install Certificates.command'
    was run. Heal that with zero dependencies: prefer certifi if present, then fall back
    to the macOS system bundle, then the default. This is still real verification — not
    a bypass."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass
    for cafile in ("/etc/ssl/cert.pem", "/private/etc/ssl/cert.pem"):
        if Path(cafile).exists():
            try:
                return ssl.create_default_context(cafile=cafile)
            except Exception:
                pass
    return ssl.create_default_context()

SSL_CTX = _ssl_context()

HERE = Path(__file__).parent
SEEDS = HERE / "seeds.txt"
PROFILE = HERE / "seeds_profile.json"
SEEN = HERE / "seen.json"          # ledger of papers already shown, so nothing repeats
SEEN_TTL_DAYS = 180                # forget shown papers after this, to bound the file
MAILTO = config.MAILTO   # OpenAlex/Crossref "polite pool" — faster, more reliable

# ----------------------------------------------------------------------------
# Quality scaffolding. The tunable values below come from config.toml (loaded via
# config.py) — edit config.toml to change them, not this file.
# ----------------------------------------------------------------------------

# Venues you trust on sight — the QUALITY signal (worth +0.7). Two parts:
#   1) This curated static list (you pruned it from your seed venues).
#   2) Auto-derived: any journal where >=2 of your seeds appear is added at build_profile
#      time (stored in seeds_profile.json -> trusted_venues), so the list self-updates as
#      you add seeds / sync Zotero. The static list below mainly carries the single-seed
#      venues you chose to keep (which the >=2 rule wouldn't catch on its own).
# Matching is equality or 'name: subtitle' (NOT loose substring) — entries are lowercased
# OpenAlex display names with any leading "the " stripped.
VENUE_ALLOWLIST = config.VENUE_ALLOWLIST

# Titles that are almost never a real research finding.
NOISE_TITLE = re.compile(
    r"^(contents|references|index|acknowledg|introduction|conclusion|"
    r"title pending|review for|corrigendum|erratum|editorial|"
    r"front matter|back matter|list of)", re.I)

# OpenAlex work types we keep.
GOOD_TYPES = {"article", "preprint", "posted-content"}

# arXiv categories worth scanning for this field.
ARXIV_CATS = config.ARXIV_CATS

WEIGHTS = config.WEIGHTS   # quality upweighted per user

# Prestige tier — journals so strong that topical fit alone should surface them, even
# though they aren't in your personal venue lists. Worth q=0.9 (your own venues: 0.7).
# Matched by equality or prefix (so the whole Nature family counts).
PRESTIGE_VENUES = config.PRESTIGE_VENUES

# Geography — US + major-European (plus other anglophone research systems) author bases
# are preferred; papers from elsewhere must be a STELLAR topical fit to surface.
PREFERRED_COUNTRIES = config.PREFERRED_COUNTRIES
GEO_GATE_REL = config.GEO_GATE_REL          # relevance a non-preferred-country paper needs to escape the gate
GEO_GATE_PENALTY = config.GEO_GATE_PENALTY      # score multiplier when it doesn't

# Publication type — journal articles and preprints are the default diet; proceedings,
# repository working papers, book reviews etc. must be REALLY well aligned to appear.
TYPE_GATE_REL = config.TYPE_GATE_REL
TYPE_GATE_PENALTY = config.TYPE_GATE_PENALTY     # harsh: below-threshold non-journal items effectively vanish
BOOK_REVIEW_RE = re.compile(r"^\s*(book review|review of\b|review essay)", re.I)

# Harvest breadth — how big a candidate pool to pull before scoring. Scoring is local
# and cheap, so a larger pool just means more thorough coverage (you'll still only see
# --top of them). With a few days' window the real raw count lands well below these caps.
OPENALEX_CONCEPTS = config.OPENALEX_CONCEPTS      # concepts queried per run — enough for 1 guaranteed slot per
                            # cluster (10) plus a dozen filled by global weight (was 14)
OPENALEX_PER_PAGE = config.OPENALEX_PER_PAGE     # papers per page; OpenAlex free-tier maximum (was 150)
OPENALEX_MAX_PER_CONCEPT = config.OPENALEX_MAX_PER_CONCEPT   # page THIS deep per concept (via cursor), not just one page.
                            # A big concept like "Politics" gets ~280 NEW papers/day, served
                            # newest-first. A relevant paper whose publication_date sits at the
                            # OLD edge of the window — common, because OpenAlex often indexes a
                            # paper several days AFTER its print date — lands far down that list
                            # and was silently truncated by the old single-page 150 cap. Paging
                            # deeper is the free fix for that GATHERING-stage recall gap; the
                            # extra embedding cost is bounded by the doc_embeddings cache (each
                            # paper is embedded exactly once, ever).
ARXIV_MAX = config.ARXIV_MAX             # arXiv preprints to scan (was 40)

# Conference proceedings are nudged below full journal articles: a genuinely strong
# proceedings paper still surfaces, it just takes a 25% haircut on its final score.
REL_NORM = config.REL_NORM              # relevance hits 1.0 at ~3 of your strongest concepts matched
PROCEEDINGS_PENALTY = config.PROCEEDINGS_PENALTY
PROCEEDINGS_MARKERS = ("proceedings", "conference", "symposium", "workshop", "amcis",
                       "hicss", "aisel.aisnet.org", "treos", "annual meeting",
                       "companion of", "extended abstracts",
                       "electronic literature", "stars.library.ucf.edu", "elo20")

# ----------------------------------------------------------------------------
# HTTP helpers
# ----------------------------------------------------------------------------

def get_json(url, tries=3, pause=1.0, headers=None):
    hdrs = {"User-Agent": f"paper-harvester (mailto:{MAILTO})"}
    if headers:
        hdrs.update(headers)
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=30, context=SSL_CTX) as r:
                return json.load(r)
        except Exception as e:
            if i == tries - 1:
                print(f"  ! failed {url[:80]}... ({e})", file=sys.stderr)
                return None
            time.sleep(pause * (i + 1))

def get_text(url, tries=3, pause=1.0):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": f"paper-harvester (mailto:{MAILTO})"})
            with urllib.request.urlopen(req, timeout=30, context=SSL_CTX) as r:
                return r.read().decode("utf-8", "replace")
        except Exception:
            if i == tries - 1:
                return None
            time.sleep(pause * (i + 1))

# ----------------------------------------------------------------------------
# 1. Build interest profile from the seed set (OpenAlex concepts)
# ----------------------------------------------------------------------------

PROFILE_TOP_CONCEPTS = 40      # global concept cap (was 25)
PER_SEED_GUARANTEE = 2         # always keep each seed's top-N concepts, even if globally weak

def build_profile():
    dois = [l.strip() for l in SEEDS.read_text().splitlines() if l.strip() and not l.lstrip().startswith("#")]
    concept_w, author_ids, seed_oa_ids = {}, {}, []
    per_seed_top = []          # each seed's strongest concept ids, so new topics survive the cap
    seed_concepts = {}         # {doi_lower: [ranked concept ids]} for per-cluster retrieval
    seed_texts = {}            # {doi_lower: "title. abstract"} for semantic embeddings
    seed_titles = {}           # {doi_lower: title} for labeling topic clusters
    venue_counts = {}          # {journal_name_lower: n seeds} -> auto-trusted at >=2
    print(f"Building profile from {len(dois)} seed DOIs ...")
    for doi in dois:
        w = get_json(f"https://api.openalex.org/works/doi:{quote(doi)}?mailto={MAILTO}")
        if not w:
            continue
        seed_oa_ids.append(w["id"])
        abstract = reconstruct_abstract(w.get("abstract_inverted_index"))
        seed_texts[doi.lower()] = ((w.get("title") or "") + ". " + abstract).strip()[:2000]
        seed_titles[doi.lower()] = (w.get("title") or "").strip()
        src = (w.get("primary_location") or {}).get("source") or {}
        if src.get("type") == "journal" and src.get("display_name"):   # journals only
            vn = src["display_name"].lower().strip()
            vn = vn[4:] if vn.startswith("the ") else vn
            venue_counts[vn] = venue_counts.get(vn, 0) + 1
        these = []
        for c in w.get("concepts", []):
            # Skip OpenAlex's broad root concepts (level 0-1: "Political science",
            # "Computer science", "Artificial intelligence"...). They match almost any
            # paper in the neighborhood and drown out what actually defines your interests.
            if c["score"] >= 0.3 and c.get("level", 0) >= 2:
                concept_w[c["id"]] = concept_w.get(c["id"], 0) + c["score"]
                these.append((c["id"], c["score"]))
        these.sort(key=lambda x: -x[1])
        per_seed_top.extend(cid for cid, _ in these[:PER_SEED_GUARANTEE])
        seed_concepts[doi.lower()] = [cid for cid, _ in these]
        for a in w.get("authorships", []):
            au = a.get("author", {})
            if au.get("id"):
                author_ids[au["id"]] = au.get("display_name", "")
        time.sleep(0.2)
    # Keep the globally strongest concepts, then union in each seed's top concepts so a
    # small new topic (a few freshly added seeds) is guaranteed to register, instead of
    # being drowned out by the larger existing clusters. Every seed still counts equally.
    top = dict(sorted(concept_w.items(), key=lambda x: -x[1])[:PROFILE_TOP_CONCEPTS])
    for cid in per_seed_top:
        if cid not in top:
            top[cid] = concept_w[cid]
    trusted_venues = sorted(v for v, n in venue_counts.items() if n >= 2)
    prof = {"concepts": top, "seed_authors": author_ids, "trusted_venues": trusted_venues,
            "seed_openalex_ids": seed_oa_ids, "built": dt.date.today().isoformat()}
    PROFILE.write_text(json.dumps(prof, indent=2))
    print(f"  saved {len(top)} concepts, {len(author_ids)} seed authors -> {PROFILE.name}")
    print(f"  auto-trusted venues (>=2 seeds): " + (", ".join(trusted_venues) or "none yet"))
    # Update the semantic-embedding cache for the seeds (best-effort: needs MindRouter /
    # the campus VPN; if it's unreachable we just keep the concept-tag profile).
    try:
        import embeddings
        if embeddings.available():
            cache, n_new = embeddings.update_seed_cache(seed_texts, {d.lower() for d in dois})
            print(f"  seed embeddings: {len(cache)} cached ({n_new} new) via MindRouter")
            (HERE / "seed_titles.json").write_text(json.dumps(seed_titles))
            clusters = build_clusters(cache, seed_titles)
            if clusters:
                # Guarantee retrieval coverage: give EVERY cluster (even a 2-3 seed one)
                # its own OpenAlex query concept, so a niche topic's matches are actually
                # fetched — then fill remaining slots by global weight. Without this, small
                # topics were never pulled from OpenAlex and nearest-seed scoring never saw them.
                prof["retrieval_concepts"] = _retrieval_concepts(clusters, seed_concepts, top, cache)
                PROFILE.write_text(json.dumps(prof, indent=2))
                synthesize_interests(clusters, seed_texts, cache)
    except embeddings.EmbeddingsUnavailable as e:
        print(f"  (seed embeddings skipped: {e})")
    except Exception as e:
        print(f"  (seed embeddings skipped: {type(e).__name__}: {e})")
    return prof

# ----------------------------------------------------------------------------
# Topic clusters — group the seeds into interest areas (for the dashboard's per-topic
# view and to see balance). Built from seed embeddings via k-means; labeled by the most
# common content words in each cluster's seed titles.
# ----------------------------------------------------------------------------

SUBDIVIDE_THRESHOLD = config.SUBDIVIDE_THRESHOLD    # a cluster larger than this is split into 2-3 sub-angles, so the
                            # reranker and retrieval drill past a big topic's vague common
                            # theme ("anything with a chatbot") to its specific sub-interests.
                            # Small clusters (below this) are left whole and coherent.

def _subdivide(members, cache):
    """Split a large cluster's member DOIs into 2-3 sub-groups by embedding (spherical
    k-means), for sharper interest statements and retrieval concepts. Returns a list of
    member-DOI lists — always [members] for a small/uncacheable cluster, so callers can
    treat the single-group and multi-group cases uniformly."""
    if not cache:
        return [members]
    valid = [d for d in members if d in cache]
    if len(valid) <= SUBDIVIDE_THRESHOLD:
        return [members]
    import embeddings
    k = min(3, max(2, round(len(valid) / 8)))          # 14-19 -> 2, ~20+ -> 3
    _, assign = embeddings.kmeans([cache[d] for d in valid], k)
    groups = {}
    for d, a in zip(valid, assign):
        groups.setdefault(a, []).append(d)
    subs = [g for g in groups.values() if len(g) >= 2]  # a 1-paper sub-angle has no throughline
    return subs if len(subs) >= 2 else [members]        # fall back if the split degenerated

def _retrieval_concepts(clusters, seed_concepts, weighted, cache=None):
    """Ordered concept list for OpenAlex harvesting: one distinct concept guaranteed per
    cluster (its most characteristic among member seeds) — or per SUB-angle for a large,
    subdivided cluster — then filled by global weight. So harvest_openalex's
    [:OPENALEX_CONCEPTS] spans every topic and every drilled-down sub-topic, small ones
    included."""
    from collections import Counter
    picked = []
    for cl in clusters:
        for group in _subdivide(cl.get("members", []), cache):
            cc = Counter()
            for d in group:
                for rank, cid in enumerate(seed_concepts.get(d, [])[:3]):
                    cc[cid] += 3 - rank
            for cid, _ in cc.most_common(6):
                if cid not in picked:
                    picked.append(cid)
                    break
    for cid in weighted:                       # fill remaining slots with strongest overall
        if cid not in picked:
            picked.append(cid)
    return picked

CLUSTERS = HERE / "clusters.json"
N_CLUSTERS = config.N_CLUSTERS             # the "top 10 topical areas" shown on the dashboard /topics page
TOPIC_WEIGHTS = HERE / "topic_weights.json"   # {label: multiplier} from dashboard ± buttons

def load_topic_weights():
    if TOPIC_WEIGHTS.exists():
        try:
            return {k: float(v) for k, v in json.loads(TOPIC_WEIGHTS.read_text()).items()}
        except Exception:
            pass
    return {}
_LABEL_STOP = set(
    "the a an of and or in on to for with from into via using study analysis based new "
    "approach role effects effect between among across toward within without paper research "
    "case review essay theory model evidence data social media online digital among about "
    "are how why what when does do is be as at by it its their our your his her they we you "
    "this that these those not no more most less than then so but can may will toward use".split())

def _cluster_label(titles):
    from collections import Counter
    words = Counter()
    for t in titles:
        for w in re.findall(r"[a-zA-Z][a-zA-Z\-]{3,}", (t or "").lower()):
            if w not in _LABEL_STOP:
                words[w] += 1
    top = [w for w, _ in words.most_common(3)]
    return " / ".join(top) if top else "misc"

def build_clusters(cache, seed_titles):
    """Cluster seed embeddings into topics, label each, save centroids to clusters.json.
    LABEL INHERITANCE: a new cluster whose members mostly overlap an old cluster keeps the
    old label — so topic weights, card numbering, and archived topic tags survive rebuilds
    instead of being orphaned every time a few seeds are added."""
    import embeddings
    dois = list(cache.keys())
    vecs = [cache[d] for d in dois]
    if len(vecs) < N_CLUSTERS:
        return
    old = []
    if CLUSTERS.exists():
        try:
            old = json.loads(CLUSTERS.read_text()).get("clusters", [])
        except Exception:
            old = []
    centroids, assign = embeddings.kmeans(vecs, N_CLUSTERS)
    clusters, used_labels = [], set()
    for ci in range(len(centroids)):
        members = [dois[i] for i in range(len(dois)) if assign[i] == ci]
        label = _cluster_label([seed_titles.get(d, "") for d in members])
        # inherit the old label with the strongest member overlap (Jaccard >= 0.4)
        best, best_j = None, 0.4
        mset = set(members)
        for oc in old:
            oset = set(oc.get("members", []))
            if not oset or oc.get("label") in used_labels:
                continue
            j = len(mset & oset) / len(mset | oset)
            if j > best_j:
                best, best_j = oc["label"], j
        if best:
            label = best
        used_labels.add(label)
        clusters.append({"label": label, "centroid": centroids[ci], "size": len(members),
                         "members": members})
    CLUSTERS.write_text(json.dumps({"clusters": clusters, "built": dt.date.today().isoformat()}))
    print(f"  topic clusters: " + ", ".join(f"{c['label']}({c['size']})" for c in clusters))
    return clusters

# ----------------------------------------------------------------------------
# Interest statements — the "why" behind each topic cluster. An LLM reads each
# cluster's seed papers and writes the specific angle that unites them (e.g. not
# "narrative" but "narrative as a mechanism of persuasion and bridging divides").
# Auto-regenerated whenever the profile rebuilds, so nothing is hardcoded and the
# statements evolve with your seeds. Used two ways: as the reranker's queries, and
# stored with embeddings in interests.json.
# ----------------------------------------------------------------------------

INTERESTS = HERE / "interests.json"

def _interest_sentence(seed_texts, member_dois):
    """One-sentence interest statement distilled from a set of seed papers, or None on
    failure/empty. Shared by the whole-cluster (umbrella) and per-sub-angle calls."""
    import embeddings
    snippets = ["- " + (seed_texts.get(d, "") or "")[:300] for d in member_dois[:15]]
    try:
        stmt = embeddings.chat(
            "You distill a researcher's specific interest from papers they chose to save. "
            "Answer with ONE sentence (no preamble) describing the precise research interest "
            "that unites these papers — name the mechanism, purpose, or context that makes "
            "them interesting to this researcher, not just the surface topic.",
            "Papers:\n" + "\n".join(snippets))
    except Exception as e:
        raise EmbeddingSynthesisError(str(e)[:60])
    return (stmt or "").strip().strip('"')[:400] or None

class EmbeddingSynthesisError(Exception):
    pass

def synthesize_interests(clusters, seed_texts, cache=None):
    """Write one interest statement per topic (interests.json). For a cluster larger than
    SUBDIVIDE_THRESHOLD, ALSO write 2-3 drilled-down `substatements`, one per embedding
    sub-angle — these become the reranker's queries (see rerank_interests), so a big topic
    is judged by its specific sub-interests instead of its lowest-common-denominator theme.
    The top-level `statement` (the umbrella) stays as the dashboard card text, so the
    /topics view, weights, and topic assignment are unchanged."""
    import embeddings
    out = []
    for c in clusters:
        if c["size"] < 2:
            continue                      # a 1-2 paper cluster has no stable throughline
        members = c.get("members", [])
        try:
            umbrella = _interest_sentence(seed_texts, members)
        except EmbeddingSynthesisError as e:
            print(f"  ! interest synthesis failed for '{c['label']}' ({e})")
            continue
        if not umbrella:
            continue
        entry = {"label": c["label"], "statement": umbrella}
        subgroups = _subdivide(members, cache)
        if len(subgroups) > 1:            # big cluster -> drill into specific sub-angles
            subs = []
            for g in subgroups:
                try:
                    s = _interest_sentence(seed_texts, g)
                except EmbeddingSynthesisError:
                    continue
                if s:
                    subs.append(s)
            if len(subs) >= 2:            # need >=2 or there's nothing to drill down TO
                entry["substatements"] = subs
        out.append(entry)
    if not out:
        return
    try:
        vecs = embeddings.embed([o["statement"] for o in out])
        for o, v in zip(out, vecs):
            o["embedding"] = v
    except Exception:
        pass
    INTERESTS.write_text(json.dumps({"interests": out, "built": dt.date.today().isoformat()}))
    print("  interest statements:")
    for o in out:
        print(f"    [{o['label']}] {o['statement'][:110]}")
        for s in o.get("substatements", []):
            print(f"        ↳ {s[:104]}")

def assign_topic(emb, clusters):
    """Nearest-centroid topic label for a candidate embedding (clusters use unit vectors)."""
    import embeddings
    v = embeddings.normalize(emb)
    best, blabel = -1e9, "—"
    for c in clusters:
        sim = sum(x * y for x, y in zip(v, c["centroid"]))
        if sim > best:
            best, blabel = sim, c["label"]
    return blabel

# ----------------------------------------------------------------------------
# 1d. Feedback — your dashboard 👍/👎 steer the ranking.
#   • 👎 a paper -> it is dropped from future harvests (hard), and candidates close to it
#     are nudged down (gentle).  • 👍 a paper -> it acts as a bonus seed (positive example).
# This is asymmetric on purpose: upvotes add positive signal; downvotes mostly just remove.
# ----------------------------------------------------------------------------

FEEDBACK = HERE / "feedback.json"
FEEDBACK_EMB = HERE / "feedback_embeddings.json"

def _slug(t):
    return re.sub(r"\W+", "", (t or "").lower())[:60]

def _feedback_votes():
    """{ident_lower: 'up'|'down'} from feedback.json; ident is the doi or a title slug."""
    if not FEEDBACK.exists():
        return {}
    try:
        fb = json.loads(FEEDBACK.read_text())
    except Exception:
        return {}
    return {(k.split("|", 1)[1] if "|" in k else k).lower(): v.get("vote") for k, v in fb.items()}

def _voted_text(ident):
    """Recover a voted paper's title+abstract from the archived data_<date>.json files."""
    for f in sorted((HERE / "briefings").glob("data_*.json")):
        try:
            papers = json.loads(f.read_text())
        except Exception:
            continue
        for p in papers:
            if (p.get("doi", "") or "").lower() == ident or _slug(p.get("title")) == ident:
                return ((p.get("title") or "") + ". " + (p.get("abstract") or "")).strip()[:2000]
    return ""

def feedback_signal():
    """Return (up_embs, down_embs, down_idents). Embeds voted papers (cached). Best-effort:
    with no embeddings, returns ([],[],down_idents) so the hard-drop still works."""
    votes = _feedback_votes()
    down_idents = {i for i, v in votes.items() if v == "down"}
    if not votes:
        return [], [], down_idents
    try:
        import embeddings
        if not embeddings.available():
            return [], [], down_idents
        cache = json.loads(FEEDBACK_EMB.read_text()) if FEEDBACK_EMB.exists() else {}
        need = [(i, _voted_text(i)) for i in votes if i not in cache]
        need = [(i, t) for i, t in need if t.strip()]
        if need:
            vecs = embeddings.embed([t for _, t in need])
            for (i, _), v in zip(need, vecs):
                cache[i] = v
            FEEDBACK_EMB.write_text(json.dumps(cache))
        up = [cache[i] for i, v in votes.items() if v == "up" and i in cache]
        down = [cache[i] for i, v in votes.items() if v == "down" and i in cache]
        return up, down, down_idents
    except Exception:
        return [], [], down_idents

# ----------------------------------------------------------------------------
# 1b. Seed portal — add new interest-defining papers (DOI or APA citation)
# ----------------------------------------------------------------------------

DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"'<>]+", re.I)

def _existing_seed_dois():
    if not SEEDS.exists():
        return [], set()
    lines = [l.strip() for l in SEEDS.read_text().splitlines() if l.strip() and not l.lstrip().startswith("#")]
    return lines, {l.lower() for l in lines}

def resolve_to_doi(text):
    """Turn one input line into a DOI. Accepts a raw DOI (or a URL/string containing
    one) directly; otherwise treats the line as an APA-style citation and asks Crossref
    for the best bibliographic match. Returns (doi, label, score); doi is None on miss
    and score is the Crossref relevance (0 for explicit DOIs / no match)."""
    text = text.strip()
    if not text:
        return None, "empty line", 0.0
    m = DOI_RE.search(text)
    if m:
        doi = m.group(0).rstrip(".,;)")     # strip trailing citation punctuation
        return doi, "explicit DOI", 0.0
    # APA (or any free-text) citation -> Crossref bibliographic search
    url = (f"https://api.crossref.org/works?query.bibliographic={quote(text)}"
           f"&rows=1&select=DOI,title,author,issued,score&mailto={MAILTO}")
    data = get_json(url)
    items = ((data or {}).get("message") or {}).get("items") or []
    if not items:
        return None, "no Crossref match", 0.0
    it = items[0]
    doi = (it.get("DOI") or "").strip()
    if not doi:
        return None, "Crossref match had no DOI", 0.0
    title = (it.get("title") or ["(untitled)"])[0]
    score = float(it.get("score", 0) or 0)
    flag = "  ⚠ low-confidence match — verify" if score < 60 else ""
    return doi, f'matched "{title[:70]}" (score {score:.0f}){flag}', score

def add_seeds(values, rebuild=True):
    """Add one or more seeds (DOIs or APA citations) to seeds.txt at equal weight,
    de-duping against what's already there, then rebuild the interest profile."""
    existing, existing_lc = _existing_seed_dois()
    added = []
    for raw in values:
        doi, label, _ = resolve_to_doi(raw)
        if not doi:
            print(f"  ✗ skipped: {raw[:60]!r} — {label}")
            continue
        if doi.lower() in existing_lc or doi.lower() in {a.lower() for a in added}:
            print(f"  • already a seed: {doi}")
            continue
        print(f"  ✓ {doi}  ({label})")
        added.append(doi)
    if not added:
        print("Nothing new to add.")
        return []
    with SEEDS.open("a") as f:
        for doi in added:
            f.write(doi + "\n")
    print(f"Added {len(added)} new seed(s) -> {SEEDS.name} (now {len(existing) + len(added)} total).")
    if rebuild:
        print("Rebuilding interest profile so the new topic enters the daily mix ...")
        build_profile()
    return added

# ----------------------------------------------------------------------------
# 1c. Zotero seed sync — a public Zotero library becomes a live seed source.
# Append-only with a ledger: adding to Zotero adds a seed; removing from Zotero
# leaves the seed in place; a manual deletion from seeds.txt is never undone.
# ----------------------------------------------------------------------------

ZOTERO_CFG = HERE / "zotero.json"
ZOTERO_LEDGER = HERE / "zotero_imported.json"   # every Zotero item/DOI ever processed
ZOTERO_MIN_MATCH = 70                            # Crossref score needed to accept a no-DOI item
# Item types worth seeding (skip attachments, notes, blog posts, web pages, etc.)
ZOTERO_SCHOLARLY = {"journalArticle", "bookSection", "book", "conferencePaper",
                    "preprint", "report", "thesis", "manuscript"}

def _zotero_items(cfg):
    """Fetch top-level (parent) items from a Zotero group/user library, paginated.
    Uses /items/top so child attachments and notes are excluded by the API."""
    lib = "groups" if cfg.get("library_type", "group") == "group" else "users"
    base = f"https://api.zotero.org/{lib}/{cfg['library_id']}"
    path = (f"/collections/{cfg['collection_key']}/items/top"
            if cfg.get("collection_key") else "/items/top")
    headers = {"Zotero-API-Version": "3"}
    if cfg.get("api_key"):
        headers["Zotero-API-Key"] = cfg["api_key"]
    out, start, limit = [], 0, 100
    while True:
        page = get_json(f"{base}{path}?format=json&limit={limit}&start={start}", headers=headers)
        if not page:
            break
        out += page
        if len(page) < limit:
            break
        start += limit
    return out

def _zotero_doi(d):
    """A DOI for one Zotero item: its own DOI field, else a DOI hiding in 'extra',
    else (best effort) resolve title + authors via Crossref — accepting only a
    confident match so a stray blog/title can't pull in the wrong paper."""
    doi = (d.get("DOI") or "").strip()
    if not doi:
        m = DOI_RE.search(d.get("extra", "") or "")
        if m:
            doi = m.group(0).rstrip(".,;)")
    if doi:
        return doi, "DOI in record"
    title = (d.get("title") or "").strip()
    if len(title) < 8:
        return "", "no DOI; title too sparse to resolve"
    names = " ".join((c.get("lastName") or c.get("name") or "")
                     for c in d.get("creators", [])[:4])
    year = (d.get("date") or "")[:4]
    rdoi, label, score = resolve_to_doi(f"{names} {title} {year}".strip())
    if rdoi and score >= ZOTERO_MIN_MATCH:
        return rdoi, f"resolved via Crossref ({label})"
    return "", f"no DOI; Crossref match too weak (score {score:.0f})"

def _load_zotero_ledger():
    if ZOTERO_LEDGER.exists():
        try:
            data = json.loads(ZOTERO_LEDGER.read_text())
            if isinstance(data, dict):
                return set(data.get("dois", [])), set(data.get("keys", []))
            return set(data), set()          # legacy: a plain list of DOIs
        except Exception:
            pass
    return set(), set()

def sync_zotero(rebuild=True):
    """Append-only sync of a public Zotero library into seeds.txt. DOI'd items go
    straight in; items without a DOI are resolved via Crossref when confident, and
    skipped (logged) otherwise. The ledger makes it idempotent and removal-safe."""
    if not ZOTERO_CFG.exists():
        print(f"No {ZOTERO_CFG.name} — copy zotero.json.example and fill it in.")
        return []
    cfg = json.loads(ZOTERO_CFG.read_text())
    items = _zotero_items(cfg)
    dois_led, keys_led = _load_zotero_ledger()
    existing, existing_lc = _existing_seed_dois()
    added, skipped = [], 0
    for it in items:
        key = it.get("key") or ""
        d = it.get("data", {})
        if d.get("itemType") not in ZOTERO_SCHOLARLY:
            continue
        if key and key in keys_led:           # already processed this exact item — once ever
            continue
        doi, why = _zotero_doi(d)
        if key:
            keys_led.add(key)
        if not doi:
            print(f"  ~ no DOI: {(d.get('title') or '')[:55]} — {why}")
            skipped += 1
            continue
        dl = doi.lower()
        if dl in dois_led:                     # pulled before -> never re-add (respects removals)
            continue
        dois_led.add(dl)
        if dl in existing_lc:                  # already a seed; just record Zotero has it
            continue
        print(f"  + {doi}  [{why}]  {(d.get('title') or '')[:50]}")
        added.append(doi)
    if added:
        with SEEDS.open("a") as f:
            for doi in added:
                f.write(doi + "\n")
    ZOTERO_LEDGER.write_text(json.dumps({"dois": sorted(dois_led), "keys": sorted(keys_led)}, indent=2))
    print(f"Zotero sync: {len(items)} parent items, {len(added)} new seed(s), "
          f"{skipped} without a resolvable DOI.")
    if added and rebuild:
        print("Rebuilding interest profile so the new seeds enter the daily mix ...")
        build_profile()
    return added

# ----------------------------------------------------------------------------
# 2. Harvest recent candidates
# ----------------------------------------------------------------------------

def harvest_openalex(prof, since):
    cids = (prof.get("retrieval_concepts") or list(prof["concepts"].keys()))[:OPENALEX_CONCEPTS]
    out = []
    for cid in cids:
        short = cid.rsplit("/", 1)[-1]
        # Cursor-page through each concept up to OPENALEX_MAX_PER_CONCEPT rather than taking
        # only the newest 200. This is what lets a relevant-but-slightly-older paper — one
        # OpenAlex indexed days after its publication_date — still be gathered instead of
        # truncated off the top of the newest-first list (the bug that hid otherwise-top-5
        # papers). The doc_embeddings cache keeps the cost of the bigger pool bounded.
        cursor, pulled = "*", 0
        while cursor and pulled < OPENALEX_MAX_PER_CONCEPT:
            url = (f"https://api.openalex.org/works?filter=from_publication_date:{since},"
                   f"concepts.id:{short},type:article&per-page={OPENALEX_PER_PAGE}"
                   f"&sort=publication_date:desc&cursor={quote(cursor)}&mailto={MAILTO}")
            data = get_json(url)
            if not data:
                break
            results = data.get("results", [])
            for w in results:
                out.append(parse_openalex(w))
            pulled += len(results)
            cursor = (data.get("meta") or {}).get("next_cursor")
            if not results:
                break
            time.sleep(0.2)
    return out

def parse_openalex(w):
    src = (w.get("primary_location") or {}).get("source") or {}
    venue = src.get("display_name") or ""
    authors = [a["author"]["display_name"] for a in w.get("authorships", []) if a.get("author")]
    abstract = reconstruct_abstract(w.get("abstract_inverted_index"))
    countries = sorted({cc for a in w.get("authorships", [])
                        for cc in (a.get("countries") or [])
                        } | {(i.get("country_code") or "").upper()
                             for a in w.get("authorships", [])
                             for i in (a.get("institutions") or []) if i.get("country_code")})
    countries = [c for c in countries if c]
    return {
        "source": "openalex",
        "doi": (w.get("doi") or "").replace("https://doi.org/", ""),
        "title": (w.get("title") or "").strip(),
        "venue": venue,
        "source_type": src.get("type") or "",   # journal | conference | repository ...
        "source_id": src.get("id") or "",       # OpenAlex source id, for venue-impact stats
        "countries": countries,                 # author-institution country codes
        "authors": authors,
        "date": w.get("publication_date", ""),
        "created": w.get("created_date", ""),   # when the record appeared ≈ online date;
                                                # advance-access items carry FUTURE print dates
        "type": w.get("type", ""),
        "abstract": abstract,
        "concept_ids": [c["id"] for c in w.get("concepts", []) if c["score"] >= 0.3],
        "author_ids": [a["author"]["id"] for a in w.get("authorships", []) if a.get("author")],
        "cited_by": w.get("cited_by_count", 0),
        "oa_url": (w.get("open_access") or {}).get("oa_url") or "",
    }

def reconstruct_abstract(inv):
    if not inv:
        return ""
    words = {}
    for word, idxs in inv.items():
        for i in idxs:
            words[i] = word
    return " ".join(words[i] for i in sorted(words))[:1500]

def harvest_arxiv(since):
    out = []
    cat_q = "+OR+".join(f"cat:{c}" for c in ARXIV_CATS)
    url = (f"https://export.arxiv.org/api/query?search_query=({cat_q})"
           f"&start=0&max_results={ARXIV_MAX}&sortBy=submittedDate&sortOrder=descending")
    xml = get_text(url)
    if not xml:
        return out
    for entry in re.findall(r"<entry>(.*?)</entry>", xml, re.S):
        def tag(t):
            m = re.search(fr"<{t}>(.*?)</{t}>", entry, re.S)
            return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
        date = tag("published")[:10]
        if date < since:
            continue
        out.append({
            "source": "arxiv", "doi": "", "title": tag("title"),
            "venue": "arXiv (preprint)",
            "authors": re.findall(r"<name>(.*?)</name>", entry),
            "date": date, "type": "preprint", "abstract": tag("summary")[:1500],
            "concept_ids": [], "author_ids": [], "cited_by": 0,
            "oa_url": (re.search(r'<id>(.*?)</id>', entry) or [None, ""])[1],
        })
    return out

def harvest_s2_recommendations(prof, since):
    """Semantic Scholar recommendations from your seed set (positive examples)."""
    out = []
    # S2 recommendations endpoint accepts a list of positive paper ids.
    # Use ALL seeds as positive examples, not just the first 40 (which starved every
    # recently-added / Zotero seed). Above S2's ~100-id practical limit, take an even
    # spread across the whole list so old core AND recent additions both contribute.
    all_seeds = [l.strip() for l in SEEDS.read_text().splitlines() if l.strip() and not l.lstrip().startswith("#")]
    if len(all_seeds) <= 100:
        chosen = all_seeds
    else:
        step = len(all_seeds) / 100.0
        chosen = [all_seeds[int(i * step)] for i in range(100)]
    body = json.dumps({"positivePaperIds": ["DOI:" + d for d in chosen]}).encode()
    url = ("https://api.semanticscholar.org/recommendations/v1/papers?limit=40"
           "&fields=title,abstract,venue,year,publicationDate,authors,externalIds,tldr,influentialCitationCount")
    data = None
    for attempt in range(3):                       # S2 rate-limits transiently (HTTP 400/429)
        try:
            req = urllib.request.Request(url, data=body,
                  headers={"Content-Type": "application/json", "User-Agent": f"harvester ({MAILTO})"})
            with urllib.request.urlopen(req, timeout=30, context=SSL_CTX) as r:
                data = json.load(r)
            break
        except Exception as e:
            if attempt == 2:
                print(f"  ! S2 recommendations failed after retries ({e}); non-fatal",
                      file=sys.stderr)
                return out
            time.sleep(3 * (attempt + 1))
    for p in data.get("recommendedPapers", []):
        if (p.get("publicationDate") or "9999") < since:
            continue
        out.append({
            "source": "s2rec", "doi": (p.get("externalIds") or {}).get("DOI", ""),
            "title": p.get("title", ""), "venue": p.get("venue", ""),
            "authors": [a["name"] for a in p.get("authors", [])],
            "date": p.get("publicationDate") or str(p.get("year", "")),
            "type": "article",
            "abstract": p.get("abstract") or (p.get("tldr") or {}).get("text", "") or "",
            "concept_ids": [], "author_ids": [],
            "cited_by": p.get("influentialCitationCount", 0), "oa_url": "",
            "s2_recommended": True,
        })
    return out

# ----------------------------------------------------------------------------
# 3. Filter + score + dedupe
# ----------------------------------------------------------------------------

def keep(c):
    t = c["title"]
    if not t or NOISE_TITLE.search(t):
        return False
    if c["type"] and c["type"] not in GOOD_TYPES:
        return False
    if len(t.split()) < 4:          # fragments / section headers
        return False
    # Drop absurd future dates (e.g. 2050, 2031 — bad metadata). A modest future print
    # date (online-now, print in a month or two) is legitimate, so allow up to +90 days.
    try:
        if (dt.date.fromisoformat(c["date"]) - dt.date.today()).days > 90:
            return False
    except Exception:
        pass
    return True

def venue_quality_match(venue, extra=()):
    """True if the venue is a trusted journal. Equality/prefix match (after dropping a
    leading 'The '), NOT 'substring anywhere' — otherwise a generic allowlist phrase like
    'communication research' spuriously matched 'Journal of International Crisis and Risk
    Communication Research' and handed e-lit pieces a 0.7 quality score."""
    v = (venue or "").lower().strip()
    if v.startswith("the "):
        v = v[4:]
    # equality or 'name: subtitle' only — no bare space-prefix, so "science" doesn't sweep
    # in "Science Advances" / "Science of the Total Environment".
    return any(v == a or v.startswith(a + ":") for a in list(VENUE_ALLOWLIST) + list(extra))

def is_proceedings(c):
    """Best-effort: conference papers, e-literature exhibitions, and repository-hosted
    working papers — but NOT arXiv preprints (those are valued and shown separately)."""
    if c.get("source") == "arxiv":
        return False
    if (c.get("source_type") or "").lower() in ("conference", "repository"):
        return True
    blob = " ".join([c.get("venue") or "", c.get("oa_url") or "", c.get("type") or ""]).lower()
    return any(m in blob for m in PROCEEDINGS_MARKERS)

def _percentile(sorted_vals, p):
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * p
    lo = int(k)
    return sorted_vals[lo] if lo + 1 >= len(sorted_vals) else \
        sorted_vals[lo] + (k - lo) * (sorted_vals[lo + 1] - sorted_vals[lo])

def attach_embedding_relevance(cands, pos_embs, neg_embs=None, nearest_k=3):
    """Set c['_rel_emb'] in [0,1] = how close a candidate sits to your nearest POSITIVE
    examples (seeds + upvoted papers), with two refinements that tune out noise:
      • contrastive: subtract part of the candidate's *average* similarity to all positives,
        so a paper that's only *generically* close (high baseline — the seagrass problem)
        scores below one that's *specifically* close to a few seeds.
      • downvote penalty: if a candidate is closer to a 👎'd paper than to your likes, push it
        down. Then min-max normalize across the run. Candidates without an embedding stay
        tag-scored. Returns how many got an embedding score."""
    import embeddings
    pos = [embeddings.normalize(e) for e in pos_embs]
    neg = [embeddings.normalize(e) for e in (neg_embs or [])]
    raws = []
    for c in cands:
        emb = c.get("_emb")
        if not emb or not pos:
            continue
        v = embeddings.normalize(emb)
        sims = sorted((sum(a * b for a, b in zip(v, p)) for p in pos), reverse=True)
        topk = sims[:nearest_k]
        base = sum(topk) / len(topk)
        meanall = sum(sims) / len(sims)
        raw = base - 0.3 * meanall                        # contrastive
        if neg:
            negmax = max(sum(a * b for a, b in zip(v, p)) for p in neg)
            raw -= 0.5 * max(0.0, negmax - base)          # gentle dislike penalty
        c["_rel_raw"] = raw
        raws.append(raw)
    if not raws:
        return 0
    raws.sort()
    # Normalize to [0,1] but keep resolution at the TOP, where selection happens: clamp the
    # bottom (10th pct -> 0) but scale the top to the actual maximum, so the best few papers
    # get distinct relevance instead of all pinning at a flat 1.0 (which made every top pick
    # score an identical 0.70).
    lo, hi = _percentile(raws, 0.10), raws[-1]
    rng = (hi - lo) or 1e-9
    n = 0
    for c in cands:
        if "_rel_raw" in c:
            c["_rel_emb"] = min(1.0, max(0.0, (c["_rel_raw"] - lo) / rng))
            n += 1
    return n

# ----------------------------------------------------------------------------
# Venue impact — a SLIDING quality scale, not just a hardcoded elite list. OpenAlex
# publishes per-journal citation stats (2yr_mean_citedness ≈ impact factor) for free;
# we batch-fetch them for each run's venues, cache ~90 days, and map them onto the same
# 0-0.9 quality range the tiers use:  IF≈2 → ~0.31,  5 → ~0.50,  10 → ~0.67,  20+ → 0.85.
# The final venue quality is max(tier, scaled impact) — so a strong journal that's in
# neither of your lists still gets credit, while your lists remain a floor.
# ----------------------------------------------------------------------------

VENUE_STATS = HERE / "venue_stats.json"
VENUE_STATS_TTL_DAYS = 90

def fetch_venue_stats(source_ids):
    """Return {source_id: 2yr_mean_citedness}, from cache + batched OpenAlex lookups."""
    cache = {}
    if VENUE_STATS.exists():
        try:
            cache = json.loads(VENUE_STATS.read_text())
        except Exception:
            cache = {}
    cutoff = (dt.date.today() - dt.timedelta(days=VENUE_STATS_TTL_DAYS)).isoformat()
    fresh = {k: v for k, v in cache.items() if v.get("fetched", "") >= cutoff}
    todo = sorted({s for s in source_ids if s and s not in fresh})
    for i in range(0, len(todo), 50):
        chunk = todo[i:i + 50]
        short = "|".join(s.rsplit("/", 1)[-1] for s in chunk)
        data = get_json(f"https://api.openalex.org/sources?filter=ids.openalex:{short}"
                        f"&select=id,summary_stats&per-page=50&mailto={MAILTO}")
        for row in (data or {}).get("results", []):
            c2 = ((row.get("summary_stats") or {}).get("2yr_mean_citedness") or 0.0)
            fresh[row["id"]] = {"c2": round(float(c2), 2), "fetched": dt.date.today().isoformat()}
        time.sleep(0.2)
    if todo:
        VENUE_STATS.write_text(json.dumps(fresh))
    return {k: v.get("c2", 0.0) for k, v in fresh.items()}

def impact_quality(c2):
    """Map a journal's 2yr mean citedness onto 0..0.85 (log scale, saturating)."""
    if not c2 or c2 <= 0:
        return 0.0
    return min(0.85, 0.28 * math.log1p(c2))

RERANK_TOP = config.RERANK_TOP             # candidates sent to the cross-encoder (by embedding relevance).
                            # Wide enough that a paper which sneaks into the final top-N via
                            # recency/quality still gets contextually judged — otherwise a
                            # tangential-but-recent match (e.g. narrative-identity in nursing
                            # education) can ride in without the "is this actually on-topic?" check.
RERANK_BLEND = config.RERANK_BLEND           # final rel = (1-b)*embedding + b*reranker

def rerank_interests(cands):
    """Second-stage relevance: a cross-encoder reads each shortlisted candidate TOGETHER
    with each auto-synthesized interest statement and judges contextual fit — this is what
    separates 'narrative, anywhere' from 'narrative as a persuasion/bridging mechanism'.
    Takes the max over interest statements (cluster-aware), blends into _rel_emb for the
    shortlist. Best-effort: any failure leaves embedding relevance untouched."""
    if not INTERESTS.exists():
        return
    try:
        import embeddings
        interests = json.loads(INTERESTS.read_text()).get("interests", [])
        # For a subdivided (big) topic, query by its specific sub-angles and DROP the vague
        # umbrella statement — otherwise the max-over-queries would still let a generically
        # on-topic paper score high off the broad theme. Small topics query by their one
        # statement as before.
        queries = []
        for i in interests:
            subs = i.get("substatements") or []
            if subs:
                queries.extend(subs)
            elif i.get("statement"):
                queries.append(i["statement"])
        if not queries:
            return
        short = sorted([c for c in cands if c.get("_rel_emb") is not None],
                       key=lambda c: -c["_rel_emb"])[:RERANK_TOP]
        if not short:
            return
        docs = [((c["title"] or "") + ". " + (c["abstract"] or ""))[:1200] for c in short]
        best = [0.0] * len(short)
        for q in queries:
            scores = embeddings.rerank(q, docs)
            best = [max(b, s) for b, s in zip(best, scores)]
        for c, r in zip(short, best):
            c["_rerank"] = round(r, 3)
            c["_rel_emb"] = (1 - RERANK_BLEND) * c["_rel_emb"] + RERANK_BLEND * r
        print(f"  reranked top {len(short)} against {len(queries)} interest statements")
    except Exception as e:
        print(f"  ! rerank skipped ({str(e)[:70]})", file=sys.stderr)

def score(c, prof):
    # relevance: prefer SEMANTIC embedding similarity to your nearest seeds (set on the
    # candidate as _rel_emb by attach_embedding_relevance). This is cluster-aware — a paper
    # close to a small seed cluster still scores high. Falls back to WEIGHTED concept
    # overlap when embeddings are unavailable (off-VPN) or the candidate has no abstract.
    if c.get("_rel_emb") is not None:
        rel = c["_rel_emb"]
        rel_src = "embed"
    else:
        prof_w = prof["concepts"]                   # {concept_id: summed weight}
        maxw = max(prof_w.values()) if prof_w else 1.0
        rel = min(sum(prof_w[cid] / maxw for cid in c["concept_ids"] if cid in prof_w) / REL_NORM, 1.0)
        rel_src = "tags"
    if c.get("s2_recommended"):
        rel = max(rel, 0.7)
    # quality tiers: prestige (Nature/Science/APSR...) 0.9 > your trusted venues 0.7 > 0;
    # a returning seed author adds 0.3 either way.
    q = 0.0
    v = (c["venue"] or "").lower().strip()
    v = v[4:] if v.startswith("the ") else v
    if (v in PRESTIGE_VENUES or v.startswith("nature ")     # whole Nature family
            or any(v.startswith(p + ":") for p in PRESTIGE_VENUES)):
        q = 0.9
    elif venue_quality_match(c["venue"], prof.get("trusted_venues", ())):
        q = 0.7
    # sliding scale: a journal's actual citation impact can lift it past the tiers
    q = max(q, impact_quality(c.get("_venue_c2", 0.0)))
    if set(c["author_ids"]) & set(prof.get("seed_authors", {})):
        q += 0.3
    q = min(q, 1.0)
    # recency: linear over a 14-day window
    try:
        age = (dt.date.today() - dt.date.fromisoformat(c["date"])).days
        # Clamp to [0,1]: a future print date (age<0) is "as fresh as it gets", not
        # an inflated score — without min() a 2050-dated record scored ~90 and buried
        # every real paper.
        rec = max(0.0, min(1.0, 1.0 - age / 14.0))
    except Exception:
        rec = 0.3
    s = WEIGHTS["relevance"]*rel + WEIGHTS["quality"]*q + WEIGHTS["recency"]*rec

    # TYPE GATE — journal articles and preprints are the default diet. Proceedings,
    # repository working papers, and book reviews only surface when REALLY well aligned:
    # above TYPE_GATE_REL they take the old mild haircut; below it they effectively vanish.
    preferred_type = (c.get("source") == "arxiv" or c.get("type") in ("preprint", "posted-content")
                      or ((c.get("source_type") or "").lower() == "journal"
                          and not BOOK_REVIEW_RE.search(c.get("title") or "")))
    gated_type = not preferred_type or is_proceedings(c)
    if gated_type:
        s *= PROCEEDINGS_PENALTY if rel >= TYPE_GATE_REL else TYPE_GATE_PENALTY

    # GEO GATE — non-US/major-European author bases need a STELLAR topical fit. Papers
    # with unknown affiliation (arXiv, S2 recs) pass untouched.
    cc = c.get("countries") or []
    gated_geo = bool(cc) and not (set(cc) & PREFERRED_COUNTRIES)
    if gated_geo and rel < GEO_GATE_REL:
        s *= GEO_GATE_PENALTY

    c["_scores"] = dict(relevance=round(rel, 2), rel_src=rel_src, quality=round(q, 2),
                        recency=round(rec, 2), type_gate=gated_type, geo_gate=gated_geo,
                        total=round(s, 3))
    return s

def key_of(c):
    """Stable identity for a paper: DOI if present, else a title slug."""
    return c["doi"].lower() or re.sub(r"\W+", "", c["title"].lower())[:60]

def dedupe(cands):
    seen, out = {}, []
    for c in cands:
        key = key_of(c)
        if key in seen:
            # prefer the record that has an abstract
            if not seen[key]["abstract"] and c["abstract"]:
                seen[key].update(c)
            continue
        seen[key] = c
        out.append(c)
    return out

def load_seen():
    """Return {key: iso-date-first-shown}, pruning anything past the TTL."""
    if not SEEN.exists():
        return {}
    data = json.loads(SEEN.read_text())
    cutoff = (dt.date.today() - dt.timedelta(days=SEEN_TTL_DAYS)).isoformat()
    return {k: v for k, v in data.items() if v >= cutoff}

def record_seen(items, seen):
    today = dt.date.today().isoformat()
    for c in items:
        seen.setdefault(key_of(c), today)
    SEEN.write_text(json.dumps(seen, indent=2))

# ----------------------------------------------------------------------------
# Abstract watchlist — advance-access papers are often listed (title, venue, DOI) days
# before their abstract is deposited to Crossref. We refuse to SHOW a paper with no real
# content, but we don't want to silently lose a good one either. So: a strong candidate
# with no abstract AND no fetchable open-access full text is held here (E), re-checked each
# day (B), and re-injected to compete the moment its abstract appears (C). Stats let you
# see the real backfill rate over time (`--watchlist-stats`).
# ----------------------------------------------------------------------------

WATCHLIST = HERE / "watchlist.json"
WATCHLIST_TTL_DAYS = 30       # stop tracking a paper whose abstract never arrives
DISPLAY_SCAN_CAP = 40         # how deep to scan (and hold) while filling the abstract-gated top-N
RECHECK_CAP = 60              # max held DOIs re-checked per run (bounds API calls)

def load_watchlist():
    if WATCHLIST.exists():
        try:
            return json.loads(WATCHLIST.read_text())
        except Exception:
            return {}
    return {}

def save_watchlist(wl):
    WATCHLIST.write_text(json.dumps(wl, indent=2))

def _has_abstract(c):
    return bool((c.get("abstract") or "").strip())

def fulltext_ok(c, outdir, idx):
    """Can we ACTUALLY fetch real open-access full text for this abstract-less candidate?
    We used to accept any paper that merely LISTED an OA url — but the real fetcher is far
    stricter (it rejects <15 KB landing/paywall stubs, non-PDF/HTML bodies, dead links), so
    a listed-but-unfetchable paper was being shown with neither abstract nor text. Now the
    gate performs the real fetch: only papers whose full text truly downloads are shown; the
    rest are held. On success the file is saved + fulltext_path set, so the later fulltext
    step reuses it instead of re-downloading. Best-effort: any error -> treat as not-ok (hold)."""
    try:
        import fetch_fulltext
        ok, _ = fetch_fulltext.fetch_one(c, outdir, idx)
        return ok
    except Exception as e:
        print(f"  ! fulltext verify failed ({str(e)[:60]}); holding paper", file=sys.stderr)
        return False

def hold_in_watchlist(c, wl):
    """Record a strong-but-abstract-less candidate so we can resurface it later. Never
    overwrites an existing entry (preserves its original first_seen / status)."""
    doi = (c.get("doi") or "").strip()
    if not doi or doi in wl:
        return
    wl[doi] = {"first_seen": dt.date.today().isoformat(),
               "first_score": round(c.get("score", 0), 3), "title": c.get("title", ""),
               "venue": c.get("venue", ""), "pub_date": c.get("date", ""),
               "checks": 0, "status": "held"}

def recheck_watchlist(wl, seen):
    """Re-check held/ready papers against OpenAlex: promote a 'held' paper to 'ready' the
    day its abstract appears (recording days_to_resolve for stats), re-inject 'ready' papers
    so they compete until shown, mark 'done' once shown, and 'expired' past the TTL. Returns
    candidate dicts to add to today's pool. Bounded to RECHECK_CAP API calls."""
    today = dt.date.today()
    out = []
    active = sorted(((d, e) for d, e in wl.items() if e.get("status") in ("held", "ready")),
                    key=lambda x: x[1].get("first_seen", ""))
    for doi, e in active[:RECHECK_CAP]:
        first = dt.date.fromisoformat(e["first_seen"])
        if (today - first).days > WATCHLIST_TTL_DAYS:
            e["status"] = "expired"
            continue
        w = get_json(f"https://api.openalex.org/works/doi:{quote(doi)}?mailto={MAILTO}", tries=1)
        e["checks"] = e.get("checks", 0) + 1
        e["last_checked"] = today.isoformat()
        if not w:
            continue
        if e["status"] == "held":
            if w.get("abstract_inverted_index"):
                e["status"] = "ready"
                e["resolved_on"] = today.isoformat()
                e["days_to_resolve"] = (today - first).days
            else:
                continue
        cand = parse_openalex(w)                       # status == 'ready' here
        if key_of(cand) in seen:
            e["status"] = "done"
            e.setdefault("shown_on", today.isoformat())
            continue
        out.append(cand)
    return out

def watchlist_stats(wl):
    from collections import Counter
    st = Counter(e.get("status", "?") for e in wl.values())
    days = sorted(e["days_to_resolve"] for e in wl.values() if e.get("days_to_resolve") is not None)
    print(f"Watchlist: {len(wl)} papers tracked")
    for k in ("held", "ready", "done", "expired"):
        print(f"  {k:8}: {st.get(k, 0)}")
    verdicts = len(days) + st.get("expired", 0)
    if days:
        print(f"  abstract appeared for {len(days)} — median {days[len(days)//2]}d, "
              f"range {days[0]}-{days[-1]}d")
    if verdicts:
        print(f"  of {verdicts} papers that reached a verdict, {100*len(days)//verdicts}% "
              f"eventually got an abstract (rest never did within {WATCHLIST_TTL_DAYS}d)")

# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build-profile", action="store_true")
    ap.add_argument("--add-seed", action="append", metavar="DOI_OR_CITATION",
                    help="add a seed paper (a DOI or an APA-style citation); repeat the "
                         "flag for several. Appends to seeds.txt at equal weight and rebuilds.")
    ap.add_argument("--add-seed-file", metavar="PATH",
                    help="add many seeds at once: one DOI or APA citation per line in PATH.")
    ap.add_argument("--sync-zotero", action="store_true",
                    help="pull new DOIs from the configured public Zotero library into "
                         "seeds.txt (append-only; resolves DOI-less items via Crossref).")
    ap.add_argument("--watchlist-stats", action="store_true",
                    help="print backfill stats for advance-access papers held for a later abstract.")
    ap.add_argument("--no-rebuild", action="store_true",
                    help="with --add-seed: append only, skip the profile rebuild (rebuild later).")
    ap.add_argument("--days", type=int, default=config.DAYS_WINDOW,
                    help="publication-date lookback window. Wider than it once was (was 3-5) so "
                         "papers OpenAlex indexes days after their print date stay in-window "
                         "long enough to be caught; the seen-ledger prevents repeats. 14 balances "
                         "late-index coverage against how fast a strong paper surfaces.")
    ap.add_argument("--top", type=int, default=config.TOP_N,
                    help="how many papers to surface this run (try 7 or 8)")
    ap.add_argument("--more", action="store_true",
                    help="surface the NEXT batch beyond what was already shown (a 'show me more' rerun)")
    ap.add_argument("--include-seen", action="store_true",
                    help="ignore the ledger and allow already-shown papers (for testing)")
    args = ap.parse_args()

    # Seed portal: add new interest-defining papers, then stop.
    seeds_to_add = list(args.add_seed or [])
    if args.add_seed_file:
        seeds_to_add += [l.strip() for l in Path(args.add_seed_file).read_text().splitlines()
                         if l.strip()]
    if seeds_to_add:
        add_seeds(seeds_to_add, rebuild=not args.no_rebuild)
        return

    if args.sync_zotero:
        sync_zotero(rebuild=not args.no_rebuild)
        return

    if args.watchlist_stats:
        watchlist_stats(load_watchlist())
        return

    if args.build_profile or not PROFILE.exists():
        prof = build_profile()
        if args.build_profile:
            return
    else:
        prof = json.loads(PROFILE.read_text())

    since = (dt.date.today() - dt.timedelta(days=args.days)).isoformat()
    print(f"Harvesting since {since} ...")
    cands = []
    cands += harvest_openalex(prof, since)
    cands += harvest_arxiv(since)
    cands += harvest_s2_recommendations(prof, since)

    # Watchlist: resurface any advance-access paper whose abstract has appeared since we
    # first saw it (and update the tracking stats). These re-enter the pool and compete.
    wl = load_watchlist()
    seen = load_seen()
    reinjected = recheck_watchlist(wl, seen)
    if reinjected:
        print(f"  watchlist: re-injected {len(reinjected)} paper(s) whose abstract landed")
    cands += reinjected
    print(f"  {len(cands)} raw candidates")

    cands = [c for c in cands if keep(c)]
    cands = dedupe(cands)

    # Feedback: drop anything you've 👎'd so it never resurfaces, and gather 👍/👎 examples.
    up_embs, down_embs, down_idents = feedback_signal()
    if down_idents:
        before = len(cands)
        cands = [c for c in cands if (c.get("doi", "") or "").lower() not in down_idents
                 and _slug(c.get("title")) not in down_idents]
        print(f"  dropped {before - len(cands)} downvoted paper(s)")

    # Semantic relevance: embed every candidate and score it by closeness to your nearest
    # POSITIVE examples (seeds + upvotes), cluster-aware. Best-effort — if MindRouter/VPN is
    # unreachable, we skip this and score() falls back to concept-tag relevance.
    clusters = []
    try:
        import embeddings
        seed_embs = list(embeddings.load_cache().values()) if embeddings.available() else []
        pos_embs = seed_embs + up_embs
        if pos_embs:
            # Candidate embeddings are CACHED per paper (doc_embeddings.json), keyed by the
            # same identity as dedup/seen. Each paper is embedded exactly once, ever — so a
            # wide window re-scanned daily costs only the papers genuinely new since yesterday,
            # not a full re-embed of the whole window every morning.
            doc_cache = embeddings.load_doc_cache()
            need_idx, need_txt = [], []
            for i, c in enumerate(cands):
                hit = doc_cache.get(key_of(c))
                if hit:
                    c["_emb"] = embeddings.unpack_vec(hit[1])
                else:
                    need_idx.append(i)
                    need_txt.append(((c["title"] or "") + ". " + (c["abstract"] or "")).strip()[:2000])
            if need_txt:
                vecs = embeddings.embed(need_txt)
                today = dt.date.today().isoformat()
                for j, i in enumerate(need_idx):
                    cands[i]["_emb"] = vecs[j]
                    doc_cache[key_of(cands[i])] = [today, embeddings.pack_vec(vecs[j])]
            cutoff = (dt.date.today() - dt.timedelta(days=embeddings.DOC_CACHE_TTL_DAYS)).isoformat()
            doc_cache = {k: v for k, v in doc_cache.items() if v and v[0] >= cutoff}
            embeddings.save_doc_cache(doc_cache)
            n = attach_embedding_relevance(cands, pos_embs, down_embs)
            print(f"  embedded {len(need_txt)} new + {len(cands) - len(need_txt)} cached"
                  f" -> {n} scored; relevance from {len(seed_embs)} seeds"
                  f" + {len(up_embs)} upvotes, {len(down_embs)} downvotes")
            rerank_interests(cands)
            if CLUSTERS.exists():
                clusters = json.loads(CLUSTERS.read_text()).get("clusters", [])
        else:
            print("  (no seed embeddings yet — run --build-profile on VPN; using tag relevance)")
    except embeddings.EmbeddingsUnavailable as e:
        print(f"  ! embeddings unavailable ({e}); using concept-tag relevance", file=sys.stderr)

    # Venue impact stats (sliding quality scale) — batched, cached, best-effort.
    try:
        stats = fetch_venue_stats([c.get("source_id", "") for c in cands])
        for c in cands:
            c["_venue_c2"] = stats.get(c.get("source_id", ""), 0.0)
        print(f"  venue impact stats for {sum(1 for c in cands if c.get('_venue_c2'))} candidates")
    except Exception as e:
        print(f"  ! venue stats skipped ({str(e)[:60]})", file=sys.stderr)

    tweights = load_topic_weights()
    for c in cands:
        c["score"] = score(c, prof)
        if clusters and c.get("_emb"):
            c["topic"] = assign_topic(c["_emb"], clusters)
            w = tweights.get(c["topic"])
            if w:                                   # dashboard ± nudges, deliberately mild
                c["score"] = round(c["score"] * w, 4)
                c["_scores"]["topic_w"] = w
    cands.sort(key=lambda c: -c["score"])

    # Exclude anything already shown on a previous run (or earlier today), so the
    # same paper never resurfaces. --more is just a normal rerun: today's picks are
    # already in the ledger, so the next-best papers come up instead.
    if not args.include_seen:
        before = len(cands)
        cands = [c for c in cands if key_of(c) not in seen]
        print(f"  excluded {before - len(cands)} already-shown papers")

    # Abstract gate + hold-back: only SHOW papers we have real content for — an abstract,
    # or fetchable open-access full text (D). A strong candidate with neither is held to the
    # watchlist (E) and re-checked daily (B/C) instead of being shown as an empty stub or
    # lost. We scan in score order until we have --top displayable papers.
    top, scanned, held = [], 0, 0
    ft_outdir = HERE / "fulltext" / dt.date.today().isoformat()
    for c in cands:
        if len(top) >= args.top or scanned >= DISPLAY_SCAN_CAP:
            break
        scanned += 1
        if _has_abstract(c):
            top.append(c)
        elif fulltext_ok(c, ft_outdir, len(top) + 1):
            c["_oa_no_abstract"] = True     # no abstract, but full text VERIFIED-fetchable
            top.append(c)
        else:
            hold_in_watchlist(c, wl)        # no abstract, no fetchable full text -> hold + track
            held += 1
    if not args.include_seen:
        save_watchlist(wl)
    print(f"  {len(cands)} eligible; showing {len(top)} with real content"
          f" ({held} strong-but-abstract-less held for re-check)")

    # Drop bulky/internal fields (the 4096-float embedding, raw similarity) before writing.
    clean = [{k: v for k, v in c.items() if k not in ("_emb", "_rel_raw", "_rel_emb")} for c in top]
    (HERE / "candidates.json").write_text(json.dumps(clean, indent=2))
    if not args.include_seen:
        record_seen(top, seen)      # mark these as shown so they don't repeat
    print("  wrote candidates.json — hand to the briefing routine.")

if __name__ == "__main__":
    main()
