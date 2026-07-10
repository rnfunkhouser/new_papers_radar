# Setup Guide — your own Daily Papers Radar

Welcome! This guide takes you from zero to a working research radar that emails you a
hand-picked, well-written briefing of new papers every morning. Every step is spelled out,
and technical terms are explained the first time they appear.

Take it slowly. You do **not** have to finish in one sitting; there are natural stopping
points, and one early step (requesting a campus computer) involves a wait, so it's normal to
spread this over a few days.

---

## What you'll end up with

- **A 6:00-ish a.m. email**, every day, with a PDF of the ~5 best new papers in your area,
  each summarized in a few tight paragraphs (question → method → findings → why it matters).
- **A web dashboard** where you can browse the archive, search past briefings, and click
  👍 / 👎 on papers — which quietly teaches the radar your taste over time.
- It all runs **by itself** once set up. You just read your email.

## How it decides what to show you

You give it a list of **papers you already love** (your "seeds"). It studies them and, each
morning, goes looking for brand-new papers that resemble them. The more representative your
seed list, the better it gets. That's the whole idea — you never maintain keywords.

---

## The big picture: what you're setting up

There are two "computers" involved:

1. **Your own laptop/desktop** — where you get the code, list your seed papers, and do a
   first test run.
2. **A small "always-on" campus computer** (a *virtual machine*, or **VM**) — where the radar
   actually lives so it can run every morning whether or not your laptop is on. You **request
   this from the university** (details below). This is the recommended setup.

> **If you'd rather not use a campus VM:** you can instead run everything on a Mac that you
> leave on and awake every morning. That path is simpler to start but less reliable (no email
> on days the Mac is asleep). It's described at the end under *Alternative: run it on an
> always-on Mac*. Most people should use the VM.

---

## ⏱️ Do this FIRST (it has a wait): request your campus computer

The radar runs on a small campus **virtual machine (VM)** — think of it as a computer that
lives in a university data center and is always on. You also need a **web address (URL)** so
you can open the dashboard in your browser. Both come from the university's **Research
Computing & Data Services (RCDS)** team.

**Email RCDS** (their address is `rcds` at `uidaho` dot `edu` — written that way here only to
dodge email scrapers) and ask for the following. Here is wording you can adapt:

> Subject: Request for a small Linux VM for a daily research tool
>
> Hi RCDS,
>
> I'd like to run a small, always-on personal tool that emails me a daily summary of new
> academic papers. Could you help me set up:
>
> 1. A small Linux virtual machine (a couple of CPUs and a few GB of RAM is plenty) that can
>    run **Docker**.
> 2. **SSH access** to it (I'll need to connect from my laptop), including help creating and
>    installing my **SSH key**.
> 3. A **web address (URL)** pointed at the machine's port **8001**, so I can open a small
>    dashboard it serves (a campus reverse proxy is fine — it does not need to be public to
>    the whole internet).
> 4. Confirmation that the machine can reach **`mindrouter.uidaho.edu`** (the campus AI
>    gateway) and send email via Gmail's SMTP (port 587), and that outbound access to public
>    scholarly APIs (OpenAlex, arXiv, Crossref, Semantic Scholar, Unpaywall) is allowed.
>
> Thanks!

**Why now?** Provisioning a machine and setting up access can take days, and it may involve a
back-and-forth. Start this email today; you can do all the laptop steps below while you wait.

**About SSH** (you'll hear this word a lot): *SSH* is the secure way your laptop talks to the
VM. An **SSH key** is like a matched pair of keys — a private one that stays on your laptop
and a public one that RCDS installs on the VM — so you can connect without typing a password
each time. **Ask RCDS to walk you through creating your SSH key and installing it**; it's a
routine request for them and they'll have you set in a few minutes.

---

## Also get these accounts/keys (you can do this while you wait)

The radar leans on a few free services. Gather these:

1. **A MindRouter API key.** MindRouter is the University of Idaho's AI gateway — it provides
   the "smart" parts (understanding paper meaning, writing the summaries) so nothing runs on
   your own hardware. Request an API **key** for it; ask **RCDS** how to obtain
   one if it isn't obvious. An *API key* is just a long secret password that lets a program
   use a service. You'll paste it into a file later.

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
- **On a Mac:** press `⌘ + Space`, type "Terminal", press Return. A window opens with a prompt.
- Throughout this guide, a line in a `code box` is something you **type (or paste) and press
  Return**. Copy them exactly.

### A1. Make sure you have Python 3.11 or newer

The radar needs **Python** (a programming language that's already on most Macs) version 3.11
or newer. Check by typing:

```bash
python3 --version
```

If it says `Python 3.11.x` or higher, you're set. If it's older or missing, install the
latest from <https://www.python.org/downloads/> (click the big yellow button, run the
installer), then re-check. *Good news: the radar uses no add-on Python packages, so there's
nothing else to install.*

### A2. Get your own copy of the code

This project is a **template** on GitHub, which means GitHub can make you your own personal
copy with one click.

1. Go to the template's GitHub page (the person who shared this with you has the link).
2. Click the green **"Use this template"** button → **"Create a new repository."**
3. Give it a name (e.g. `my-paper-radar`), choose **Private**, and create it.
4. On your new repository's page, click the green **"Code"** button and follow the option to
   **download** it (or, if you're comfortable, "clone" it). Unzip it somewhere easy to find,
   like your Desktop.

Now, in Terminal, move into that folder. Type `cd ` (with a space), then **drag the folder
from Finder onto the Terminal window** (that pastes its path), then press Return:

```bash
cd /path/to/your/my-paper-radar
```

You're now "inside" the project. Everything below happens here.

### A3. List your seed papers

This is the most important step — it defines your taste.

```bash
cp seeds.txt.example seeds.txt
```

That command (`cp` = copy) makes your own `seeds.txt` from the template. Open `seeds.txt` in
any text editor (TextEdit is fine) and replace the example lines with **your** papers — one
per line. Each line can be:
- a **DOI** (the `10.xxxx/...` identifier printed on most papers), or
- a **pasted citation** (the radar will look up the DOI for you).

Aim for **at least 15–20 papers** to start; more is better. Lines starting with `#` are notes
and are ignored, so you can group and label your seeds if you like. Save the file.

*(Prefer Zotero? See A5 below — you can skip typing seeds and sync them from a library.)*

### A4. Tell it your email and (optionally) tune your field

Open **`config.toml`** in a text editor. It's plain text with lots of comments. At minimum,
find the `[contact]` section and change the email:

```toml
mailto = "you@uidaho.edu"
```

That address is only used as a polite "who's calling" identifier for the free paper
databases — it makes them faster and more reliable. While you're in there, you *can* adjust
the `[briefing] audience` line to describe your field, and edit the journal lists and arXiv
categories under `[field]` — but you can also **leave all of that for later**; the defaults
work, and the radar mostly learns from your seeds. Save the file.

### A5. Fill in the three private files (your secrets)

Three settings hold secrets, so they live in their own files that **never** get shared or
uploaded. Each has a `.example` template — copy it, then fill in your values.

**1. MindRouter (the AI key):**
```bash
cp mindrouter.json.example mindrouter.json
```
Open `mindrouter.json` and paste your MindRouter API key between the quotes after
`"api_key":`. Leave the `base_url` as is.

**2. Email sending (your Gmail App Password):**
```bash
cp .briefing_env.example .briefing_env
```
Open `.briefing_env` and set your Gmail address, the 16-character App Password (no spaces),
and where you want the briefing sent (usually your uidaho.edu address).

**3. (Optional) Zotero:**
```bash
cp zotero.json.example zotero.json
```
Open `zotero.json` and fill in your library ID (and a key if the library is private). Skip
this file entirely if you're not using Zotero.

> These three files, plus your `seeds.txt`, are automatically kept out of GitHub (they're
> listed in `.gitignore`). Your secrets stay on your machine.

### A6. Build your taste profile and do a test run

Now let the radar study your seeds:

```bash
python3 harvest.py --build-profile
```

This reads each seed paper, works out your topic areas, and (using MindRouter) builds your
"taste fingerprint." It prints what it found — your topic clusters and trusted journals.

> **Note:** this step needs to reach MindRouter. On campus or on the VM it just works. From
> off-campus you may need the university VPN, or simply run this step on the VM later (Part B).
> If it says embeddings were skipped, that's why — it's not broken.

Then try a real harvest:

```bash
python3 harvest.py
```

You'll get a `candidates.json` file — the ranked shortlist of today's best papers. If you
want to see the whole thing end-to-end (write-ups + PDF + email) on your laptop, you can run
`bash run_daily.sh`, but the tidiest place to run it every day is the VM — that's Part B.

If you're using Zotero, pull your seeds from it any time with:
```bash
python3 harvest.py --sync-zotero
```

---

## Part B — put it on the campus VM (so it runs every morning)

Once RCDS has given you the VM and your SSH access works, you'll copy the radar onto it and
let it run itself via **Docker**. *Docker* is a tool that packages the app so it runs the same
way everywhere; RCDS will have installed it on your VM.

You'll need two pieces of information from RCDS:
- the **SSH address** of your VM — it looks like `devops@your-vm.nkn.uidaho.edu`
- the **folder** on it to use — e.g. `/home/devops/paper-radar` (any folder is fine)

### B1. Check the VM can reach everything (optional but reassuring)

From your project folder on your laptop, run the read-only preflight against the VM:

```bash
ssh devops@your-vm.nkn.uidaho.edu 'bash -s' < vm_recon.sh
```

(Use your real SSH address.) It checks — and changes nothing — that the VM can reach
MindRouter, the paper databases, and Gmail. Green "OK" lines are good.

### B2. Tell `deploy.sh` where your VM is

Create a small file named `.deploy_env` in the project folder (it's kept private) with your
two values:

```bash
VM=devops@your-vm.nkn.uidaho.edu
DEST=/home/devops/paper-radar
```

### B3. Copy your secrets + seeds onto the VM (once)

These private files are deliberately **not** sent by the normal deploy, so copy them over by
hand this one time (`scp` = secure copy):

```bash
scp mindrouter.json .briefing_env seeds.txt zotero.json  devops@your-vm.nkn.uidaho.edu:/home/devops/paper-radar/
```

(Drop `zotero.json` from that line if you're not using Zotero.)

### B4. Deploy, build your profile on the VM, and test

```bash
./deploy.sh
```

This copies the code to the VM and starts the app in Docker. Then build your profile **on the
VM** (where MindRouter is always reachable) and do one full test run — the commands are
printed in the `FIRST_TIME_SETUP` notes at the bottom of `deploy.sh`, but here they are:

```bash
# build your taste profile on the VM
ssh devops@your-vm.nkn.uidaho.edu "cd /home/devops/paper-radar && docker compose exec -T briefing python3 harvest.py --build-profile"

# do one full morning run right now (writes + emails today's briefing)
ssh devops@your-vm.nkn.uidaho.edu "cd /home/devops/paper-radar && docker compose exec -T briefing bash -c 'PROJECT_DIR=/app BRIEFING_WRITER=mindrouter bash run_daily.sh'"
```

If a briefing lands in your inbox, **you're done** — the VM will now run this automatically
every morning at 05:57 (set in `crontab`; the timezone is set in `docker-compose.yml` — change
it to `America/Boise` if you like).

### B5. Open your dashboard

Visit the **web address** RCDS pointed at the VM (port 8001) in your browser. You'll see
today's papers as cards, an Archive, and a Topics page. Click 👍 / 👎 to teach it your taste.

> **One small security note:** rating papers is protected by a simple password so random bots
> can't skew your taste model. It's set by `DASH_PASSWORD` in `docker-compose.yml` and ships
> as `changeme` — change it to something of your own before you deploy. Reading the dashboard
> stays open to anyone with the link; only the 👍/👎 buttons ask for the password.

---

## Living with it, day to day

- **Read your email.** That's the point.
- **Rate papers** 👍/👎 on the dashboard now and then — it steadily sharpens the picks.
- **Add new seeds** whenever your interests drift: put new DOIs in `seeds.txt` (or add to your
  Zotero library), then re-run `--build-profile`. On the VM:
  ```bash
  scp seeds.txt devops@your-vm.nkn.uidaho.edu:/home/devops/paper-radar/
  ssh devops@your-vm.nkn.uidaho.edu "cd /home/devops/paper-radar && docker compose exec -T briefing python3 harvest.py --build-profile"
  ```
- **Update the code** later (if you pull improvements): just run `./deploy.sh` again. It never
  touches your accumulated history or secrets.

---

## Tuning it (whenever you feel like it)

Open **`config.toml`** — it's all plain text with explanations. You can widen the search,
change how many papers you get, add your field's flagship journals, adjust how strict the
quality/geography filters are, and more. After any change, rebuild the profile
(`--build-profile`) so it takes effect. Nothing there can break the radar; the worst case is
you get more or fewer papers than you like, and you dial it back.

---

## If something goes wrong

- **No email arrived.** Check the logs on the VM:
  `ssh devops@your-vm.nkn.uidaho.edu "docker logs --tail 100 paper-briefing"`. The most common
  causes are a wrong Gmail App Password or the VM's outbound email being blocked (ask RCDS).
- **"embeddings unavailable" / summaries look thin.** MindRouter wasn't reachable when the
  profile was built. Rebuild the profile on the VM (Part B4), where it always is.
- **The dashboard won't load.** Confirm the container is running
  (`ssh ... "docker ps"`) and that RCDS's web address points at port 8001.
- **A `config.toml` error on startup.** You likely deleted a quote or bracket while editing —
  every `"` and `[` needs its partner. Compare against `config.toml` in a fresh copy.
- **Still stuck?** The `HANDOFF`-style comments at the top of each script explain what it does,
  and RCDS can help with anything VM-, SSH-, or network-related.

---

## Alternative: run it on an always-on Mac (no VM)

If you skip the VM, you can run the radar on a Mac you leave on and awake every morning:

1. Do all of **Part A**.
2. Two small background jobs keep it running: one to harvest+email each morning, one to serve
   the dashboard at `http://localhost:8765`. Templates are provided:
   `com.example.papersradar.plist` (the morning job) and
   `com.example.papersradar-dashboard.plist` (the dashboard). Open each file — the comments at
   the top tell you exactly what to edit (your folder's path) and the `launchctl` commands to
   install them.
3. Because MindRouter must be reachable when the morning job runs, this Mac should be on
   campus or on the university VPN. If it's asleep at 05:57, that day's briefing is skipped.

The VM path avoids all of those caveats, which is why it's the recommended one.

---

*Questions about the radar itself can go to whoever shared this template with you. Questions
about the VM, SSH, MindRouter access, or campus networking go to RCDS (`rcds` at `uidaho`
dot `edu`).*
