"""Regular expressions supplied by users (Find & Replace, the validation 'pattern' rule), with a leash.

Python's `re` has no timeout, so a pattern like (a|aa)+$ on a long cell can pin a worker for minutes. This
module uses the `regex` package, which can stop a match after a deadline:
  * the pattern is length-capped and must compile,
  * every match gets CELL_TIMEOUT seconds,
  * a whole request gets TOTAL_SECONDS (a Budget).
Hitting a limit raises PatternTooComplex; a bad pattern raises PatternError. Both are ValueErrors with messages that
are safe to show the user.
"""
import time
from functools import lru_cache

import regex

MAX_PATTERN_LENGTH = 500
CELL_TIMEOUT = 0.05
TOTAL_SECONDS = 20


class PatternError(ValueError):
    """The pattern is empty, too long or not a valid regular expression."""


class PatternTooComplex(ValueError):
    """Matching exceeded its time limit."""


@lru_cache(maxsize=128)
def compile_pattern(pattern, ignore_case=False):
    if not isinstance(pattern, str) or not pattern:
        raise PatternError('The pattern is empty')
    if len(pattern) > MAX_PATTERN_LENGTH:
        raise PatternError(f'The pattern is too long (maximum {MAX_PATTERN_LENGTH} characters)')
    try:
        return regex.compile(pattern, regex.IGNORECASE if ignore_case else 0)
    except regex.error as e:
        raise PatternError(f'Invalid pattern: {e}')


class Budget:
    """Wall-clock allowance for everything one request matches."""

    def __init__(self, seconds=TOTAL_SECONDS):
        self.deadline = time.monotonic() + seconds

    def timeout(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise PatternTooComplex('The pattern took too long to run on this data')
        return min(CELL_TIMEOUT, remaining)


def substitute(compiled, replacement, text, budget):
    """(new_text, number_of_matches). `replacement` is a template (\\1, \\g<name>) or a function."""
    try:
        return compiled.subn(replacement, text, timeout=budget.timeout())
    except TimeoutError:
        raise PatternTooComplex('The pattern is too complex for this data (it took too long on a single value)')
    except (regex.error, IndexError) as e:
        raise PatternError(f'Invalid replacement: {e}')


def fullmatch(compiled, text, budget):
    try:
        return compiled.fullmatch(text, timeout=budget.timeout()) is not None
    except TimeoutError:
        raise PatternTooComplex('The pattern is too complex for this data (it took too long on a single value)')
