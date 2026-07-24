#!/usr/bin/env python3
"""
deliver.py — turn a finished briefing_<date>.md into a polished PDF and email it.

Used by the daily routine after Claude writes the markdown:
    python deliver.py briefings/briefing_2026-06-29.md

What it does:
  1. Converts the markdown to a styled, print-ready PDF (no extra Python packages —
     a small built-in markdown converter + headless Chrome/Edge, which macOS already
     has; falls back to wkhtmltopdf or pandoc if you prefer those).
  2. Emails the PDF to you via Gmail SMTP.

Credentials (so nothing secret lives in code) are read from the environment, or from a
".briefing_env" file next to this script (KEY=VALUE per line):
    BRIEFING_GMAIL_USER=you@gmail.com
    BRIEFING_GMAIL_APP_PASSWORD=xxxxxxxxxxxxxxxx     # a Gmail *App Password*, not your login
    BRIEFING_EMAIL_TO=you@example.edu

Flags:
    --no-email     just build the PDF (handy for testing the conversion)
    --pdf PATH     write the PDF here (default: alongside the .md)
"""

import argparse, html, os, re, shutil, smtplib, ssl, subprocess, sys, tempfile
from email.message import EmailMessage
from pathlib import Path

HERE = Path(__file__).parent


def ca_context():
    """Same CA-bundle healing harvest.py uses: stock macOS python.org Python often
    ships without a CA bundle, so the default SSL context fails (CERTIFICATE_VERIFY_FAILED)
    on Gmail's SMTP too. Prefer certifi, then the macOS system bundle, then default."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass
    for cafile in ("/etc/ssl/cert.pem", "/private/etc/ssl/cert.pem"):
        if Path(cafile).exists():
            try:
                return ssl.create_default_context(cafile=cafile)
            except Exception:
                pass
    return ssl.create_default_context()

# ----------------------------------------------------------------------------
# config loading
# ----------------------------------------------------------------------------

def load_env():
    """Environment wins; otherwise read KEY=VALUE lines from .briefing_env."""
    env = dict(os.environ)
    cfg = HERE / ".briefing_env"
    if cfg.exists():
        for line in cfg.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return env

# ----------------------------------------------------------------------------
# minimal markdown -> HTML (covers exactly what the briefing uses:
# h1/h2/h3, bold, italic, links, horizontal rules, lists, paragraphs)
# ----------------------------------------------------------------------------

def _inline(text):
    text = html.escape(text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*(?!\*)([^*]+)\*(?!\*)", r"<em>\1</em>", text)
    return text

def md_to_html_body(md):
    out, para, in_list = [], [], False
    def flush_para():
        if para:
            out.append("<p>" + " ".join(para) + "</p>")
            para.clear()
    def close_list():
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False
    for raw in md.splitlines():
        line = raw.rstrip()
        if not line.strip():
            flush_para(); close_list(); continue
        if re.match(r"^---+\s*$", line):
            flush_para(); close_list(); out.append("<hr>"); continue
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            flush_para(); close_list()
            lvl = len(m.group(1))
            out.append(f"<h{lvl}>{_inline(m.group(2))}</h{lvl}>"); continue
        m = re.match(r"^[-*]\s+(.*)$", line)
        if m:
            flush_para()
            if not in_list:
                out.append("<ul>"); in_list = True
            out.append(f"<li>{_inline(m.group(1))}</li>"); continue
        close_list()
        para.append(_inline(line.strip()))
    flush_para(); close_list()
    return "\n".join(out)

CSS = """
@page { size: Letter; margin: 0.9in 0.85in; }
body { font: 11.5pt/1.5 -apple-system, "Helvetica Neue", Arial, sans-serif;
       color: #1a1a1a; max-width: 100%; }
h1 { font-size: 20pt; margin: 0 0 2pt; }
h2 { font-size: 14pt; margin: 20pt 0 4pt; border-bottom: 1px solid #ddd; padding-bottom: 3pt; }
h3 { font-size: 11pt; color: #444; margin: 2pt 0 6pt; font-weight: 600; }
p  { margin: 7pt 0; }
a  { color: #0b5; text-decoration: none; word-break: break-word; }
hr { border: none; border-top: 1px solid #e3e3e3; margin: 16pt 0; }
em { color: #555; }
strong { color: #000; }
li { margin: 3pt 0; }
"""

def md_to_html(md_path):
    md = Path(md_path).read_text()
    return f"<!doctype html><meta charset='utf-8'><style>{CSS}</style>\n{md_to_html_body(md)}\n"

# ----------------------------------------------------------------------------
# HTML -> PDF, trying whatever the machine has (no install needed for Chrome/Edge)
# ----------------------------------------------------------------------------

CHROME_PATHS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
]

def html_to_pdf(html_str, pdf_path):
    """Browser/wkhtmltopdf/pandoc path — prettier, but needs one of them installed
    and (for Chrome) permission to launch. deliver.py only calls this when --engine
    browser is requested; the default is the dependency-free pdfgen below."""
    pdf_path = Path(pdf_path)
    with tempfile.TemporaryDirectory() as td:
        html_file = Path(td) / "briefing.html"
        html_file.write_text(html_str)
        # 1. Headless Chromium-family (already on most Macs)
        for chrome in CHROME_PATHS:
            if Path(chrome).exists():
                prof = Path(td) / "prof"
                cmd = [chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
                       f"--user-data-dir={prof}", "--no-pdf-header-footer",
                       f"--print-to-pdf={pdf_path}", html_file.as_uri()]
                r = subprocess.run(cmd, capture_output=True, text=True)
                if pdf_path.exists() and pdf_path.stat().st_size > 0:
                    return chrome
                # older Chrome needs the legacy flag name
                cmd[cmd.index("--no-pdf-header-footer")] = "--print-to-pdf-no-header"
                subprocess.run(cmd, capture_output=True, text=True)
                if pdf_path.exists() and pdf_path.stat().st_size > 0:
                    return chrome
        # 2. wkhtmltopdf
        if shutil.which("wkhtmltopdf"):
            subprocess.run(["wkhtmltopdf", "--quiet", str(html_file), str(pdf_path)])
            if pdf_path.exists():
                return "wkhtmltopdf"
        # 3. pandoc
        if shutil.which("pandoc"):
            subprocess.run(["pandoc", str(html_file), "-o", str(pdf_path)])
            if pdf_path.exists():
                return "pandoc"
    raise RuntimeError(
        "No PDF engine found. Install one of:\n"
        "  • Google Chrome  (recommended — zero config)\n"
        "  • wkhtmltopdf    (brew install wkhtmltopdf)\n"
        "  • pandoc + LaTeX (brew install pandoc basictex)")

# ----------------------------------------------------------------------------
# email
# ----------------------------------------------------------------------------

def send_email(pdf_path, subject, env):
    user = env.get("BRIEFING_GMAIL_USER")
    pw   = env.get("BRIEFING_GMAIL_APP_PASSWORD")
    to   = env.get("BRIEFING_EMAIL_TO", user)
    if not (user and pw and to):
        raise RuntimeError(
            "Email not configured. Set BRIEFING_GMAIL_USER, BRIEFING_GMAIL_APP_PASSWORD, "
            "and BRIEFING_EMAIL_TO (env vars or a .briefing_env file). See the header of "
            "deliver.py for how to create a Gmail App Password.")
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = user, to, subject
    dash_url = os.environ.get("DASH_URL", "http://localhost:8765")
    msg.set_content("Your Daily Papers Radar briefing is attached as a PDF.\n\n"
                    "Rate today's papers (👍/👎) and search the archive on the dashboard:\n"
                    f"  {dash_url}\n\n"
                    "The full markdown archive also lives in the briefings/ folder.")
    data = Path(pdf_path).read_bytes()
    msg.add_attachment(data, maintype="application", subtype="pdf",
                       filename=Path(pdf_path).name)
    ctx = ca_context()
    with smtplib.SMTP("smtp.gmail.com", 587) as s:
        s.starttls(context=ctx)
        s.login(user, pw)
        s.send_message(msg)
    print(f"  emailed {Path(pdf_path).name} -> {to}")

# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("md", help="path to briefing_<date>.md")
    ap.add_argument("--pdf", help="output PDF path (default: alongside the .md)")
    ap.add_argument("--no-email", action="store_true", help="build the PDF only")
    ap.add_argument("--engine", choices=["stdlib", "browser"], default="stdlib",
                    help="stdlib (default, zero deps, always works) or browser "
                         "(Chrome/wkhtmltopdf/pandoc — prettier, needs one installed).")
    args = ap.parse_args()

    md_path = Path(args.md)
    if not md_path.exists():
        sys.exit(f"No such file: {md_path}")
    pdf_path = Path(args.pdf) if args.pdf else md_path.with_suffix(".pdf")

    if args.engine == "browser":
        engine = html_to_pdf(md_to_html(md_path), pdf_path)
    else:
        import pdfgen
        engine = pdfgen.build_pdf(md_path, pdf_path)
    print(f"  wrote {pdf_path.name} via {Path(engine).name}")

    if not args.no_email:
        date = re.search(r"(\d{4}-\d{2}-\d{2})", md_path.name)
        subject = f"Daily Papers Radar — {date.group(1) if date else md_path.stem}"
        send_email(pdf_path, subject, load_env())

if __name__ == "__main__":
    main()
