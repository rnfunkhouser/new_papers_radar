#!/usr/bin/env python3
"""rank_report.py — READ-ONLY ranking report. Reproduces the daily harvest+score pipeline
(using the cached candidate embeddings, so it's fast) but writes NOTHING and records no
'seen' state. Prints where a given paper lands both overall and among UNSEEN papers — the
latter is what the daily briefing actually draws its top-5 from.

  docker compose exec -T briefing python3 rank_report.py --doi <DOI> --days 21
"""
import argparse, json, datetime as dt
import harvest as H, embeddings as E


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--doi", required=True)
    ap.add_argument("--days", type=int, default=21)
    ap.add_argument("--show", type=int, default=20, help="how many top UNSEEN to print")
    a = ap.parse_args()
    doi = a.doi.replace("https://doi.org/", "").lower()

    prof = json.loads(H.PROFILE.read_text())
    since = (dt.date.today() - dt.timedelta(days=a.days)).isoformat()
    cands = []
    cands += H.harvest_openalex(prof, since)
    cands += H.harvest_arxiv(since)
    cands += H.harvest_s2_recommendations(prof, since)
    wl, seen = H.load_watchlist(), H.load_seen()
    cands += H.recheck_watchlist(wl, seen)
    cands = [c for c in cands if H.keep(c)]
    cands = H.dedupe(cands)
    up, down, down_id = H.feedback_signal()
    cands = [c for c in cands if (c.get("doi", "") or "").lower() not in down_id
             and H._slug(c.get("title")) not in down_id]

    # embeddings from the cache (fast); embed only anything not yet cached
    seed_embs = list(E.load_cache().values())
    pos = seed_embs + up
    dc = E.load_doc_cache()
    need_i, need_t = [], []
    for i, c in enumerate(cands):
        hit = dc.get(H.key_of(c))
        if hit:
            c["_emb"] = E.unpack_vec(hit[1])
        else:
            need_i.append(i)
            need_t.append(((c["title"] or "") + ". " + (c["abstract"] or "")).strip()[:2000])
    if need_t:
        v = E.embed(need_t)
        for j, i in enumerate(need_i):
            cands[i]["_emb"] = v[j]
    print(f"  {len(cands)} candidates ({len(cands) - len(need_t)} from cache, {len(need_t)} freshly embedded)")
    H.attach_embedding_relevance(cands, pos, down)
    H.rerank_interests(cands)
    stats = H.fetch_venue_stats([c.get("source_id", "") for c in cands])
    for c in cands:
        c["_venue_c2"] = stats.get(c.get("source_id", ""), 0.0)
    tw = H.load_topic_weights()
    clusters = json.loads(H.CLUSTERS.read_text()).get("clusters", []) if H.CLUSTERS.exists() else []
    for c in cands:
        c["score"] = H.score(c, prof)
        if clusters and c.get("_emb"):
            c["topic"] = H.assign_topic(c["_emb"], clusters)
            w = tw.get(c["topic"])
            if w:
                c["score"] = round(c["score"] * w, 4)
    cands.sort(key=lambda c: -c["score"])

    # locate target overall + among unseen
    ur = 0
    tgt_overall = tgt_unseen = None
    for i, c in enumerate(cands):
        is_seen = H.key_of(c) in seen
        if not is_seen:
            ur += 1
        if (c.get("doi") or "").lower() == doi:
            tgt_overall, tgt_unseen = i + 1, (None if is_seen else ur)
    n_seen_in_pool = sum(1 for c in cands if H.key_of(c) in seen)
    print(f"\n  TARGET overall rank: {tgt_overall}/{len(cands)}")
    print(f"  TARGET among-UNSEEN rank: {tgt_unseen}   ({n_seen_in_pool} of the pool already shown)")
    print(f"\n  Top {a.show} UNSEEN (what the daily briefing draws its 5 from):")
    ur = 0
    for i, c in enumerate(cands):
        if H.key_of(c) in seen:
            continue
        ur += 1
        is_t = (c.get("doi") or "").lower() == doi
        if ur <= a.show or is_t:
            print(f"    unseen#{ur:<3} (overall {i+1:<4}) score={c['score']:.3f}  "
                  f"{(c['title'] or '')[:52]}{'   <<< TARGET' if is_t else ''}")
        if ur > a.show and is_t:
            break


if __name__ == "__main__":
    main()
