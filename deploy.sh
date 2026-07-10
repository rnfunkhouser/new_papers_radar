#!/usr/bin/env bash
# deploy.sh — push CODE to your campus VM and (re)build/restart the container.
# It rsyncs ONLY code + container files; it NEVER overwrites the VM's accumulated
# state (seeds.txt, *_profile/embeddings/clusters/interests.json, seen/watchlist/feedback,
# briefings/, fulltext/, logs/) or the VM-side secrets. Those are set up once (see
# FIRST-TIME SETUP below) and then preserved across deploys.
#
# WHERE IS YOUR VM? Set these two once, either as environment variables or by
# creating a file called ".deploy_env" next to this script (it is git-ignored):
#
#     VM=devops@your-vm.nkn.uidaho.edu       # the ssh target RCDS gave you
#     DEST=/home/devops/paper-radar          # a folder on the VM to hold the app
#
# (See SETUP_GUIDE.md — the VM, its hostname, and your ssh access all come from a
#  request to Research Computing & Data Services, RCDS.)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

# Load VM/DEST from .deploy_env if present (env vars still win).
if [[ -f "$HERE/.deploy_env" ]]; then
  # shellcheck disable=SC1091
  set -a; source "$HERE/.deploy_env"; set +a
fi

VM="${VM:-}"
DEST="${DEST:-/home/devops/paper-radar}"
if [[ -z "$VM" ]]; then
  echo "ERROR: VM is not set. Put your VM's ssh target in .deploy_env, e.g.:" >&2
  echo '   VM=devops@your-vm.nkn.uidaho.edu' >&2
  echo '   DEST=/home/devops/paper-radar' >&2
  echo "(Ask RCDS for a VM and ssh access first — see SETUP_GUIDE.md.)" >&2
  exit 1
fi

CODE=(
  config.py config.toml
  harvest.py embeddings.py deliver.py pdfgen.py dashboard.py fetch_fulltext.py
  write_briefing.py run_daily.sh
  Dockerfile docker-compose.yml entrypoint.sh crontab
  sample_briefing_2026-06-29.md
)

echo ">> ensuring $DEST exists on $VM"
ssh "$VM" "mkdir -p '$DEST'"

echo ">> rsyncing code to $VM:$DEST"
rsync -avz "${CODE[@]}" "$VM:$DEST/"

echo ">> building + (re)starting the container"
ssh "$VM" "cd '$DEST' && docker compose up -d --build"

echo ">> done. Dashboard is served on the VM's port 8001."
echo "   Logs:  ssh $VM 'docker logs -f paper-briefing'"

: <<'FIRST_TIME_SETUP'
Run these ONCE, before the first deploy. They place your secrets + seeds on the VM and build
the interest profile there (where MindRouter is reachable), so retrieval_concepts/clusters/
interests are generated for your real seeds:

  # 1. Secrets + seeds onto the VM (NOT rsynced by deploy.sh — these hold private keys):
  scp mindrouter.json .briefing_env seeds.txt zotero.json  "$VM:$DEST/"

  # 2. First deploy (build + start):
  ./deploy.sh

  # 3. Build the interest profile inside the container (MindRouter reachable here):
  ssh "$VM" "cd '$DEST' && docker compose exec -T briefing python3 harvest.py --build-profile"
  #   -> confirm 'retrieval_concepts' is non-empty and clusters/interests regenerated.

  # 4. One manual end-to-end run to verify (writes + emails today's briefing):
  ssh "$VM" "cd '$DEST' && docker compose exec -T briefing bash -c 'PROJECT_DIR=/app BRIEFING_WRITER=mindrouter bash run_daily.sh'"
  #   -> confirm briefings/data_<date>.json shows rel_src='embed' (NOT 'tags') and the
  #      dashboard on port 8001 serves it.
FIRST_TIME_SETUP
