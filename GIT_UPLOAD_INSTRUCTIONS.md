# How to publish this as a PUBLIC template repo

**Read this first.** These are the steps to turn this folder into its own public GitHub
**template repository** — the kind with a green *"Use this template"* button that other UIdaho
faculty click to get their own copy. This folder was authored in a *separate* session inside
another project and intentionally left un-pushed, so nothing here is connected to any existing
git history yet.

> **Context for whoever runs this (human or a fresh Claude Code session):** This is the
> sanitized, field-agnostic template ported from the author's private radar. The private repo
> (`rnfunkhouser/new_papers_dashboard`) must stay private and untouched. This becomes a
> brand-new, standalone **public** repo. Do the work from *this* folder, in a session rooted
> here — not from the private project.

---

## Step 0 — sanity checks before anything touches GitHub

Run these from inside this folder and confirm each is clean:

```bash
# 0a. You are in the template folder, NOT the private project.
pwd     # should end in /new_papers_radar

# 0b. There is no git repo here yet (we want a fresh one).
git status 2>/dev/null && echo "!! a repo already exists — stop and investigate" || echo "no repo yet — good"

# 0c. No secrets or personal state are present (only *.example templates should exist).
ls -1 | grep -E '^(mindrouter\.json|\.briefing_env|zotero\.json|seeds\.txt|\.deploy_env)$' \
  && echo "!! a secret/personal file is present — remove it before publishing" \
  || echo "no secret/personal files present — good"

# 0d. No personal identifiers remain in the code/config.
grep -rniE "funkhouser|ryan\.n\.funkhouser|6600995|newspapers\.nkn|/home/devops/paper-briefing" \
  --include="*.py" --include="*.sh" --include="*.yml" --include="*.toml" --include="*.example" \
  --include="crontab" --include="Dockerfile" . \
  && echo "!! found a personal identifier above — fix before publishing" \
  || echo "no personal identifiers — good"
```

If 0c or 0d flag anything, fix it before continuing. (The email placeholder `you@uidaho.edu`
in `config.toml` and `.example` files is intentional and fine.)

---

## Step 1 — initialize the repo and make the first commit

```bash
git init
git add -A
git status          # eyeball the list: config.toml SHOULD be staged; seeds.txt / *.json
                    # secrets should NOT appear (they're git-ignored)
git commit -m "Initial public template: Daily Papers Radar"
```

---

## Step 2 — create the PUBLIC repo on GitHub as a template

Using the GitHub CLI (`gh`) is easiest. Pick a name — e.g. `new_papers_radar`.

```bash
# Create a new PUBLIC repo under your account and push this folder to it.
gh repo create new_papers_radar --public --source=. --remote=origin --push \
  --description "A personal daily research-paper radar for UIdaho faculty (template)."
```

Then flip on the **"Template repository"** flag so others get the *Use this template* button:

```bash
gh repo edit --template
```

*(No `gh`? Create an empty public repo in the GitHub web UI, then:*
`git remote add origin https://github.com/<you>/new_papers_radar.git && git branch -M main && git push -u origin main`*, and tick*
**Settings → General → Template repository** *on the repo page.)*

---

## Step 3 — verify

```bash
gh repo view --web        # opens the repo; confirm "Use this template" appears and it's Public
```

Skim the rendered `README.md` and `SETUP_GUIDE.md` on GitHub to confirm they look right.

---

## Step 4 — (optional) tidy up

Once the repo is live and confirmed, you can delete this file (`GIT_UPLOAD_INSTRUCTIONS.md`) so
new users who click *Use this template* don't see internal publishing notes:

```bash
git rm GIT_UPLOAD_INSTRUCTIONS.md && git commit -m "Remove internal publishing notes" && git push
```

That's it — the template is live, and it has no connection to your private repo.
