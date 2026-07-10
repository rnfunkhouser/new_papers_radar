# Project notes for Claude Code

This is the **Daily Papers Radar** — a personal research radar that emails a faculty member a
daily, hand-picked briefing of new scholarly papers and serves a companion dashboard. It is a
**public template** for University of Idaho faculty; someone using it has clicked *"Use this
template"* to make their own copy.

## Orientation
- **Start with `SETUP_GUIDE.md`** — the plain-language, faculty-facing setup walkthrough.
- **`README.md`** explains how the pipeline thinks (gather → filter → rank → select → write →
  deliver) and lists every file.
- **`config.toml`** is the single place users tune the radar (email, field journals, arXiv
  categories, scoring weights, gate thresholds). `config.py` loads it via the stdlib `tomllib`.

## Design constraints (please preserve)
- **Pure standard library.** No third-party Python packages. Requires **Python 3.11+** (for
  `tomllib`). The container also uses `pypdf` for full-text extraction, installed in the image.
- **Reproducible & standalone.** Every script is runnable on its own from the command line with
  explicit file inputs/outputs. Don't introduce hidden state or interactive-only steps.
- **Secrets never get committed.** `mindrouter.json`, `.briefing_env`, `zotero.json`,
  `.deploy_env`, and the user's `seeds.txt` are git-ignored. Only their `*.example` templates
  and `config.toml` are tracked. Keep it that way.
- **No hardcoded personal or machine-specific values.** Field/tuning values go in `config.toml`;
  VM/host paths come from env or `.deploy_env`. Don't bake in emails, hostnames, or absolute
  paths.
- **Campus services.** The "smart" work (embeddings, reranking, summary writing) runs on
  **MindRouter** (`mindrouter.uidaho.edu`), the UIdaho AI gateway. The daily job runs on a
  campus **VM** (requested from `rcds@uidaho.edu`) via Docker; a launchd alternative exists for
  an always-on Mac.

## When making changes
- If you add a new tunable, expose it in `config.toml` (with a comment) and read it through
  `config.py`, rather than hardcoding it in a script.
- Keep `README.md` and `SETUP_GUIDE.md` in sync with behavior changes.
- The state files (`seeds_profile.json`, `*_embeddings.json`, `seen.json`, `clusters.json`,
  etc.) are all rebuildable via `python3 harvest.py --build-profile`; never commit them.
