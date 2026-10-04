"""The cover sheet of DataDragon's Excel outputs: the same look as the PDF report, as the first sheet of a workbook.

Two steps, because a workbook's cover must be created before its other sheets (to come first) but can only be
filled in once those sheets are known:

    cover = begin_cover(workbook)                # before any other sheet is added
    ... write the data sheets ...
    finish_cover(workbook, cover, log, [(sheet name, rows, columns), ...])

`workbook` is an xlsxwriter workbook (also `pandas.ExcelWriter(...).book`). The cover says which tool made the
file, when, what went in and out, and what each sheet holds, with a link to every sheet. It holds no cell values
from the data. Fonts cannot be embedded in a workbook, so it names common ones (Georgia, Calibri, Consolas).
"""
import os

COVER_SHEET = 'DataDragon'
ROOT = os.path.dirname(os.path.abspath(__file__))
MARK_IMAGE = os.path.join(ROOT, 'static', 'images', 'brand', 'datadragon-mark-512.png')
BANNER_IMAGE = os.path.join(ROOT, 'report_assets', 'images', 'banner.jpg')

# "Ember & Ink", light theme (static/css/tokens.css)
PAPER, SUNKEN, INK, INK_2, INK_3, RULE, TEAL, EMBER = (
    '#F6F1E7', '#EDE6D6', '#14211F', '#44524E', '#5E6A66', '#D9D1BF', '#17605C', '#C4411B')


def begin_cover(workbook):
    """Create the (still empty) cover as the workbook's first sheet."""
    return workbook.add_worksheet(COVER_SHEET)


def finish_cover(workbook, cover, log, sheets):
    """Fill the cover in. `log` is the record from make_log; `sheets` is [(name, rows, columns)] for the data sheets."""
    def fmt(**properties):
        return workbook.add_format({'bg_color': PAPER, 'font_color': INK, 'font_name': 'Calibri', 'font_size': 11,
                                    'valign': 'vcenter', **properties})

    paper = fmt()
    cover.hide_gridlines(2)
    cover.set_column(0, 0, 3, paper)
    cover.set_column(1, 1, 26, paper)
    cover.set_column(2, 2, 16, paper)
    cover.set_column(3, 3, 16, paper)
    cover.set_column(4, 4, 44, paper)
    cover.set_column(5, 40, 9, paper)
    for blank_row in range(64):                 # real cells: some viewers ignore row and column formats on empty cells
        cover.set_row(blank_row, None, paper)
        for blank_column in range(14):
            cover.write_blank(blank_row, blank_column, None, paper)

    row = 1
    if os.path.exists(MARK_IMAGE):
        cover.set_row(row, 54, paper)
        cover.insert_image(row, 1, MARK_IMAGE, {'x_scale': 0.14, 'y_scale': 0.14, 'x_offset': 0, 'y_offset': 0,
                                                'object_position': 3, 'decorative': True})
        row += 1
    wordmark = fmt(font_name='Georgia', font_size=20, bold=True)
    wordmark_italic = fmt(font_name='Georgia', font_size=20, italic=True)
    cover.set_row(row, 32, paper)
    cover.write_rich_string(row, 1, wordmark, 'Data', wordmark_italic, 'Dragon', wordmark)
    row += 2

    cover.write_string(row, 1, 'result of', fmt(font_name='Consolas', font_size=9, font_color=INK_3))
    row += 1
    cover.set_row(row, 40, paper)
    cover.write_string(row, 1, str(log.get('Tool') or 'DataDragon'), fmt(font_name='Georgia', font_size=26))
    row += 1
    cover.set_row(row, 6, fmt(bottom=1, bottom_color=INK))
    for column in range(1, 5):
        cover.write_blank(row, column, None, fmt(bottom=1, bottom_color=INK))
    row += 2

    label = fmt(font_color=INK_2)
    value = fmt(font_name='Consolas', font_size=10)
    number = fmt(font_name='Consolas', font_size=10, num_format='#,##0', align='left')
    facts = [('Generated (UTC)', log.get('Timestamp (UTC)')), ('Rows in', log.get('Rows in')),
             ('Rows out', log.get('Rows out')), ('Version', log.get('Version'))]
    for name, fact in facts:
        if fact is None or fact == '':
            continue
        cover.write_string(row, 1, name, label)
        if isinstance(fact, (int, float)) and not isinstance(fact, bool):
            cover.write_number(row, 2, fact, number)
        else:
            cover.write_string(row, 2, str(fact), value)
        row += 1
    if log.get('Warnings'):
        row += 1
        cover.write_string(row, 1, 'Note', fmt(font_color=EMBER, bold=True))
        cover.merge_range(row, 2, row, 4, str(log['Warnings']), fmt(text_wrap=True, valign='top'))
        cover.set_row(row, 15 * min(6, 1 + len(str(log['Warnings'])) // 80), paper)
        row += 1
    row += 1

    cover.write_string(row, 1, 'In this workbook', fmt(font_name='Georgia', font_size=14))
    row += 1
    header = fmt(bg_color=SUNKEN, font_color=INK_2, font_name='Consolas', font_size=9, bottom=1, bottom_color=INK_3)
    for column, text in enumerate(('sheet', 'rows', 'columns', 'what it is'), 1):
        cover.write_string(row, column, text, header)
    row += 1
    link = fmt(font_color=TEAL, underline=1, font_name='Consolas', font_size=10, bottom=1, bottom_color=RULE)
    cell = fmt(font_name='Consolas', font_size=10, num_format='#,##0', align='left', bottom=1, bottom_color=RULE)
    note = fmt(font_color=INK_2, bottom=1, bottom_color=RULE)
    listed = [(name, rows, columns, 'data' if i == 0 else '') for i, (name, rows, columns) in enumerate(sheets)]
    listed.append(('_DataDragon_Log', None, None, 'what was done: the tool, its settings, counts'))
    for name, rows, columns, what in listed:
        quoted = name.replace("'", "''")
        cover.write_url(row, 1, f"internal:'{quoted}'!A1", link, string=name)
        for column, count in ((2, rows), (3, columns)):
            if count is None:
                cover.write_blank(row, column, None, cell)
            else:
                cover.write_number(row, column, count, cell)
        if what:
            cover.write_string(row, 4, what, note)
        else:
            cover.write_blank(row, 4, None, note)
        row += 1
    row += 1

    small = fmt(font_color=INK_3, font_size=9)
    cover.write_string(row, 1, 'This file was produced on the server that runs DataDragon. Nothing in it was sent to another service.', small)
    row += 1
    cover.write_string(row, 1, 'To get files without this cover sheet, switch it off in the sidebar of DataDragon.', small)
    row += 2
    if os.path.exists(BANNER_IMAGE):
        cover.insert_image(row, 1, BANNER_IMAGE, {'x_scale': 0.72, 'y_scale': 0.72, 'object_position': 3, 'decorative': True})

    cover.set_first_sheet()
    cover.activate()
    cover.set_landscape()
    cover.fit_to_pages(1, 1)
