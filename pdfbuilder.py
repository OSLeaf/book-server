from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A5
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import registerFontFamily
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
)
from reportlab.platypus import Image as RLImage

PAGE_SIZE = A5  # e.g. reportlab.lib.pagesizes.A4 for larger pages
MARGIN = 18 * mm
BODY_SIZE = 11
FONT_DIR_CANDIDATES = [  # first directory holding all four files is used
    "/usr/share/fonts/TTF",
    "/usr/share/fonts/truetype/dejavu",
    "/usr/share/fonts/dejavu",
    "C:/Windows/Fonts",
]


def register_fonts():
    """Use DejaVu Serif (wide Unicode coverage) if all four styles exist, else Times."""
    names = {
        "BookFont": "DejaVuSerif.ttf",
        "BookFont-Bold": "DejaVuSerif-Bold.ttf",
        "BookFont-Italic": "DejaVuSerif-Italic.ttf",
        "BookFont-BoldItalic": "DejaVuSerif-BoldItalic.ttf",
    }
    for d in FONT_DIR_CANDIDATES:
        if all((Path(d) / f).exists() for f in names.values()):
            for name, f in names.items():
                pdfmetrics.registerFont(TTFont(name, str(Path(d) / f)))
            registerFontFamily(
                "BookFont",
                normal="BookFont",
                bold="BookFont-Bold",
                italic="BookFont-Italic",
                boldItalic="BookFont-BoldItalic",
            )
            return "BookFont", "BookFont-Bold"
    registerFontFamily(
        "Times-Roman",
        normal="Times-Roman",
        bold="Times-Bold",
        italic="Times-Italic",
        boldItalic="Times-BoldItalic",
    )
    return "Times-Roman", "Times-Bold"


class ChapterHeading(Paragraph):
    """Paragraph that also becomes a PDF bookmark."""

    def __init__(self, text, style):
        super().__init__(escape(text), style)
        self.plain = text


class BookDoc(BaseDocTemplate):
    def afterFlowable(self, flowable):
        if isinstance(flowable, ChapterHeading):
            self._n = getattr(self, "_n", 0) + 1
            key = f"chapter{self._n}"
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(flowable.plain[:80], key, 0)


def build_pdf(blocks, title, author, cover_path, out_path):
    """blocks: [(kind, value), ...] with kind in title|para|heading|image|rule.
    title values are plain text; para/heading values are ready-made markup
    (already escaped, with <b>/<i>/<u>/<br/>). cover_path may be None."""
    body_font, bold_font = register_fonts()
    w, h = PAGE_SIZE

    para = ParagraphStyle(
        "para",
        fontName=body_font,
        fontSize=BODY_SIZE,
        leading=BODY_SIZE * 1.4,
        spaceAfter=7,
        alignment=TA_LEFT,
    )
    chapter = ParagraphStyle(
        "chapter",
        fontName=bold_font,
        fontSize=17,
        leading=22,
        alignment=TA_CENTER,
        spaceBefore=30,
        spaceAfter=22,
    )
    heading = ParagraphStyle(
        "heading",
        fontName=bold_font,
        fontSize=BODY_SIZE + 2,
        leading=(BODY_SIZE + 2) * 1.4,
        spaceBefore=8,
        spaceAfter=8,
    )
    rule = ParagraphStyle(
        "rule",
        fontName=body_font,
        fontSize=BODY_SIZE,
        alignment=TA_CENTER,
        spaceBefore=6,
        spaceAfter=12,
    )
    title_style = ParagraphStyle(
        "title",
        fontName=bold_font,
        fontSize=26,
        leading=32,
        alignment=TA_CENTER,
        spaceAfter=18,
    )
    author_style = ParagraphStyle(
        "author", fontName=body_font, fontSize=14, leading=18, alignment=TA_CENTER
    )

    def draw_cover(canvas, doc):
        # Fit the whole image on the page, centered, aspect ratio kept.
        canvas.drawImage(
            str(cover_path),
            0,
            0,
            width=w,
            height=h,
            preserveAspectRatio=True,
            anchor="c",
        )

    def draw_number(canvas, doc):
        canvas.setFont(body_font, 9)
        canvas.drawCentredString(w / 2, 10 * mm, str(doc.page))

    inner = Frame(MARGIN, MARGIN, w - 2 * MARGIN, h - 2 * MARGIN, id="inner")
    full = Frame(
        0,
        0,
        w,
        h,
        leftPadding=0,
        rightPadding=0,
        topPadding=0,
        bottomPadding=0,
        id="full",
    )

    templates = []
    if cover_path:
        templates.append(PageTemplate("cover", [full], onPage=draw_cover))
    templates.append(PageTemplate("title", [inner]))
    templates.append(PageTemplate("main", [inner], onPage=draw_number))

    doc = BookDoc(
        str(out_path),
        pagesize=PAGE_SIZE,
        title=title,
        author=author,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN,
        bottomMargin=MARGIN,
    )
    doc.addPageTemplates(templates)

    story = []
    if cover_path:
        story += [NextPageTemplate("title"), PageBreak()]  # page 1 = cover
    story += [Spacer(1, h * 0.28), Paragraph(escape(title), title_style)]
    if author:
        story.append(Paragraph(escape(author), author_style))
    story += [NextPageTemplate("main"), PageBreak()]

    max_w = w - 2 * MARGIN - 12
    max_h = h - 2 * MARGIN - 24

    first = True
    for kind, value in blocks:
        if kind == "title":
            if not first:
                story.append(PageBreak())
            story.append(ChapterHeading(value, chapter))
        elif kind == "heading":
            story.append(Paragraph(value, heading))
        elif kind == "rule":
            story.append(Paragraph("*&nbsp;&nbsp;*&nbsp;&nbsp;*", rule))
        elif kind == "image":
            try:
                iw, ih = ImageReader(value).getSize()
                scale = min(1.0, max_w / iw, max_h / ih)  # shrink to fit, never enlarge
                img = RLImage(value, width=iw * scale, height=ih * scale, mask="auto")
                img.hAlign = "CENTER"
                story += [Spacer(1, 4), img, Spacer(1, 10)]
            except Exception:
                continue  # unreadable image: skip it
        else:
            story.append(Paragraph(value, para))
        first = False

    doc.build(story)
