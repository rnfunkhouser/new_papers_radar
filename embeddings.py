#!/usr/bin/env python3
"""
embeddings.py — semantic embeddings via MindRouter (UIdaho campus LLM gateway).

Pure standard library (urllib): the /v1/embeddings endpoint is OpenAI-compatible, so
no `openai` package or other pip dependency is needed — matching the rest of this project.

Used to score a candidate paper by how close its abstract sits to your *nearest* seed
papers (cosine similarity). That is inherently cluster-aware: a paper in a small niche of
your interests scores high because it is close to that niche's few seeds, no matter how many
seeds you have in other areas — which fixes the "big topic drowns out a small one" problem
that plain concept-tag counting suffered.

Everything here is best-effort: if MindRouter is unreachable (e.g. off the campus VPN at
5:57am), embed() raises EmbeddingsUnavailable and the harvester falls back to tag scoring.
"""

import json, math, ssl, urllib.request
from pathlib import Path

HERE = Path(__file__).parent
CFG = HERE / "mindrouter.json"
CACHE = HERE / "seeds_embeddings.json"     # {doi: vector} for seeds, so we embed each seed once
DOC_CACHE = HERE / "doc_embeddings.json"   # {key: [iso_first_seen, packed_f16_vector]} for
                                           # CANDIDATE papers, so each is embedded once ever
                                           # (not re-embedded every day it sits in the window)
DOC_CACHE_TTL_DAYS = 35                     # drop cached vectors older than this (window is ~21d)
DEFAULT_MODEL = "Qwen/Qwen3-Embedding-8B"


class EmbeddingsUnavailable(Exception):
    """Raised when MindRouter can't be reached or isn't configured. Caller falls back."""


def _ssl_ctx():
    for cafile in ("/etc/ssl/cert.pem", "/private/etc/ssl/cert.pem"):
        if Path(cafile).exists():
            try:
                return ssl.create_default_context(cafile=cafile)
            except Exception:
                pass
    return ssl.create_default_context()


def _config():
    if not CFG.exists():
        return None
    try:
        c = json.loads(CFG.read_text())
    except Exception:
        return None
    if not (c.get("base_url") and c.get("api_key", "").strip()):
        return None
    return c


def available():
    """True if a usable mindrouter.json exists (does NOT prove the network is up)."""
    return _config() is not None


def _get(path, timeout=30):
    cfg = _config()
    if not cfg:
        raise EmbeddingsUnavailable("mindrouter.json missing or has no api_key")
    req = urllib.request.Request(
        cfg["base_url"].rstrip("/") + path,
        headers={"Authorization": f"Bearer {cfg['api_key'].strip()}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx()) as r:
            return json.load(r)
    except Exception as e:
        raise EmbeddingsUnavailable(f"{type(e).__name__}: {e}")


def list_models():
    """Return {id: model_dict} from GET /v1/models. MindRouter does EXACT name matching
    (no aliases) and the admin 'default model' is chat-UI-only, so we resolve client-side."""
    data = _get("/models")
    return {m["id"]: m for m in data.get("data", []) if m.get("id")}


_RESOLVED_CHAT = None       # cache within a run so we log/resolve once

def resolve_chat_model(cfg=None):
    """Pick the chat model for the writing step. If config pins a literal name, use it.
    Otherwise walk an ordered preference list and return the first present in the LIVE
    catalog; if none match, fall back to the largest chat-capable model. This keeps a
    decommissioned model from silently breaking the daily run. Cached per process."""
    global _RESOLVED_CHAT
    if _RESOLVED_CHAT:
        return _RESOLVED_CHAT
    cfg = cfg or _config() or {}
    pinned = cfg.get("chat_model")
    prefs = cfg.get("chat_models") or [
        "openai/gpt-oss-120b", "qwen/qwen3.5-122b", "Nemotron-3-Super-120b",
        "google/gemma-4-31b", "default-llm-large", "default-llm"]
    if pinned and pinned != "auto":
        _RESOLVED_CHAT = pinned
        return pinned
    catalog = list_models()
    for name in prefs:
        if name in catalog:
            _RESOLVED_CHAT = name
            return name
    # last resort: largest non-embedding, non-reranker chat model by parameter_count
    chat = [(m.get("parameter_count") or 0, mid) for mid, m in catalog.items()
            if not m.get("capabilities", {}).get("embeddings")
            and "rerank" not in mid.lower()]
    if not chat:
        raise EmbeddingsUnavailable("no chat-capable model found in catalog")
    _RESOLVED_CHAT = max(chat)[1]
    return _RESOLVED_CHAT


def embed(texts, batch_size=64, timeout=120):
    """Embed a list of strings -> list of float vectors (same order). Batched to keep the
    request count low. Raises EmbeddingsUnavailable on any config/network/HTTP failure."""
    cfg = _config()
    if not cfg:
        raise EmbeddingsUnavailable("mindrouter.json missing or has no api_key")
    base = cfg["base_url"].rstrip("/")
    key = cfg["api_key"].strip()
    model = cfg.get("embedding_model") or DEFAULT_MODEL
    ctx = _ssl_ctx()
    out = []
    for i in range(0, len(texts), batch_size):
        chunk = [t if t.strip() else " " for t in texts[i:i + batch_size]]
        body = json.dumps({"model": model, "input": chunk}).encode()
        req = urllib.request.Request(
            base + "/embeddings", data=body, method="POST",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                data = json.load(r)
        except Exception as e:
            raise EmbeddingsUnavailable(f"{type(e).__name__}: {e}")
        rows = sorted(data.get("data", []), key=lambda d: d.get("index", 0))
        if len(rows) != len(chunk):
            raise EmbeddingsUnavailable("response count mismatch")
        out.extend(d["embedding"] for d in rows)
    return out


def _post(path, payload, timeout=180):
    cfg = _config()
    if not cfg:
        raise EmbeddingsUnavailable("mindrouter.json missing or has no api_key")
    base = cfg["base_url"].rstrip("/")
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        base + path, data=body, method="POST",
        headers={"Authorization": f"Bearer {cfg['api_key'].strip()}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx()) as r:
            return json.load(r)
    except Exception as e:
        raise EmbeddingsUnavailable(f"{type(e).__name__}: {e}")


def chat(system, user, model=None, temperature=0.4):
    """One MindRouter chat call. model=None resolves via resolve_chat_model(). gpt-oss/
    reasoning models put output in reasoning_content with null content — read both.
    Raises EmbeddingsUnavailable on failure."""
    if model is None:
        model = resolve_chat_model()
    data = _post("/chat/completions", {
        "model": model, "temperature": temperature,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}]}, timeout=360)
    msg = (data.get("choices") or [{}])[0].get("message", {})
    return (msg.get("content") or msg.get("reasoning_content") or "").strip()


def rerank(query, documents, model="Qwen/Qwen3-Reranker-8B", batch=64):
    """Cross-encoder relevance of each document to the query -> list of floats [0,1],
    same order as documents. Much sharper contextual fit than cosine similarity."""
    scores = [0.0] * len(documents)
    for i in range(0, len(documents), batch):
        chunk = documents[i:i + batch]
        data = _post("/rerank", {"model": model, "query": query, "documents": chunk})
        for r in data.get("results", []):
            scores[i + r["index"]] = float(r.get("relevance_score", 0.0))
    return scores


def cosine(a, b):
    s = na = nb = 0.0
    for x, y in zip(a, b):
        s += x * y; na += x * x; nb += y * y
    return s / (math.sqrt(na) * math.sqrt(nb)) if na and nb else 0.0


def normalize(v):
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _sqdist(a, b):
    return sum((x - y) * (x - y) for x, y in zip(a, b))


def kmeans(vectors, k, iters=30, seed=1234):
    """Tiny spherical k-means (normalize first, then Lloyd's with k-means++ init).
    Returns (centroids, assignments). Pure stdlib; fine for ~100 seeds x 4096 dims."""
    import random
    pts = [normalize(v) for v in vectors]
    n = len(pts)
    if n == 0:
        return [], []
    k = max(1, min(k, n))
    rng = random.Random(seed)
    centroids = [pts[rng.randrange(n)][:]]
    while len(centroids) < k:                                  # k-means++ spread
        d2 = [min(_sqdist(p, c) for c in centroids) for p in pts]
        tot = sum(d2) or 1.0
        r, acc, idx = rng.random() * tot, 0.0, 0
        for i, dd in enumerate(d2):
            acc += dd
            if acc >= r:
                idx = i; break
        centroids.append(pts[idx][:])
    assign = [0] * n
    for _ in range(iters):
        changed = False
        for i, p in enumerate(pts):
            best = min(range(len(centroids)), key=lambda c: _sqdist(p, centroids[c]))
            if best != assign[i]:
                assign[i] = best; changed = True
        for c in range(len(centroids)):
            members = [pts[i] for i in range(n) if assign[i] == c]
            if members:
                centroids[c] = normalize([sum(col) / len(members) for col in zip(*members)])
        if not changed:
            break
    return centroids, assign


def load_cache():
    if CACHE.exists():
        try:
            return json.loads(CACHE.read_text())
        except Exception:
            return {}
    return {}


def save_cache(cache):
    CACHE.write_text(json.dumps(cache))


def update_seed_cache(seed_texts, all_seed_dois):
    """Embed any seeds not yet cached, and prune embeddings for seeds no longer present.
    seed_texts: {doi_lower: 'title. abstract'} for seeds we have text for.
    all_seed_dois: the authoritative set (lowercased) of DOIs currently in seeds.txt.
    Returns (cache, n_new). Raises EmbeddingsUnavailable if the embed call fails."""
    cache = load_cache()
    cache = {d: v for d, v in cache.items() if d in all_seed_dois}      # prune removed seeds
    todo = [(d, t) for d, t in seed_texts.items() if d not in cache and t.strip()]
    if todo:
        vecs = embed([t for _, t in todo])
        for (d, _), v in zip(todo, vecs):
            cache[d] = v
    save_cache(cache)
    return cache, len(todo)


# --- candidate embedding cache: embed each harvested paper once, ever -------------------
# Vectors are stored as base64(float16) — ~6x smaller than JSON floats, and half precision
# is ample for cosine similarity — so the cache stays a manageable size even across weeks
# of daily harvests.

def pack_vec(v):
    import struct, base64
    return base64.b64encode(struct.pack(f"{len(v)}e", *v)).decode("ascii")


def unpack_vec(s):
    import struct, base64
    b = base64.b64decode(s)
    return list(struct.unpack(f"{len(b) // 2}e", b))


def load_doc_cache():
    if DOC_CACHE.exists():
        try:
            return json.loads(DOC_CACHE.read_text())
        except Exception:
            return {}
    return {}


def save_doc_cache(cache):
    DOC_CACHE.write_text(json.dumps(cache))
