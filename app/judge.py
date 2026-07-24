#!/usr/bin/env python3
"""
judge.py — the LLM relevance judge: reads each shortlisted paper's title+abstract
against the researcher's plain-English interest profile (interest_profile.json) and
scores fit 0-10 with a one-sentence rationale.

This replaced the cross-encoder "rerank against interest statements" pass on
2026-07-23: measured on a 15,289-paper pool, the cross-encoder's scores were nearly
query-invariant (a robotics paper scored 0.96 against "political discourse"), while
the judge's blind-rated top-5 scored a perfect 2.00/2 from the user
(see intersection_comparison.py and docs/design_rethink_2026-07-23_*.md).

Key properties:
  - Verdicts are cached in judge_cache.json keyed by paper identity; each paper is
    judged ONCE ever — the daily cost is only papers new since yesterday.
  - The cache is bound to the profile's `version`. Editing the profile (e.g. via the
    dashboard's Selection Criteria page, which bumps the version) discards all verdicts, so
    the next run re-judges the whole window under the new wording.
  - Recent 👍/👎 votes are appended to the prompt as boundary examples (capped at
    MAX_VOTE_EXAMPLES per side, newest first) — each vote teaches the judge a case.
  - Everything is best-effort: any failure leaves embedding relevance in charge.

Also home to two profile-adjacent utilities:
  - flavor_embeddings(): embedding of each flavor description (cached per version),
    used by harvest as an extra retrieval channel so a flavor thinly covered by the
    seed papers still gets candidates in front of the judge.
  - downvote_flavor_alert(): if several recent downvotes cluster nearest the same
    flavor, writes alerts.json suggesting a profile edit, with an LLM one-liner
    naming what the downvoted papers have in common (surfaced in the briefing +
    dashboard).

Standalone check:  python3 judge.py            # prints profile version + prompt
"""

import json, re, datetime as dt
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import config
import embeddings

HERE = Path(__file__).parent
PROFILE_F = HERE / "interest_profile.json"
CACHE_F = HERE / "judge_cache.json"
FEEDBACK_F = HERE / "feedback.json"
FEEDBACK_EMB_F = HERE / "feedback_embeddings.json"
ALERTS_F = HERE / "alerts.json"

CACHE_TTL_DAYS = 35          # match the doc-embedding cache; window is ~14d
TEMPERATURE = 0.0            # determinism: same paper + same profile -> same verdict
WORKERS = 6
MAX_VOTE_EXAMPLES = config.MAX_VOTE_EXAMPLES    # newest votes per side appended to the prompt
ALERT_MIN_DOWNVOTES = config.ALERT_MIN_DOWNVOTES  # downvotes near one flavor within ALERT_WINDOW_DAYS
ALERT_WINDOW_DAYS = config.ALERT_WINDOW_DAYS


def load_profile():
    """The user's editable interest profile, or None if absent/unreadable."""
    try:
        p = json.loads(PROFILE_F.read_text())
        return p if p.get("core_statement") else None
    except Exception:
        return None


def build_prompt(profile, extra_pos=(), extra_neg=()):
    """System prompt for the judge. extra_pos/extra_neg are recent voted titles —
    appended AFTER the curated exemplars so fresh feedback speaks last."""
    negs = "\n".join(f"- {n}" for n in profile.get("negatives", []))
    pos = list(profile.get("positive_exemplar_titles", [])) + \
        [f"{t} (recent 👍)" for t in list(extra_pos)[:MAX_VOTE_EXAMPLES]]
    neg_ex = list(profile.get("negative_exemplar_titles", [])) + \
        [f"{t} (recent 👎)" for t in list(extra_neg)[:MAX_VOTE_EXAMPLES]]
    pos_s = "\n".join(f"- {t}" for t in pos)
    neg_s = "\n".join(f"- {t}" for t in neg_ex)
    if profile.get("flavors"):
        areas = "\n".join(f"- {f['key']}: {f['description']}" for f in profile["flavors"])
        rubric = f"""INTEREST FLAVORS (each is already an intersection of the researcher's interests):
{areas}

FIT RULE: {profile.get('fit_rule', '')}

Given one paper's title and abstract, judge which flavors it SUBSTANTIVELY engages
(the flavor is the paper's central question or design — not a passing mention,
not just the application domain) and score fit for THIS researcher:

  9-10  squarely inside >=1 flavor: the flavor's defining combination IS the
        paper's central question (10 if it also connects a second flavor)
  7-8   clearly within a flavor, but it shares the stage with other aims, OR
        combines two flavors' components in a nearby, non-central way
  4-6   competent on one COMPONENT of a flavor (AI alone, politics alone,
        persuasion alone, narrative alone) without the flavored combination
  1-3   tangential; shares vocabulary but not the research space
  0     off-target or in the NOT-of-interest list"""
    else:                                   # legacy facet profile
        facets = "\n".join(
            f"- {f['key']}{' (CORE)' if f.get('core') else ' (secondary)'}: {f['description']}"
            for f in profile.get("facets", []))
        rubric = f"""FACETS:
{facets}

INTERSECTION RULE: {profile.get('intersection_rule', '')}

Score fit: 9-10 intersection of >=2 core facets is the topic; 7-8 one core facet
plus real engagement of a second; 4-6 solid single-facet; 1-3 tangential; 0 off-target."""
    return f"""You screen new academic papers for one specific researcher.

RESEARCHER PROFILE:
{profile['core_statement']}

EXPLICITLY NOT OF INTEREST:
{negs}

PAPERS THE RESEARCHER LIKED:
{pos_s}

PAPERS THE RESEARCHER REJECTED OR DOWNGRADED:
{neg_s}

{rubric}

Respond with STRICT JSON only, no prose around it:
{{"facets": ["<engaged flavor keys>"], "fit": <0-10>, "why": "<ONE short sentence>"}}"""


def _parse(txt):
    m = re.search(r"\{.*\}", txt or "", re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
        fit = float(d.get("fit", -1))
        if not 0 <= fit <= 10:
            return None
        return {"facets": [str(x) for x in (d.get("facets") or [])],
                "fit": round(fit, 1), "why": str(d.get("why", ""))[:300]}
    except Exception:
        return None


def _load_cache(version):
    """{key: verdict} for the given profile version; a version mismatch discards all
    (the profile changed, so every cached judgment is stale). TTL-pruned."""
    try:
        data = json.loads(CACHE_F.read_text())
    except Exception:
        return {}
    if data.get("version") != version:
        return {}
    cutoff = (dt.date.today() - dt.timedelta(days=CACHE_TTL_DAYS)).isoformat()
    return {k: v for k, v in data.get("verdicts", {}).items() if v[0] >= cutoff}


def _save_cache(version, verdicts, extra=None):
    data = {"version": version, "verdicts": verdicts}
    if extra:
        data.update(extra)
    CACHE_F.write_text(json.dumps(data))


def recent_vote_titles():
    """(upvoted_titles, downvoted_titles), newest first, capped — prompt examples."""
    try:
        fb = json.loads(FEEDBACK_F.read_text())
    except Exception:
        return [], []
    rows = sorted(fb.items(), key=lambda kv: kv[1].get("ts", ""), reverse=True)
    up = [v.get("title", "") for _, v in rows if v.get("vote") == "up" and v.get("title")]
    down = [v.get("title", "") for _, v in rows if v.get("vote") == "down" and v.get("title")]
    return up[:MAX_VOTE_EXAMPLES], down[:MAX_VOTE_EXAMPLES]


def judge(cands, key_of, profile=None, extra_pos=(), extra_neg=(), model=None,
          progress=None):
    """Judge candidate dicts (title+abstract required) -> {key: verdict}. Cached per
    paper per profile version. Raises nothing: a paper that fails 3 parse attempts
    gets fit -1 (callers treat as unjudged)."""
    profile = profile or load_profile()
    if not profile:
        return {}
    version = profile.get("version", "v0")
    cache = _load_cache(version)
    todo = [c for c in cands if key_of(c) not in cache]
    if not todo:
        return {k: v[1] for k, v in cache.items()}
    system = build_prompt(profile, extra_pos, extra_neg)
    mdl = model or embeddings.resolve_chat_model()
    if progress:
        progress(f"judging {len(todo)} new papers with {mdl} "
                 f"({len(cache)} cached, profile {version})")
    today = dt.date.today().isoformat()

    def one(c):
        user = (f"Title: {c.get('title')}\nVenue: {c.get('venue') or '?'}\n"
                f"Abstract: {(c.get('abstract') or '')[:2500]}")
        for _ in range(3):
            try:
                out = _parse(embeddings.chat(system, user, model=mdl,
                                             temperature=TEMPERATURE))
            except embeddings.EmbeddingsUnavailable:
                out = None
            if out:
                return key_of(c), out
        return key_of(c), {"facets": [], "fit": -1, "why": "judge failed"}

    done = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for key, res in ex.map(one, todo):
            cache[key] = [today, res]
            done += 1
            if done % 50 == 0:
                _save_cache(version, cache)
                if progress:
                    progress(f"  {done}/{len(todo)}")
    _save_cache(version, cache)
    return {k: v[1] for k, v in cache.items()}


def flavor_embeddings(profile):
    """[(flavor_key, embedding)] for each flavor description, cached per version in
    the judge cache file (piggybacked; tiny)."""
    flavors = profile.get("flavors") or []
    if not flavors:
        return []
    version = profile.get("version", "v0")
    try:
        data = json.loads(CACHE_F.read_text())
        if data.get("version") == version and data.get("flavor_embs"):
            fe = data["flavor_embs"]
            if set(fe) == {f["key"] for f in flavors}:
                return [(k, embeddings.unpack_vec(v)) for k, v in fe.items()]
    except Exception:
        data = {}
    vecs = embeddings.embed([f["description"] for f in flavors])
    fe = {f["key"]: embeddings.pack_vec(v) for f, v in zip(flavors, vecs)}
    verdicts = data.get("verdicts", {}) if data.get("version") == version else {}
    _save_cache(version, verdicts, extra={"flavor_embs": fe})
    return [(f["key"], v) for f, v in zip(flavors, vecs)]


def downvote_flavor_alert(profile):
    """If >= ALERT_MIN_DOWNVOTES downvotes from the last ALERT_WINDOW_DAYS sit nearest
    the SAME flavor, write alerts.json with a suggested profile edit and an LLM
    one-liner naming the downvotes' common theme. Returns the alert dict or None."""
    try:
        fb = json.loads(FEEDBACK_F.read_text())
        femb = json.loads(FEEDBACK_EMB_F.read_text())
    except Exception:
        return None
    flavs = flavor_embeddings(profile)
    if not flavs:
        return None
    cutoff = (dt.date.today() - dt.timedelta(days=ALERT_WINDOW_DAYS)).isoformat()
    by_flavor = {}
    for ident, v in fb.items():
        if v.get("vote") != "down" or (v.get("ts", "") or "")[:10] < cutoff:
            continue
        emb = femb.get(ident)
        if not emb:
            continue
        best = max(flavs, key=lambda kv: embeddings.cosine(emb, kv[1]))
        by_flavor.setdefault(best[0], []).append(v.get("title", ""))
    hot = [(k, ts) for k, ts in by_flavor.items() if len(ts) >= ALERT_MIN_DOWNVOTES]
    if not hot:
        ALERTS_F.unlink(missing_ok=True)
        return None
    key, titles = max(hot, key=lambda kv: len(kv[1]))
    theme = ""
    try:
        theme = embeddings.chat(
            "In ONE sentence (<=25 words), name what these rejected academic papers have "
            "in common topically. No preamble.", "\n".join(f"- {t}" for t in titles),
            temperature=0.2)
    except Exception:
        pass
    alert = {"date": dt.date.today().isoformat(), "flavor": key, "count": len(titles),
             "titles": titles, "theme": theme,
             "suggestion": (f"You've downvoted {len(titles)} recent papers nearest your "
                            f"'{key}' flavor. Consider editing your interest profile "
                            f"(dashboard → Selection Criteria) to exclude this more explicitly"
                            + (f": {theme}" if theme else "."))}
    ALERTS_F.write_text(json.dumps(alert, indent=2))
    return alert


CLUSTERS_F = HERE / "clusters.json"
SEED_TITLES_F = HERE / "seed_titles.json"
PROPOSALS_F = HERE / "proposals.json"
COVERAGE_MIN_CLUSTER = 5     # ignore tiny seed clusters (2-4 papers) — too noisy to propose from
COVERAGE_RATIO = 0.75        # uncovered if best flavor sim < this × the median cluster's best


def coverage_check(profile):
    """Gathering↔Selection coverage audit (2026-07-24). The seed clusters steer what is
    GATHERED; the flavors decide what is SELECTED. A new project's seeds create a cluster
    the judge then vetoes — silently — unless a flavor covers it. So: compare each
    cluster centroid to the flavor-description embeddings; a sizable cluster whose best
    flavor similarity falls well below the median cluster's gets flagged, and an LLM
    drafts a ready-to-review flavor from that cluster's seed titles. Proposals live in
    proposals.json (status pending/dismissed, keyed by cluster label); the dashboard
    Selection page offers accept/dismiss and the briefing carries a note. A cluster that
    becomes covered (or vanishes) has its entry cleaned up. Returns pending proposals."""
    try:
        clusters = json.loads(CLUSTERS_F.read_text()).get("clusters", [])
        titles = json.loads(SEED_TITLES_F.read_text()) if SEED_TITLES_F.exists() else {}
    except Exception:
        return []
    flavs = flavor_embeddings(profile)
    big = [c for c in clusters if c.get("size", 0) >= COVERAGE_MIN_CLUSTER
           and c.get("centroid")]
    if not flavs or len(big) < 2:
        return []
    best = {c["label"]: max(embeddings.cosine(c["centroid"], fv) for _, fv in flavs)
            for c in big}
    med = sorted(best.values())[len(best) // 2]
    uncovered = {l for l, s in best.items() if s < COVERAGE_RATIO * med}

    try:
        props = json.loads(PROPOSALS_F.read_text())
    except Exception:
        props = {}
    props = {l: p for l, p in props.items() if l in uncovered}   # covered/gone -> clean up
    members_of = {c["label"]: c.get("members", []) for c in big}
    for label in uncovered:
        if label in props:                                       # pending or dismissed: don't re-nag
            continue
        cl_titles = [titles.get(d, "") for d in members_of.get(label, [])]
        cl_titles = [t for t in cl_titles if t][:15]
        draft = None
        try:
            raw = embeddings.chat(
                "These seed papers form one research area the user tracks, but their "
                "selection criteria don't cover it yet. Draft ONE 'flavor' entry for it. "
                "Respond with STRICT JSON only: {\"key\": \"<short_snake_case>\", "
                "\"description\": \"<2-3 sentence description of the area, concrete, in "
                "the style of: 'Narrative as a persuasion mechanism, in any domain: "
                "narrative transportation, entertainment-education, story-based appeals.'\"}",
                "\n".join(f"- {t}" for t in cl_titles), temperature=0.2)
            m = re.search(r"\{.*\}", raw or "", re.S)
            d = json.loads(m.group(0)) if m else {}
            if d.get("key") and d.get("description"):
                draft = {"key": re.sub(r"\W+", "_", d["key"].strip().lower())[:40],
                         "description": str(d["description"])[:600]}
        except Exception:
            pass
        props[label] = {"date": dt.date.today().isoformat(), "cluster": label,
                        "size": len(members_of.get(label, [])),
                        "best_sim": round(best[label], 3), "median_sim": round(med, 3),
                        "titles": cl_titles[:6], "flavor": draft, "status": "pending"}
    PROPOSALS_F.write_text(json.dumps(props, indent=2, ensure_ascii=False))
    return [p for p in props.values() if p.get("status") == "pending"]


if __name__ == "__main__":
    p = load_profile()
    if not p:
        raise SystemExit("no readable interest_profile.json")
    up, down = recent_vote_titles()
    print(f"profile version: {p.get('version')} · {len(p.get('flavors', []))} flavors "
          f"· votes in prompt: {len(up)} up / {len(down)} down\n")
    print(build_prompt(p, up, down))
