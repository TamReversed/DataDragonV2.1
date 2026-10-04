"""The Data Readiness Report: a branded PDF with charts, built with ReportLab.

One entry point: `generate_readiness_report(output_path, state, transformation_log=None)`.
It reads only what the pipeline already holds in `state` (the analysis, the decisions and the key results) and
computes nothing new about the data.

Assets are in `report_assets/` (fonts and images) and the mark is read from `templates/brand/mark_paths.svg`.
A missing asset never fails a report: the fonts fall back to Helvetica/Courier and a missing image is left out.
"""
import os
import re
from datetime import datetime
from xml.sax.saxutils import escape as pdf_text   # Paragraphs parse <...> as markup: escape file-derived text

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, CondPageBreak, Flowable, Frame, Image, KeepTogether, NextPageTemplate,
                                PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle)

ROOT = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(ROOT, 'report_assets')
MARK_SVG = os.path.join(ROOT, 'templates', 'brand', 'mark_paths.svg')

# "Ember & Ink", light theme (static/css/tokens.css). A report is printed, so it is always light.
PAPER = colors.HexColor('#F6F1E7')
COVER_PAPER = colors.HexColor('#F5EFE0')          # the background colour of the cover artwork
SURFACE = colors.HexColor('#FFFDF8')
SUNKEN = colors.HexColor('#EDE6D6')
INK = colors.HexColor('#14211F')
INK_2 = colors.HexColor('#44524E')
INK_3 = colors.HexColor('#5E6A66')
RULE = colors.HexColor('#D9D1BF')
TEAL = colors.HexColor('#17605C')
EMBER = colors.HexColor('#E4572E')
OK = colors.HexColor('#1F6B3A')
WARN = colors.HexColor('#8A5A00')
BAD = colors.HexColor('#B3261E')
VIZ = [colors.HexColor(c) for c in ('#17605C', '#C4411B', '#0072B2', '#A8507F', '#8A6A00', '#2E7D4F', '#2B7FB8', '#6B6B6B')]

PAGE_W, PAGE_H = letter
MARGIN = 0.8 * inch
CONTENT_W = PAGE_W - 2 * MARGIN

FONTS = {   # role: (registered name, file, fallback)
    'display': ('DD-Display', 'Fraunces-400.ttf', 'Times-Roman'),
    'display-italic': ('DD-Display-Italic', 'Fraunces-400-Italic.ttf', 'Times-Italic'),
    'display-bold': ('DD-Display-Bold', 'Fraunces-600.ttf', 'Times-Bold'),
    'ui': ('DD-UI', 'InstrumentSans-400.ttf', 'Helvetica'),
    'ui-bold': ('DD-UI-Bold', 'InstrumentSans-600.ttf', 'Helvetica-Bold'),
    'mono': ('DD-Mono', 'JetBrainsMono-400.ttf', 'Courier'),
    'mono-bold': ('DD-Mono-Bold', 'JetBrainsMono-500.ttf', 'Courier-Bold'),
}
_font_names = {}


def font(role):
    """The font to use for a role; registers the brand fonts on first use and falls back to a built-in one."""
    if role not in _font_names:
        name, filename, fallback = FONTS[role]
        try:
            pdfmetrics.registerFont(TTFont(name, os.path.join(ASSETS, 'fonts', filename)))
            _font_names[role] = name
        except Exception:
            _font_names[role] = fallback
    return _font_names[role]


def asset_image(name):
    path = os.path.join(ASSETS, 'images', name)
    return path if os.path.exists(path) else None


def fit_text(text, font_name, size, width):
    """Shorten `text` with an ellipsis so it fits `width` points."""
    text = str(text)
    if pdfmetrics.stringWidth(text, font_name, size) <= width:
        return text
    while len(text) > 1 and pdfmetrics.stringWidth(text + '…', font_name, size) > width:
        text = text[:-1]
    return text + '…'


# ---------------------------------------------------------------------------------------------------------------
# The mark, drawn as vectors from the same paths the pages use
# ---------------------------------------------------------------------------------------------------------------
MARK_BOX = (420, 460, 1200, 1120)                 # the viewBox of the mark: x, y, width, height
_mark_layers = None


def mark_layers():
    """[(layer, [(command, numbers...)])] for the ink, eye and ember layers of the mark; [] if the file is missing."""
    global _mark_layers
    if _mark_layers is None:
        _mark_layers = []
        try:
            with open(MARK_SVG, encoding='utf-8') as f:
                svg = f.read()
            for layer, d in zip(('ink', 'eye', 'ember'), re.findall(r' d="([^"]*)"', svg)):
                tokens = re.findall(r'[MLCZz]|-?\d+(?:\.\d+)?', d)
                commands, i = [], 0
                while i < len(tokens):
                    op = tokens[i]
                    count = {'M': 2, 'L': 2, 'C': 6}.get(op, 0)
                    commands.append((op.upper(),) + tuple(float(t) for t in tokens[i + 1:i + 1 + count]))
                    i += 1 + count
                _mark_layers.append((layer, commands))
        except (OSError, ValueError):
            _mark_layers = []
    return _mark_layers


def draw_mark(canvas, x, y, width, background=SURFACE):
    """Draw the dragon mark with its lower-left corner at (x, y). Returns its height."""
    box_x, box_y, box_w, box_h = MARK_BOX
    scale = width / box_w
    fills = {'ink': INK, 'eye': background, 'ember': EMBER}
    for layer, commands in mark_layers():
        path = canvas.beginPath()
        for command in commands:
            points = [(x + (command[i] - box_x) * scale, y + (box_y + box_h - command[i + 1]) * scale)
                      for i in range(1, len(command), 2)]
            if command[0] == 'M':
                path.moveTo(*points[0])
            elif command[0] == 'L':
                path.lineTo(*points[0])
            elif command[0] == 'C':
                path.curveTo(*points[0], *points[1], *points[2])
            else:
                path.close()
        canvas.setFillColor(fills[layer])
        canvas.drawPath(path, stroke=0, fill=1)
    return box_h * scale


# ---------------------------------------------------------------------------------------------------------------
# Flowables
# ---------------------------------------------------------------------------------------------------------------
class BarChart(Flowable):
    """Horizontal bars: label, bar, value. rows = [(label, value, colour)]; values run from 0 to `maximum`."""

    def __init__(self, rows, maximum, value_format, width=CONTENT_W, label_width=150, row_height=17):
        super().__init__()
        self.rows, self.maximum, self.value_format = rows, maximum or 1, value_format
        self.width, self.label_width, self.row_height = width, label_width, row_height
        self.height = len(rows) * row_height + 6

    def wrap(self, available_width, available_height):
        return self.width, self.height

    def draw(self):
        c = self.canv
        value_width = 58
        track = self.width - self.label_width - value_width - 12
        for i, (label, value, color) in enumerate(self.rows):
            y = self.height - (i + 1) * self.row_height
            c.setFillColor(INK)
            c.setFont(font('mono'), 8)
            c.drawRightString(self.label_width, y + 5, fit_text(label, font('mono'), 8, self.label_width - 4))
            c.setFillColor(SUNKEN)
            c.rect(self.label_width + 8, y + 3, track, self.row_height - 7, stroke=0, fill=1)
            c.setFillColor(color)
            c.rect(self.label_width + 8, y + 3, max(track * min(value / self.maximum, 1), 0.8), self.row_height - 7, stroke=0, fill=1)
            c.setFillColor(INK_2)
            c.drawString(self.label_width + 8 + track + 6, y + 5, self.value_format(value))


class Legend(Flowable):
    """A row of colour swatches with labels, for a chart whose colours carry meaning."""

    def __init__(self, items, width=CONTENT_W):
        super().__init__()
        self.items, self.width, self.height = items, width, 14

    def wrap(self, available_width, available_height):
        return self.width, self.height

    def draw(self):
        c, x = self.canv, 0
        c.setFont(font('ui'), 8)
        for label, color in self.items:
            c.setFillColor(color)
            c.rect(x, 3, 8, 8, stroke=0, fill=1)
            c.setFillColor(INK_2)
            c.drawString(x + 12, 4, label)
            x += 12 + pdfmetrics.stringWidth(label, font('ui'), 8) + 16


class Stats(Flowable):
    """The headline numbers: a row of boxes, each a big serif number over a small label."""

    def __init__(self, items, width=CONTENT_W):
        super().__init__()
        self.items, self.width, self.height = items, width, 62

    def wrap(self, available_width, available_height):
        return self.width, self.height

    def draw(self):
        c = self.canv
        gap = 8
        box = (self.width - gap * (len(self.items) - 1)) / len(self.items)
        for i, (value, label, color) in enumerate(self.items):
            x = i * (box + gap)
            c.setFillColor(SUNKEN)
            c.rect(x, 0, box, self.height, stroke=0, fill=1)
            c.setFillColor(color or INK)
            size = 22
            while size > 10 and pdfmetrics.stringWidth(str(value), font('display'), size) > box - 16:
                size -= 1
            c.setFont(font('display'), size)
            c.drawString(x + 10, 26, str(value))
            c.setFillColor(INK_2)
            c.setFont(font('ui'), 8)
            c.drawString(x + 10, 11, label)


class SectionHeading(Flowable):
    """A numbered section title on a hairline, with an optional small illustration at the right."""

    def __init__(self, number, title, italic, image=None, width=CONTENT_W):
        super().__init__()
        self.number, self.title, self.italic, self.image, self.width = number, title, italic, image, width
        self.height = 64 if image else 40

    def wrap(self, available_width, available_height):
        return self.width, self.height

    def draw(self):
        c = self.canv
        c.setFillColor(INK_3)
        c.setFont(font('mono'), 8)
        c.drawString(0, 28, f'{self.number:02d}')
        c.setFillColor(INK)
        c.setFont(font('display'), 20)
        c.drawString(0, 8, self.title + ' ')
        x = pdfmetrics.stringWidth(self.title + ' ', font('display'), 20)
        c.setFont(font('display-italic'), 20)
        c.drawString(x, 8, self.italic)
        c.setStrokeColor(INK)
        c.setLineWidth(0.6)
        c.line(0, 0, self.width, 0)
        if self.image:
            c.drawImage(self.image, self.width - 58, 4, 58, 58, mask='auto')


# ---------------------------------------------------------------------------------------------------------------
# Styles and small builders
# ---------------------------------------------------------------------------------------------------------------
def styles():
    body = ParagraphStyle('body', fontName=font('ui'), fontSize=9.5, leading=14, textColor=INK, spaceAfter=5)
    return {
        'body': body,
        'muted': ParagraphStyle('muted', parent=body, textColor=INK_2),
        'small': ParagraphStyle('small', parent=body, fontSize=8, leading=11, textColor=INK_3),
        'lead': ParagraphStyle('lead', parent=body, fontSize=11, leading=16, textColor=INK_2, spaceAfter=10),
        'sub': ParagraphStyle('sub', parent=body, fontName=font('ui-bold'), fontSize=10, spaceBefore=8, spaceAfter=4),
        'finding': ParagraphStyle('finding', parent=body, leftIndent=14, bulletIndent=2, spaceAfter=4),
        'callout': ParagraphStyle('callout', parent=body, fontName=font('mono'), fontSize=10.5, leading=15, textColor=INK),
        'cell': ParagraphStyle('cell', fontName=font('mono'), fontSize=8, leading=10.5, textColor=INK),
    }


def bold(text):
    return f'<font name="{font("ui-bold")}">{text}</font>'


def data_table(rows, col_widths, numeric_columns=()):
    """A table in the data style: sunken header, hairlines, mono text, numbers right-aligned."""
    table = Table(rows, colWidths=col_widths, repeatRows=1)
    style = [
        ('FONTNAME', (0, 0), (-1, -1), font('mono')),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('TEXTCOLOR', (0, 0), (-1, 0), INK_2),
        ('TEXTCOLOR', (0, 1), (-1, -1), INK),
        ('BACKGROUND', (0, 0), (-1, 0), SUNKEN),
        ('LINEBELOW', (0, 0), (-1, 0), 0.6, INK_3),
        ('LINEBELOW', (0, 1), (-1, -1), 0.4, RULE),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
    ]
    for column in numeric_columns:
        style.append(('ALIGN', (column, 0), (column, -1), 'RIGHT'))
    table.setStyle(TableStyle(style))
    return table


def callout(text, style):
    """A fact worth a box: teal edge, sunken background."""
    table = Table([[Paragraph(text, style)]], colWidths=[CONTENT_W])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), SUNKEN),
        ('LINEBEFORE', (0, 0), (0, -1), 3, TEAL),
        ('LEFTPADDING', (0, 0), (-1, -1), 12),
        ('TOPPADDING', (0, 0), (-1, -1), 9),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 9),
    ]))
    return table


def key_text(key):
    return ' + '.join(map(str, key)) if isinstance(key, (list, tuple)) else str(key)


def plural(count, word):
    return f'{count:,} {word}' + ('' if count == 1 else 's')


# ---------------------------------------------------------------------------------------------------------------
# What the report says: facts taken from the pipeline state
# ---------------------------------------------------------------------------------------------------------------
def report_facts(state, transformation_log):
    analysis = state.stage_data.get(1) or {}
    overview = analysis.get('overview', {})
    shape = overview.get('shape', {})
    columns = analysis.get('columns', {}) or {}
    rows, column_count = shape.get('rows', 0) or 0, shape.get('columns', 0) or 0
    cells = rows * column_count
    missing_cells = sum((info.get('null_count') or 0) for info in columns.values())
    key_data = state.stage_data.get(3) or {}
    candidates = key_data.get('minimal_combinations') or key_data.get('minimal_combinations_after_dedup') or []
    selected_key = key_data.get('user_selected_key', candidates[0] if candidates else [])
    return {
        'analysis': analysis, 'overview': overview, 'columns': columns, 'rows': rows, 'column_count': column_count,
        'completeness': (100 - missing_cells / cells * 100) if cells else None,
        'duplicates': overview.get('duplicate_rows', 0) or 0,
        'gap_summary': analysis.get('gap_summary', []) or [],
        'gap_triage': state.user_decisions.get(2, {}) or {},
        'key_data': key_data, 'candidates': candidates, 'selected_key': selected_key,
        'transform_decisions': state.user_decisions.get(4, {}) or {},
        'log': transformation_log or [],
        'types': overview.get('detected_types_count', {}) or {},
    }


def findings(facts):
    """Three to five plain sentences: what a reader should take away. Every one restates a number in the report."""
    out = []
    rows, columns = facts['rows'], facts['column_count']
    out.append(f"The file has {bold(plural(rows, 'row'))} and {bold(plural(columns, 'column'))}.")
    gaps = facts['gap_summary']
    if gaps:
        worst = max(gaps, key=lambda g: g.get('null_percentage', 0))
        out.append(f"{bold(plural(len(gaps), 'column'))} of {columns} {'has' if len(gaps) == 1 else 'have'} missing values. "
                   f"The largest gap is {bold(pdf_text(str(worst.get('column'))))}, "
                   f"with {worst.get('null_percentage', 0):.1f}% of its values missing.")
    else:
        out.append('No column has missing values.')
    if facts['duplicates']:
        out.append(f"{bold(plural(facts['duplicates'], 'row'))} {'is an exact duplicate' if facts['duplicates'] == 1 else 'are exact duplicates'} of another row.")
    else:
        out.append('No row is an exact duplicate of another.')
    if facts['selected_key']:
        after = ' once the duplicate rows are removed' if facts['key_data'].get('duplicate_rows') else ''
        out.append(f"{bold(pdf_text(key_text(facts['selected_key'])))} identifies every row{after}.")
    elif facts['key_data']:
        out.append('No column or combination of columns identifies every row.')
    applied = [entry for entry in facts['log'] if entry.get('status', 'applied') == 'applied']
    if applied:
        touched = sum(len(entry.get('columns', [])) for entry in applied)
        out.append(f"{bold(plural(len(applied), 'transformation'))} changed the data, across {plural(touched, 'column')}.")
    return out


# ---------------------------------------------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------------------------------------------
def draw_cover(canvas, doc):
    facts, filename, generated = doc.dd_facts, doc.dd_filename, doc.dd_generated
    canvas.saveState()
    canvas.setFillColor(COVER_PAPER)
    canvas.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)

    # brand line
    top = PAGE_H - MARGIN
    mark_height = draw_mark(canvas, MARGIN, top - 30, 36, background=COVER_PAPER)
    canvas.setFillColor(INK)
    canvas.setFont(font('display-bold'), 17)
    canvas.drawString(MARGIN + 46, top - 30 + mark_height / 2 - 6, 'Data')
    x = MARGIN + 46 + pdfmetrics.stringWidth('Data', font('display-bold'), 17)
    canvas.setFont(font('display-italic'), 17)
    canvas.drawString(x, top - 30 + mark_height / 2 - 6, 'Dragon')

    # title
    y = top - 150
    canvas.setFillColor(INK_3)
    canvas.setFont(font('mono'), 9)
    canvas.drawString(MARGIN, y + 62, 'data readiness pipeline')
    canvas.setFillColor(INK)
    canvas.setFont(font('display'), 46)
    canvas.drawString(MARGIN, y, 'Data Readiness')
    canvas.setFont(font('display-italic'), 46)
    canvas.drawString(MARGIN, y - 50, 'Report')

    # what it is about
    y -= 104
    canvas.setStrokeColor(INK)
    canvas.setLineWidth(0.6)
    canvas.line(MARGIN, y + 22, PAGE_W - MARGIN, y + 22)
    canvas.setFont(font('mono-bold'), 11)
    canvas.drawString(MARGIN, y, fit_text(filename, font('mono-bold'), 11, CONTENT_W))
    canvas.setFillColor(INK_2)
    canvas.setFont(font('mono'), 9)
    line = f"{plural(facts['rows'], 'row')}  ·  {plural(facts['column_count'], 'column')}"
    if facts['completeness'] is not None:
        line += f"  ·  {facts['completeness']:.1f}% complete"
    canvas.drawString(MARGIN, y - 16, line)
    canvas.drawString(MARGIN, y - 30, 'generated ' + generated)

    # artwork
    art = asset_image('cover.jpg')
    if art:
        width = PAGE_W - 2 * MARGIN + 40
        height = width * 688 / 1024
        canvas.drawImage(art, (PAGE_W - width) / 2, MARGIN + 6, width, height)
    canvas.setFillColor(INK_3)
    canvas.setFont(font('mono'), 7.5)
    canvas.drawString(MARGIN, MARGIN - 18, 'The numbers in this report describe the file as it was uploaded. Nothing in it was sent to another service.')
    canvas.restoreState()


def draw_page_frame(canvas, doc):
    canvas.saveState()
    top = PAGE_H - 0.5 * inch
    draw_mark(canvas, MARGIN, top - 13, 17, background=colors.white)
    canvas.setFillColor(INK_2)
    canvas.setFont(font('ui'), 8)
    canvas.drawString(MARGIN + 23, top - 8, 'DataDragon  ·  Data Readiness Report')
    canvas.setFont(font('mono'), 7.5)
    canvas.drawRightString(PAGE_W - MARGIN, top - 8, fit_text(doc.dd_filename, font('mono'), 7.5, 250))
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.5)
    canvas.line(MARGIN, top - 19, PAGE_W - MARGIN, top - 19)
    canvas.line(MARGIN, 0.62 * inch, PAGE_W - MARGIN, 0.62 * inch)
    canvas.setFillColor(INK_3)
    canvas.drawString(MARGIN, 0.45 * inch, 'generated ' + doc.dd_generated)
    canvas.drawRightString(PAGE_W - MARGIN, 0.45 * inch, f'page {canvas.getPageNumber()}')
    canvas.restoreState()


def summary_section(facts, st):
    completeness = facts['completeness']
    stats = [
        (f"{facts['rows']:,}", 'row' if facts['rows'] == 1 else 'rows', None),
        (f"{facts['column_count']:,}", 'column' if facts['column_count'] == 1 else 'columns', None),
        (f'{completeness:.1f}%' if completeness is not None else '–', 'cells filled in',
         None if completeness is None else OK if completeness >= 95 else WARN if completeness >= 70 else BAD),
        (f"{facts['duplicates']:,}", 'duplicate row' if facts['duplicates'] == 1 else 'duplicate rows',
         WARN if facts['duplicates'] else None),
        ('yes' if facts['selected_key'] else 'no', 'natural key found', OK if facts['selected_key'] else WARN),
    ]
    story = [SectionHeading(1, 'Summary', 'at a glance'), Spacer(1, 12), Stats(stats), Spacer(1, 14)]
    banner = asset_image('banner.jpg')
    if banner:
        story += [Image(banner, width=CONTENT_W, height=CONTENT_W * 300 / 1024), Spacer(1, 12)]
    story.append(Paragraph('What stands out', st['sub']))
    for sentence in findings(facts):
        story.append(Paragraph(sentence, st['finding'], bulletText='■'))
    overview = facts['overview']
    story += [Spacer(1, 6), Paragraph(
        f"In memory the data takes about {overview.get('memory_usage_mb', 0):.2f} MB.", st['small'])]
    return story


def shape_section(facts, st):
    story = [SectionHeading(2, 'Shape of', 'the data'), Spacer(1, 10)]
    types = sorted(facts['types'].items(), key=lambda item: -item[1])
    if types:
        story.append(Paragraph('Columns by detected type', st['sub']))
        story.append(BarChart([(name, count, VIZ[i % len(VIZ)]) for i, (name, count) in enumerate(types)],
                              max(count for _, count in types), lambda v: plural(int(v), 'column'), label_width=110))
        story.append(Spacer(1, 10))
    columns = facts['columns']
    if columns:
        story.append(Paragraph('Every column', st['sub']))
        rows = [['column', 'type', 'filled in', 'unique values', 'note']]
        for name, info in columns.items():
            notes = []
            outliers = (info.get('outliers') or {}).get('count') or 0
            if outliers:
                notes.append(plural(outliers, 'outlier'))
            if facts['rows'] and (info.get('unique_count') or 0) == facts['rows'] and not info.get('null_count'):
                notes.append('all values differ')
            rows.append([Paragraph(pdf_text(str(name)), st['cell']), str(info.get('detected_type', info.get('dtype', ''))),
                         f"{100 - (info.get('null_percentage') or 0):.1f}%", f"{info.get('unique_count') or 0:,}",
                         ', '.join(notes)])
        story.append(data_table(rows, [2.3 * inch, 1.05 * inch, 0.8 * inch, 1.05 * inch, CONTENT_W - 5.2 * inch],
                                numeric_columns=(2, 3)))
    else:
        story.append(Paragraph('The shape analysis was not run.', st['muted']))

    numeric = [(name, info) for name, info in columns.items() if info.get('is_numeric') and info.get('statistics')]
    if numeric:
        def number(value):
            if value is None:
                return ''
            return f'{value:,.0f}' if abs(value) >= 1000 or float(value).is_integer() else f'{value:,.2f}'
        rows = [['column', 'lowest', 'median', 'mean', 'highest', 'outliers']]
        for name, info in numeric:
            stats = info['statistics']
            rows.append([Paragraph(pdf_text(str(name)), st['cell']), number(stats.get('min')), number(stats.get('median')),
                         number(stats.get('mean')), number(stats.get('max')), f"{(info.get('outliers') or {}).get('count') or 0:,}"])
        story.append(KeepTogether([Spacer(1, 12), Paragraph('Numeric columns', st['sub']),
                                   data_table(rows, [2.0 * inch] + [(CONTENT_W - 2.0 * inch) / 5] * 5, numeric_columns=(1, 2, 3, 4, 5)),
                                   Spacer(1, 4),
                                   Paragraph('Outliers are values far outside the middle half of the column (the interquartile-range rule).', st['small'])]))
    return story


def gaps_section(facts, st):
    story = [SectionHeading(3, 'Missing', 'data', asset_image('spot-gaps.jpg')), Spacer(1, 10)]
    gaps, triage = facts['gap_summary'], facts['gap_triage']
    if not gaps:
        story.append(Paragraph('No column has missing values.', st['body']))
        return story
    ordered = sorted(gaps, key=lambda g: -g.get('null_percentage', 0))
    story.append(Paragraph(f"{plural(len(gaps), 'column')} {'has' if len(gaps) == 1 else 'have'} missing values. "
                           'The bars show the share of rows where the value is missing.', st['body']))
    shown = ordered[:20]
    story.append(BarChart([(g.get('column'), g.get('null_percentage', 0),
                            BAD if g.get('null_percentage', 0) >= 30 else WARN if g.get('null_percentage', 0) >= 5 else TEAL)
                           for g in shown], 100, lambda v: f'{v:.1f}%'))
    story.append(Legend([('under 5% missing', TEAL), ('5% to 30%', WARN), ('30% or more', BAD)]))
    if len(ordered) > len(shown):
        story.append(Paragraph(f'Showing the {len(shown)} largest of {len(ordered)}.', st['small']))
    story.append(Spacer(1, 10))
    story.append(Paragraph('Decisions', st['sub']))
    if triage:
        percentages = {g['column']: g.get('null_percentage', 0) for g in gaps}
        counts = {g['column']: g.get('null_count', 0) for g in gaps}
        rows = [['column', 'missing', 'rows', 'decision']]
        for column, decision in sorted(triage.items(), key=lambda item: -percentages.get(item[0], 0)):
            rows.append([Paragraph(pdf_text(str(column)), st['cell']), f'{percentages.get(column, 0):.1f}%',
                         f'{counts.get(column, 0):,}', 'needs attention' if decision == 'needs_attention' else 'acceptable'])
        story.append(data_table(rows, [2.6 * inch, 0.9 * inch, 0.9 * inch, CONTENT_W - 4.4 * inch], numeric_columns=(1, 2)))
        story.append(Spacer(1, 4))
        story.append(Paragraph('The gaps are recorded here; no value was filled in and no row was removed.', st['small']))
    else:
        story.append(Paragraph('No triage decisions were made for these columns.', st['muted']))
    return story


def keys_section(facts, st):
    story = [SectionHeading(4, 'Natural', 'keys', asset_image('spot-keys.jpg')), Spacer(1, 10)]
    key_data, selected, candidates = facts['key_data'], facts['selected_key'], facts['candidates']
    if not key_data:
        story.append(Paragraph('Natural key discovery was not performed.', st['muted']))
        return story
    if selected:
        story.append(Paragraph('Selected natural key', st['sub']))
        story.append(callout(pdf_text(key_text(selected)), st['callout']))
        story.append(Spacer(1, 6))
        duplicates = key_data.get('duplicate_rows', 0)
        if duplicates:
            story.append(Paragraph(f'This column combination is unique only after removing {duplicates:,} exact duplicate rows; '
                                   'on the full data it repeats.', st['body']))
        else:
            story.append(Paragraph('This column combination uniquely identifies each row in your dataset and can serve as '
                                   'a primary key.', st['body']))
    else:
        story.append(Paragraph('No natural key was selected.', st['body']))
    if key_data.get('excluded_null_columns'):
        story.append(Paragraph('Columns skipped because they contain blank values: '
                               + pdf_text(', '.join(map(str, key_data['excluded_null_columns']))), st['muted']))

    columns, rows = facts['columns'], facts['rows']
    if columns and rows:
        ranked = sorted(((name, (info.get('unique_count') or 0) / rows * 100) for name, info in columns.items()),
                        key=lambda item: -item[1])[:10]
        story.append(Spacer(1, 6))
        story.append(Paragraph('How close each column is to a key', st['sub']))
        story.append(BarChart([(name, share, TEAL if share >= 99 else VIZ[2] if share >= 50 else VIZ[1]) for name, share in ranked],
                              100, lambda v: f'{v:.1f}%'))
        story.append(Legend([('99% or more unique (key candidate)', TEAL), ('50% to 99%', VIZ[2]), ('under 50%', VIZ[1])]))
        if len(columns) > len(ranked):
            story.append(Paragraph(f'Showing the {len(ranked)} most unique of {len(columns)} columns.', st['small']))

    others = [key for key in candidates if key != selected]
    if others:
        block = [Spacer(1, 8), Paragraph('Other available keys', st['sub'])]
        for index, key in enumerate(others[:5], 1):
            block.append(Paragraph(f'{index}. {pdf_text(key_text(key))}', st['cell']))
        if len(others) > 5:
            block.append(Paragraph(f'... and {len(others) - 5} more candidate(s)', st['small']))
        story.append(KeepTogether(block))
    elif candidates:
        story.append(Paragraph('This was the only natural key candidate found.', st['muted']))
    return story


def transformations_section(facts, st):
    story = [SectionHeading(5, 'Transformations', 'chosen and applied'), Spacer(1, 10)]
    chosen = [(kind, config) for kind, config in facts['transform_decisions'].items() if config.get('enabled')]
    story.append(Paragraph('Chosen', st['sub']))
    if chosen:
        for kind, config in chosen:
            columns = config.get('columns', [])
            names = pdf_text(', '.join(map(str, columns[:8]))) + (f' and {len(columns) - 8} more' if len(columns) > 8 else '')
            story.append(Paragraph(f"{bold(pdf_text(str(kind).title()))}: {plural(len(columns), 'column')}"
                                   + (f' ({names})' if columns else ''), st['finding'], bulletText='■'))
    else:
        story.append(Paragraph('No transformations were selected.', st['muted']))
    story.append(Paragraph('What was done', st['sub']))
    if facts['log']:
        for entry in facts['log']:
            status = entry.get('status', 'applied')
            count = len(entry.get('columns', []))
            kind = pdf_text(str(entry.get('type', 'Unknown')).title())
            if status == 'applied':
                line = f"{bold(kind)}: {plural(count, 'column')} affected"
            else:
                line = f"{bold(kind)}: {pdf_text(str(status))} ({plural(count, 'column')} selected, data unchanged)"
            story.append(Paragraph(line, st['finding'], bulletText='■'))
            if entry.get('note'):
                story.append(Paragraph(pdf_text(entry['note']), ParagraphStyle('note', parent=st['small'], leftIndent=14)))
    else:
        story.append(Paragraph('No transformations were executed.', st['muted']))
    return story


def generate_readiness_report(output_path, state, transformation_log=None):
    """Write the Data Readiness Report for a pipeline session to `output_path` (a PDF, US Letter)."""
    facts = report_facts(state, transformation_log)
    st = styles()

    doc = BaseDocTemplate(output_path, pagesize=letter, leftMargin=MARGIN, rightMargin=MARGIN,
                          topMargin=0.95 * inch, bottomMargin=0.85 * inch,
                          title='Data Readiness Report', author='DataDragon', subject=str(state.filename))
    doc.dd_facts, doc.dd_filename = facts, str(state.filename)
    doc.dd_generated = datetime.now().strftime('%Y-%m-%d %H:%M')
    body = Frame(MARGIN, 0.85 * inch, CONTENT_W, PAGE_H - 1.8 * inch, leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    cover = Frame(MARGIN, MARGIN, CONTENT_W, PAGE_H - 2 * MARGIN, leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates([PageTemplate(id='cover', frames=[cover], onPage=draw_cover),
                          PageTemplate(id='body', frames=[body], onPage=draw_page_frame)])

    story = [NextPageTemplate('body'), PageBreak()]
    story += summary_section(facts, st)
    story += [PageBreak()] + shape_section(facts, st)
    story += [PageBreak()] + gaps_section(facts, st)
    story += [PageBreak()] + keys_section(facts, st)
    story += [Spacer(1, 22), CondPageBreak(2.6 * inch)] + transformations_section(facts, st)      # never a heading alone at a page end
    story += [Spacer(1, 24), Paragraph('Generated by DataDragon - Data Readiness Pipeline', st['small'])]
    doc.build(story)
