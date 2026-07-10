#!/usr/bin/env python3
"""
pdfgen.py — dependency-free Markdown -> PDF, styled to resemble a journal LaTeX article.

Pure Python standard library: no pip packages, no headless browser, no system
permissions. Hand-writes a PDF using the built-in Times fonts, so it runs anywhere —
including locked-down/managed Macs where launching Chrome is blocked.

Look & feel (loosely after Oxford/JCMC article style): serif body (Times), fully
justified paragraphs, a centered title block, bold serif section headings, restrained
dark-blue links, and roomy margins. Covers the markdown the briefing uses (#/##/###,
**bold**, *italic*, [text](url), ---, and - lists).

For an exact browser render, deliver.py --engine browser still uses Chrome/wkhtmltopdf;
this is the always-works fallback and now the default.
"""

import re
from pathlib import Path

# --- AFM advance widths (units/1000 em) for wrapping + justification ----------
_TIMES = (
    "250 333 408 500 500 833 778 180 333 333 500 564 250 333 250 278 500 500 500 "
    "500 500 500 500 500 500 500 278 278 564 564 564 444 921 722 667 667 722 611 "
    "556 722 722 333 389 722 611 889 722 722 556 722 667 556 611 722 722 944 722 "
    "722 611 333 278 333 469 500 333 444 500 444 500 444 333 500 500 278 278 500 "
    "278 778 500 500 500 500 333 389 278 500 500 722 500 500 444 480 200 480 541")
_TIMESB = (
    "250 333 555 500 500 1000 833 278 333 333 500 570 250 333 250 278 500 500 500 "
    "500 500 500 500 500 500 500 333 333 570 570 570 500 930 722 667 722 722 667 "
    "611 778 778 389 500 778 667 944 722 778 611 778 722 556 667 722 722 1000 722 "
    "722 667 333 278 333 581 500 333 500 556 444 556 444 333 500 556 278 333 556 "
    "278 833 556 500 556 556 444 389 333 556 500 722 500 500 444 394 220 394 520")
_TIMESI = (
    "250 333 420 500 500 833 778 214 333 333 500 675 250 333 250 278 500 500 500 "
    "500 500 500 500 500 500 500 333 333 675 675 675 500 920 611 611 667 722 611 "
    "611 722 722 333 444 667 556 833 667 722 611 722 611 500 556 722 611 833 611 "
    "556 556 389 278 389 422 500 333 500 500 444 500 444 278 500 500 278 278 444 "
    "278 722 500 500 500 500 389 389 278 500 444 667 444 444 389 400 275 400 541")

def _widths(spec):
    return {chr(32 + i): int(w) for i, w in enumerate(spec.split())}

W = {"reg": _widths(_TIMES), "bold": _widths(_TIMESB), "ital": _widths(_TIMESI)}

def _style_key(bold, italic):
    return "bold" if bold else ("ital" if italic else "reg")

def _word_w(word, bold, italic, size):
    table = W[_style_key(bold, italic)]
    return sum(table.get(c if ord(c) < 128 else "n", 500) for c in word) / 1000.0 * size

# --- Unicode -> WinAnsi byte, with transliteration for the rest ---------------
_WINANSI = {0x2018: 0x91, 0x2019: 0x92, 0x201C: 0x93, 0x201D: 0x94, 0x2013: 0x96,
            0x2014: 0x97, 0x2022: 0x95, 0x2026: 0x85, 0x2122: 0x99, 0x00A0: 0x20}
_TRANSLIT = {0x2248: "~", 0x2265: ">=", 0x2264: "<=", 0x2212: "-", 0x2192: "->",
             0x2190: "<-", 0x2713: "[x]", 0x2717: "x", 0x26A0: "(!)", 0x00D7: "x",
             0x2032: "'", 0x2033: '"', 0x2009: " ", 0x200A: " ", 0x202F: " ",
             # hyphen/dash family the LLM emits that WinAnsi lacks -> ASCII hyphen
             # (these were rendering as "?"): U+2010 hyphen, U+2011 non-breaking hyphen,
             # U+2012 figure dash, U+2015 horizontal bar, U+2043 hyphen bullet, U+00AD soft.
             0x2010: "-", 0x2011: "-", 0x2012: "-", 0x2015: "-", 0x2043: "-",
             0x00AD: "-", 0x2044: "/"}

def _encode(s):
    out = bytearray()
    for ch in s:
        o = ord(ch)
        if o < 0x80:
            out.append(o)
        elif 0xA0 <= o <= 0xFF:
            out.append(o)
        elif o in _WINANSI:
            out.append(_WINANSI[o])
        elif o in _TRANSLIT:
            out += _TRANSLIT[o].encode("latin-1")
        else:
            out.append(ord("?"))
    return out

def _pdf_escape(b):
    return bytes(b).replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")

# --- inline markdown -> styled runs -------------------------------------------
def _runs(text):
    """Return [(text, bold, italic, is_link)] for one line of markdown."""
    runs, i = [], 0
    pat = re.compile(r"\[([^\]]+)\]\(([^)]+)\)|\*\*([^*]+)\*\*|(?<!\*)\*(?!\*)([^*]+)\*(?!\*)")
    for m in pat.finditer(text):
        if m.start() > i:
            runs.append((text[i:m.start()], False, False, False))
        if m.group(1) is not None:
            runs.append((m.group(1), False, False, m.group(2)))   # 4th = URL string
        elif m.group(3) is not None:
            runs.append((m.group(3), True, False, False))
        else:
            runs.append((m.group(4), False, True, False))
        i = m.end()
    if i < len(text):
        runs.append((text[i:], False, False, False))
    return runs or [("", False, False, False)]

# --- block model --------------------------------------------------------------
def _blocks(md):
    blocks, para = [], []
    def flush():
        if para:
            blocks.append(("p", " ".join(para))); para.clear()
    for raw in md.splitlines():
        line = raw.rstrip()
        if not line.strip():
            flush(); continue
        if re.match(r"^---+\s*$", line):
            flush(); blocks.append(("hr", "")); continue
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            flush(); blocks.append((f"h{len(m.group(1))}", m.group(2))); continue
        m = re.match(r"^[-*]\s+(.*)$", line)
        if m:
            flush(); blocks.append(("li", m.group(1))); continue
        para.append(line.strip())
    flush()
    return blocks

# --- layout config ------------------------------------------------------------
PAGE_W, PAGE_H = 612, 792
ML, MR, MT, MB = 78, 78, 84, 72                 # ~1.08in side margins, roomy like an article
LINK_RGB = (0.12, 0.18, 0.46)                   # restrained dark blue (hyperref-ish)
RULE_RGB = (0.55, 0.55, 0.55)
# kind -> (size, leading, space_before, bold, italic, align)
STYLE = {
    "h1": (20, 24, 6,  True,  False, "left"),
    "h2": (13.5, 17, 17, True, False, "left"),
    "h3": (11, 14.5, 9, True,  False, "left"),
    "p":  (10.5, 15.2, 7, False, False, "justify"),
    "li": (10.5, 15.2, 3, False, False, "left"),
}

class _Page:
    def __init__(self):
        self.ops = []
        self.annots = []        # (url, x0, y0, x1, y1) clickable link rects
    def line(self, x, baseline, tokens, size, base_bold, base_ital, tw):
        self.ops.append(b"BT")
        if tw:
            self.ops.append(f"{tw:.3f} Tw".encode())
        self.ops.append(f"1 0 0 1 {x:.2f} {baseline:.2f} Tm".encode())
        cur_font = cur_rgb = None
        cx = x
        cur_link = None         # (url, x0) of the link run currently being laid down
        def flush_link(end_x):
            nonlocal cur_link
            if cur_link:
                self.annots.append((cur_link[0], cur_link[1],
                                    baseline - 0.16 * size, end_x, baseline + 0.74 * size))
                cur_link = None
        for txt, bold, italic, url in tokens:
            b, it = bold or base_bold, italic or base_ital
            font = b"/F2" if b else (b"/F3" if it else b"/F1")
            if font != cur_font:
                self.ops.append(font + f" {size:.1f} Tf".encode()); cur_font = font
            rgb = LINK_RGB if url else (0, 0, 0)
            if rgb != cur_rgb:
                self.ops.append(f"{rgb[0]:.2f} {rgb[1]:.2f} {rgb[2]:.2f} rg".encode()); cur_rgb = rgb
            self.ops.append(b"(" + _pdf_escape(_encode(txt)) + b") Tj")
            if url:
                if not (cur_link and cur_link[0] == url):
                    flush_link(cx); cur_link = (url, cx)
            else:
                flush_link(cx)
            cx += _word_w(txt, b, it, size) + (tw if txt == " " else 0.0)
        flush_link(cx)
        if tw:
            self.ops.append(b"0 Tw")
        self.ops.append(b"ET")
    def hrule(self, y):
        self.ops.append(f"{RULE_RGB[0]} {RULE_RGB[1]} {RULE_RGB[2]} RG 0.6 w "
                        f"{ML} {y:.2f} m {PAGE_W-MR} {y:.2f} l S".encode())

def _wrap(tokens, size, max_w):
    """Greedy word-wrap styled tokens into lines (each line keeps its space tokens)."""
    lines, line, line_w = [], [], 0.0
    for txt, bold, italic, link in tokens:
        for w in re.split(r"(\s+)", txt):
            if w == "":
                continue
            ww = _word_w(w, bold, italic, size)
            if w.isspace():
                if line:
                    line.append((" ", bold, italic, link)); line_w += ww
                continue
            if line_w + ww > max_w and line:
                while line and line[-1][0] == " ":   # trim trailing space
                    line_w -= _word_w(" ", *line[-1][1:3], size); line.pop()
                lines.append(line); line, line_w = [], 0.0
            line.append((w, bold, italic, link)); line_w += ww
    if line:
        while line and line[-1][0] == " ":
            line.pop()
        lines.append(line)
    return lines or [[("", False, False, False)]]

def _line_width(line, size):
    return sum(_word_w(t, b, i, size) for t, b, i, _ in line)

def build_pdf(md_path, pdf_path):
    md = Path(md_path).read_text()
    blocks = _blocks(md)
    pages, page = [], _Page()
    y = PAGE_H - MT
    col_w = PAGE_W - ML - MR

    def new_page():
        nonlocal page, y
        pages.append(page); page = _Page(); y = PAGE_H - MT

    for kind, text in blocks:
        if kind == "hr":
            y -= 9
            if y < MB: new_page()
            page.hrule(y); y -= 9; continue
        size, leading, before, bold, ital, align = STYLE.get(kind, STYLE["p"])
        y -= before
        indent = ML + (16 if kind == "li" else 0)
        width = col_w - (16 if kind == "li" else 0)
        tokens = _runs(text)
        if kind == "li":
            tokens = [("•", False, False, False), (" ", False, False, False)] + tokens
        lines = _wrap(tokens, size, width)
        for li, ln in enumerate(lines):
            if y - leading < MB:
                new_page()
            natural = _line_width(ln, size)
            tw, x = 0.0, indent
            last = (li == len(lines) - 1)
            if align == "center":
                x = ML + (col_w - natural) / 2
            elif align == "justify" and not last:
                gaps = sum(1 for t in ln if t[0] == " ")
                slack = width - natural
                if gaps and 0 < slack:
                    tw = min(slack / gaps, 6.0)        # cap stretch to avoid rivers
            page.line(x, y - size, ln, size, bold, ital, tw)
            y -= leading
    pages.append(page)
    _assemble(pages, pdf_path)
    return "pdfgen (stdlib)"

# --- PDF file assembly --------------------------------------------------------
def _assemble(pages, pdf_path):
    objs = []
    def add(body): objs.append(body); return len(objs)

    font_objs = {}
    for fid, base in (("F1", "Times-Roman"), ("F2", "Times-Bold"), ("F3", "Times-Italic")):
        font_objs[fid] = add(
            b"<< /Type /Font /Subtype /Type1 /BaseFont /" + base.encode() +
            b" /Encoding /WinAnsiEncoding >>")

    pages_id = add(b"")
    page_ids = []
    for pg in pages:
        stream = b"\n".join(pg.ops) + b"\n"
        content_id = add(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
                         + stream + b"endstream")
        annot_ids = []
        for url, x0, y0, x1, y1 in pg.annots:
            uri = _pdf_escape(url.encode("latin-1", "replace"))
            aid = add(b"<< /Type /Annot /Subtype /Link /Rect ["
                      + f"{x0:.2f} {y0:.2f} {x1:.2f} {y1:.2f}".encode()
                      + b"] /Border [0 0 0] /H /N /A << /S /URI /URI (" + uri + b") >> >>")
            annot_ids.append(aid)
        res = (b"<< /Font << /F1 " + str(font_objs["F1"]).encode() + b" 0 R /F2 "
               + str(font_objs["F2"]).encode() + b" 0 R /F3 " + str(font_objs["F3"]).encode()
               + b" 0 R >> >>")
        annots = (b" /Annots [" + b" ".join(str(a).encode() + b" 0 R" for a in annot_ids)
                  + b"]") if annot_ids else b""
        pid = add(b"<< /Type /Page /Parent " + str(pages_id).encode()
                  + b" 0 R /MediaBox [0 0 " + f"{PAGE_W} {PAGE_H}".encode()
                  + b"] /Resources " + res + b" /Contents " + str(content_id).encode()
                  + b" 0 R" + annots + b" >>")
        page_ids.append(pid)

    kids = b" ".join(str(p).encode() + b" 0 R" for p in page_ids)
    objs[pages_id - 1] = (b"<< /Type /Pages /Count " + str(len(page_ids)).encode()
                          + b" /Kids [" + kids + b"] >>")
    catalog = add(b"<< /Type /Catalog /Pages " + str(pages_id).encode() + b" 0 R >>")

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += str(i).encode() + b" 0 obj\n" + body + b"\nendobj\n"
    xref_pos = len(out)
    out += b"xref\n0 " + str(len(objs) + 1).encode() + b"\n0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (b"trailer\n<< /Size " + str(len(objs) + 1).encode() + b" /Root "
            + str(catalog).encode() + b" 0 R >>\nstartxref\n" + str(xref_pos).encode() + b"\n%%EOF")
    Path(pdf_path).write_bytes(out)

if __name__ == "__main__":
    import sys
    build_pdf(sys.argv[1], sys.argv[2])
    print("wrote", sys.argv[2])
