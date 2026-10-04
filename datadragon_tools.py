"""Simple tools on one scaffold: a tool is a description (name, options) plus one function on a DataFrame.

This module has no Flask in it. `datadragon.py` gives every registered tool a page, a "preview the changes"
endpoint and a "run" endpoint (see `register_tool_routes` there); `templates/tool.html` and `static/js/tool.js`
draw the page from the description.

A tool function is `run(df, options) -> ToolResult`. It must not change `df` in place, and it reports what it did
in `ToolResult.summary` (shown to the user) and `ToolResult.details` (written to the log: structure and counts,
never cell values).
"""
import re
import unicodedata
from dataclasses import dataclass, field

import numpy as np
import pandas as pd


class ToolError(ValueError):
    """Something the user can fix (a missing choice, a column of the wrong kind). The message is shown as it is."""


@dataclass
class Option:
    id: str
    label: str
    kind: str                       # columns | column | select | checkbox | text | number | json (checked by `check`)
    default: object = None
    choices: list = field(default_factory=list)      # select: [(value, label)]
    help: str = ''
    required: bool = False
    optional_blank: str = ''        # column: label of the "none" choice, if choosing nothing is allowed
    show_if: dict = field(default_factory=dict)      # {other option id: [values]}: shown only then
    literal: bool = False           # text the user typed that may be data: kept out of the log
    minimum: float = None
    maximum: float = None
    check: object = None            # json: function(value) -> checked value, raising ToolError


@dataclass
class ToolResult:
    df: pd.DataFrame
    summary: list = field(default_factory=list)      # [(label, value)] shown to the user
    details: dict = field(default_factory=dict)      # for the log: counts and structure, no cell values
    extra_sheets: list = field(default_factory=list)  # [(sheet name, DataFrame)] written after the main sheet
    notes: list = field(default_factory=list)        # plain sentences the user should read (limits that applied)


@dataclass
class Tool:
    slug: str
    name: str
    title: tuple                    # (plain words, italic word) for the page heading
    description: str                # one sentence, as on the hub
    action: str                     # the label of the run button
    suffix: str                     # added to the output file name
    options: list
    run: object
    how: list = field(default_factory=list)          # [(heading, text)] for the "How it works" panel
    sheet_name: str = 'Result'
    page: bool = True               # False: the tool has its own page elsewhere and is here so recipes can replay it


TOOLS = {}


def register(tool):
    TOOLS[tool.slug] = tool
    return tool


# ---------------------------------------------------------------------------------------------------------------
# Options: from the request to checked values
# ---------------------------------------------------------------------------------------------------------------
def parse_options(tool, raw, df):
    """Check the submitted options against the tool's description and the file's columns."""
    raw = raw if isinstance(raw, dict) else {}
    columns = [str(c) for c in df.columns]
    by_name = dict(zip(columns, df.columns))          # the request carries names as text; the frame may not
    values = {}
    for option in tool.options:
        value = raw.get(option.id, option.default)
        if option.kind == 'columns':
            chosen = value if isinstance(value, list) else []
            unknown = [c for c in chosen if str(c) not in by_name]
            if unknown:
                raise ToolError(f'Column "{unknown[0]}" is not in this file.')
            value = [by_name[str(c)] for c in dict.fromkeys(map(str, chosen))]
            if option.required and not value:
                raise ToolError(f'Choose at least one column for "{option.label}".')
        elif option.kind == 'column':
            if value in (None, ''):
                if option.required:
                    raise ToolError(f'Choose a column for "{option.label}".')
                value = None
            elif str(value) not in by_name:
                raise ToolError(f'Column "{value}" is not in this file.')
            else:
                value = by_name[str(value)]
        elif option.kind == 'select':
            allowed = [choice[0] for choice in option.choices]
            if value not in allowed:
                raise ToolError(f'"{option.label}" must be one of: {", ".join(map(str, allowed))}.')
        elif option.kind == 'json':
            value = option.check(value)
        elif option.kind == 'checkbox':
            value = value is True or str(value).lower() == 'true'
        elif option.kind == 'number':
            if value in (None, ''):
                if option.required:
                    raise ToolError(f'Enter a number for "{option.label}".')
                value = None
            else:
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    raise ToolError(f'"{option.label}" must be a number.')
                if not np.isfinite(value):
                    raise ToolError(f'"{option.label}" must be a number.')
                if option.minimum is not None and value < option.minimum:
                    raise ToolError(f'"{option.label}" must be at least {option.minimum:g}.')
                if option.maximum is not None and value > option.maximum:
                    raise ToolError(f'"{option.label}" must be at most {option.maximum:g}.')
                value = int(value) if value.is_integer() else value
        else:                                           # text
            value = '' if value is None else str(value)
            if len(value) > 500:
                raise ToolError(f'"{option.label}" is too long (500 characters at most).')
            if option.required and not value:
                raise ToolError(f'Enter a value for "{option.label}".')
        values[option.id] = value
    return values


def step_record(tool, options, full=False):
    """The step as data: which tool, with which settings.

    For the log (`full=False`) text the user typed is recorded by length only, because it may be data.
    For a recipe (`full=True`) the settings are complete, so the step can be replayed.
    """
    recorded = {}
    for option in tool.options:
        value = options.get(option.id)
        if option.literal and not full:
            recorded[option.id] = {'literal': True, 'length': len(str(value or ''))}
        elif option.kind == 'json' and not full:
            recorded[option.id] = {'literal': True, 'items': len(value) if isinstance(value, (list, dict)) else 1}
        elif option.kind == 'columns':
            recorded[option.id] = [str(c) for c in value]
        elif option.kind == 'column':
            recorded[option.id] = None if value is None else str(value)
        else:
            recorded[option.id] = value
    step = {'tool': tool.slug, 'options': recorded}
    if full:
        step['name'] = tool.name
    return step


# ---------------------------------------------------------------------------------------------------------------
# Recipes: recorded steps, replayed on another file
# ---------------------------------------------------------------------------------------------------------------
RECIPE_VERSION = 1
MAX_RECIPE_STEPS = 50


def make_recipe(steps, name=''):
    return {'datadragon_recipe': RECIPE_VERSION, 'name': str(name)[:120], 'steps': steps}


def check_recipe(data):
    """The steps of a recipe file, or a ToolError saying what is wrong with it."""
    if not isinstance(data, dict) or 'datadragon_recipe' not in data:
        raise ToolError('This is not a DataDragon recipe file.')
    if data['datadragon_recipe'] != RECIPE_VERSION:
        raise ToolError('This recipe was made by a newer version of DataDragon and cannot be read here.')
    steps = data.get('steps')
    if not isinstance(steps, list) or not steps:
        raise ToolError('The recipe has no steps.')
    if len(steps) > MAX_RECIPE_STEPS:
        raise ToolError(f'The recipe has {len(steps)} steps; the most that can be replayed is {MAX_RECIPE_STEPS}.')
    for number, step in enumerate(steps, 1):
        if not isinstance(step, dict) or not isinstance(step.get('tool'), str) or not isinstance(step.get('options'), dict):
            raise ToolError(f'Step {number} of the recipe is not readable.')
        if step['tool'] not in TOOLS:
            raise ToolError(f'Step {number} uses "{step["tool"]}", which this version of DataDragon cannot replay.')
    return steps


def replay(steps, df):
    """Apply the steps in order. Returns (final table, [per-step report], [(sheet name, table)] of side outputs).

    A step whose settings no longer fit the table (a column that is gone, for example) stops the replay with a
    ToolError that names the step; nothing is half-applied, because every step works on a copy.
    """
    reports, side_sheets = [], []
    for number, step in enumerate(steps, 1):
        tool = TOOLS[step['tool']]
        try:
            options = parse_options(tool, step['options'], df)
            result = tool.run(df, options)
        except ToolError as error:
            raise ToolError(f'Step {number} ({tool.name}): {error}')
        change = describe_change(df, result.df, sample=0)
        reports.append({'step': number, 'tool': tool.name, 'rows_in': len(df), 'rows_out': len(result.df),
                        'cells_changed': change['cells_changed'], 'columns_added': change['columns_added'],
                        'columns_removed': change['columns_removed'],
                        'summary': [[label, value] for label, value in result.summary]})
        for name, frame in result.extra_sheets:
            side_sheets.append((f'Step {number} {name}'[:31], frame))
        df = result.df
    return df, reports, side_sheets


def describe(tool):
    """The tool as plain data, for the page."""
    return {
        'slug': tool.slug, 'name': tool.name, 'action': tool.action,
        'options': [{'id': o.id, 'label': o.label, 'kind': o.kind, 'default': o.default,
                     'choices': [list(c) for c in o.choices], 'help': o.help, 'required': o.required,
                     'optional_blank': o.optional_blank, 'show_if': o.show_if,
                     'minimum': o.minimum, 'maximum': o.maximum} for o in tool.options],
    }


# ---------------------------------------------------------------------------------------------------------------
# What a run would change (shown before it is applied)
# ---------------------------------------------------------------------------------------------------------------
def is_blank(series):
    """True where a cell is empty: missing, or text with nothing but spaces in it."""
    blank = series.isna()
    if series.dtype == object:
        text = series.map(lambda v: isinstance(v, str) and v.strip() == '')
        blank = blank | text.astype(bool)
    return blank


def same_cells(before, after):
    """Boolean frame: True where a cell is unchanged (two missing values count as the same)."""
    left, right = before.astype(object), after.astype(object)
    equal = (left == right) | (left.isna() & right.isna())
    return equal.fillna(False).astype(bool)


def describe_change(before, after, sample=8):
    """Compare a table before and after a tool: counts, and a few changed rows side by side."""
    change = {
        'rows_before': int(len(before)), 'rows_after': int(len(after)),
        'columns_added': [str(c) for c in after.columns if c not in before.columns],
        'columns_removed': [str(c) for c in before.columns if c not in after.columns],
        'cells_changed': None, 'rows_changed': None, 'sample': [], 'reordered': False,
    }
    shared = [c for c in before.columns if c in after.columns]
    if len(before) == len(after) and before.index.equals(after.index) and shared:
        same = same_cells(before[shared], after[shared])
        changed_rows = ~same.all(axis=1)
        change['cells_changed'] = int((~same).to_numpy().sum())
        change['rows_changed'] = int(changed_rows.sum())
        for index in before.index[changed_rows.to_numpy()][:sample]:
            cells = [{'column': str(c), 'before': _show(before.at[index, c]), 'after': _show(after.at[index, c])}
                     for c in shared if not same.at[index, c]]
            change['sample'].append({'row': int(before.index.get_loc(index)) + 2, 'cells': cells})   # +2: header, 1-based
    elif len(before) == len(after) and sorted(map(str, before.index)) == sorted(map(str, after.index)):
        change['reordered'] = True
    return change


def _show(value):
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    text = str(value)
    return text if len(text) <= 80 else text[:79] + '…'


# ---------------------------------------------------------------------------------------------------------------
# Text Cleaner
# ---------------------------------------------------------------------------------------------------------------
_NUMBER_TEXT = re.compile(r'^\s*[-+]?(\d[\d,]*(\.\d*)?|\.\d+)([eE][-+]?\d+)?\s*%?\s*$')
_SPACES = re.compile(r'\s+')
_PUNCTUATION = re.compile(r'[^\w\s]', re.UNICODE)


def _nonprinting(text):
    return ''.join(ch for ch in text if ch in '\t\n\r' or unicodedata.category(ch) not in ('Cc', 'Cf'))


def _accents(text):
    return ''.join(ch for ch in unicodedata.normalize('NFKD', text) if not unicodedata.combining(ch))


def _sentence(text):
    lowered = text.lower()
    for i, ch in enumerate(lowered):
        if ch.isalpha():
            return lowered[:i] + ch.upper() + lowered[i + 1:]
    return lowered


TEXT_RULES = [      # (option id, label for the summary, function) in the order they are applied
    ('remove_nonprinting', 'Non-printing characters removed', _nonprinting),
    ('trim', 'Spaces trimmed at the ends', str.strip),
    ('collapse_spaces', 'Repeated spaces collapsed', lambda text: _SPACES.sub(' ', text)),
    ('remove_accents', 'Accents removed', _accents),
    ('remove_punctuation', 'Punctuation removed', lambda text: _PUNCTUATION.sub('', text)),
]
CASES = {'lower': str.lower, 'upper': str.upper, 'title': str.title, 'sentence': _sentence}


def clean_text(df, options):
    columns = options['columns']
    result = df.copy()
    counts = {label: 0 for _, label, _ in TEXT_RULES}
    counts['Case changed'] = 0
    rules = [(label, function) for option_id, label, function in TEXT_RULES if options[option_id]]
    if options['case'] != 'keep':
        rules.append(('Case changed', CASES[options['case']]))
    if not rules:
        raise ToolError('Choose at least one thing to clean.')
    cells_changed = 0
    for column in columns:
        values = result[column].to_numpy(dtype=object).copy()
        for i, value in enumerate(values):
            # numbers, dates and blanks are left alone; so is text that is a number (every CSV cell arrives as text)
            if not isinstance(value, str) or _NUMBER_TEXT.match(value):
                continue
            original = value
            for label, function in rules:
                cleaned = function(value)
                if cleaned != value:
                    counts[label] += 1
                    value = cleaned
            if value != original:
                values[i] = value
                cells_changed += 1
        result[column] = pd.Series(values, index=result.index, dtype=object)
    summary = [('Columns cleaned', len(columns)), ('Cells changed', cells_changed)]
    summary += [(label, count) for label, count in counts.items() if count]
    return ToolResult(result, summary, {'columns': len(columns), 'cells_changed': cells_changed,
                                       'changes_by_rule': {k: v for k, v in counts.items() if v}})


register(Tool(
    slug='text-cleaner', name='Text Cleaner', title=('Text', 'Cleaner'),
    description='Tidy text in bulk: stray spaces, hidden characters, case, accents and punctuation.',
    action='Clean text', suffix='cleaned', run=clean_text,
    options=[
        Option('columns', 'Columns to clean', 'columns', required=True,
               help='Only text is changed. Numbers and blank cells are left alone.'),
        Option('trim', 'Trim spaces at the start and end', 'checkbox', default=True),
        Option('collapse_spaces', 'Collapse repeated spaces, tabs and line breaks into one space', 'checkbox', default=True),
        Option('remove_nonprinting', 'Remove non-printing characters (zero-width spaces, control characters)', 'checkbox', default=True),
        Option('case', 'Letter case', 'select', default='keep',
               choices=[('keep', 'Leave as it is'), ('lower', 'lower case'), ('upper', 'UPPER CASE'),
                        ('title', 'Title Case'), ('sentence', 'Sentence case')]),
        Option('remove_accents', 'Remove accents (é becomes e)', 'checkbox', default=False),
        Option('remove_punctuation', 'Remove punctuation and symbols', 'checkbox', default=False,
               help='Keeps letters, digits, underscores and spaces.'),
    ],
    how=[('What it does', 'Applies the chosen clean-ups to every text cell in the chosen columns, in this order: hidden '
                          'characters, trimming, repeated spaces, accents, punctuation, then letter case.'),
         ('What it leaves alone', 'Numbers and blank cells are never changed, and no row is added or removed. Dates '
                                  'stored as real dates are left alone too; a date written as text (as in every CSV file) is '
                                  'text, so removing punctuation would strip its dashes or slashes.'),
         ('The result', 'The same table with the cleaned text, and a count of cells changed by each rule.')],
))


# ---------------------------------------------------------------------------------------------------------------
# Fill Missing
# ---------------------------------------------------------------------------------------------------------------
def fill_missing(df, options):
    columns, method = options['columns'], options['method']
    result = df.copy()
    blank = {column: is_blank(result[column]) for column in columns}
    total_blank = int(sum(mask.sum() for mask in blank.values()))

    if method == 'drop_rows':
        any_blank = pd.concat(blank, axis=1).any(axis=1)
        kept = result[~any_blank]
        removed = result[any_blank]
        return ToolResult(kept, [('Rows removed', int(any_blank.sum())), ('Rows kept', len(kept))],
                          {'method': method, 'columns': len(columns), 'rows_removed': int(any_blank.sum())},
                          extra_sheets=[('Removed rows', removed)] if len(removed) else [])

    filled_by_column, flags = {}, pd.Series([[] for _ in range(len(result))], index=result.index, dtype=object)
    for column in columns:
        mask = blank[column]
        series = result[column].astype(object).where(~mask, np.nan)      # whitespace-only text counts as blank
        if method == 'constant':
            if options['value'] == '':
                raise ToolError('Enter the value to fill blanks with.')
            replacement = _as_number_if_numeric(options['value'], result[column])
            filled = series.where(~mask, replacement)
        elif method in ('previous', 'next'):
            with pd.option_context('future.no_silent_downcasting', True):       # keep the cells as they are typed
                filled = series.ffill() if method == 'previous' else series.bfill()
        else:
            numbers = pd.to_numeric(series, errors='coerce')
            if method in ('mean', 'median'):
                if numbers.notna().sum() != series.notna().sum() or numbers.notna().sum() == 0:
                    raise ToolError(f'"{column}" is not a numeric column, so it has no {method}. '
                                    'Choose another method for it, or leave it out.')
                replacement = numbers.mean() if method == 'mean' else numbers.median()
                filled = series.where(~mask, _like_the_column(float(replacement), series))
            else:                                                       # most common value
                present = series.dropna()
                if present.empty:
                    filled = series
                else:
                    counts = present.value_counts(sort=False).sort_values(ascending=False, kind='stable')
                    filled = series.where(~mask, counts.index[0])
        now_filled = mask & filled.notna()
        filled_by_column[str(column)] = int(now_filled.sum())
        result[column] = filled if result[column].dtype == object else filled.infer_objects()
        if options['flag']:
            for index in result.index[now_filled.to_numpy()]:
                flags.at[index] = flags.at[index] + [str(column)]

    filled_total = sum(filled_by_column.values())
    if options['flag']:
        name = 'filled_columns'
        while name in result.columns:
            name += '_'
        result[name] = flags.map(lambda names: ', '.join(names) if names else None)
    summary = [('Blank cells found', total_blank), ('Cells filled', filled_total)]
    if total_blank - filled_total:
        summary.append(('Still blank (nothing to fill from)', total_blank - filled_total))
    summary += [(f'Filled in {name}', count) for name, count in filled_by_column.items() if count][:12]
    return ToolResult(result, summary, {'method': method, 'columns': len(columns), 'blank_cells': total_blank,
                                       'cells_filled': filled_total, 'flag_column': bool(options['flag'])})


def _holds_numbers(series):
    """True when every filled cell of the column is a number (not text that looks like one)."""
    present = series.dropna()
    return len(present) > 0 and all(isinstance(v, (int, float, np.integer, np.floating)) and not isinstance(v, (bool, np.bool_))
                                    for v in present)


def _as_number_if_numeric(text, series):
    """A typed fill value becomes a number when the column holds numbers, so the column stays numeric."""
    if _holds_numbers(series):
        try:
            number = float(text)
            return int(number) if number.is_integer() else number
        except ValueError:
            pass
    return text


def _like_the_column(number, series):
    """A computed fill value in the column's own form: a number among numbers, text among text (a CSV column)."""
    return number if _holds_numbers(series) else format(number, '.10g')


register(Tool(
    slug='fill-missing', name='Fill Missing', title=('Fill', 'Missing'),
    description='Fill blank cells with a value, the row above, or the column average; or drop the rows.',
    action='Fill blanks', suffix='filled', run=fill_missing,
    options=[
        Option('columns', 'Columns to look at', 'columns', required=True,
               help='A cell counts as blank when it is empty or holds only spaces.'),
        Option('method', 'What to do with blanks', 'select', default='constant',
               choices=[('constant', 'Fill with a value I type'), ('previous', 'Fill with the value above (fill down)'),
                        ('next', 'Fill with the value below (fill up)'), ('mean', 'Fill with the column average (numbers)'),
                        ('median', 'Fill with the column median (numbers)'), ('mode', 'Fill with the most common value'),
                        ('drop_rows', 'Remove rows that have a blank in these columns')]),
        Option('value', 'Value to fill with', 'text', show_if={'method': ['constant']}, literal=True),
        Option('flag', 'Add a column that lists which cells were filled', 'checkbox', default=False,
               show_if={'method': ['constant', 'previous', 'next', 'mean', 'median', 'mode']}),
    ],
    how=[('What it does', 'Finds blank cells in the chosen columns and fills them by the method you pick, or removes the '
                          'rows that have them.'),
         ('Good to know', 'Fill down and fill up leave a blank where there is nothing above or below. Average and median '
                          'work only on columns where every value is a number. When two values are equally common, the '
                          'one that appears first in the file is used.'),
         ('The result', 'The filled table, with a count of cells filled per column. Removed rows go to a second sheet.')],
))


# ---------------------------------------------------------------------------------------------------------------
# Remove Duplicates
# ---------------------------------------------------------------------------------------------------------------
def _comparison_key(series, loose):
    """The values as compared: blanks are equal to each other; with `loose`, text ignores case and extra spaces."""
    def key(value):
        if value is None or (not isinstance(value, str) and pd.isna(value)):
            return ''
        if isinstance(value, str):
            return _SPACES.sub(' ', value.strip()).casefold() if loose else value
        return value
    return series.map(key)


def remove_duplicates(df, options):
    columns = options['columns'] or list(df.columns)
    keys = pd.DataFrame({i: _comparison_key(df[column], options['ignore_case_and_spaces'])
                         for i, column in enumerate(columns)}, index=df.index)
    groups = keys.groupby(list(keys.columns), sort=False, dropna=False).ngroup()
    keep = options['keep']
    if keep == 'most_complete':
        filled = df.notna().sum(axis=1)
        order = pd.DataFrame({'group': groups, 'filled': -filled, 'position': np.arange(len(df))}, index=df.index)
        winners = order.sort_values(['group', 'filled', 'position'], kind='stable').drop_duplicates('group').index
        duplicate = ~df.index.isin(winners)
    else:
        duplicate = groups.duplicated(keep='first' if keep == 'first' else 'last').to_numpy()
    kept, removed = df[~duplicate], df[duplicate]
    group_sizes = groups.value_counts()
    summary = [('Rows in', len(df)), ('Rows kept', len(kept)), ('Duplicate rows removed', len(removed)),
               ('Groups that had duplicates', int((group_sizes > 1).sum()))]
    return ToolResult(kept, summary,
                      {'columns': len(columns), 'whole_row': not options['columns'], 'keep': keep,
                       'ignore_case_and_spaces': bool(options['ignore_case_and_spaces']), 'rows_removed': len(removed)},
                      extra_sheets=[('Removed rows', removed)] if len(removed) else [])


register(Tool(
    slug='remove-duplicates', name='Remove Duplicates', title=('Remove', 'Duplicates'),
    description='Remove repeated rows and get the cleaned file, with the removed rows kept on a second sheet.',
    action='Remove duplicates', suffix='deduplicated', run=remove_duplicates, sheet_name='Kept rows',
    options=[
        Option('columns', 'Columns that make a row a duplicate', 'columns',
               help='Rows with the same values in all of these columns are duplicates. Choose none to compare whole rows.'),
        Option('keep', 'Which row to keep from each group', 'select', default='first',
               choices=[('first', 'The first one in the file'), ('last', 'The last one in the file'),
                        ('most_complete', 'The one with the fewest blank cells')]),
        Option('ignore_case_and_spaces', 'Treat text as the same when only case or spacing differs', 'checkbox', default=False,
               help='"Acme  Corp" and "acme corp" then count as the same value.'),
    ],
    how=[('What it does', 'Groups rows that have the same values in the chosen columns, keeps one row from each group and '
                          'removes the rest.'),
         ('Good to know', 'Blank cells are equal to each other. Rows keep their original order. With "fewest blank cells", '
                          'ties go to the earlier row.'),
         ('The result', 'Sheet "Kept rows" is the cleaned table; sheet "Removed rows" holds what was taken out, so nothing '
                        'is lost. Kept plus removed always equals the rows that went in.')],
))


# ---------------------------------------------------------------------------------------------------------------
# Sort, Rank & Sample
# ---------------------------------------------------------------------------------------------------------------
def _sort_key(series):
    """Sortable values: numbers as numbers when the whole column is numeric, otherwise text without case."""
    if pd.api.types.is_numeric_dtype(series) or pd.api.types.is_datetime64_any_dtype(series):
        return series
    numbers = pd.to_numeric(series, errors='coerce')
    if numbers.notna().sum() == series.notna().sum():
        return numbers
    return series.map(lambda value: value if value is None or (not isinstance(value, str) and pd.isna(value))
                      else str(value).casefold())


def sort_rank_sample(df, options):
    levels = [(options[f'sort{i}'], options[f'direction{i}'] == 'ascending') for i in (1, 2, 3) if options[f'sort{i}'] is not None]
    if len({column for column, _ in levels}) != len(levels):
        raise ToolError('The same column is chosen twice to sort by.')
    result = df
    if levels:
        keys = pd.DataFrame({i: _sort_key(df[column]) for i, (column, _) in enumerate(levels)}, index=df.index)
        order = keys.sort_values(list(keys.columns), ascending=[ascending for _, ascending in levels],
                                 kind='stable', na_position='last').index
        result = df.loc[order]
    if options['rank']:
        if not levels:
            raise ToolError('Choose a column to sort by before adding a rank.')
        name = 'rank'
        while name in result.columns:
            name += '_'
        result = result.copy()
        within = options['rank_within']
        if within is None:
            result[name] = np.arange(1, len(result) + 1)
        else:
            result[name] = result.groupby(within, sort=False, dropna=False).cumcount() + 1

    keep, count = options['keep'], options['count']
    rows_before = len(result)
    if keep != 'all':
        if count is None or count < 1 or int(count) != count:
            raise ToolError('Enter how many rows, as a whole number of 1 or more.')
        count = int(count)
        if keep == 'first':
            result = result.head(count)
        elif keep == 'last':
            result = result.tail(count)
        elif keep == 'every_nth':
            result = result.iloc[::count]
        else:                                                           # random sample, in the current order
            seed = int(options['seed'] if options['seed'] is not None else 42)
            if count < len(result):
                picked = np.sort(np.random.default_rng(seed).choice(len(result), size=count, replace=False))
                result = result.iloc[picked]
    summary = [('Rows in', len(df)), ('Rows out', len(result))]
    if levels:
        summary.append(('Sorted by', ', '.join(f'{column} ({"ascending" if ascending else "descending"})' for column, ascending in levels)))
    if keep == 'sample':
        summary.append(('Sample seed', int(options['seed'] if options['seed'] is not None else 42)))
    return ToolResult(result, summary, {'sort_levels': len(levels), 'rank': bool(options['rank']), 'keep': keep,
                                       'rows_before_keep': rows_before, 'rows_out': len(result)})


_DIRECTIONS = [('ascending', 'A to Z, smallest first'), ('descending', 'Z to A, largest first')]
_KEEP_WITH_COUNT = ['first', 'last', 'sample', 'every_nth']

register(Tool(
    slug='sort-and-sample', name='Sort, Rank & Sample', title=('Sort, Rank &', 'Sample'),
    description='Sort by up to three columns, add a rank, and keep the top rows or a random sample.',
    action='Apply', suffix='sorted', run=sort_rank_sample,
    options=[
        Option('sort1', 'Sort by', 'column', optional_blank='Do not sort'),
        Option('direction1', 'Order', 'select', default='ascending', choices=_DIRECTIONS),
        Option('sort2', 'Then by', 'column', optional_blank='Nothing'),
        Option('direction2', 'Order', 'select', default='ascending', choices=_DIRECTIONS),
        Option('sort3', 'Then by', 'column', optional_blank='Nothing'),
        Option('direction3', 'Order', 'select', default='ascending', choices=_DIRECTIONS),
        Option('rank', 'Add a rank column (1, 2, 3 in the sorted order)', 'checkbox', default=False),
        Option('rank_within', 'Restart the rank for each value of', 'column', optional_blank='Do not restart',
               show_if={'rank': [True]}),
        Option('keep', 'Rows to keep', 'select', default='all',
               choices=[('all', 'All rows'), ('first', 'The first rows (top N)'), ('last', 'The last rows (bottom N)'),
                        ('sample', 'A random sample'), ('every_nth', 'Every nth row')]),
        Option('count', 'How many (or n)', 'number', minimum=1, show_if={'keep': _KEEP_WITH_COUNT}),
        Option('seed', 'Sample seed', 'number', default=42, minimum=0, show_if={'keep': ['sample']},
               help='The same seed on the same file gives the same sample.'),
    ],
    how=[('What it does', 'Sorts the rows, optionally numbers them, and optionally keeps only some of them.'),
         ('Good to know', 'A column whose values are all numbers sorts as numbers; other columns sort as text, ignoring '
                          'case. Blank cells go last. Rows that tie keep their original order. A random sample keeps the '
                          'sorted order and is repeatable: the seed is shown with the result.'),
         ('The result', 'The sorted (and possibly shortened) table.')],
))
