"""feeding_schedules.txt — the one reader and writer.

Format: one "HH:MM,portion[,fixed][,graze]" per line. Parsing used to live in four
places that disagreed on the legacy "HH:MM,fixed" shape (the UI showed it
as fixed, the scheduler randomized it); this module is the single source.

Callers hold feeder_core.STATE_LOCK for read-modify-write sequences.
"""

import logging
import os

from atomicfile import write_atomic
from servo_controller import PORTION_SIZES, DEFAULT_PORTION

log = logging.getLogger('petfeedr')

SCHEDULES_FILE = 'feeding_schedules.txt'


def parse_line(line):
    """(time_str, portion, is_fixed, graze) for one schedule line.

    Unknown portions fall back to the default. The legacy "HH:MM,fixed"
    shape (fixed in the portion slot) reads as fixed with the default portion.
    Flags after the portion may come in either order.
    """
    parts = [p.strip() for p in line.strip().split(',')]
    time_str = parts[0]
    portion = DEFAULT_PORTION
    is_fixed = False

    if len(parts) > 1:
        if parts[1].lower() == 'fixed':
            is_fixed = True
        elif parts[1] in PORTION_SIZES:
            portion = parts[1]
    flags = {p.lower() for p in parts[2:]}
    is_fixed = is_fixed or 'fixed' in flags

    return time_str, portion, is_fixed, 'graze' in flags


def format_line(time_str, portion, is_fixed, graze=False):
    return ','.join([time_str, portion] + ['fixed'] * is_fixed + ['graze'] * graze)


def read_entries():
    """All schedule entries as dicts, in file order. Missing file → []."""
    try:
        with open(SCHEDULES_FILE) as f:
            lines = f.readlines()
    except FileNotFoundError:
        return []
    entries = []
    for line in lines:
        if not line.strip():
            continue
        time_str, portion, is_fixed, graze = parse_line(line)
        entries.append({'time': time_str, 'portion': portion, 'is_fixed': is_fixed, 'graze': graze})
    return entries


def write_entries(entries):
    write_atomic(SCHEDULES_FILE, ''.join(
        format_line(e['time'], e['portion'], e['is_fixed'], e.get('graze', False)) + '\n'
        for e in entries))


def exists():
    return os.path.isfile(SCHEDULES_FILE)
