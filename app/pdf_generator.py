"""
GPA-ERP — Legal document PDF generator.
Overlays typed content (including rich HTML from TipTap) onto the
company KOP SURAT (letterhead) template.
"""
import io
import shutil
from datetime import datetime
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_JUSTIFY
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, Table, TableStyle
from reportlab.lib import colors
from reportlab.pdfgen import canvas as rl_canvas
from pypdf import PdfReader, PdfWriter

from app.config import get_settings

TEMPLATE_PATH = Path(__file__).parent / "templates" / "kop_surat.pdf"
MD_SIGNATURE_PATH = Path(get_settings().UPLOAD_DIR).resolve() / "system" / "md_signature.png"
LEGACY_MD_SIGNATURE_PATH = Path(__file__).parent / "templates" / "md_signature.png"


def resolve_md_signature_path() -> Path:
    """Move a legacy bundled signature into configurable persistent storage."""
    if not MD_SIGNATURE_PATH.exists() and LEGACY_MD_SIGNATURE_PATH.exists():
        try:
            MD_SIGNATURE_PATH.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(LEGACY_MD_SIGNATURE_PATH, MD_SIGNATURE_PATH)
        except OSError:
            return LEGACY_MD_SIGNATURE_PATH
    return MD_SIGNATURE_PATH

# ── Layout constants (A4 = 595.28 × 841.89 pt) ──────────────────────────────
PAGE_W, PAGE_H = A4

MARGIN_L      = 65
MARGIN_R      = PAGE_W - 65
CONTENT_W     = MARGIN_R - MARGIN_L
HEADER_H      = 108
FOOTER_H      = 62
CONTENT_TOP   = PAGE_H - HEADER_H - 12
CONTENT_BOTTOM= FOOTER_H + 10

FONT_R = "Helvetica"
FONT_B = "Helvetica-Bold"
FONT_I = "Helvetica-Oblique"

MONTHS_ID = [
    "", "Januari", "Februari", "Maret", "April", "Mei", "Juni",
    "Juli", "Agustus", "September", "Oktober", "November", "Desember",
]

_TYPE_CODES = {
    "proposal":     "SPH",
    "berita_acara": "BA",
    "surat_jalan":  "SJ",
    "other":        "SRT",
}


def _id_date(dt: datetime) -> str:
    return f"Jakarta, {dt.day} {MONTHS_ID[dt.month]} {dt.year}"


# ── Paragraph styles ──────────────────────────────────────────────────────────

def _make_styles():
    normal  = ParagraphStyle("normal",   fontName=FONT_R, fontSize=10, leading=14, spaceAfter=4)
    justify = ParagraphStyle("justify",  fontName=FONT_R, fontSize=10, leading=14, spaceAfter=4, alignment=TA_JUSTIFY)
    h2      = ParagraphStyle("h2",       fontName=FONT_B, fontSize=12, leading=16, spaceBefore=6, spaceAfter=4)
    h3      = ParagraphStyle("h3",       fontName=FONT_B, fontSize=10, leading=14, spaceBefore=4, spaceAfter=2)
    bullet  = ParagraphStyle("bullet",   fontName=FONT_R, fontSize=10, leading=14, leftIndent=16, spaceAfter=2)
    numbered= ParagraphStyle("numbered", fontName=FONT_R, fontSize=10, leading=14, leftIndent=16, spaceAfter=2)
    return normal, justify, h2, h3, bullet, numbered

STYLE_NORMAL, STYLE_JUSTIFY, STYLE_H2, STYLE_H3, STYLE_BULLET, STYLE_NUMBERED = _make_styles()


# ── HTML → render blocks ──────────────────────────────────────────────────────

class _Block:
    __slots__ = ("kind", "markup", "list_idx")
    def __init__(self, kind: str, markup: str, list_idx: int = 0):
        self.kind     = kind        # "p" | "h2" | "h3" | "li_ul" | "li_ol"
        self.markup   = markup      # reportlab XML
        self.list_idx = list_idx


class _TableCell:
    __slots__ = ("markup", "colspan", "rowspan", "header")
    def __init__(self, markup: str, colspan: int = 1, rowspan: int = 1, header: bool = False):
        self.markup = markup
        self.colspan = max(1, colspan)
        self.rowspan = max(1, rowspan)
        self.header = header


class _TableBlock:
    __slots__ = ("rows",)
    def __init__(self, rows: list[list[_TableCell]]):
        self.rows = rows


class _HTMLTableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows: list[list[_TableCell]] = []
        self._row: list[_TableCell] | None = None
        self._cell: dict | None = None
        self._buf = ""

    def handle_starttag(self, tag, attrs):
        attrs_d = dict(attrs)
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._cell = {
                "colspan": int(attrs_d.get("colspan") or 1),
                "rowspan": int(attrs_d.get("rowspan") or 1),
                "header": tag == "th",
            }
            self._buf = ""
        elif self._cell and tag in ("strong", "b"):
            self._buf += "<b>"
        elif self._cell and tag in ("em", "i"):
            self._buf += "<i>"
        elif self._cell and tag == "br":
            self._buf += "<br/>"

    def handle_endtag(self, tag):
        if tag in ("strong", "b") and self._cell:
            self._buf += "</b>"
        elif tag in ("em", "i") and self._cell:
            self._buf += "</i>"
        elif tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(_TableCell(self._buf.strip(), **self._cell))
            self._cell = None
            self._buf = ""
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            data = data.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            self._buf += data


def _parse_table(html: str) -> _TableBlock:
    p = _HTMLTableParser()
    p.feed(html)
    return _TableBlock(p.rows)


class _HTMLToBlocks(HTMLParser):
    """Convert TipTap HTML output to a list of _Block objects."""

    INLINE = {"strong", "b", "em", "i", "u"}

    def __init__(self):
        super().__init__()
        self.blocks: list[_Block] = []
        self._buf      = ""
        self._in_blk   = False
        self._tag      = ""
        self._list     = ""   # "ul" | "ol"
        self._ol_n     = 0
        self._in_table = False
        self._table_depth = 0
        self._table_html = ""

    @staticmethod
    def _tag_html(tag: str, attrs) -> str:
        attr_txt = "".join(f' {k}="{v}"' for k, v in attrs)
        return f"<{tag}{attr_txt}>"

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._in_table = True
            self._table_depth = 1
            self._table_html = self._tag_html(tag, attrs)
            return
        if self._in_table:
            self._table_html += self._tag_html(tag, attrs)
            if tag == "table":
                self._table_depth += 1
            return
        if tag in ("p", "h1", "h2", "h3", "h4"):
            self._buf = ""; self._in_blk = True; self._tag = tag
        elif tag == "ul":
            self._list = "ul"
        elif tag == "ol":
            self._list = "ol"; self._ol_n = 0
        elif tag == "li":
            self._buf = ""; self._in_blk = True; self._tag = "li"
            if self._list == "ol":
                self._ol_n += 1
        elif tag in ("strong", "b") and self._in_blk:
            self._buf += "<b>"
        elif tag in ("em", "i") and self._in_blk:
            self._buf += "<i>"
        elif tag == "u" and self._in_blk:
            self._buf += "<u>"
        elif tag == "br" and self._in_blk:
            self._buf += "<br/>"

    def handle_endtag(self, tag):
        if self._in_table:
            self._table_html += f"</{tag}>"
            if tag == "table":
                self._table_depth -= 1
                if self._table_depth <= 0:
                    self.blocks.append(_parse_table(self._table_html))
                    self._in_table = False
                    self._table_html = ""
            return
        if tag in ("strong", "b") and self._in_blk:
            self._buf += "</b>"
        elif tag in ("em", "i") and self._in_blk:
            self._buf += "</i>"
        elif tag == "u" and self._in_blk:
            self._buf += "</u>"
        elif tag in ("p", "h1", "h2", "h3", "h4"):
            kind = "h2" if tag in ("h1", "h2") else ("h3" if tag in ("h3", "h4") else "p")
            self.blocks.append(_Block(kind, self._buf.strip()))
            self._buf = ""; self._in_blk = False; self._tag = ""
        elif tag == "li":
            kind = f"li_{self._list or 'ul'}"
            self.blocks.append(_Block(kind, self._buf.strip(), self._ol_n))
            self._buf = ""; self._in_blk = False; self._tag = ""
        elif tag in ("ul", "ol"):
            self._list = ""

    def handle_data(self, data):
        if self._in_table:
            self._table_html += data
            return
        if self._in_blk:
            # Escape XML special chars in raw text (our manually-inserted tags are safe)
            data = data.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            self._buf += data

    def handle_entityref(self, name):
        if self._in_table:
            self._table_html += f"&{name};"
            return
        if self._in_blk:
            self._buf += f"&{name};"

    def handle_charref(self, name):
        if self._in_table:
            self._table_html += f"&#{name};"
            return
        if self._in_blk:
            self._buf += f"&#{name};"


def _parse_html(html: str) -> list[_Block]:
    """Parse HTML body (TipTap output) into render blocks."""
    p = _HTMLToBlocks()
    p.feed(html)
    return p.blocks


def _plain_blocks(text: str) -> list[_Block]:
    """Fallback for plain-text bodies (legacy docs)."""
    blocks = []
    for line in text.splitlines():
        blocks.append(_Block("p", line.replace("&", "&amp;")))
    return blocks


# ── Canvas helpers ────────────────────────────────────────────────────────────

def _draw_para(c: rl_canvas.Canvas, markup: str, style: ParagraphStyle,
               x: float, y: float, width: float) -> float:
    """Draw a Paragraph at (x, y) and return the height consumed."""
    try:
        para = Paragraph(markup, style)
    except Exception:
        # If markup is invalid XML, strip to plain text
        import re
        plain = re.sub(r"<[^>]+>", "", markup)
        para  = Paragraph(plain, style)
    w, h = para.wrap(width, 9999)
    para.drawOn(c, x, y - h)
    return h


def _table_flowable(block: _TableBlock, width: float) -> tuple[Table, float]:
    grid: list[list[object]] = []
    spans: list[tuple[int, int, int, int]] = []
    header_cells: list[tuple[int, int]] = []

    for r, row in enumerate(block.rows):
        while len(grid) <= r:
            grid.append([])
        c_idx = 0
        for cell in row:
            while c_idx < len(grid[r]) and grid[r][c_idx] != "":
                c_idx += 1
            while len(grid[r]) <= c_idx:
                grid[r].append(None)
            para_style = ParagraphStyle(
                "table_header" if cell.header else "table_cell",
                fontName=FONT_B if cell.header else FONT_R,
                fontSize=9,
                leading=12,
                alignment=1,
            )
            grid[r][c_idx] = Paragraph(cell.markup or "&nbsp;", para_style)
            if cell.header:
                header_cells.append((c_idx, r))
            if cell.colspan > 1 or cell.rowspan > 1:
                spans.append((c_idx, r, c_idx + cell.colspan - 1, r + cell.rowspan - 1))
            for rr in range(r, r + cell.rowspan):
                while len(grid) <= rr:
                    grid.append([])
                while len(grid[rr]) <= c_idx + cell.colspan - 1:
                    grid[rr].append(None)
                for cc in range(c_idx, c_idx + cell.colspan):
                    if rr == r and cc == c_idx:
                        continue
                    grid[rr][cc] = ""
            c_idx += cell.colspan

    cols = max((len(row) for row in grid), default=1)
    data = [(row + [""] * (cols - len(row))) for row in grid]
    if cols == 6:
        col_widths = [width * n for n in (0.10, 0.23, 0.10, 0.12, 0.23, 0.22)]
    else:
        col_widths = [width / cols] * cols

    style_cmds = [
        ("GRID", (0, 0), (-1, -1), 0.75, colors.black),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]
    style_cmds.extend(("SPAN", (c1, r1), (c2, r2)) for c1, r1, c2, r2 in spans)
    style_cmds.extend(("FONTNAME", (c, r), (c, r), FONT_B) for c, r in header_cells)

    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle(style_cmds))
    _, h = table.wrap(width, 9999)
    return table, h


# ── Main generator ────────────────────────────────────────────────────────────

def generate_document_pdf(
    doc_number:        str,
    doc_type:          str,
    title:             str,
    subject:           str,
    body:              str,
    recipient_name:    str | None,
    recipient_company: str | None,
    recipient_address: str | None,
    closing:           str | None,
    quoted_amount:     Decimal | None,
    creator_name:      str,
    signer_name:       str | None,
    signer_title:      str | None,
    signed_at:         datetime | None,
    created_at:        datetime,
) -> bytes:
    """
    Renders the document onto the KOP SURAT and returns PDF bytes.
    body may be TipTap HTML or plain text.
    """
    buf = io.BytesIO()
    c   = rl_canvas.Canvas(buf, pagesize=A4)

    y = CONTENT_TOP   # vertical cursor — decrements downward

    def ln(pts: float = 0.0):
        nonlocal y
        y -= pts

    def new_page():
        nonlocal y
        c.showPage()
        y = CONTENT_TOP

    def check_space(needed: float):
        if y - needed < CONTENT_BOTTOM:
            new_page()

    # ── Date ──────────────────────────────────────────────────────────────
    ln(4)
    c.setFont(FONT_R, 10)
    c.drawRightString(MARGIN_R, y, _id_date(created_at))
    ln(18)

    # ── Doc meta (Nomor / Lampiran / Perihal) ─────────────────────────────
    label_x = MARGIN_L
    colon_x = MARGIN_L + 72
    value_x = colon_x + 8
    meta_rows = [
        ("Nomor",    doc_number or "-"),
        ("Lampiran", "-"),
        ("Perihal",  subject),
    ]
    for label, value in meta_rows:
        c.setFont(FONT_R, 10)
        c.drawString(label_x, y, label)
        c.drawString(colon_x, y, ":")
        # Wrap Perihal if long
        avail = MARGIN_R - value_x - 4
        if c.stringWidth(value, FONT_R, 10) > avail:
            h = _draw_para(c, value, STYLE_NORMAL, value_x, y + 2, avail)
            ln(max(h, 13))
        else:
            c.drawString(value_x, y, value)
            ln(14)
    ln(12)

    # ── Recipient ──────────────────────────────────────────────────────────
    c.setFont(FONT_R, 10)
    c.drawString(MARGIN_L, y, "Kepada Yth.:")
    ln(13)
    for line in filter(None, [recipient_name, recipient_company, recipient_address]):
        for sub in line.splitlines():
            c.drawString(MARGIN_L + 10, y, sub)
            ln(13)
    ln(10)

    # ── Salutation ─────────────────────────────────────────────────────────
    c.setFont(FONT_R, 10)
    c.drawString(MARGIN_L, y, "Dengan hormat,")
    ln(18)

    # ── Body (HTML or plain text) ──────────────────────────────────────────
    is_html = body.strip().startswith("<")
    blocks  = _parse_html(body) if is_html else _plain_blocks(body)

    for blk in blocks:
        if isinstance(blk, _TableBlock):
            table, h = _table_flowable(blk, CONTENT_W)
            check_space(h + 12)
            table.drawOn(c, MARGIN_L, y - h)
            ln(h + 10)
            continue

        markup = blk.markup
        if not markup:
            ln(6)
            continue

        if blk.kind == "h2":
            style  = STYLE_H2
            needed = 20
        elif blk.kind == "h3":
            style  = STYLE_H3
            needed = 16
        elif blk.kind == "li_ul":
            markup = f"• &nbsp;{markup}"
            style  = STYLE_BULLET
            needed = 14
        elif blk.kind == "li_ol":
            markup = f"{blk.list_idx}.&nbsp;{markup}"
            style  = STYLE_NUMBERED
            needed = 14
        else:
            style  = STYLE_NORMAL
            needed = 14

        check_space(needed)
        h = _draw_para(c, markup, style, MARGIN_L, y + 2, CONTENT_W)
        ln(h + style.spaceAfter)

    ln(6)

    # ── Quoted amount ──────────────────────────────────────────────────────
    if quoted_amount is not None:
        check_space(32)
        c.setFont(FONT_B, 10)
        c.drawString(MARGIN_L, y, "Nilai Penawaran:")
        ln(15)
        formatted = f"Rp {quoted_amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        c.setFont(FONT_B, 11)
        c.drawString(MARGIN_L + 10, y, formatted)
        ln(20)

    # ── Closing ────────────────────────────────────────────────────────────
    if closing:
        check_space(20)
        h = _draw_para(c, closing.replace("&", "&amp;"), STYLE_NORMAL, MARGIN_L, y + 2, CONTENT_W)
        ln(h + 4)
    else:
        default = (
            "Demikian surat ini kami sampaikan. "
            "Atas perhatian dan kerja sama Bapak/Ibu, kami ucapkan terima kasih."
        )
        h = _draw_para(c, default, STYLE_NORMAL, MARGIN_L, y + 2, CONTENT_W)
        ln(h + 4)

    ln(22)

    # ── Signature block ────────────────────────────────────────────────────
    check_space(80)
    sig_x = MARGIN_R - 145
    sign_date = signed_at or created_at
    c.setFont(FONT_R, 10)
    c.drawString(sig_x, y, _id_date(sign_date))
    ln(13)
    c.drawString(sig_x, y, "Hormat kami,")
    ln(13)
    c.setFont(FONT_B, 10)
    c.drawString(sig_x, y, "PT GARUDA PRIMA AKSARA")
    signature_path = resolve_md_signature_path()
    if signed_at and signer_title == "Managing Director" and signature_path.exists():
        c.drawImage(
            str(signature_path),
            sig_x,
            y - 48,
            width=118,
            height=42,
            preserveAspectRatio=True,
            mask="auto",
        )
    ln(50)   # signature gap

    if signer_name:
        c.setFont(FONT_B, 10)
        c.drawString(sig_x, y, signer_name)
        ln(13)
    if signer_title:
        c.setFont(FONT_R, 9)
        c.drawString(sig_x, y, signer_title)

    # ── DRAFT watermark (unsigned docs) ───────────────────────────────────
    if signer_name is None:
        c.saveState()
        c.setFillColorRGB(0.82, 0.82, 0.82)
        c.setFont(FONT_B, 60)
        c.translate(PAGE_W / 2, PAGE_H / 2)
        c.rotate(35)
        c.drawCentredString(0, 0, "DRAFT")
        c.restoreState()

    c.save()
    buf.seek(0)
    content_bytes = buf.read()

    # ── Merge content onto KOP SURAT background ───────────────────────────
    tmpl    = PdfReader(str(TEMPLATE_PATH))
    content = PdfReader(io.BytesIO(content_bytes))
    writer  = PdfWriter()

    for i in range(len(content.pages)):
        bg = tmpl.pages[0]          # KOP SURAT on every page
        bg.merge_page(content.pages[i])
        writer.add_page(bg)

    out = io.BytesIO()
    writer.write(out)
    out.seek(0)
    return out.read()


# ── Pay Slip PDF ──────────────────────────────────────────────────────────────

def generate_payslip(
    period_label:      str,
    employee_no:       str,
    employee_name:     str,
    department:        str | None,
    bank_name:         str | None,
    bank_account:      str | None,
    gross_salary:      Decimal,
    bpjs_tk_employee:  Decimal,
    bpjs_kes_employee: Decimal,
    bpjs_tk_employer:  Decimal,
    bpjs_kes_employer: Decimal,
    pph21_amount:      Decimal,
    pph21_method:      str,
    tunjangan_pajak:   Decimal,
    thr_amount:        Decimal | None,
    net_salary:        Decimal,
    generated_at:      datetime,
    components_snapshot: dict | None = None,
) -> bytes:
    """
    Generates a clean pay-slip PDF overlaid on the KOP SURAT letterhead.
    Returns PDF bytes.
    """
    buf = io.BytesIO()
    c   = rl_canvas.Canvas(buf, pagesize=A4)

    y = CONTENT_TOP

    def ln(pts: float = 0.0):
        nonlocal y
        y -= pts

    def rp(amount: Decimal) -> str:
        return f"Rp {float(amount):,.0f}".replace(",", ".")

    def draw_line_item(label: str, value: Decimal, bold: bool = False,
                       color: tuple = (0.1, 0.1, 0.1), bg: tuple | None = None):
        nonlocal y
        row_h = 17
        if bg:
            c.saveState()
            c.setFillColorRGB(*bg)
            c.rect(MARGIN_L, y - 2, CONTENT_W, row_h, fill=1, stroke=0)
            c.restoreState()
        c.setFont(FONT_B if bold else FONT_R, 9.5)
        c.setFillColorRGB(*color)
        c.drawString(MARGIN_L + 6, y + 3, label)
        c.drawRightString(MARGIN_R - 6, y + 3, rp(value))
        c.setFillColorRGB(0, 0, 0)
        ln(row_h)

    def section_header(title: str):
        nonlocal y
        c.setFillColorRGB(0.10, 0.16, 0.28)
        c.rect(MARGIN_L, y - 1, CONTENT_W, 19, fill=1, stroke=0)
        c.setFont(FONT_B, 9.5)
        c.setFillColorRGB(1, 1, 1)
        c.drawString(MARGIN_L + 6, y + 4, title.upper())
        c.setFillColorRGB(0, 0, 0)
        ln(22)

    # ── Title ─────────────────────────────────────────────────────────
    ln(4)
    c.setFont(FONT_B, 14)
    c.setFillColorRGB(0.10, 0.16, 0.28)
    c.drawString(MARGIN_L, y, f"SLIP GAJI — {period_label.upper()}")
    ln(16)
    c.setFont(FONT_I, 8.5)
    c.setFillColorRGB(0.55, 0.55, 0.55)
    c.drawString(MARGIN_L, y, "RAHASIA — Dokumen ini hanya untuk karyawan yang bersangkutan")
    c.setFillColorRGB(0, 0, 0)
    ln(20)

    c.setStrokeColorRGB(0.85, 0.85, 0.85)
    c.setLineWidth(0.5)
    c.line(MARGIN_L, y, MARGIN_R, y)
    ln(14)

    # ── Employee info table ────────────────────────────────────────────
    right_x  = MARGIN_L + CONTENT_W * 0.5 + 4
    label_w  = 80

    def info_row(lbl1: str, val1: str, lbl2: str = "", val2: str = ""):
        nonlocal y
        c.setFont(FONT_R, 8.5)
        c.setFillColorRGB(0.50, 0.50, 0.50)
        c.drawString(MARGIN_L, y, lbl1)
        c.setFont(FONT_B, 9)
        c.setFillColorRGB(0.10, 0.10, 0.10)
        c.drawString(MARGIN_L + label_w, y, f": {val1}")
        if lbl2:
            c.setFont(FONT_R, 8.5)
            c.setFillColorRGB(0.50, 0.50, 0.50)
            c.drawString(right_x, y, lbl2)
            c.setFont(FONT_B, 9)
            c.setFillColorRGB(0.10, 0.10, 0.10)
            c.drawString(right_x + label_w, y, f": {val2 or chr(8212)}")
        c.setFillColorRGB(0, 0, 0)
        ln(14)

    info_row("No. Karyawan", employee_no,              "Departemen",    department or chr(8212))
    info_row("Nama",         employee_name,             "Metode PPh 21", pph21_method)
    info_row("Bank",         bank_name or chr(8212),    "No. Rekening",  bank_account or chr(8212))
    info_row("Tanggal Cetak", generated_at.strftime("%d %B %Y"))
    ln(10)

    # ── Pendapatan ────────────────────────────────────────────────────
    section_header("Pendapatan")
    draw_line_item("Gaji Kotor (Gross)", gross_salary)
    if tunjangan_pajak > 0:
        draw_line_item("Tunjangan Pajak (Gross-Up)", tunjangan_pajak)
    if thr_amount is not None and thr_amount > 0:
        draw_line_item("Tunjangan Hari Raya (THR)", thr_amount)
    total_earn = gross_salary + tunjangan_pajak + (thr_amount or Decimal(0))
    draw_line_item("TOTAL PENDAPATAN", total_earn, bold=True, bg=(0.96, 0.98, 1.0))
    ln(8)

    # ── Potongan ──────────────────────────────────────────────────────
    section_header("Potongan")
    draw_line_item("BPJS Ketenagakerjaan (Karyawan)", bpjs_tk_employee)
    draw_line_item("BPJS Kesehatan (Karyawan)",       bpjs_kes_employee)
    draw_line_item(f"PPh 21 ({pph21_method})",        pph21_amount)
    manual_deductions = Decimal(0)
    snapshot_items = (components_snapshot or {}).get("components", [])
    for item in snapshot_items if isinstance(snapshot_items, list) else []:
        if not isinstance(item, dict) or item.get("component_type") not in ("BPJS", "TAX"):
            continue
        amount = Decimal(str(item.get("amount") or 0))
        if amount > 0:
            draw_line_item(item.get("component_name") or item.get("component_type"), amount)
            manual_deductions += amount
    total_deduct = bpjs_tk_employee + bpjs_kes_employee + pph21_amount + manual_deductions
    draw_line_item("TOTAL POTONGAN", total_deduct, bold=True, bg=(1.0, 0.97, 0.95))
    ln(8)

    # ── Net salary highlight ───────────────────────────────────────────
    net_h = 30
    c.setFillColorRGB(0.02, 0.52, 0.48)
    c.roundRect(MARGIN_L, y - 4, CONTENT_W, net_h, 4, fill=1, stroke=0)
    c.setFont(FONT_B, 10.5)
    c.setFillColorRGB(1, 1, 1)
    c.drawString(MARGIN_L + 10, y + 8, "GAJI BERSIH (NET)")
    c.drawRightString(MARGIN_R - 10, y + 8, rp(net_salary))
    c.setFillColorRGB(0, 0, 0)
    ln(net_h + 14)

    # ── Employer contributions (informational) ─────────────────────────
    c.setFont(FONT_B, 8.5)
    c.setFillColorRGB(0.45, 0.45, 0.45)
    c.drawString(MARGIN_L, y, "KONTRIBUSI PERUSAHAAN (tidak mempengaruhi gaji bersih):")
    ln(13)
    c.setFont(FONT_R, 8.5)
    c.drawString(MARGIN_L + 6, y, f"BPJS TK: {rp(bpjs_tk_employer)}")
    c.drawString(MARGIN_L + CONTENT_W * 0.40, y, f"BPJS Kesehatan: {rp(bpjs_kes_employer)}")
    c.setFillColorRGB(0, 0, 0)
    ln(22)

    # ── Footer rule + disclaimer ───────────────────────────────────────
    c.setStrokeColorRGB(0.85, 0.85, 0.85)
    c.line(MARGIN_L, y, MARGIN_R, y)
    ln(10)
    disc_style = ParagraphStyle(
        "disc", fontName=FONT_I, fontSize=7.5, leading=11,
        textColor=colors.Color(0.65, 0.65, 0.65),
    )
    _draw_para(
        c,
        "Slip gaji ini digenerate secara otomatis oleh GPA Cost Control ERP. "
        "Apabila terdapat perbedaan, harap hubungi tim HR/Finance.",
        disc_style, MARGIN_L, y, CONTENT_W,
    )
    c.setFillColorRGB(0, 0, 0)

    c.save()
    buf.seek(0)
    content_bytes = buf.read()

    # ── Merge onto KOP SURAT background ──────────────────────────────
    tmpl    = PdfReader(str(TEMPLATE_PATH))
    content = PdfReader(io.BytesIO(content_bytes))
    writer  = PdfWriter()

    for i in range(len(content.pages)):
        bg = tmpl.pages[0]
        bg.merge_page(content.pages[i])
        writer.add_page(bg)

    out = io.BytesIO()
    writer.write(out)
    out.seek(0)
    return out.read()
