#!/usr/bin/env python3
"""
config.py — load user settings from config.toml (pure standard library).

Everything a person would reasonably want to tune — their email, their field's
journals and arXiv categories, the scoring weights and gate thresholds — used to
be hardcoded near the top of harvest.py. It now lives in config.toml, so you can
make the radar yours by editing plain text instead of code. This module reads
that file once, fills in sensible defaults for anything you leave out, and
exposes the values as module-level constants that harvest.py and friends import.

Reading uses `tomllib`, which is part of the Python standard library on Python
3.11 and newer (no `pip install` needed) — matching the zero-dependency design
of the rest of this project. If you are on an older Python, upgrade to 3.11+.

    import config
    print(config.MAILTO, config.ARXIV_CATS)
"""

import sys
from pathlib import Path

try:
    import tomllib                      # Python 3.11+ standard library
except ModuleNotFoundError:             # pragma: no cover
    sys.exit("config.py needs Python 3.11 or newer (for the built-in 'tomllib'). "
             "Please upgrade Python and try again.")

HERE = Path(__file__).parent
_CFG_PATH = HERE / "config.toml"


def _load():
    """Return the parsed config.toml as a dict, or {} if it is missing/unreadable
    (in which case every value below falls back to its built-in default)."""
    if not _CFG_PATH.exists():
        print(f"  (no config.toml found next to config.py — using built-in defaults)",
              file=sys.stderr)
        return {}
    try:
        with _CFG_PATH.open("rb") as f:
            return tomllib.load(f)
    except Exception as e:
        sys.exit(f"config.toml could not be parsed ({e}). Check for a typo — every "
                 "opening quote/bracket needs a matching closing one.")


_cfg = _load()


def _section(name):
    sec = _cfg.get(name, {})
    return sec if isinstance(sec, dict) else {}


_contact  = _section("contact")
_briefing = _section("briefing")
_field    = _section("field")
_harvest  = _section("harvest")
_scoring  = _section("scoring")
_rerank   = _section("rerank")

# ---------------------------------------------------------------------------
# [contact]
# ---------------------------------------------------------------------------
MAILTO = _contact.get("mailto", "you@example.edu")

# ---------------------------------------------------------------------------
# [briefing]
# ---------------------------------------------------------------------------
BRIEFING_AUDIENCE = _briefing.get(
    "audience",
    "a researcher (summarize each paper faithfully for a specialist in the field)")

# ---------------------------------------------------------------------------
# [field]  — names match the constants harvest.py has always used, so the rest
# of the code imports them unchanged.
# ---------------------------------------------------------------------------
ARXIV_CATS = list(_field.get("arxiv_categories", ["cs.CY", "cs.SI", "cs.CL"]))

VENUE_ALLOWLIST = [v.lower().strip() for v in _field.get("trusted_venues", [])]

PRESTIGE_VENUES = tuple(v.lower().strip() for v in _field.get("prestige_venues", (
    "nature", "science", "proceedings of the national academy of sciences", "pnas",
    "nature human behaviour", "nature communications", "science advances",
    "psychological science",
)))

PREFERRED_COUNTRIES = {c.upper().strip() for c in _field.get("preferred_countries", [
    "US", "CA", "GB", "IE", "AU", "NZ",
    "DE", "FR", "NL", "BE", "CH", "AT", "SE", "NO", "DK", "FI", "IT", "ES", "PT",
])}

# ---------------------------------------------------------------------------
# [harvest]
# ---------------------------------------------------------------------------
DAYS_WINDOW              = int(_harvest.get("days_window", 14))
TOP_N                    = int(_harvest.get("top_n", 5))
OPENALEX_CONCEPTS        = int(_harvest.get("openalex_concepts", 22))
OPENALEX_PER_PAGE        = int(_harvest.get("openalex_per_page", 200))
OPENALEX_MAX_PER_CONCEPT = int(_harvest.get("openalex_max_per_concept", 800))
ARXIV_MAX                = int(_harvest.get("arxiv_max", 120))

# ---------------------------------------------------------------------------
# [scoring]
# ---------------------------------------------------------------------------
WEIGHTS = dict(
    relevance=float(_scoring.get("weight_relevance", 0.50)),
    quality=float(_scoring.get("weight_quality", 0.35)),
    recency=float(_scoring.get("weight_recency", 0.15)),
)
REL_NORM            = float(_scoring.get("rel_norm", 3.0))
PROCEEDINGS_PENALTY = float(_scoring.get("proceedings_penalty", 0.75))
GEO_GATE_REL        = float(_scoring.get("geo_gate_rel", 0.90))
GEO_GATE_PENALTY    = float(_scoring.get("geo_gate_penalty", 0.55))
TYPE_GATE_REL       = float(_scoring.get("type_gate_rel", 0.85))
TYPE_GATE_PENALTY   = float(_scoring.get("type_gate_penalty", 0.40))

# ---------------------------------------------------------------------------
# [rerank]
# ---------------------------------------------------------------------------
N_CLUSTERS          = int(_rerank.get("n_clusters", 10))
SUBDIVIDE_THRESHOLD = int(_rerank.get("subdivide_threshold", 13))
RERANK_TOP          = int(_rerank.get("rerank_top", 200))
RERANK_BLEND        = float(_rerank.get("rerank_blend", 0.6))
