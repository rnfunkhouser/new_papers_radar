#!/usr/bin/env bash
# run_daily.sh — the whole morning routine, in order:
#   1. harvest fresh candidates (Zotero sync + OpenAlex/arXiv/S2, scored)
#   2. the briefing prose is written in the house style -> briefings/briefing_<date>.md
#   3. deliver.py makes a PDF and emails it
#
# Portable between the Mac (launchd) and the campus VM container (cron). Two env knobs,
# both with Mac-preserving defaults so nothing changes on the Mac if they're unset:
#   PROJECT_DIR       — folder path (default: the folder this script lives in)
#   BRIEFING_WRITER   — "llm" (default; uses write_briefing.py via the OpenAI-compatible
#                       API in llm_api.json; this is what the container sets) or
#                       "claude" (alternative writer; needs the Claude Code CLI + login)
PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "$0")" && pwd)}"
BRIEFING_WRITER="${BRIEFING_WRITER:-llm}"

set -uo pipefail
cd "$PROJECT_DIR" || exit 1
DATE="$(date +%F)"
mkdir -p briefings logs
LOG="logs/run_${DATE}.log"
exec >>"$LOG" 2>&1
echo "===== $(date) starting daily run (writer=$BRIEFING_WRITER) ====="

# Make binaries findable under a minimal cron/launchd PATH (Mac + Linux locations).
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:/usr/local/sbin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

# 1. Harvest -------------------------------------------------------------------
# Pull any new papers from the public Zotero seed library first (append-only; a no-op
# if zotero.json isn't configured or nothing new was added).
echo "--- zotero sync ---"
python3 harvest.py --sync-zotero || echo "zotero sync skipped"

# The lookback window (config.toml -> [harvest] days_window, default 14): the no-repeat ledger means this surfaces papers you haven't already seen
# from the last two weeks, so (a) a strong paper that missed the top-5 on a busy day keeps
# re-competing, and (b) a paper OpenAlex indexes days AFTER its print date still lands inside
# the window instead of being missed. 14 days covers the typical 2-5 day indexing lag with
# buffer while keeping competition (and time-to-surface) reasonable. The wider window costs
# almost nothing: candidate embeddings are cached per paper (doc_embeddings.json), so each day
# only embeds papers that are genuinely new since yesterday, not the whole window.
echo "--- harvest ---"
python3 harvest.py || { echo "harvest failed"; exit 1; }

# Try to fetch open-access FULL TEXT for the selected papers (arXiv / Unpaywall / oa_url),
# so summaries can be grounded in the actual paper. Best-effort; adds fulltext_path to
# candidates.json records it succeeds on.
echo "--- fulltext ---"
python3 fetch_fulltext.py || echo "fulltext fetch skipped"

# 2. Write the briefing -------------------------------------------------------
if [[ "$BRIEFING_WRITER" == "llm" ]]; then
  echo "--- llm briefing ---"
  python3 write_briefing.py "$DATE" || { echo "llm briefing failed"; exit 1; }
else
echo "--- claude briefing ---"
PROMPT="Read candidates.json in this folder (a ranked shortlist of new papers with \
abstracts; each record has 'doi' and 'oa_url' fields). Write today's briefing and save \
it to briefings/briefing_${DATE}.md. Title the document EXACTLY '# Daily Papers \
Radar' on the first line, with '### <full weekday>, <Month> <D>, <YYYY>' as the date \
subheading on the next line. For everything else use this house style: \
a hook headline per paper and the citation line (authors · venue · date). DATES: records may carry a FUTURE 'date' (an advance-access print date) — \
never present a future date as the publication date; cite those as 'in press' and, when \
the record has a past 'created' date, give it as the online date (e.g. 'online June 28, \
2026 · in press'). Write EVERY paper at full depth — roughly 300-450 words each by default, \
structured as flowing prose (not headed subsections) that covers: (1) the background — \
what question the paper takes up and why it arose in the literature; (2) the method — \
design, sample size, measures, analytic approach, as specifically as the record allows; \
(3) the results — the concrete findings with numbers and effect sizes wherever available, \
not just the headline; and (4) the discussion — what the authors conclude and why it \
matters. Tone: tight, academic/journalistic summary. Do NOT editorialize or add opinions; \
it IS fine to translate the results' practical import in plain language in the closing \
'why it matters' turn. \
FULL TEXT: some records in candidates.json carry a 'fulltext_path' — the actual paper \
(PDF or HTML), fetched from an open-access source. For those papers, READ the file (use \
the pages parameter in chunks for long PDFs) and ground the entry in the paper itself: \
the real design, exact Ns, effect sizes from the results section, and limitations the \
authors state. For these full-text entries you MAY run up to ~75% longer (up to ~780 \
words) — but ONLY when the paper genuinely contains additional meaningful insight \
(important moderators, surprising robustness checks, notable limitations, striking \
secondary findings). Default to the standard length; never pad to reach the ceiling. \
GROUNDING — for papers WITHOUT fulltext_path you see only title/abstract/metadata: NEVER \
invent methods, sample sizes, numbers, effect sizes, or findings not stated in the \
record. When the abstract omits them, say so plainly ('the abstract does not report \
effect sizes'). If a record has no abstract at all, write at most a short 1-2 paragraph \
entry grounded strictly in title/venue/authors and label it as such — a shorter honest \
entry always beats a longer speculative one. \
EVERY paper MUST include a clickable markdown link to its source article, on its own \
line in the citation block: use https://doi.org/<doi> when the record has a non-empty \
doi, otherwise use its oa_url; if it has neither, write '(no link available)'. Format \
links as [readable label](url). \
Order papers best-first. If fewer than five clear the bar, include fewer. End with the \
short 'How these were chosen' note. \
ALSO write a second file, briefings/cards_${DATE}.json — a JSON array with one object per \
paper you INCLUDED (skip any you dropped), each: {\"title\": <the exact 'title' string \
from candidates.json>, \"blurb\": <the SAME full summary you wrote for this paper in the \
briefing above — i.e. the descriptive body text for this paper, EXCLUDING only its '## ' \
hook headline and the citation/link line. Reproduce the full-depth prose verbatim (or all \
but trivially reworded) so the dashboard shows exactly the same summary as the PDF, same \
length, same full-text grounding, same anti-fabrication rules. Do NOT shorten or condense \
it.>}. Output only the two files; do not print the briefing."

claude -p "$PROMPT" \
  --permission-mode acceptEdits \
  --allowed-tools "Read" "Write" \
  || { echo "claude briefing failed"; exit 1; }
fi

BRIEF="briefings/briefing_${DATE}.md"
if [[ ! -s "$BRIEF" ]]; then
  echo "no briefing file produced at $BRIEF"; exit 1
fi

# Build the dashboard's data for today from the papers Claude kept (+ its summaries),
# so the dashboard mirrors the briefing instead of the raw candidate list.
echo "--- dashboard data ---"
python3 dashboard.py build "$DATE" || echo "dashboard build skipped"

# 3. PDF + email ---------------------------------------------------------------
echo "--- deliver ---"
python3 deliver.py "$BRIEF" || { echo "deliver failed"; exit 1; }

echo "===== $(date) done ====="
