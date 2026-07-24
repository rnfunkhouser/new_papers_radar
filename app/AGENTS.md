# Instructions for AI coding assistants

You are helping a researcher (often not a programmer) set up their personal "Daily Papers
Radar" — this repository. Your most common job is **connecting the app to their LLM
provider**. Read this whole file before acting.

## What the app needs from an LLM provider

Exactly two capabilities, via any **OpenAI-compatible HTTP API**:

1. **Embeddings** — `POST {base_url}/embeddings` with `{"model": ..., "input": [texts]}`.
   Used to fingerprint papers (a few thousand short texts on the first run; only new papers
   daily). Any modern text-embedding model works.
2. **Chat completions** — `POST {base_url}/chat/completions` with system+user messages.
   Used by the relevance judge (a few hundred short calls/day, cached so each paper is
   judged once ever) and to write ~5 summaries/day. A small/cheap model is fine for both.

Optional, legacy-only: `POST {base_url}/rerank` (cross-encoder). The daily pipeline does
NOT need it — only two offline diagnostic scripts use it. Never block setup on it.

All calls go through `embeddings.py` (pure standard library, `urllib`). There is no OpenAI
SDK dependency and none should be added.

## The one file you configure: `llm_api.json`

Copy `llm_api.json.example` → `llm_api.json` (this file is gitignored — it holds the API
key; never commit it) and fill in:

```json
{
  "base_url": "https://api.openai.com/v1",
  "api_key": "sk-...",
  "embedding_model": "<an available embedding model>",
  "chat_model": "<an available chat model>"
}
```

- `base_url` must be the path that has `/embeddings` and `/chat/completions` under it
  (for OpenAI that's `https://api.openai.com/v1`; for campus gateways and self-hosted
  servers ask the user or probe `GET {base_url}/models`).
- `embedding_model` is REQUIRED (the code refuses to guess). Pick a current, cheap
  embedding model available on the user's account — check `GET {base_url}/models` rather
  than assuming model names from your training data.
- `chat_model` is strongly recommended. If unset, the app probes `/models` and picks the
  largest chat-capable model, which may be needlessly expensive — set it explicitly.
- `chat_models` (a preference list) and `rerank_model` are optional.

For **self-hosted** (Ollama, vLLM, LM Studio, etc.): any server exposing the OpenAI API
shape works; `api_key` can be any non-empty string if the server doesn't check it. Confirm
the server hosts BOTH an embedding model and a chat model.

## How to verify the connection (do this before declaring success)

Run from this `app/` directory:

```bash
python3 - <<'EOF'
import embeddings
print("config found:", embeddings.available())
v = embeddings.embed(["hello world"])           # embeddings round-trip
print("embedding ok, dim =", len(v[0]))
print("chat model:", embeddings.resolve_chat_model())
print("chat ok:", embeddings.chat("Reply with exactly: OK", "ping", temperature=0.0)[:20])
EOF
```

All three lines must succeed. Typical failures: wrong `base_url` path (missing `/v1`),
model name not available on the account (list `GET {base_url}/models`), network/VPN
restrictions on campus gateways.

If the user has run the app before your fix, stale state is not a problem — everything
recomputes; but do NOT delete `seeds.txt`, `interest_profile.json`, `feedback.json`,
`seen.json`, or anything the user typed.

## Other jobs you may be asked to do

- **First-time setup**: follow ../SETUP_GUIDE.md literally; your role is the `llm_api.json`
  step, `.briefing_env` (Gmail app password — the user must create it themselves), and
  running `python3 harvest.py --build-profile` then `python3 harvest.py` and interpreting
  errors.
- **Estimate costs**: first run embeds a ~2-week backlog (thousands of short texts) and
  judges ~1,000 abstracts; afterwards only new papers pay. With typical commercial pricing
  this is dollars for the first run and cents per day after — compute a real estimate from
  the user's provider prices and say what you assumed. Also warn that costs scale with the
  knobs in `config.toml` `[harvest]` and `[judge]`.
- **Deploying to an always-on machine**: `deploy.sh` (reads `.deploy_env` for VM/DEST),
  Docker via `docker-compose.yml`, cron at 05:57 in `crontab`. Secrets are scp'd once, never
  rsynced.

## Rules

- Do not modify the ranking/judging logic, prompts, thresholds, or file formats to "fix" a
  connection problem — connection problems live in `llm_api.json`, the network, or model
  names.
- Keep the zero-dependency rule: standard library only, no `pip install`.
- Never commit or print the contents of `llm_api.json`, `.briefing_env`, or `zotero.json`.
- The user-owned files are sacred: `seeds.txt`, `interest_profile.json` (also editable from
  the dashboard's Selection Criteria page), `config.toml`, `feedback.json`.
