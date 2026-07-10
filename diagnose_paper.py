#!/usr/bin/env python3
"""diagnose_paper.py — why did (or didn't) a specific paper surface, and how does the
sub-angle reranker change its ranking vs. the old umbrella-statement reranker?

Standalone + reproducible: it re-imports the live harvest/embeddings modules, re-harvests
the real OpenAlex pool for a date window (so the target competes against the same papers it
actually would have), embeds + scores everything, then reranks the shortlist TWICE — once with
the OLD umbrella interest statements, once with the NEW drilled-down sub-angles — and reports
where the target paper lands each way.

Run inside the VM container (MindRouter reachable):
  docker compose exec -T briefing python3 diagnose_paper.py \
      --doi 10.1177/20563051261462091 --since 2026-07-01 --top 5

Outputs a human report to stdout and a JSON blob to diagnose_<doi-slug>.json.
"""
import argparse, json, re, sys, copy
import harvest as H
import embeddings as E


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--doi", required=True)
    ap.add_argument("--since", required=True, help="from_publication_date for the harvest window")
    ap.add_argument("--top", type=int, default=5, help="how many the daily run surfaces")
    ap.add_argument("--rerank-top", type=int, default=H.RERANK_TOP)
    return ap.parse_args()


def build_query_sets(interests):
    """umbrella = one statement per topic (OLD).  sub = sub-angles where present, else the
    statement (NEW / current code).  Returns (umbrella_list, sub_list, provenance dict)."""
    umbrella, sub, prov = [], [], {}
    for i in interests:
        st = i.get("statement")
        label = i.get("label", "?")
        if st:
            umbrella.append(st)
            prov[st] = f"[umbrella] {label}"
        subs = i.get("substatements") or []
        if subs:
            for s in subs:
                sub.append(s)
                prov[s] = f"[sub-angle] {label}"
        elif st:
            sub.append(st)
    return umbrella, sub, prov


def rerank_variant(short, base_rel, queries, rerank_cache, docs):
    """Return {idx: blended_rel} and {idx: (best_query, best_raw)} for this query set."""
    best = [0.0] * len(short)
    best_q = [None] * len(short)
    for q in queries:
        if q not in rerank_cache:
            rerank_cache[q] = E.rerank(q, docs)
        for j, s in enumerate(rerank_cache[q]):
            if s > best[j]:
                best[j] = s
                best_q[j] = q
    blended = {}
    for j, c in enumerate(short):
        blended[id(c)] = (1 - H.RERANK_BLEND) * base_rel[id(c)] + H.RERANK_BLEND * best[j]
    return blended, {id(short[j]): (best_q[j], best[j]) for j in range(len(short))}


def rank_pool(cands, prof, blended_map, base_rel):
    """Set each candidate's _rel_emb to its variant value, score, and return sorted list."""
    for c in cands:
        c["_rel_emb"] = blended_map.get(id(c), base_rel[id(c)])
        c["score"] = H.score(c, prof)
    return sorted(cands, key=lambda c: -c["score"])


def main():
    a = parse_args()
    doi = a.doi.replace("https://doi.org/", "").lower()
    prof = json.loads(H.PROFILE.read_text())
    interests = json.loads(H.INTERESTS.read_text()).get("interests", [])

    # ---- 1. RETRIEVAL: does the daily harvest actually return the target? ----
    print(f"\n=== RETRIEVAL TEST (harvest_openalex, since={a.since}) ===")
    pool = H.harvest_openalex(prof, a.since)
    print(f"  harvest_openalex returned {len(pool)} raw candidates "
          f"(top {H.OPENALEX_CONCEPTS} concepts x {H.OPENALEX_PER_PAGE}/page)")
    in_harvest = [c for c in pool if (c.get("doi") or "").lower() == doi]
    print(f"  target in raw harvest: {'YES' if in_harvest else 'NO'}")

    # Fetch the target explicitly so we can score it even if harvest missed it.
    tw = H.get_json(f"https://api.openalex.org/works/doi:{doi}?mailto={H.MAILTO}")
    if not tw:
        print("  ! could not fetch target from OpenAlex", file=sys.stderr); sys.exit(1)
    target = H.parse_openalex(tw)
    target["_is_target"] = True
    if not in_harvest:
        pool.append(target)   # inject so we can still measure how it WOULD score
        print("  (injected target manually so it can compete in scoring)")

    # ---- 2. filter + dedupe like the real run ----
    pool = [c for c in pool if H.keep(c)]
    pool = H.dedupe(pool)
    tgt = next((c for c in pool if (c.get("doi") or "").lower() == doi), None)
    if tgt is None:
        print("  ! target dropped by keep()/dedupe — cannot score", file=sys.stderr); sys.exit(1)
    tgt["_is_target"] = True
    print(f"  {len(pool)} candidates after keep()+dedupe")

    # ---- 3. embed + base (pre-rerank) relevance ----
    up_embs, down_embs, _ = H.feedback_signal()
    seed_embs = list(E.load_cache().values())
    pos_embs = seed_embs + up_embs
    texts = [((c["title"] or "") + ". " + (c["abstract"] or "")).strip()[:2000] for c in pool]
    vecs = E.embed(texts)
    for c, v in zip(pool, vecs):
        c["_emb"] = v
    H.attach_embedding_relevance(pool, pos_embs, down_embs)
    base_rel = {id(c): c.get("_rel_emb", 0.0) for c in pool}
    print(f"\n=== SEMANTIC RELEVANCE (vs {len(seed_embs)} seeds + {len(up_embs)} upvotes) ===")
    ranked_by_emb = sorted(pool, key=lambda c: -base_rel[id(c)])
    tgt_emb_rank = ranked_by_emb.index(tgt) + 1
    print(f"  target base _rel_emb = {base_rel[id(tgt)]:.3f}  "
          f"(rank {tgt_emb_rank}/{len(pool)} by embedding alone, pre-rerank)")

    # ---- 4. rerank shortlist BOTH ways ----
    short = sorted([c for c in pool if base_rel[id(c)] is not None],
                   key=lambda c: -base_rel[id(c)])[:a.rerank_top]
    docs = [((c["title"] or "") + ". " + (c["abstract"] or ""))[:1200] for c in short]
    tgt_in_short = tgt in short
    umbrella_q, sub_q, prov = build_query_sets(interests)
    print(f"\n=== RERANK A/B  (shortlist={len(short)}, target in shortlist: {tgt_in_short}) ===")
    print(f"  umbrella queries: {len(umbrella_q)} | sub-angle queries: {len(sub_q)}")
    cache = {}
    umb_blend, umb_best = rerank_variant(short, base_rel, umbrella_q, cache, docs)
    sub_blend, sub_best = rerank_variant(short, base_rel, sub_q, cache, docs)

    results = {}
    ranking = {}
    for name, blend, best in [("umbrella", umb_blend, umb_best), ("sub-angle", sub_blend, sub_best)]:
        ranked = rank_pool(pool, prof, blend, base_rel)
        ranking[name] = {id(c): (pos + 1, c["score"]) for pos, c in enumerate(ranked)}
        rank = ranked.index(tgt) + 1
        bq, braw = best.get(id(tgt), (None, 0.0))
        results[name] = {
            "target_rank": rank, "n": len(pool),
            "target_score": tgt["_scores"]["total"],
            "target_rel": round(tgt["_rel_emb"], 3),
            "rerank_raw": round(braw, 3),
            "best_query": prov.get(bq, bq),
            "would_show_top": rank <= a.top,
            "top15": [(round(c["score"], 3), (c.get("doi") or "")[:32],
                       (c["title"] or "")[:70], c.get("_is_target", False))
                      for c in ranked[:15]],
        }

    # ---- 4b. biggest rank movers between the two rerankers (the real A/B) ----
    movers = []
    for c in short:
        ru, su_ = ranking["umbrella"][id(c)], ranking["sub-angle"][id(c)]
        d = ru[0] - su_[0]           # +ve => moved UP under sub-angle
        if d != 0:
            uq = prov.get(umb_best.get(id(c), (None,))[0], "")
            sq = prov.get(sub_best.get(id(c), (None,))[0], "")
            movers.append((d, ru[0], su_[0], (c["title"] or "")[:60], uq, sq))
    n_changed = len(movers)
    movers.sort(key=lambda x: -abs(x[0]))
    print(f"\n=== A/B: {n_changed}/{len(short)} shortlisted papers changed rank; "
          f"top movers (‑ = demoted by sub-angle, + = promoted) ===")
    for d, ru, su_, title, uq, sq in movers[:12]:
        print(f"  {d:+4d}  (#{ru}->#{su_})  {title}")
        print(f"        sub-angle best: {sq}")
    results["n_changed"] = n_changed

    # ---- 5. report ----
    for name in ("umbrella", "sub-angle"):
        r = results[name]
        print(f"\n----- {name.upper()} reranker -----")
        print(f"  TARGET rank {r['target_rank']}/{r['n']}  score={r['target_score']}  "
              f"rel={r['target_rel']}  rerank_raw={r['rerank_raw']}")
        print(f"  best-matching query: {r['best_query']}")
        print(f"  would land in top-{a.top}: {'YES' if r['would_show_top'] else 'NO'}")
        print("  top 15:")
        for sc, d, t, isT in r["top15"]:
            print(f"    {'>>' if isT else '  '} {sc:5.3f}  {t}")

    slug = re.sub(r"\W+", "", doi)[-12:]
    out = f"diagnose_{slug}.json"
    json.dump({"doi": doi, "in_raw_harvest": bool(in_harvest),
               "target_emb_rank": tgt_emb_rank, "base_rel": base_rel[id(tgt)],
               "results": {k: {kk: vv for kk, vv in v.items() if kk != "top15"}
                           for k, v in results.items()}},
              open(out, "w"), indent=2)
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
