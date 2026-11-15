"""Where a graze may sit: its whole window to itself, ending before midnight.

Pure functions over schedule entries ({'time', 'is_fixed', 'graze', ...}).
The schedule routes refuse edits that break these, the editor shows the
reason on rows that can't graze, and the daily randomizer keeps the same
window clear. A schedule saved before these rules still loads and runs.
"""

from servo_controller import GRAZE_SPAN_S

GRAZE_MIN = GRAZE_SPAN_S // 60
VARY_MIN = 30     # a ±30 slot's roll in either direction
BUFFER_MIN = 10   # the randomizer's clear gap between any two feedings
GRAZE_GAP_MIN = GRAZE_MIN + BUFFER_MIN
DAY_MIN = 24 * 60


def minutes(hhmm):
    hours, mins = hhmm.split(':')
    return int(hours) * 60 + int(mins)


def twelve_hour(mins):
    hours, mins = divmod(mins % DAY_MIN, 60)
    return f"{hours % 12 or 12}:{mins:02d} {'AM' if hours < 12 else 'PM'}"


def latest_graze_start(is_fixed):
    """The last set time, in minutes, a graze may have. Its last pulse lands
    GRAZE_MIN after the start and must still be dated today (a pulse after
    midnight would mark tomorrow's slot fed), so a Fixed graze starts by
    23:29 and a ±30 one, which can roll half an hour later, by 22:59."""
    return DAY_MIN - 1 - GRAZE_MIN - (0 if is_fixed else VARY_MIN)


def gap_minutes(a, b):
    """Minutes between two set times, the short way round the clock: a
    23:50 meal and a 00:10 graze are 20 minutes apart."""
    d = abs(minutes(a) - minutes(b))
    return min(d, DAY_MIN - d)


def midnight_problem(time, is_fixed):
    """Why a graze can't start at `time`, or None when it can."""
    if minutes(time) <= latest_graze_start(is_fixed):
        return None
    message = f"A graze needs {GRAZE_MIN} minutes before midnight."
    if not is_fixed and minutes(time) <= latest_graze_start(True):
        return (f"{message} Set this one to Fixed, or move it before "
                f"{twelve_hour(latest_graze_start(False) + 1)}.")
    return f"{message} Move it before {twelve_hour(latest_graze_start(is_fixed) + 1)}."


def spacing_problem(time, graze, entries):
    """Why a slot at `time` (a graze or not) can't sit among `entries`, or
    None. Two set times need GRAZE_GAP_MIN between them when either is a
    graze; the entry at `time` itself is skipped."""
    for other in entries:
        if other['time'] == time or not (graze or other['graze']):
            continue
        if gap_minutes(time, other['time']) < GRAZE_GAP_MIN:
            return (f"Too close to {twelve_hour(minutes(other['time']))}. A graze needs "
                    f"{GRAZE_GAP_MIN} minutes clear of any other feeding.")
    return None


def graze_problem(entry, entries):
    """Why `entry` can't be a graze where it is, or None."""
    return (midnight_problem(entry['time'], entry['is_fixed'])
            or spacing_problem(entry['time'], True, entries))
