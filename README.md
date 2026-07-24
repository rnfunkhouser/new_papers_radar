# Daily Papers Radar

_[note: I very much 'vibe-coded' this project, and while it's working great for me, you may
find issues with it or with this (largely) AI-generated documentation]_

I was sick of relying on really imprecise Google Scholar alerts or the luck of seeing a
relevant new article get shared by a colleague on social media, so I built a personal
research radar. Every morning it reads the day's new scholarly papers across several
databases, figures out which ones are most perfectly suited to your exact research interests
— first by learning from a set of papers you already love, then by having an LLM read each 
finalist against a description of your interests that you write and edit — and emails you a daily
briefing of the top 5, plus a web dashboard where you can browse, search, and 👍/👎 papers to 
sharpen it over time.

To run it you need two things: **an LLM API** (any OpenAI-compatible endpoint — a commercial
key should run for a few dollars a month, and some campuses provide local models for free/cheap) 
and **an always-on machine** (a small Linux VM from your institution or cloud provider, or a lab
computer that stays awake). Everything else is free public scholarly APIs and plain Python. 
*(To my University of Idaho colleagues: see the boxed note in the setup guide — you can run this 
entirely for free.)*

**👉 [SETUP_GUIDE.md](SETUP_GUIDE.md)** — the walkthrough that takes you from zero to a
working radar.
**🧠 [DESIGN_DECISIONS.md](DESIGN_DECISIONS.md)** — a breakdown of the design decisions and how
it actually works for anyone who wants to understand (or change) the machinery. All the code 
lives in [`app/`](app/).
