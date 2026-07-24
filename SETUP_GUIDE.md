# Setup Guide — your own Daily Papers Radar

Welcome! This guide takes you from zero to a working research radar that emails you a
hand-picked, well-written briefing of new papers every morning. Every step is spelled out,
and technical terms are explained the first time they appear.

Take it slowly. You do **not** have to finish in one sitting; there are natural stopping
points, and arranging your always-on machine may involve a wait, so it's normal to spread
this over a few days.

---

## What you'll end up with

- **A 6:00-ish a.m. email**, every day, with a PDF of the ~5 best new papers in your area,
  each with an honest summary (short and plain when only the abstract is available; full
  depth when the open-access text could be read).
- **A web dashboard** where you can browse the archive, search past briefings, click
  👍 / 👎 on papers, and — importantly — **edit the plain-English description of your
  interests** that the radar judges every paper against.
- It all runs **by itself** once set up. You just read your email.

## How it decides what to show you

Two stages, and it helps to know their names because the dashboard uses them:

1. **Gathering** — you give it a list of **papers you already love** (your "seeds"). It
   studies them and, each morning, casts a wide net: thousands of brand-new papers from the
   areas your seeds live in.
2. **Selection** — an LLM then *reads* each shortlisted paper against your **Selection
   Criteria**: a page of plain English, written by you, describing what you actually care
   about (and what you don't). It scores each paper's fit 0–10, and the top scorers make
   your briefing — each with a visible score and the interest area it matched.

Seeds cast the net; your written criteria make the call. You tune the first by adding
papers, and the second by editing a paragraph.

---

## What you need (the two real requirements)

**1. An LLM API.** The "smart" parts — understanding what papers mean, judging fit against
your criteria, writing the summaries — run on a language-model service that this app calls
over the internet. Any **OpenAI-compatible endpoint** that provides *embeddings* and *chat*
works. The most common path is simply an [OpenAI](https://platform.openai.com) account and
API key: you'd create a key, put it (plus your chosen embedding and chat model names) in one
small config file, and at this app's usage the bill is typically **a few dollars a month**
(the first day costs the most while it scores a backlog; after that it only pays for what's
new each morning). I haven't run the OpenAI path myself — I use a free campus gateway — so
rather than hand you steps I haven't verified: **the fastest way to get connected is to open
this project in an AI coding assistant** (Claude Code, Cursor, Copilot Workspace, etc.) and
ask it to hook up your provider. The file [`app/AGENTS.md`](app/AGENTS.md) contains exact
instructions written for your AI tool — it knows what to do from there. Self-hosted and
campus-hosted OpenAI-compatible servers work the same way.

**2. An always-on machine.** The radar needs a computer that's awake at ~6 a.m. every day.
In order of niceness:
- **A small Linux virtual machine (VM) from your institution** — most universities'
  research-computing groups will provision one on request (a couple of CPUs and a few GB of
  RAM is plenty; it needs to run Docker, allow SSH, and ideally have a web address pointed
  at port 8001 so you can open your dashboard from anywhere).
- **A small cloud VM** (AWS/Azure/GCP/DigitalOcean and friends, ~$5–10/month) — same specs.
- **A Mac you leave on and awake** — simplest to start, least reliable (no briefing on days
  it's asleep). Covered at the end under *Alternative: run it on an always-on Mac*.

> **🎓 For my colleagues at the University of Idaho:** you can use the **MindRouter** API to
> connect this app to the university's locally hosted LLMs at **no cost**, and **RCDS** can
> set up a virtual machine that can host the app and a URL-accessible dashboard. Email RCDS
> to request a small Docker-capable Linux VM with SSH access, a web address pointed at port
> 8001, and a MindRouter API key — then use MindRouter's URL and key in `llm_api.json` in
> step A5.

Start whichever machine request applies to you **today** (it can take days); everything in
Part A happens on your own laptop while you wait.

---

## Also get these accounts/keys (you can do this while you wait)

1. **Your LLM API key** (see above — OpenAI key, campus gateway key, or your self-hosted
   server's address).

2. **A Gmail account with an "App Password"** (for *sending* your morning email). It can be a
   personal Gmail or a project one. An **App Password** is a special 16-character password
   Google gives you for programs (separate from your normal login). To create one:
   - Turn on 2-Step Verification: <https://myaccount.google.com/signinoptions/two-step-verification>
   - Create an app password: <https://myaccount.google.com/apppasswords> — name it "paper
     radar"; Google shows a 16-character code **once**. Copy it somewhere safe.

3. **(Optional) A Zotero library.** If you keep papers in [Zotero](https://www.zotero.org),
   the radar can pull your seed papers straight from a library and keep them in sync. You'll
   need the library's numeric ID (and, for a private library, a read-only key). This is
   optional — you can also just type/paste seeds into a text file.

---

## Part A — set up on your own laptop

You'll do a few steps in the **Terminal**, which is a plain-text way to run commands.
- **On a Mac:** press `⌘ + Space`, type "Terminal", press Return.
- A line in a `code box` is something you **type (or paste) and press Return**.

### A1. Make sure you have Python 3.11 or newer

```bash
python3 --version
```

If it says `Python 3.11.x` or higher, you're set. If it's older or missing, install the
latest from <https://www.python.org/downloads/>, then re-check. *Good news: the radar uses
no add-on Python packages, so there's nothing else to install.*

### A2. Get your own copy of the code

This project is a **template** on GitHub, which means GitHub can make you your own copy with
one click.

1. Go to the template's GitHub page.
2. Click the green **"Use this template"** button → **"Create a new repository."**
3. Give it a name (e.g. `my-paper-radar`), choose **Private**, and create it.
4. On your new repository's page, click the green **"Code"** button and download (or clone)
   it. Unzip it somewhere easy to find.

All the working files live in the **`app/`** folder, so move into it. In Terminal, type
`cd ` (with a space), drag the **app folder** from Finder onto the Terminal window, and
press Return:

```bash
cd /path/to/your/my-paper-radar/app
```

Everything below happens here, inside `app/`.

### A3. List your seed papers (Gathering)

This defines where the radar looks.

```bash
cp seeds.txt.example seeds.txt
```

Open `seeds.txt` in any text editor and replace the example lines with **your** papers — one
per line, each either a **DOI** (`10.xxxx/...`) or a **pasted citation** (the radar will look
up the DOI). Aim for **at least 15–20 papers**; more is better. Lines starting with `#` are
notes and are ignored. Save.

*(Prefer Zotero? See A5 — you can sync seeds from a library instead of typing them.)*

### A4. Write your Selection Criteria

This defines what actually makes the briefing — it's the most powerful file in the project.

```bash
cp interest_profile.example.json interest_profile.json
```

Open `interest_profile.json`. It ships with a worked example (mine — a political-communication
profile) showing the format: a core statement of who you are and what you want, a few
**"flavors"** (your interest areas, each described in 2–3 concrete sentences), a list of
things you're explicitly *not* interested in, and a few example paper titles you'd love or
reject. **Rewrite every field in your own words.** Don't overthink the first draft — once
the dashboard is running you can edit all of this on its *Selection Criteria* page, and each
edit takes effect the next morning.

Also open **`config.toml`** and set your email in the `[contact]` section (it's only used as
a polite identifier for the free paper databases). Everything else in there can wait.

### A5. Fill in the private files (your secrets)

Secrets live in their own files that **never** get uploaded (they're in `.gitignore`).

**1. The LLM connection:**
```bash
cp llm_api.json.example llm_api.json
```
Open `llm_api.json` and fill in your provider's `base_url`, your `api_key`, and the
`embedding_model` / `chat_model` names. The `.example` file shows an OpenAI-shaped block and
a campus/self-hosted block. **This is the step your AI coding assistant can do for you** —
point it at [`app/AGENTS.md`](AGENTS.md) and tell it which provider you have.

**2. Email sending (your Gmail App Password):**
```bash
cp .briefing_env.example .briefing_env
```
Open `.briefing_env` and set your Gmail address, the 16-character App Password, and where
you want the briefing sent.

**3. (Optional) Zotero:**
```bash
cp zotero.json.example zotero.json
```
Fill in your library ID (and a key if private). Skip entirely if you're not using Zotero.

### A6. Build your taste profile and do a test run

Let the radar study your seeds:

```bash
python3 harvest.py --build-profile
```

This reads each seed paper, works out your topic areas, and builds your "taste fingerprint"
(this is the first step that talks to your LLM API — if it fails, your `llm_api.json` needs
attention; ask your AI assistant to debug it against `AGENTS.md`).

Then try a real harvest:

```bash
python3 harvest.py
```

The first run is the slow one (it embeds and judges a two-week backlog — tens of minutes and
the priciest LLM day you'll have; every later day only pays for what's new). You'll get a
`candidates.json` — the ranked shortlist of today's best papers, each with the judge's fit
score and reason. To see the whole thing end-to-end (write-ups + PDF + email) run
`bash run_daily.sh` — but the tidiest place for the daily routine is the always-on machine:
Part B.

---

## Part B — put it on the always-on machine

Once you have your VM and SSH access works, the radar runs itself there via **Docker** (a
tool that packages the app so it runs the same way everywhere — ask for it to be installed
when the VM is provisioned).

You need two pieces of information:
- the **SSH address** of your machine — like `yourname@your-vm.your.edu`
- the **folder** on it to use — e.g. `/home/yourname/paper-radar`

### B1. Tell `deploy.sh` where your machine is

Create a small file named `.deploy_env` inside `app/` (it's kept private):

```bash
VM=yourname@your-vm.your.edu
DEST=/home/yourname/paper-radar
```

### B2. Copy your secrets + seeds onto the machine (once)

These private files are deliberately **not** sent by the normal deploy; copy them by hand
this one time (`scp` = secure copy):

```bash
scp llm_api.json .briefing_env seeds.txt interest_profile.json zotero.json  yourname@your-vm.your.edu:/home/yourname/paper-radar/
```

(Drop `zotero.json` if you're not using it.)

### B3. Deploy, build your profile there, and test

```bash
./deploy.sh
```

This copies the code over and starts the app in Docker. Then build the profile **on the
machine** and do one full test run:

```bash
# build your taste profile
ssh yourname@your-vm.your.edu "cd /home/yourname/paper-radar && docker compose exec -T briefing python3 harvest.py --build-profile"

# one full morning run right now (writes + emails today's briefing)
ssh yourname@your-vm.your.edu "cd /home/yourname/paper-radar && docker compose exec -T briefing bash run_daily.sh"
```

If a briefing lands in your inbox, **you're done** — the machine now runs this automatically
every morning at 05:57 (set in `crontab`; the timezone is in `docker-compose.yml`).

### B4. Open your dashboard

Visit the web address pointed at your machine's port 8001 (or `http://your-vm:8001`).
You'll see today's papers as cards with their fit scores, plus four pages: **Today**,
**Archive**, **Gathering** (the areas being searched, learned from your seeds), and
**Selection Criteria** (your editable judging page). Click 👍/👎 to teach it; edit the
Selection Criteria whenever the picks drift.

> **One small security note:** anything that changes the model (votes, edits) is protected
> by a simple password so bots can't skew your radar. It's set by `DASH_PASSWORD` in
> `docker-compose.yml` and ships as `changeme` — change it before you deploy. Reading stays
> open to anyone with the link.

---

## Living with it, day to day

- **Read your email.** That's the point.
- **Rate papers** 👍/👎 — recent votes are shown to the judge as examples of your boundary.
- **Edit your Selection Criteria** on the dashboard when picks feel off — it re-judges
  everything overnight under your new wording.
- **Add seeds** when your interests grow: new DOIs in `seeds.txt` (or your Zotero library),
  then re-run `--build-profile`. If your seeds develop an area your criteria don't cover,
  the radar notices and proposes a drafted new flavor on the dashboard (and notes it in the
  briefing) — accept, edit, or dismiss it.
- **Update the code** later: `./deploy.sh` again. It never touches your history or secrets.

## If something goes wrong

- **No email arrived.** Check logs: `ssh ... "docker logs --tail 100 paper-briefing"`. Most
  common: wrong Gmail App Password, or the machine's outbound email is blocked.
- **"embeddings unavailable" in the log.** The LLM API wasn't reachable or `llm_api.json`
  is misconfigured — this is exactly what your AI assistant + `AGENTS.md` are for.
- **The dashboard won't load.** Confirm the container is running (`ssh ... "docker ps"`)
  and the web address points at port 8001.
- **A `config.toml` error.** A quote or bracket got deleted while editing; compare against
  a fresh copy.

---

## Alternative: run it on an always-on Mac (no VM)

1. Do all of **Part A**.
2. Two small background jobs keep it running: one to harvest+email each morning, one to
   serve the dashboard at `http://localhost:8765`. Templates are provided:
   `com.example.papersradar.plist` (the morning job) and
   `com.example.papersradar-dashboard.plist` (the dashboard). The comments at the top of
   each tell you what to edit (your folder's path) and the `launchctl` commands to install
   them.
3. The LLM API must be reachable when the job runs (for a campus-only gateway that means
   campus network or VPN). If the Mac is asleep at 05:57, that day's briefing is skipped.

The VM path avoids those caveats, which is why it's recommended.
