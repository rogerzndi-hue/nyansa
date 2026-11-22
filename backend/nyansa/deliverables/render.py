"""Exports HTML et DOCX (bibliothèque standard uniquement) et utilitaires communs aux trois formats (HTML, DOCX, PDF)."""

from __future__ import annotations

import html
import io
import re
import zipfile
from datetime import datetime
from xml.sax.saxutils import escape as xml_escape

from .catalog import LABELS

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
STATUS_KEYS = ("confirmed", "single_source", "unconfirmed_social")


def clean(text: object) -> str:
    """Retire les caractères de contrôle (interdits en XML 1.0, parasites en PDF) venus de flux externes."""
    return _CTRL.sub("", str(text))


def safe_url(url: str | None) -> str | None:
    return url if url and url.startswith(("http://", "https://")) and " " not in url else None


def watermark(report: dict) -> str | None:
    """Filigrane : données fictives et/ou document non validé. Aucun filigrane seulement pour un document validé réel."""
    L = LABELS[report["language"]]
    parts = []
    if report["meta"].get("demo"):
        parts.append("DONNÉES FICTIVES — DÉMONSTRATION" if report["language"] == "fr" else "FICTITIOUS DATA — DEMONSTRATION")
    if report["status"] not in ("validated", "archived"):
        parts.append(L["watermark"])
    return " · ".join(parts) or None


def period_text(report: dict) -> str:
    p = report["params"]
    return f"{p['start'][:10]} – {p['end'][:10]}"


def block_label(report: dict, block: dict) -> str:
    return LABELS[report["language"]][block["kind"]]


def status_label(report: dict, block: dict) -> str | None:
    s = block.get("status")
    return LABELS[report["language"]][s] if s in STATUS_KEYS else None


def refs_text(block: dict) -> str:
    return "".join(f"[{n}]" for n in block.get("refs", []))


def cover_rows(report: dict) -> list[tuple[str, str]]:
    L, m = LABELS[report["language"]], report["meta"]
    rows = [
        (L["classification"], m["classification"]),
        (L["status"], f"{L[report['status']]} — {L['version']} {report['version']}"),
        (L["period"], period_text(report)),
        (L["analyst"], m["analyst"] or "–"),
        (L["organization"], m["organization"] or "–"),
        (L["recipient"], m["recipient"] or "–"),
        (L["generated"], m["built_at"][:16].replace("T", " ") + " UTC"),
    ]
    if report.get("validated_by"):
        rows.append(("Validation" if report["language"] == "fr" else "Validation", f"{report['validated_by']} — {report['validated_at'][:10]}"))
    return rows


def template_title(report: dict) -> str:
    from .catalog import TEMPLATES

    t = TEMPLATES.get(report["template_id"])
    return getattr(t, report["language"]) if t else report["template_id"]


# ----------------------------------------------------------------------------------------------- HTML
_CSS = """
@page { size: A4; margin: 18mm 16mm 20mm; }
* { box-sizing: border-box; }
body { font: 10.5pt/1.55 "Segoe UI", "DejaVu Sans", Arial, sans-serif; color: #1b2433; margin: 0 auto; max-width: 190mm; padding: 8mm; }
.cover .brand { color: #566077; font-size: 8pt; font-weight: 700; letter-spacing: .16em; text-transform: uppercase; margin-bottom: 7mm; }
.cover .meta-kicker { color: #566077; font-size: 8pt; letter-spacing: .08em; text-transform: uppercase; margin: 0 0 3mm; }
.cover table { width: 100%; border-collapse: collapse; margin-top: 3mm; }
.cover tr:nth-child(even) td { background: #f4f6fb; }
.cover td { padding: 5px 8px; border-bottom: 1px solid #dce2ec; vertical-align: top; }
.cover td:first-child { width: 34%; color: #566077; font-size: 8pt; font-weight: 700; text-transform: uppercase; letter-spacing: .04em; }
.cover td:last-child { color: #1b2433; font-weight: 600; }
.wm { position: fixed; top: 42%; left: 0; right: 0; text-align: center; font-size: 34pt; font-weight: 700; color: #c0392b; opacity: .13; transform: rotate(-28deg); pointer-events: none; z-index: 9; }
.cover { min-height: 230mm; border-top: 8px solid #12203b; padding-top: 24mm; page-break-after: always; }
.cover .cls { display: inline-block; border: 2px solid #c0392b; color: #c0392b; font-weight: 700; padding: 2px 10px; letter-spacing: .08em; }
.cover h1 { font-size: 25pt; line-height: 1.12; margin: 10mm 0 2mm; color: #12203b; letter-spacing: -.02em; } .cover .sub { font-size: 13pt; color: #566; margin-bottom: 10mm; }
.cover .statusline { display: inline-block; margin: 0 0 7mm; padding: 5px 9px; border-left: 3px solid #c0392b; background: #f4f6fb; color: #34415a; font-size: 8.5pt; font-weight: 700; }
.cover table { border-collapse: collapse; } .cover td { padding: 3px 14px 3px 0; vertical-align: top; } .cover td:first-child { color: #566; }
.warn { margin-top: 10mm; padding: 6px 10px; border-left: 4px solid #e08a1e; background: #fdf3e3; }
h2 { font-size: 14pt; color: #12203b; border-bottom: 1px solid #c9d1e0; padding-bottom: 2px; margin: 8mm 0 3mm; break-after: avoid; }
h3 { font-size: 11.5pt; margin: 5mm 0 1mm; break-after: avoid; }
p { margin: 0 0 2.2mm; } .lbl { display: inline-block; min-width: 18mm; font-size: 8pt; font-weight: 700; text-transform: uppercase; color: #fff; background: #12203b; padding: 0 4px; border-radius: 2px; margin-right: 4px; }
.lbl.fact { background: #1f6f4a; } .lbl.estimate { background: #8a6a12; } .lbl.hypothesis { background: #7a3b8f; } .lbl.computed { background: #4a5a78; }
.st { font-size: 8.5pt; padding: 0 5px; border-radius: 8px; border: 1px solid #aab; } .st.unconfirmed_social { background: #fdf3e3; border-color: #e08a1e; color: #7a4a00; }
.refs a, .refs { font-size: 8.5pt; color: #1a5fb4; text-decoration: none; }
.todo { color: #8a93a6; font-style: italic; } .guide { color: #6a7388; font-size: 9pt; }
table.t { border-collapse: collapse; width: 100%; font-size: 8.5pt; margin: 2mm 0 4mm; } .t th { background: #12203b; color: #fff; text-align: left; } .t th, .t td { border: 1px solid #c9d1e0; padding: 2px 5px; vertical-align: top; word-break: break-word; }
.t tr:nth-child(even) td { background: #f4f6fb; } thead { display: table-header-group; } tr { break-inside: avoid; }
footer { margin-top: 10mm; border-top: 1px solid #c9d1e0; padding-top: 2mm; font-size: 8.5pt; color: #566; }
"""


def render_html(report: dict) -> str:
    L, e = LABELS[report["language"]], lambda x: html.escape(clean(x), quote=True)  # noqa: E731
    out = [f'<!doctype html><html lang="{report["language"]}"><head><meta charset="utf-8"><title>{e(report["title"])}</title><style>{_CSS}</style></head><body>']
    wm = watermark(report)
    if wm:
        out.append(f'<div class="wm">{e(wm)}</div>')
    out.append(f'<section class="cover"><div class="brand">NYANSA · Renseignement Afrique · Sources ouvertes</div><span class="cls">{e(report["meta"]["classification"])}</span><h1>{e(report["title"])}</h1><div class="sub">{e(template_title(report))}</div><div class="statusline">{e(L[report["status"]])} · {e(L["version"])} {report["version"]}</div><div class="meta-kicker">Informations du document</div><table>')
    out += [f"<tr><td>{e(k)}</td><td>{e(v)}</td></tr>" for k, v in cover_rows(report)]
    out.append("</table>")
    out += [f'<div class="warn">{e(w)}</div>' for w in report.get("warnings", [])]
    out.append("</section>")
    for sec in report["sections"]:
        out.append(f'<h2>{e(sec["title"])}</h2>')
        if sec.get("na"):
            out.append(f'<p class="todo">{e(L["na"])}</p>')
            continue
        for b in sec["blocks"]:
            kind = b["kind"]
            if kind == "heading":
                out.append(f"<h3>{e(b['text'])}</h3>")
            elif kind == "table":
                out.append('<table class="t"><thead><tr>' + "".join(f"<th>{e(h)}</th>" for h in b["head"]) + "</tr></thead><tbody>")
                is_src = sec["key"] == "sources"
                for row in b["rows"]:
                    cells = []
                    for i, c in enumerate(row):
                        url = safe_url(c)
                        if url:
                            cells.append(f'<td><a href="{e(url)}" rel="noopener noreferrer">{e(url)}</a></td>')
                        elif is_src and i == 0:
                            cells.append(f'<td id="src-{e(c)}">{e(c)}</td>')
                        else:
                            cells.append(f"<td>{e(c)}</td>")
                    out.append("<tr>" + "".join(cells) + "</tr>")
                out.append("</tbody></table>")
            elif not b["text"].strip():
                out.append(f'<p class="todo">{e(L["to_write"])}</p>' + (f'<p class="guide">{e(sec["guide"])}</p>' if sec.get("guide") else ""))
            else:
                st = status_label(report, b)
                refs = "".join(f'<a href="#src-{n}">[{n}]</a>' for n in b.get("refs", []))
                out.append(f'<p><span class="lbl {kind}">{e(block_label(report, b))}</span>{e(b["text"])}'
                           + (f' <span class="st {e(b["status"])}">{e(st)}</span>' if st else "")
                           + (f' <span class="refs">{refs}</span>' if refs else "") + "</p>")
    out.append(f'<footer>{e(report["meta"]["classification"])} · {e(report["title"])} · {e(L["version"])} {report["version"]}</footer></body></html>')
    return "".join(out)


# ----------------------------------------------------------------------------------------------- DOCX
_NS = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
       'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"')
_STYLES = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles {_NS}>
<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:cs="Calibri"/><w:sz w:val="21"/><w:lang w:val="fr-FR"/></w:rPr></w:rPrDefault>
<w:pPrDefault><w:pPr><w:spacing w:after="100" w:line="264" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:before="1200" w:after="200"/></w:pPr><w:rPr><w:b/><w:color w:val="12203B"/><w:sz w:val="52"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:before="420" w:after="140"/><w:pBdr><w:bottom w:val="single" w:sz="6" w:space="1" w:color="C9D1E0"/></w:pBdr><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:color w:val="12203B"/><w:sz w:val="30"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:before="200" w:after="40"/><w:outlineLvl w:val="1"/></w:pPr><w:rPr><w:b/><w:sz w:val="24"/></w:rPr></w:style>
<w:style w:type="table" w:styleId="TableGrid"><w:name w:val="Table Grid"/><w:tblPr><w:tblBorders><w:top w:val="single" w:sz="4" w:color="C9D1E0"/><w:left w:val="single" w:sz="4" w:color="C9D1E0"/><w:bottom w:val="single" w:sz="4" w:color="C9D1E0"/><w:right w:val="single" w:sz="4" w:color="C9D1E0"/><w:insideH w:val="single" w:sz="4" w:color="C9D1E0"/><w:insideV w:val="single" w:sz="4" w:color="C9D1E0"/></w:tblBorders><w:tblCellMar><w:left w:w="70" w:type="dxa"/><w:right w:w="70" w:type="dxa"/></w:tblCellMar></w:tblPr></w:style>
</w:styles>"""
_KIND_COLOR = {"fact": "1F6F4A", "estimate": "8A6A12", "hypothesis": "7A3B8F", "computed": "4A5A78"}


class _Docx:
    def __init__(self) -> None:
        self.body: list[str] = []
        self.links: list[str] = []  # URL externes -> rId100+i
        self._bm = 0

    def run(self, text: str, *, bold: bool = False, italic: bool = False, color: str | None = None, size: int | None = None, underline: bool = False) -> str:
        pr = ("<w:b/>" if bold else "") + ("<w:i/>" if italic else "") + (f'<w:color w:val="{color}"/>' if color else "") \
            + (f'<w:sz w:val="{size}"/>' if size else "") + ('<w:u w:val="single"/>' if underline else "")
        return f'<w:r>{"<w:rPr>" + pr + "</w:rPr>" if pr else ""}<w:t xml:space="preserve">{xml_escape(clean(text))}</w:t></w:r>'

    def para(self, runs: str, style: str | None = None, extra: str = "") -> str:
        ppr = (f'<w:pStyle w:val="{style}"/>' if style else "") + extra
        return f'<w:p>{"<w:pPr>" + ppr + "</w:pPr>" if ppr else ""}{runs}</w:p>'

    def ext_link(self, url: str, text: str) -> str:
        self.links.append(url)
        return f'<w:hyperlink r:id="rId{99 + len(self.links)}">{self.run(text, color="1A5FB4", underline=True)}</w:hyperlink>'

    def int_link(self, anchor: str, text: str) -> str:
        return f'<w:hyperlink w:anchor="{anchor}">{self.run(text, color="1A5FB4", size=17)}</w:hyperlink>'

    def bookmark(self, name: str, inner: str) -> str:
        self._bm += 1
        return f'<w:bookmarkStart w:id="{self._bm}" w:name="{name}"/>{inner}<w:bookmarkEnd w:id="{self._bm}"/>'

    def table(self, head: list[str], rows: list[list[str]], widths: list[int], src: bool = False) -> str:
        def cell(content: str, w: int, header: bool = False) -> str:
            shade = '<w:shd w:val="clear" w:color="auto" w:fill="12203B"/>' if header else ""
            return f'<w:tc><w:tcPr><w:tcW w:w="{w}" w:type="dxa"/>{shade}</w:tcPr><w:p><w:pPr><w:spacing w:after="0"/></w:pPr>{content}</w:p></w:tc>'

        grid = "".join(f'<w:gridCol w:w="{w}"/>' for w in widths)
        out = [f'<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/><w:tblW w:w="{sum(widths)}" w:type="dxa"/><w:tblLayout w:type="fixed"/></w:tblPr><w:tblGrid>{grid}</w:tblGrid>']
        out.append('<w:tr><w:trPr><w:tblHeader/><w:cantSplit/></w:trPr>' + "".join(cell(self.run(h, bold=True, color="FFFFFF", size=17), w, True) for h, w in zip(head, widths)) + "</w:tr>")
        for row in rows:
            cells = []
            for i, (c, w) in enumerate(zip(row, widths)):
                url = safe_url(c)
                if url:
                    inner = self.ext_link(url, url)
                elif src and i == 0:
                    inner = self.bookmark(f"src-{xml_escape(clean(c))}", self.run(c, size=17))
                else:
                    inner = self.run(c, size=17)
                cells.append(cell(inner, w))
            out.append("<w:tr><w:trPr><w:cantSplit/></w:trPr>" + "".join(cells) + "</w:tr>")
        out.append("</w:tbl>")
        return "".join(out) + self.para("")


def _widths(n: int, key: str) -> list[int]:
    total = 9638  # largeur utile A4, marges de 20 mm (en vingtièmes de point)
    if key == "sources":
        return [400, 1650, 900, 1100, 2600, 2988]
    if n == 9:
        return [1150, 900, 2150, 1050, 750, 900, 850, 950, 938]
    return [total // n] * n


def render_docx(report: dict) -> bytes:
    L, d = LABELS[report["language"]], _Docx()
    d.body.append(d.para(d.run("NYANSA · RENSEIGNEMENT AFRIQUE · SOURCES OUVERTES", bold=True, color="556677", size=16)))
    for text, kw in ((report["meta"]["classification"], dict(bold=True, color="C0392B")),):
        d.body.append(d.para(d.run(text, **kw)))
    d.body.append(d.para(d.run(report["title"], bold=True), "Title"))
    d.body.append(d.para(d.run(f'{L[report["status"]]} · {L["version"]} {report["version"]}', bold=True, color="4A5A78", size=18)))
    d.body.append(d.para(d.run(template_title(report), color="556677", size=26)))
    for k, v in cover_rows(report):
        d.body.append(d.para(d.run(f"{k} : ", color="556677") + d.run(v)))
    for w in report.get("warnings", []):
        d.body.append(d.para(d.run("⚠ " + w, color="7A4A00")))
    d.body.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')
    for sec in report["sections"]:
        d.body.append(d.para(d.run(sec["title"]), "Heading1"))
        if sec.get("na"):
            d.body.append(d.para(d.run(L["na"], italic=True, color="8A93A6")))
            continue
        for b in sec["blocks"]:
            kind = b["kind"]
            if kind == "heading":
                d.body.append(d.para(d.run(b["text"]), "Heading2"))
            elif kind == "table":
                d.body.append(d.table(b["head"], b["rows"], _widths(len(b["head"]), sec["key"]), src=sec["key"] == "sources"))
            elif not b["text"].strip():
                d.body.append(d.para(d.run(L["to_write"], italic=True, color="8A93A6")))
            else:
                runs = d.run(f"{block_label(report, b)} ", bold=True, color=_KIND_COLOR.get(kind), size=17) + d.run(b["text"])
                st = status_label(report, b)
                if st:
                    runs += d.run(f"  [{st}]", italic=True, color="7A4A00" if b["status"] == "unconfirmed_social" else "556677", size=17)
                runs += "".join(d.int_link(f"src-{n}", f"[{n}]") for n in b.get("refs", []))
                d.body.append(d.para(runs))
    wm = watermark(report)
    header = d.para(d.run(wm or "", bold=True, color="C0392B", size=22), extra='<w:jc w:val="center"/>')
    footer_txt = f'{report["meta"]["classification"]} · {report["title"][:90]} · {L["version"]} {report["version"]} · {L["page"]} '
    field = ('<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>'
             '<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>1</w:t></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r>')
    footer = d.para(d.run(footer_txt, color="556677", size=16) + field, extra='<w:jc w:val="center"/>')
    sect = ('<w:sectPr><w:headerReference w:type="default" r:id="rId3"/><w:footerReference w:type="default" r:id="rId4"/>'
            '<w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134" w:header="567" w:footer="567" w:gutter="0"/></w:sectPr>')
    document = f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {_NS}><w:body>{"".join(d.body)}{sect}</w:body></w:document>'
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
            '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header1.xml"/>'
            '<Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/>'
            + "".join(f'<Relationship Id="rId{100 + i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="{xml_escape(u, {chr(34): "&quot;"})}" TargetMode="External"/>'
                      for i, u in enumerate(d.links)) + "</Relationships>")
    created = datetime.fromisoformat(report["meta"]["built_at"]).strftime("%Y-%m-%dT%H:%M:%SZ")
    core = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f'<dc:title>{xml_escape(clean(report["title"]))}</dc:title><dc:creator>{xml_escape(clean(report["meta"]["analyst"]))}</dc:creator>'
            f'<dcterms:created xsi:type="dcterms:W3CDTF">{created}</dcterms:created></cp:coreProperties>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                   '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
                   '<Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/>'
                   '<Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>'
                   '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/></Types>')
        z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
                   '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/></Relationships>')
        z.writestr("word/document.xml", document)
        z.writestr("word/styles.xml", _STYLES)
        z.writestr("word/_rels/document.xml.rels", rels)
        z.writestr("word/header1.xml", f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:hdr {_NS}>{header}</w:hdr>')
        z.writestr("word/footer1.xml", f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:ftr {_NS}>{footer}</w:ftr>')
        z.writestr("docProps/core.xml", core)
    return buf.getvalue()
