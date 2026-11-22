"""Export PDF A4 avec reportlab (dépendance optionnelle : pip install reportlab).

Couverture, filigrane (brouillon, données fictives), en-tête et pied de page « Page x / y », tableaux à en-tête répété,
renvois [n] cliquables vers la table des sources, liens externes cliquables. Limites : l'arabe n'est pas rendu
(pas de mise en forme droite-gauche) ; les caractères absents de la police sont remplacés par « ? ».
"""

from __future__ import annotations

import io
import os
from functools import lru_cache
from xml.sax.saxutils import escape

from .catalog import LABELS
from .render import clean, cover_rows, safe_url, status_label, template_title, watermark


class PdfUnavailable(RuntimeError):
    """reportlab n'est pas installé."""


_FONT_CANDIDATES = (  # (regular, bold, italic)
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf"),
    ("/Library/Fonts/DejaVuSans.ttf", "/Library/Fonts/DejaVuSans-Bold.ttf", "/Library/Fonts/DejaVuSans-Oblique.ttf"),
    ("C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/segoeuii.ttf"),
)


@lru_cache(maxsize=1)
def _fonts() -> tuple[str, str, str, set[int]]:
    """Enregistre la meilleure police disponible (DejaVu, Segoe UI, sinon Vera livrée avec reportlab)."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    import reportlab

    candidates = list(_FONT_CANDIDATES)
    vera = os.path.join(os.path.dirname(reportlab.__file__), "fonts")
    candidates.append(tuple(os.path.join(vera, f) for f in ("Vera.ttf", "VeraBd.ttf", "VeraIt.ttf")))
    for regular, bold, italic in candidates:
        if all(os.path.exists(p) for p in (regular, bold, italic)):
            pdfmetrics.registerFont(TTFont("NY", regular))
            pdfmetrics.registerFont(TTFont("NY-B", bold))
            pdfmetrics.registerFont(TTFont("NY-I", italic))
            pdfmetrics.registerFontFamily("NY", normal="NY", bold="NY-B", italic="NY-I", boldItalic="NY-B")
            return "NY", "NY-B", "NY-I", set(pdfmetrics.getFont("NY").face.charToGlyph)
    raise PdfUnavailable("Aucune police TrueType trouvée")


def _txt(text: object) -> str:
    """Texte sûr pour reportlab : sans caractère de contrôle, sans glyphe manquant, échappé pour le balisage."""
    covered = _fonts()[3]
    text = "".join(c if (ord(c) in covered or c in "\n\t") else "?" for c in clean(text))
    return escape(text)


def render_pdf(report: dict, logo_path: str | None = None) -> bytes:
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_LEFT
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.pdfgen import canvas as rl_canvas
        from reportlab.platypus import Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError as exc:
        raise PdfUnavailable("Export PDF indisponible : installez reportlab (pip install reportlab)") from exc

    font, bold, italic, _ = _fonts()
    L = LABELS[report["language"]]
    navy, red, grey = colors.HexColor("#12203B"), colors.HexColor("#C0392B"), colors.HexColor("#566077")
    S = {
        "body": ParagraphStyle("body", fontName=font, fontSize=9.5, leading=13, spaceAfter=4, alignment=TA_LEFT),
        "h1": ParagraphStyle("h1", fontName=bold, fontSize=14, leading=18, textColor=navy, spaceBefore=12, spaceAfter=5, keepWithNext=1),
        "h2": ParagraphStyle("h2", fontName=bold, fontSize=11, leading=14, spaceBefore=8, spaceAfter=2, keepWithNext=1),
        "todo": ParagraphStyle("todo", fontName=italic, fontSize=9.5, leading=13, textColor=colors.HexColor("#8A93A6")),
        "cell": ParagraphStyle("cell", fontName=font, fontSize=7.5, leading=9.5),
        "cellh": ParagraphStyle("cellh", fontName=bold, fontSize=7.5, leading=9.5, textColor=colors.white),
        "title": ParagraphStyle("title", fontName=bold, fontSize=25, leading=30, textColor=navy, spaceBefore=18 * mm, spaceAfter=6),
        "brand": ParagraphStyle("brand", fontName=bold, fontSize=8, leading=11, textColor=grey, spaceAfter=8 * mm),
        "status": ParagraphStyle("status", fontName=bold, fontSize=8.5, leading=11, textColor=navy, backColor=colors.HexColor("#F4F6FB"), borderColor=colors.HexColor("#C0392B"), borderWidth=0, borderPadding=6, spaceAfter=6 * mm),
        "sub": ParagraphStyle("sub", fontName=font, fontSize=13, leading=17, textColor=grey, spaceAfter=14 * mm),
        "warn": ParagraphStyle("warn", fontName=font, fontSize=9, leading=12, textColor=colors.HexColor("#7A4A00"), backColor=colors.HexColor("#FDF3E3"), borderPadding=5, spaceAfter=6),
    }
    kind_color = {"fact": "#1F6F4A", "estimate": "#8A6A12", "hypothesis": "#7A3B8F", "computed": "#4A5A78"}
    wm = watermark(report)
    margin = 18 * mm

    class Numbered(rl_canvas.Canvas):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self._saved: list[dict] = []

        def showPage(self):  # noqa: N802 (API reportlab) : on diffère le dessin pour connaître le nombre total de pages
            self._saved.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._saved)
            for state in self._saved:
                self.__dict__.update(state)
                self._decorate(self._pageNumber, total)
                super().showPage()
            super().save()

        def _decorate(self, page: int, total: int) -> None:
            w, h = A4
            if wm:  # une ligne par motif (données fictives / non validé), taille réduite pour rester dans la page
                lines = [_txt_plain(x) for x in wm.split(" · ")]
                size = min(30, 520 / (max(len(x) for x in lines) * 0.66))
                self.saveState()
                self.setFillColor(red)
                self.setFillAlpha(0.13)
                self.setFont(bold, size)
                self.translate(w / 2, h / 2)
                self.rotate(35)
                for i, line in enumerate(lines):
                    self.drawCentredString(0, (len(lines) / 2 - i - 0.8) * size * 1.3, line)
                self.restoreState()
            if page > 1:
                self.setStrokeColor(colors.HexColor("#C9D1E0"))
                self.line(margin, h - 13 * mm, w - margin, h - 13 * mm)
                self.setFont(bold, 7.5)
                self.setFillColor(red)
                self.drawString(margin, h - 11 * mm, _txt_plain(report["meta"]["classification"]))
                self.setFont(font, 7.5)
                self.setFillColor(grey)
                self.drawRightString(w - margin, h - 11 * mm, _txt_plain(report["title"][:95]))
                self.line(margin, 14 * mm, w - margin, 14 * mm)
                self.drawString(margin, 9.5 * mm, _txt_plain(f"{L['version']} {report['version']} · {L[report['status']]}"))
                self.drawRightString(w - margin, 9.5 * mm, f"{L['page']} {page} / {total}")

    def _txt_plain(text: str) -> str:  # canvas : pas de balisage, mais mêmes remplacements de glyphes
        covered = _fonts()[3]
        return "".join(c if ord(c) in covered else "?" for c in clean(text))

    def para(text: str, style: str = "body") -> Paragraph:
        return Paragraph(text, S[style])

    story: list = []
    if logo_path and os.path.isfile(logo_path):
        try:
            story.append(Image(logo_path, width=40 * mm, height=18 * mm, kind="proportional", hAlign="LEFT"))
        except Exception:  # noqa: BLE001 : un logo illisible ne doit pas empêcher l'export
            pass
    story.append(para(_txt("NYANSA · RENSEIGNEMENT AFRIQUE · SOURCES OUVERTES"), "brand"))
    story.append(Table([[_txt_plain(report["meta"]["classification"])]], style=TableStyle([
        ("FONT", (0, 0), (-1, -1), bold, 10), ("TEXTCOLOR", (0, 0), (-1, -1), red), ("BOX", (0, 0), (-1, -1), 1.5, red), ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8)]), hAlign="LEFT"))
    story += [para(_txt(report["title"]), "title"), para(_txt(template_title(report)), "sub"),
              para(_txt(f'{L[report["status"]]} · {L["version"]} {report["version"]}'), "status")]
    cover = Table([[para(f"<font color='#566077'>{_txt(k)}</font>"), para(_txt(v))] for k, v in cover_rows(report)], colWidths=[40 * mm, 110 * mm], hAlign="LEFT")
    cover.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 1), ("TOPPADDING", (0, 0), (-1, -1), 1)]))
    story += [cover, Spacer(1, 10 * mm)]
    story += [para(_txt("⚠ " + w), "warn") for w in report.get("warnings", [])]
    story.append(PageBreak())

    usable = A4[0] - 2 * margin

    def table(sec_key: str, head: list[str], rows: list[list[str]]) -> Table:
        n = len(head)
        rel = [0.05, 0.18, 0.09, 0.11, 0.30, 0.27] if sec_key == "sources" else ([0.09, 0.10, 0.27, 0.08, 0.07, 0.08, 0.09, 0.09, 0.13] if n == 9 else [1 / n] * n)
        data = [[Paragraph(_txt(h), S["cellh"]) for h in head]]
        for row in rows:
            cells = []
            for i, c in enumerate(row):
                url = safe_url(c)
                if url:
                    cells.append(Paragraph(f'<a href="{escape(url, {chr(34): "&quot;"})}" color="#1a5fb4">{_txt(url)}</a>', S["cell"]))
                elif sec_key == "sources" and i == 0:
                    cells.append(Paragraph(f'<a name="src-{_txt(c)}"/>{_txt(c)}', S["cell"]))
                else:
                    cells.append(Paragraph(_txt(c), S["cell"]))
            data.append(cells)
        t = Table(data, colWidths=[usable * r for r in rel], repeatRows=1)
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), navy), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#C9D1E0")),
                               ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F4F6FB")]),
                               ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)]))
        return t

    for sec in report["sections"]:
        story.append(para(_txt(sec["title"]), "h1"))
        if sec.get("na"):
            story.append(para(_txt(L["na"]), "todo"))
            continue
        for b in sec["blocks"]:
            kind = b["kind"]
            if kind == "heading":
                story.append(para(_txt(b["text"]), "h2"))
            elif kind == "table":
                story += [table(sec["key"], b["head"], b["rows"]), Spacer(1, 4)]
            elif not b["text"].strip():
                story.append(para(_txt(L["to_write"]), "todo"))
            else:
                st = status_label(report, b)
                refs = " ".join(f'<a href="#src-{n}" color="#1a5fb4">[{n}]</a>' for n in b.get("refs", []))
                label = f"<font name='{bold}' size='7.5' color='{kind_color.get(kind, '#4A5A78')}'>{_txt(L[kind]).upper()}</font> "
                badge = f" <font name='{italic}' size='8' color='{'#7A4A00' if b.get('status') == 'unconfirmed_social' else '#566077'}'>[{_txt(st)}]</font>" if st else ""
                story.append(KeepTogether(para(f"{label}{_txt(b['text'])}{badge} {refs}")))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=margin, rightMargin=margin, topMargin=19 * mm, bottomMargin=20 * mm,
                            title=clean(report["title"]), author=clean(report["meta"]["analyst"]), subject=template_title(report), creator="Nyansa")
    doc.build(story, canvasmaker=Numbered)
    return buf.getvalue()
