#!/usr/bin/env bash
# vm_recon.sh — read-only preflight for containerizing the paper-briefing app.
# Run ON the VM (or: ssh devops@your-vm.nkn.uidaho.edu 'bash -s' < vm_recon.sh)
# Optional: export MR_KEY=mr2_xxx  before running, to list MindRouter models.
# Nothing here changes any state.

line(){ printf '\n=== %s ===\n' "$1"; }
ok(){ printf '  OK   %s\n' "$1"; }
bad(){ printf '  FAIL %s\n' "$1"; }
probe(){ # probe <label> <url>
  if curl -fsS --max-time 12 -o /dev/null "$2" 2>/dev/null; then ok "$1 reachable"; else bad "$1 UNreachable ($2)"; fi
}

line "host / resources"
hostname; uname -a
echo "CPUs: $(nproc 2>/dev/null)  |  $(free -h 2>/dev/null | awk '/Mem:/{print \"RAM \"$2\" (avail \"$7\")\"}')"
df -h / 2>/dev/null | awk 'NR==1||/\/$/'

line "docker / compose"
if command -v docker >/dev/null; then docker --version; docker compose version 2>/dev/null || echo "  (no 'docker compose' subcommand)"; docker-compose --version 2>/dev/null
  docker info --format '  rootless={{.SecurityOptions}}' 2>/dev/null | grep -i rootless || echo "  (rootful or info blocked)"
else bad "docker NOT installed"; fi
command -v podman >/dev/null && { echo "podman present:"; podman --version; }
command -v apptainer >/dev/null && echo "apptainer present" ; command -v singularity >/dev/null && echo "singularity present"

line "outbound egress to the services the pipeline calls"
probe "OpenAlex"        "https://api.openalex.org/works?per-page=1"
probe "arXiv"           "https://export.arxiv.org/api/query?max_results=1"
probe "Semantic Scholar" "https://api.semanticscholar.org/graph/v1/paper/search?query=test&limit=1"
probe "Crossref"        "https://api.crossref.org/works?rows=1"
probe "Unpaywall"       "https://api.unpaywall.org/v2/10.1038/nature12373?email=test@test.edu"
probe "Zotero API"      "https://api.zotero.org/groups/0000000/items?limit=1"  # your group id, or skip

line "MindRouter (campus, no VPN needed here)"
probe "MindRouter /healthz" "https://mindrouter.uidaho.edu/healthz"
if [ -n "$MR_KEY" ]; then
  echo "  chat/embedding/rerank models visible to your key:"
  curl -fsS --max-time 15 -H "Authorization: Bearer $MR_KEY" https://mindrouter.uidaho.edu/v1/models \
    | python3 -c 'import sys,json;d=json.load(sys.stdin);[print("   -",m["id"],"| ctx",m.get("context_length"),"| caps",m.get("capabilities")) for m in d.get("data",[])]' 2>/dev/null \
    || echo "   (could not parse /v1/models — check key)"
else
  echo "  (set MR_KEY to list models: export MR_KEY=mr2_...)"
fi

line "SMTP egress (Gmail delivery) — often blocked on campus nets"
for hp in smtp.gmail.com:587 smtp.gmail.com:465; do
  h=${hp%:*}; p=${hp#*:}
  if timeout 8 bash -c "cat < /dev/null > /dev/tcp/$h/$p" 2>/dev/null; then ok "$hp open"; else bad "$hp blocked"; fi
done

line "port 8001 free to bind?"
if command -v ss >/dev/null; then ss -ltn 2>/dev/null | grep -q ':8001 ' && bad "8001 already in use" || ok "8001 free"; fi

line "done"
