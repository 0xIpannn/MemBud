#!/usr/bin/env python3
"""Build a compressed, two-column DOCX of the Chapter 3 draft for a 6-page paper.

Source content: the user's Chapter 3 DOCX (paper/source/chapter-3-source.txt),
compressed to ~950 words + Table 1 + Figure 1 (architecture) so the method
section costs ~1.2-1.4 pages in IEEE-style two-column format.

Outputs (only these):
  paper/compressed/chapter-3-compressed-double-column.docx
  paper/compressed/chapter-3-compressed.md
"""
from __future__ import annotations

import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "paper" / "compressed"
OUT.mkdir(parents=True, exist_ok=True)
FIG = ROOT / "paper" / "figures" / "fig-3-5-membud-architecture.png"

# ---- content blocks: (kind, payload) ---------------------------------------
# kinds: title, h1, h2, p (supports **bold**), table, figure, caption
BLOCKS = [
    ("title", "3. Implementation Method"),
    ("h1", "3.1 Thinking Framework"),
    ("p", "MemBud is a pre-commit safety layer for LLM memory agents. A memory agent proposes WRITE, REVISE and DELETE operations, and a wrong operation becomes persistent state; the errors of interest are omission of required information, corruption of valid prior records, and unsupported additions (Chen et al., 2026; Yang et al., 2026). Transition verification already exists for textual views of memory, but it is untested whether an explicit graph of records, claims, evidence and operations detects errors that a text verifier given the same facts would miss. MemBud converts each proposed update into a canonical transition, verifies coverage, preservation and faithfulness, and returns ACCEPT or HOLD before anything is written. The same frozen transition is judged by an unguarded pipeline and by text, text-fair and graph verifiers, so any difference is attributable to the verifier rather than the input; results are reported as transition-level error rates, valid-update acceptance, latency and token cost, with a negative result treated as a valid outcome. RQ1 (reliability) maps to this controlled comparison, RQ2 (modularity) to the adapter contract, and RQ3 (trade-offs) to acceptance and cost."),
    ("h1", "3.2 Research Methods"),
    ("p", "**Data collection.** The evaluation uses a hand-authored synthetic set of 36 transition cases, because public memory benchmarks do not supply the per-claim labels a transition verifier needs. A case is one frozen package: the prior memory state, evidence with supported and optionally contradicted claims, required claims, one proposed resulting state, and declared operations (WRITE, REVISE, DELETE, SUPERSEDE, NOOP). The set covers 18 categories with two cases each: seven valid-update categories (14 cases), six faulty-update categories (12 cases: omission, corruption without or with insufficient evidence, unsupported addition, unsupported provenance, unresolved contradiction), and five invalid-input families (10 cases: deletion without structured authorisation and undeclared insertion, modification or removal). The 36 cases are split 18 development / 18 held-out (7 valid, 6 faulty, 5 invalid each) and were frozen before any version-2 run. Labels are stored separately and never enter verifier input or prompts; they are single-reviewer exploratory annotations, and an independent second review is pending. Cases and labels are SHA-256 pinned, and no licensed benchmark data is used."),
    ("p", "**Preprocessing.** Preprocessing performs no imputation or feature engineering. Each case is parsed into typed records, evidence items, required claims and operations, normalised to explicit operations (legacy supersedes notation becomes REVISE or SUPERSEDE), and validated under schema version 2. Every actual state change must be declared by exactly the matching operation; a prior record and a candidate record may each be targeted once; DELETE requires structured authorisation (source, evidence_id, target_id, rationale) whose evidence exists and whose target matches; and NOOP must leave the state unchanged. A case that breaks any invariant is recorded as INVALID_INPUT for every arm, rejected before verification, and never writes memory. From the validated transition T = (B, C, E, R, O) the system derives two renderings of identical content: a canonical graph G with transition, record, claim, evidence and operation nodes and typed edges, and a deterministic compact text serialisation Z. Claims are compared as exact strings; paraphrase and contradiction judgements belong to the optional semantic module."),
    ("p", "**Verifier configurations.** Every guarded arm uses one decision policy: ACCEPT only if coverage, preservation and faithfulness all pass; any FAIL or UNCERTAIN yields HOLD; schema-invalid input is INVALID_INPUT before verification. Table 1 lists the arms. Text-fair exists because the original text baseline does not read evidence.contradicts; giving it the same annotation access as the graph makes it an information-parity control, so a graph-versus-text comparison does not confound representation with annotation access. An optional LLM verifier applies the same three checks to graph or text serialisations with one model, prompt policy, temperature and output schema; a separate semantic comparator adds a fourth check and fails closed to UNCERTAIN."),
    ("table", {
        "caption": "Table 1. Rule-based experimental arms.",
        "header": ["Arm", "Verifier", "Contradiction access"],
        "rows": [
            ["Unguarded", "Applies the candidate unchanged", "No"],
            ["Text", "Exact claim-set checks", "No"],
            ["Text-fair", "Claim sets + contradiction access", "Yes (control)"],
            ["Graph", "Structural verification over adjacency + contradiction access", "Yes"],
        ],
    }),
    ("figure", {
        "path": FIG,
        "width_in": 3.2,
        "caption": "Figure 1. MemBud architecture. The adapter builds the canonical transition; graph and text renderings feed their verifiers; the decision policy returns ACCEPT or HOLD; only ACCEPT reaches the versioned store; HOLD leaves it unchanged; every outcome is appended to the audit log.",
    }),
    ("p", "**Adapter and backends.** An adapter provides snapshot(scope) and an atomic replace_state(scope, records); it must not mutate the host before ACCEPT and must document the provenance, timestamps and relations its mapping loses. A reference backend (in-memory and SQLite) demonstrates the full intercept, verify and commit path, with byte-identical state after HOLD. Mem0 2.2.1 and A-Mem were source-inspected for a safe pre-commit boundary; neither exposes one, so no third-party compatibility is claimed and a staged or forked integration remains future work."),
    ("p", "**Evaluation protocol.** The unit is the case, paired across arms. For expected-faulty cases F and expected-valid cases V we report detection (faulty held / |F|), false-accept rate (1 - detection), valid acceptance, false-hold rate, and F1 for holding faulty transitions; error-type rates use claim- and record-slot denominators. INVALID_INPUT cases are scored in a separate block and excluded from precision and recall. LLM arms are reported as-completed and as fail-closed intention-to-treat, where an execution error is a non-accepting HOLD that earns no detection credit. Paired differences are reported with case identifiers, and two contrasts are pre-specified: text versus text-fair (the value of contradiction access) and text-fair versus graph (representation with information held equal). At n = 36 single-reviewer synthetic cases the analysis is descriptive: no significance or power claim is made, and a zero difference is read as not detectable at this size, not as equivalence. A larger fresh set with independent annotation, exact intervals and a paired test is planned; cost, tokens and latency are instrumented, and downstream question-answering utility is not measured."),
    ("p", "**Threats.** Cases were authored alongside the verifiers, labels are single-reviewer, n is small, claims are matched as exact strings, and LLM arms differ in token and latency budget. The independent fresh set and the second review address these limitations and are not yet collected."),
]

# ---- OOXML helpers ---------------------------------------------------------
NS = (
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"'
)


def rpr(bold=False, italic=False, size=None) -> str:
    x = ""
    if bold:
        x += "<w:b/>"
    if italic:
        x += "<w:i/>"
    if size:
        x += f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>'
    return f"<w:rPr>{x}</w:rPr>" if x else ""


def runs(text: str, size=None, italic=False) -> str:
    """Split **bold** markers into runs."""
    parts = text.split("**")
    out = []
    for i, part in enumerate(parts):
        if not part:
            continue
        out.append(f'<w:r>{rpr(bold=i % 2 == 1, italic=italic, size=size)}<w:t xml:space="preserve">{escape(part)}</w:t></w:r>')
    return "".join(out)


def para(text: str, style: str | None = None, align: str | None = None, size=None, italic=False) -> str:
    ppr = ""
    if style or align:
        ppr = "<w:pPr>"
        if style:
            ppr += f'<w:pStyle w:val="{style}"/>'
        if align:
            ppr += f'<w:jc w:val="{align}"/>'
        ppr += "</w:pPr>"
    return f"<w:p>{ppr}{runs(text, size=size, italic=italic)}</w:p>"


def figure_block(path: Path, width_in: float, caption: str, rid: str = "rIdImg") -> str:
    # Known aspect ratio of fig-3-5: 2200x1400
    w_emu = int(width_in * 914400)
    h_emu = int(w_emu * 1400 / 2200)
    drawing = (
        "<w:r><w:drawing>"
        f'<wp:inline distT="0" distB="0" distL="0" distR="0">'
        f'<wp:extent cx="{w_emu}" cy="{h_emu}"/>'
        '<wp:docPr id="1" name="Figure 1"/>'
        '<wp:cNvGraphicFramePr><a:graphicFrameLocks noChangeAspect="1"/></wp:cNvGraphicFramePr>'
        '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        "<pic:pic>"
        '<pic:nvPicPr><pic:cNvPr id="1" name="fig-3-5-membud-architecture.png"/><pic:cNvPicPr/></pic:nvPicPr>'
        f'<pic:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
        f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{w_emu}" cy="{h_emu}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>'
        "</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r>"
    )
    return f'<w:p><w:pPr><w:jc w:val="center"/><w:spacing w:before="60" w:after="20"/></w:pPr>{drawing}</w:p>' + para(caption, "Caption")


def table_block(spec: dict) -> str:
    widths = [1000, 2750, 1146]
    border = '<w:tblBorders><w:top w:val="single" w:sz="4"/><w:left w:val="single" w:sz="4"/><w:bottom w:val="single" w:sz="4"/><w:right w:val="single" w:sz="4"/><w:insideH w:val="single" w:sz="4"/><w:insideV w:val="single" w:sz="4"/></w:tblBorders>'
    xml = ['<w:tbl><w:tblPr><w:tblW w:w="4896" w:type="dxa"/><w:jc w:val="center"/>' + border + '</w:tblPr><w:tblGrid>']
    for w in widths:
        xml.append(f'<w:gridCol w:w="{w}"/>')
    xml.append("</w:tblGrid>")

    def row(cells, header=False):
        tr = ["<w:tr>"]
        for i, cell in enumerate(cells):
            shd = '<w:shd w:val="clear" w:color="auto" w:fill="E9EEF4"/>' if header else ""
            tr.append(
                f'<w:tc><w:tcPr><w:tcW w:w="{widths[i]}" w:type="dxa"/>{shd}</w:tcPr>'
                + para(cell, "TableText", size=17, italic=False)
                + "</w:tc>"
            )
        tr.append("</w:tr>")
        return "".join(tr)

    xml.append(row(spec["header"], header=True))
    for r in spec["rows"]:
        xml.append(row(r))
    xml.append("</w:tbl>")
    return para(spec["caption"], "Caption") + "".join(xml)


# ---- assemble document -----------------------------------------------------
def build_document() -> str:
    body = []
    # Title in a single-column first section.
    body.append(para("3. Implementation Method", "Title", align="center"))
    body.append(
        '<w:p><w:pPr><w:sectPr><w:type w:val="continuous"/>'
        '<w:pgSz w:w="12240" w:h="15840"/>'
        '<w:pgMar w:top="1080" w:right="1080" w:bottom="1080" w:left="1080" w:header="540" w:footer="540" w:gutter="0"/>'
        '<w:cols w:num="1" w:space="288"/></w:sectPr></w:pPr></w:p>'
    )
    for kind, payload in BLOCKS:
        if kind == "title":
            continue  # already emitted
        if kind == "h1":
            body.append(para(payload, "Heading1"))
        elif kind == "h2":
            body.append(para(payload, "Heading2"))
        elif kind == "p":
            body.append(para(payload))
        elif kind == "table":
            body.append(table_block(payload))
        elif kind == "figure":
            body.append(figure_block(payload["path"], payload["width_in"], payload["caption"]))
    body.append(
        '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/>'
        '<w:pgMar w:top="1080" w:right="1080" w:bottom="1080" w:left="1080" w:header="540" w:footer="540" w:gutter="0"/>'
        '<w:cols w:num="2" w:space="288"/></w:sectPr>'
    )
    return f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {NS}><w:body>{"".join(body)}</w:body></w:document>'


STYLES = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:docDefaults>
<w:rPrDefault><w:rPr><w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman"/><w:sz w:val="20"/><w:szCs w:val="20"/></w:rPr></w:rPrDefault>
<w:pPrDefault><w:pPr><w:spacing w:after="60" w:line="240" w:lineRule="auto"/><w:jc w:val="both"/></w:pPr></w:pPrDefault>
</w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:before="0" w:after="120"/><w:jc w:val="center"/></w:pPr><w:rPr><w:b/><w:sz w:val="28"/><w:szCs w:val="28"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:before="140" w:after="40"/><w:jc w:val="left"/></w:pPr><w:rPr><w:b/><w:sz w:val="22"/><w:szCs w:val="22"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:before="100" w:after="30"/><w:jc w:val="left"/></w:pPr><w:rPr><w:b/><w:i/><w:sz w:val="20"/><w:szCs w:val="20"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Caption"><w:name w:val="caption"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:before="20" w:after="100"/><w:jc w:val="center"/></w:pPr><w:rPr><w:i/><w:sz w:val="16"/><w:szCs w:val="16"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="TableText"><w:name w:val="Table Text"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:after="0" w:line="220" w:lineRule="auto"/><w:jc w:val="left"/></w:pPr><w:rPr><w:sz w:val="16"/><w:szCs w:val="16"/></w:rPr></w:style>
</w:styles>'''

CONTENT_TYPES = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Default Extension="png" ContentType="image/png"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
</Types>'''

ROOT_RELS = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
</Relationships>'''

DOC_RELS = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rIdImg" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/image1.png"/>
<Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>'''

CORE = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<dc:title>MemBud Chapter 3 (compressed, two-column)</dc:title>
<dc:creator>MemBud paper team</dc:creator>
<cp:lastModifiedBy>MemBud paper team</cp:lastModifiedBy>
</cp:coreProperties>'''


def write_markdown() -> int:
    lines = []
    for kind, payload in BLOCKS:
        if kind == "title":
            lines.append(f"# {payload}")
        elif kind == "h1":
            lines.append(f"## {payload}")
        elif kind == "p":
            lines.append(payload)
        elif kind == "table":
            lines.append(payload["caption"])
            lines.append("")
            lines.append("| " + " | ".join(payload["header"]) + " |")
            lines.append("|" + "|".join(["---"] * len(payload["header"])) + "|")
            for r in payload["rows"]:
                lines.append("| " + " | ".join(r) + " |")
        elif kind == "figure":
            lines.append(payload["caption"])
            lines.append("")
            lines.append("![](" + str(payload["path"].relative_to(ROOT)) + ")")
        lines.append("")
    md = "\n".join(lines)
    (OUT / "chapter-3-compressed.md").write_text(md, encoding="utf-8")
    return len(re.findall(r"\S+", md))


import re  # noqa: E402  (used by write_markdown)


def main() -> None:
    docx = OUT / "chapter-3-compressed-double-column.docx"
    with zipfile.ZipFile(docx, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("_rels/.rels", ROOT_RELS)
        z.writestr("docProps/core.xml", CORE)
        z.writestr("word/document.xml", build_document())
        z.writestr("word/styles.xml", STYLES)
        z.writestr("word/_rels/document.xml.rels", DOC_RELS)
        z.write(FIG, "word/media/image1.png")
    words = write_markdown()
    # rough page estimate for 10-pt Times, two 3.4-in columns, ~9.4-in text height
    print(f"wrote {docx}")
    print(f"content words (incl. captions/table): {words}")
    print("rough estimate: ~1.2-1.4 double-column pages (no local renderer to verify pages)")


if __name__ == "__main__":
    main()
