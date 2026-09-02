#!/usr/bin/env python3
"""
KeenPay documentation -> PDF builder.

One script, one renderer. Converts the project's markdown docs into styled,
print-ready PDFs for reviewers who want a single file they can read offline.

Usage
-----
    pip install reportlab
    python scripts/build_docs.py                 # build everything
    python scripts/build_docs.py --only PRD      # build one doc (substring match)
    python scripts/build_docs.py --no-combined   # skip the combined dossier

Output lands in docs/pdf/. Edit the DOCS list below to change what is built.

Why one script and not one per document: every document shares the same
renderer, so a styling fix happens in exactly one place. Adding a document
means adding a line to DOCS, never copying this file.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    ListFlowable,
    ListItem,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Preformatted,
    Spacer,
    Table,
    TableStyle,
)

# ---------------------------------------------------------------------------
# Configuration -- edit this list to change what gets built.
# ---------------------------------------------------------------------------

PROJECT = "KeenPay"
TAGLINE = "Agentic commerce with bounded AI"
REPO_URL = "https://github.com/ambitiouss22/KeenPay"

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "docs" / "pdf"

# (source path relative to repo root, output stem, cover subtitle)
DOCS = [
    ("README.md",                          "KeenPay-Overview",         "Project overview"),
    ("docs/PRD.md",                        "KeenPay-PRD",              "Product requirements"),
    ("docs/ARCHITECTURE.md",               "KeenPay-Architecture",     "System architecture"),
    ("docs/AI_JUDGMENT.md",                "KeenPay-AI-Judgment",      "Where the AI decides"),
    ("docs/GUARDRAILS_AND_SAFETY.md",      "KeenPay-Safety",           "Guardrails and safety model"),
    ("docs/DISCOUNT_POLICY_SAFETY_FIX.md", "KeenPay-Bounded-AI",       "Bounded AI: merchant-defined discount limits"),
    ("docs/DISCOUNT_POLICY_QUICK_START.md","KeenPay-Policy-QuickStart","Discount policy quick start"),
    ("docs/API_SPEC.md",                   "KeenPay-API",              "API specification"),
    ("docs/AUTH.md",                       "KeenPay-Auth",             "Authentication"),
    ("docs/SCHEMA.sql",                    "KeenPay-Database-Schema",  "Database schema (DDL)"),
    ("docs/DISCOUNT_POLICY_SCHEMA.sql",    "KeenPay-Policy-Schema",    "Discount policy schema (DDL)"),
]

# Documents included in the single combined dossier, in narrative order.
COMBINED_STEM = "KeenPay-Technical-Dossier"
COMBINED = [
    "README.md",
    "docs/PRD.md",
    "docs/ARCHITECTURE.md",
    "docs/AI_JUDGMENT.md",
    "docs/GUARDRAILS_AND_SAFETY.md",
    "docs/DISCOUNT_POLICY_SAFETY_FIX.md",
    "docs/API_SPEC.md",
    "docs/AUTH.md",
]

# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------

INK        = colors.HexColor("#12151A")
BODY       = colors.HexColor("#2B3038")
MUTED      = colors.HexColor("#6B7280")
ACCENT     = colors.HexColor("#0F766E")
ACCENT_DK  = colors.HexColor("#0B4F4A")
RULE       = colors.HexColor("#D9DEE5")
CODE_BG    = colors.HexColor("#F5F7F9")
CODE_BD    = colors.HexColor("#DCE2E9")
TABLE_HEAD = colors.HexColor("#EDF1F4")
TABLE_ALT  = colors.HexColor("#FAFBFC")

PAGE_W, PAGE_H = A4
MARGIN_X, MARGIN_TOP, MARGIN_BOT = 20 * mm, 22 * mm, 20 * mm
FRAME_W = PAGE_W - 2 * MARGIN_X

# ---------------------------------------------------------------------------
# Fonts. Prefer a Unicode face: the docs contain box-drawing diagrams, arrows
# and the rupee sign, none of which exist in reportlab's built-in Helvetica.
# ---------------------------------------------------------------------------

FONT_SANS = "Helvetica"
FONT_SANS_B = "Helvetica-Bold"
FONT_SANS_I = "Helvetica-Oblique"
FONT_MONO = "Courier"
FONT_MONO_B = "Courier-Bold"
_font_cmap: set[int] | None = None


def _font_candidates():
    """Directories that may hold DejaVu, in order of preference."""
    dirs = [
        "/usr/share/fonts/truetype/dejavu",
        "/usr/share/fonts/dejavu",
        "/Library/Fonts",
        os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
    ]
    try:  # matplotlib bundles DejaVu and is cross-platform
        import matplotlib
        dirs.insert(0, os.path.join(
            os.path.dirname(matplotlib.__file__), "mpl-data", "fonts", "ttf"))
    except Exception:
        pass
    return dirs


def register_fonts() -> None:
    """Register DejaVu if we can find it; otherwise stay on the built-ins."""
    global FONT_SANS, FONT_SANS_B, FONT_SANS_I, FONT_MONO, FONT_MONO_B, _font_cmap

    wanted = {
        "DejaVuSans":          "DejaVuSans.ttf",
        "DejaVuSans-Bold":     "DejaVuSans-Bold.ttf",
        "DejaVuSans-Oblique":  "DejaVuSans-Oblique.ttf",
        "DejaVuSansMono":      "DejaVuSansMono.ttf",
        "DejaVuSansMono-Bold": "DejaVuSansMono-Bold.ttf",
    }
    for d in _font_candidates():
        if not os.path.isdir(d):
            continue
        if all(os.path.exists(os.path.join(d, f)) for f in wanted.values()):
            try:
                for name, fn in wanted.items():
                    pdfmetrics.registerFont(TTFont(name, os.path.join(d, fn)))
            except Exception:
                continue
            FONT_SANS, FONT_SANS_B, FONT_SANS_I = "DejaVuSans", "DejaVuSans-Bold", "DejaVuSans-Oblique"
            FONT_MONO, FONT_MONO_B = "DejaVuSansMono", "DejaVuSansMono-Bold"
            try:
                face = pdfmetrics.getFont("DejaVuSans").face
                _font_cmap = set(face.charToGlyph.keys())
            except Exception:
                _font_cmap = None
            print(f"  fonts: DejaVu from {d}")
            return
    print("  fonts: DejaVu not found, using Helvetica/Courier "
          "(diagram glyphs will be transliterated)")


# Characters with no glyph in the fallback fonts, mapped to safe equivalents.
_ALWAYS_MAP = {
    "\ufe0f": "",       # emoji variation selector
    "\u2705": "\u2713",  # white heavy check mark -> check mark
    "\u274c": "\u2717",  # cross mark -> ballot X
}
_ASCII_MAP = {
    "\u2500": "-", "\u2502": "|", "\u251c": "+", "\u2514": "+", "\u2524": "+",
    "\u250c": "+", "\u2510": "+", "\u2518": "+", "\u2534": "+", "\u252c": "+",
    "\u253c": "+", "\u2192": "->", "\u2190": "<-", "\u2193": "v", "\u2191": "^",
    "\u2014": "--", "\u2013": "-", "\u2018": "'", "\u2019": "'",
    "\u201c": '"', "\u201d": '"', "\u2022": "*", "\u2713": "[ok]",
    "\u2717": "[x]", "\u26a0": "!", "\u20b9": "INR ", "\u2026": "...",
}


def sanitize(text: str) -> str:
    """Replace characters the active font cannot draw."""
    for a, b in _ALWAYS_MAP.items():
        text = text.replace(a, b)
    if _font_cmap is None:  # built-in fonts: strip to ASCII equivalents
        for a, b in _ASCII_MAP.items():
            text = text.replace(a, b)
        return text.encode("latin-1", "replace").decode("latin-1")
    missing = {c for c in set(text) if ord(c) > 127 and ord(c) not in _font_cmap}
    for c in missing:
        text = text.replace(c, _ASCII_MAP.get(c, "?"))
    return text


# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------

def build_styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()["Normal"]
    S = {}
    S["body"] = ParagraphStyle(
        "body", parent=base, fontName=FONT_SANS, fontSize=9.5, leading=14.5,
        textColor=BODY, spaceAfter=7, alignment=TA_LEFT)
    S["h1"] = ParagraphStyle(
        "h1", parent=S["body"], fontName=FONT_SANS_B, fontSize=19, leading=24,
        textColor=INK, spaceBefore=6, spaceAfter=10)
    S["h2"] = ParagraphStyle(
        "h2", parent=S["body"], fontName=FONT_SANS_B, fontSize=14, leading=19,
        textColor=ACCENT_DK, spaceBefore=15, spaceAfter=7)
    S["h3"] = ParagraphStyle(
        "h3", parent=S["body"], fontName=FONT_SANS_B, fontSize=11.5, leading=16,
        textColor=INK, spaceBefore=11, spaceAfter=5)
    S["h4"] = ParagraphStyle(
        "h4", parent=S["body"], fontName=FONT_SANS_B, fontSize=10, leading=14,
        textColor=BODY, spaceBefore=9, spaceAfter=4)
    S["quote"] = ParagraphStyle(
        "quote", parent=S["body"], leftIndent=10, borderPadding=(0, 0, 0, 8),
        textColor=MUTED, fontName=FONT_SANS_I)
    S["cell"] = ParagraphStyle(
        "cell", parent=S["body"], fontSize=8.2, leading=11.5, spaceAfter=0)
    S["cellh"] = ParagraphStyle(
        "cellh", parent=S["cell"], fontName=FONT_SANS_B, textColor=INK)
    S["caption"] = ParagraphStyle(
        "caption", parent=S["body"], fontSize=7.6, leading=10,
        textColor=MUTED, spaceBefore=1, spaceAfter=8)
    S["cover_t"] = ParagraphStyle(
        "cover_t", parent=S["body"], fontName=FONT_SANS_B, fontSize=34,
        leading=40, textColor=INK, alignment=TA_CENTER, spaceAfter=6)
    S["cover_s"] = ParagraphStyle(
        "cover_s", parent=S["body"], fontSize=13, leading=19, textColor=ACCENT,
        alignment=TA_CENTER, spaceAfter=4)
    S["cover_m"] = ParagraphStyle(
        "cover_m", parent=S["body"], fontSize=9.5, leading=15, textColor=MUTED,
        alignment=TA_CENTER)
    S["divider"] = ParagraphStyle(
        "divider", parent=S["body"], fontName=FONT_SANS_B, fontSize=24,
        leading=30, textColor=INK, spaceBefore=0, spaceAfter=8)
    return S


# ---------------------------------------------------------------------------
# Inline markdown -> reportlab mini-HTML
# ---------------------------------------------------------------------------

def esc(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def inline(text: str) -> str:
    """Convert inline markdown. Code spans are protected from other rules."""
    text = sanitize(text)
    spans: list[str] = []

    def stash(m):
        spans.append(m.group(1))
        return f"\x00{len(spans) - 1}\x00"

    text = re.sub(r"`([^`]+)`", stash, text)
    text = esc(text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)                 # images -> alt
    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)[^)]*\)",
                  r'<link href="\2" color="#0F766E">\1</link>', text)
    text = re.sub(r"\*\*\*(.+?)\*\*\*", r"<b><i>\1</i></b>", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", text)
    text = re.sub(r"(?<![\w_])__(.+?)__(?![\w_])", r"<b>\1</b>", text)
    text = re.sub(r"~~(.+?)~~", r"<strike>\1</strike>", text)

    def pop(m):
        code = esc(spans[int(m.group(1))])
        # No padding spaces: they leave a visible gap before following
        # punctuation (e.g. "Foo ,"). The background colour is the affordance.
        return (f'<font face="{FONT_MONO}" size="8.6" '
                f'backColor="#EEF2F5" color="#0B4F4A">{code}</font>')

    return re.sub(r"\x00(\d+)\x00", pop, text)


# ---------------------------------------------------------------------------
# Block parsing
# ---------------------------------------------------------------------------

FENCE = re.compile(r"^\s*```+\s*([A-Za-z0-9_+-]*)\s*$")
ATX = re.compile(r"^(#{1,6})\s+(.*?)\s*#*$")
HRULE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
BULLET = re.compile(r"^(\s*)[-*+]\s+(.*)$")
NUMBER = re.compile(r"^(\s*)(\d+)[.)]\s+(.*)$")
TABLE_SEP = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]*$")


def _code_font_size(lines: list[str], avail: float) -> float:
    """Shrink code font until the widest line fits. Keeps ASCII diagrams intact."""
    longest = max((len(l) for l in lines), default=0)
    for size in (8.4, 8.0, 7.5, 7.0, 6.5, 6.0, 5.5, 5.0):
        if longest * pdfmetrics.stringWidth("M", FONT_MONO, size) <= avail:
            return size
    return 5.0


BOX_CHARS = "─│┌┐└┘├┤┬┴┼"
BOX_ENDS = "│┐┘┤"  # characters that close a box on the right


def align_box_art(lines: list[str]) -> list[str]:
    """Square up the right-hand edge of an ASCII box diagram.

    Hand-written box art usually has a ragged right edge because trailing
    spaces drift by a character or two. Rendered in a monospace font that
    reads as a broken diagram, so pad each closing edge to the block width.
    Display only -- the source markdown is never modified.
    """
    if not any(any(c in BOX_CHARS for c in l) for l in lines):
        return lines
    width = max(len(l) for l in lines)
    out = []
    for l in lines:
        if l and l[-1] in BOX_ENDS and len(l) < width:
            l = l[:-1] + " " * (width - len(l)) + l[-1]
        out.append(l)
    return out


def code_block(lines: list[str], lang: str, S) -> list:
    lines = [sanitize(l.rstrip()) for l in lines]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        return []
    lines = align_box_art(lines)
    avail = FRAME_W - 16
    size = _code_font_size(lines, avail)
    style = ParagraphStyle(
        f"code{size}", fontName=FONT_MONO, fontSize=size, leading=size * 1.32,
        textColor=colors.HexColor("#1F2933"), spaceAfter=0, spaceBefore=0)

    # A Preformatted block does not split across pages, so a listing longer
    # than one frame would overflow and be clipped. Chunk it into page-sized
    # pieces and emit one boxed table per chunk.
    usable_h = PAGE_H - MARGIN_TOP - MARGIN_BOT - 30
    per_page = max(12, int(usable_h / (size * 1.32)))

    def boxed(chunk: list[str]):
        t = Table([[Preformatted("\n".join(chunk), style)]], colWidths=[FRAME_W])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), CODE_BG),
            ("BOX", (0, 0), (-1, -1), 0.6, CODE_BD),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        return t

    out: list = [Spacer(1, 3)]
    if len(lines) <= per_page:
        out.append(boxed(lines))
    else:
        for i in range(0, len(lines), per_page):
            out.append(boxed(lines[i:i + per_page]))
            if i + per_page < len(lines):
                out.append(Spacer(1, 2))
    label = {"mermaid": "Mermaid diagram source",
             "sql": "SQL", "json": "JSON", "yaml": "YAML",
             "bash": "Shell", "http": "HTTP", "python": "Python"}.get(lang.lower())
    out.append(Paragraph(label, S["caption"]) if label else Spacer(1, 8))
    return out


def make_table(rows: list[list[str]], S):
    if not rows:
        return None
    ncols = max(len(r) for r in rows)
    rows = [r + [""] * (ncols - len(r)) for r in rows]

    # Column widths. Each column must be at least wide enough for its longest
    # single word, otherwise reportlab breaks words mid-character ("Phas e").
    # Remaining space is then shared out in proportion to total content length.
    PAD = 12          # left + right cell padding
    CELL_FS = 8.2     # must match S["cell"] / S["cellh"] font size

    def word_floor(col: int) -> float:
        widest = 0.0
        for r_i, r in enumerate(rows):
            txt = re.sub(r"[*`\[\]]", "", r[col])
            font = FONT_SANS_B if r_i == 0 else FONT_SANS
            for w in txt.split():
                widest = max(widest, pdfmetrics.stringWidth(w, font, CELL_FS))
        return min(widest + PAD + 2, FRAME_W * 0.45)

    floors = [word_floor(c) for c in range(ncols)]
    weights = [max(max(len(re.sub(r"[*`\[\]]", "", r[c])) for r in rows), 6)
               for c in range(ncols)]

    if sum(floors) >= FRAME_W:
        k = FRAME_W / sum(floors)          # pathological: scale to fit
        widths = [f * k for f in floors]
    else:
        surplus = FRAME_W - sum(floors)
        tw = sum(weights)
        widths = [f + surplus * w / tw for f, w in zip(floors, weights)]

    data = [[Paragraph(inline(c), S["cellh"] if i == 0 else S["cell"])
             for c in row] for i, row in enumerate(rows)]
    tbl = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), TABLE_HEAD),
        ("GRID", (0, 0), (-1, -1), 0.4, RULE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    for i in range(2, len(data), 2):
        style.append(("BACKGROUND", (0, i), (-1, i), TABLE_ALT))
    tbl.setStyle(TableStyle(style))
    return tbl


def hrule():
    t = Table([[""]], colWidths=[FRAME_W], rowHeights=[0.7])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), RULE)]))
    return t


def parse_markdown(md: str, S, first_heading_style="h1") -> list:
    """Markdown -> list of reportlab flowables."""
    md = md.replace("\r\n", "\n").replace("\r", "\n")
    lines = md.split("\n")
    flow: list = []
    i, n = 0, len(lines)
    seen_h1 = False

    while i < n:
        line = lines[i]

        # fenced code
        m = FENCE.match(line)
        if m:
            lang, buf, i = m.group(1), [], i + 1
            while i < n and not FENCE.match(lines[i]):
                buf.append(lines[i]); i += 1
            i += 1
            flow += code_block(buf, lang, S)
            continue

        if not line.strip():
            i += 1
            continue

        if HRULE.match(line):
            flow += [Spacer(1, 7), hrule(), Spacer(1, 9)]
            i += 1
            continue

        # heading
        m = ATX.match(line)
        if m:
            level, text = len(m.group(1)), m.group(2)
            key = {1: "h1", 2: "h2", 3: "h3"}.get(level, "h4")
            if level == 1 and not seen_h1:
                key, seen_h1 = first_heading_style, True
            flow.append(Paragraph(inline(text), S[key]))
            i += 1
            continue

        # table: a header row followed by a |---|---| separator
        if "|" in line and i + 1 < n and TABLE_SEP.match(lines[i + 1]):
            def split_row(r):
                r = r.strip()
                if r.startswith("|"): r = r[1:]
                if r.endswith("|"): r = r[:-1]
                return [c.strip() for c in r.split("|")]
            rows = [split_row(line)]
            i += 2
            while i < n and "|" in lines[i] and lines[i].strip():
                rows.append(split_row(lines[i])); i += 1
            tbl = make_table(rows, S)
            if tbl is not None:
                flow += [Spacer(1, 4), tbl, Spacer(1, 10)]
            continue

        # blockquote
        if line.lstrip().startswith(">"):
            buf = []
            while i < n and lines[i].lstrip().startswith(">"):
                buf.append(lines[i].lstrip()[1:].strip()); i += 1
            flow.append(Paragraph(inline(" ".join(buf)), S["quote"]))
            flow.append(Spacer(1, 5))
            continue

        # lists
        if BULLET.match(line) or NUMBER.match(line):
            items, ordered = [], bool(NUMBER.match(line))
            while i < n:
                mb, mn = BULLET.match(lines[i]), NUMBER.match(lines[i])
                if not (mb or mn):
                    # a wrapped continuation line belongs to the previous item
                    if lines[i].strip() and lines[i].startswith((" ", "\t")) and items:
                        items[-1] += " " + lines[i].strip(); i += 1
                        continue
                    break
                items.append((mb.group(2) if mb else mn.group(3)))
                i += 1
            flow.append(ListFlowable(
                [ListItem(Paragraph(inline(t), S["body"]), leftIndent=14)
                 for t in items],
                bulletType="1" if ordered else "bullet",
                bulletFontName=FONT_SANS, bulletFontSize=8.5,
                bulletColor=ACCENT, leftIndent=16, spaceBefore=1, spaceAfter=7))
            continue

        # paragraph
        buf = []
        while i < n and lines[i].strip() and not ATX.match(lines[i]) \
                and not FENCE.match(lines[i]) and not HRULE.match(lines[i]) \
                and not BULLET.match(lines[i]) and not NUMBER.match(lines[i]) \
                and not lines[i].lstrip().startswith(">"):
            buf.append(lines[i].strip()); i += 1
        if buf:
            flow.append(Paragraph(inline(" ".join(buf)), S["body"]))
    return flow


# ---------------------------------------------------------------------------
# Document assembly
# ---------------------------------------------------------------------------

class Doc(BaseDocTemplate):
    def __init__(self, path, title, **kw):
        super().__init__(str(path), pagesize=A4, title=title,
                         author=PROJECT, subject=TAGLINE,
                         leftMargin=MARGIN_X, rightMargin=MARGIN_X,
                         topMargin=MARGIN_TOP, bottomMargin=MARGIN_BOT, **kw)
        self.doc_title = title
        frame = Frame(MARGIN_X, MARGIN_BOT, FRAME_W,
                      PAGE_H - MARGIN_TOP - MARGIN_BOT, id="main")
        self.addPageTemplates([
            PageTemplate(id="cover", frames=[frame]),
            PageTemplate(id="body", frames=[frame], onPage=self._chrome),
        ])

    def _chrome(self, canvas, doc):
        canvas.saveState()
        canvas.setFont(FONT_SANS, 7.5)
        canvas.setFillColor(MUTED)
        y = PAGE_H - MARGIN_TOP + 8
        canvas.drawString(MARGIN_X, y, f"{PROJECT} \u00b7 {self.doc_title}"
                          if _font_cmap else f"{PROJECT} - {self.doc_title}")
        canvas.setStrokeColor(RULE)
        canvas.setLineWidth(0.5)
        canvas.line(MARGIN_X, y - 4, PAGE_W - MARGIN_X, y - 4)
        canvas.line(MARGIN_X, MARGIN_BOT - 9, PAGE_W - MARGIN_X, MARGIN_BOT - 9)
        canvas.drawString(MARGIN_X, MARGIN_BOT - 19, REPO_URL)
        canvas.drawRightString(PAGE_W - MARGIN_X, MARGIN_BOT - 19,
                               str(canvas.getPageNumber()))
        canvas.restoreState()


def cover(title: str, subtitle: str, S) -> list:
    stamp = dt.date.today().strftime("%d %B %Y")
    bar = Table([[""]], colWidths=[46 * mm], rowHeights=[2.6])
    bar.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), ACCENT)]))
    return [
        Spacer(1, 62 * mm),
        Paragraph(PROJECT, S["cover_t"]),
        Paragraph(sanitize(TAGLINE), S["cover_s"]),
        Spacer(1, 7 * mm),
        bar,
        Spacer(1, 7 * mm),
        Paragraph(f"<b>{esc(sanitize(title))}</b>", S["cover_m"]),
        Paragraph(esc(sanitize(subtitle)), S["cover_m"]),
        Spacer(1, 5 * mm),
        Paragraph(stamp, S["cover_m"]),
        Paragraph(REPO_URL, S["cover_m"]),
        NextPageTemplate("body"),
        PageBreak(),
    ]


def load_source(path: Path) -> str:
    """Read a source file as markdown.

    A .sql file has no markdown structure, so wrap it: split on the comment
    banners the schema files already use, turning each into a heading with
    its statements in a fenced block. Everything downstream stays identical.
    """
    raw = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix.lower() != ".sql":
        return raw

    lines = raw.replace("\r\n", "\n").split("\n")
    rule = re.compile(r"^\s*--\s*[-=]{4,}\s*$")      # -- ==========
    comment = re.compile(r"^\s*--\s?(.*)$")

    out: list[str] = []
    buf: list[str] = []
    seen_title = False

    def flush():
        body = "\n".join(buf).strip("\n")
        if body.strip():
            out.extend(["```sql", body, "```", ""])
        buf.clear()

    i, n = 0, len(lines)
    while i < n:
        # A heading is a comment sandwiched between two rule lines. Nothing
        # else qualifies -- ordinary prose comments stay in the SQL verbatim,
        # because guessing at them mangles the document.
        if (i + 2 < n and rule.match(lines[i]) and rule.match(lines[i + 2])
                and comment.match(lines[i + 1]) and not rule.match(lines[i + 1])):
            text = comment.match(lines[i + 1]).group(1).strip()
            if text:
                flush()
                out.extend([f"{'#' if not seen_title else '##'} {text}", ""])
                seen_title = True
                i += 3
                continue
        buf.append(lines[i])
        i += 1
    flush()

    if not seen_title:
        out.insert(0, f"# {path.stem.replace('_', ' ')}")
        out.insert(1, "")
    return "\n".join(out)


def title_from(md: str, fallback: str) -> str:
    for line in md.split("\n"):
        m = ATX.match(line)
        if m and len(m.group(1)) == 1:
            return re.sub(r"[*`]", "", m.group(2)).strip()
    return fallback


def build_one(src: Path, stem: str, subtitle: str, S) -> Path | None:
    if not src.exists():
        print(f"  skip {src.relative_to(REPO_ROOT)} (not found)")
        return None
    md = load_source(src)
    title = title_from(md, stem.replace("-", " "))
    out = OUT_DIR / f"{stem}.pdf"
    doc = Doc(out, title)
    story = cover(title, subtitle, S) + parse_markdown(md, S)
    doc.multiBuild(story)
    print(f"  {out.relative_to(REPO_ROOT)}  ({out.stat().st_size // 1024} KB)")
    return out


def build_combined(S) -> Path | None:
    parts = [(REPO_ROOT / p, p) for p in COMBINED]
    parts = [(f, p) for f, p in parts if f.exists()]
    if not parts:
        return None
    out = OUT_DIR / f"{COMBINED_STEM}.pdf"
    doc = Doc(out, "Technical Dossier")
    story = cover("Technical Dossier",
                  "Complete project documentation in one file", S)

    # Contents page
    story.append(Paragraph("Contents", S["h1"]))
    rows = [["#", "Section", "Source"]]
    for i, (f, p) in enumerate(parts, 1):
        rows.append([str(i),
                     title_from(f.read_text(encoding="utf-8", errors="replace"), f.stem),
                     p])
    story += [make_table(rows, S), PageBreak()]

    for i, (f, p) in enumerate(parts, 1):
        md = f.read_text(encoding="utf-8", errors="replace")
        t = title_from(md, f.stem)
        story.append(Paragraph(f'<font color="#0F766E">{i:02d}</font>', S["divider"]))
        story.append(Paragraph(esc(sanitize(t)), S["divider"]))
        story.append(Paragraph(p, S["caption"]))
        story += [hrule(), Spacer(1, 10)]
        story += parse_markdown(md, S, first_heading_style="h2")
        if i < len(parts):
            story.append(PageBreak())

    doc.multiBuild(story)
    print(f"  {out.relative_to(REPO_ROOT)}  ({out.stat().st_size // 1024} KB)")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Build KeenPay documentation PDFs.")
    ap.add_argument("--only", help="build only docs whose path/stem contains this")
    ap.add_argument("--no-combined", action="store_true",
                    help="skip the combined dossier")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"{PROJECT} docs -> {OUT_DIR.relative_to(REPO_ROOT)}")
    register_fonts()
    S = build_styles()

    todo = DOCS
    if args.only:
        k = args.only.lower()
        todo = [d for d in DOCS if k in d[0].lower() or k in d[1].lower()]
        if not todo:
            print(f"nothing matches {args.only!r}")
            return 1

    built = [b for b in (build_one(REPO_ROOT / s, stem, sub, S)
                         for s, stem, sub in todo) if b]
    if not args.no_combined and not args.only:
        b = build_combined(S)
        if b:
            built.append(b)

    print(f"done: {len(built)} PDF(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
