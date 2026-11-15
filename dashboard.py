"""View-model for the index page: schedule rows, timeline, stats, hopper card.

Split out of web_interface so the routes file holds routes. Read-only —
nothing here mutates state.
"""

from datetime import date, datetime, timedelta

from feeder_core import load_todays_schedule, log, slot_outcome, todays_records
from feeding_stats import (EXTRA_SOURCES, parse_recent_activity, parse_weekly_stats, build_week_summary,
                           calculate_consumption_rate, calculate_daily_total, on_time_summary,
                           rhythm_week)
from servo_controller import PORTION_SIZES, GRAZE_SPAN_S, graze_pulse_count
import events
import hopper
import manual_limit
import schedule_store

# A slot this recently due with nothing recorded is "due", not "missed" —
# the dispense lands within seconds, the page just rendered first
DUE_GRACE_MIN = 2
# A running graze with no closing record this long after its expected end is
# shown as fed, not grazing — the closing write failed, the food came out
GRAZING_STALE_MIN = 5
# A graze's trail of crumbs on Today's bar, in px past its head dot (lead +
# gap per extra crumb, matching styles.css), and the narrowest bar it must
# fit on (a 320px phone). Spacing is fixed, not to scale, so the bar's end is
# padded to keep a late graze's trail on the bar.
TRAIL_LEAD_PX, TRAIL_GAP_PX = 15, 7
NARROWEST_BAR_PX = 250


def read_schedules_with_details():
    """Schedule entries merged with today's actual (possibly randomized) times."""
    todays_by_base = {s['base_time']: s for s in load_todays_schedule() or []}

    schedules = []
    for entry in schedule_store.read_entries():
        time_str = entry['time']
        actual_time = todays_by_base.get(time_str, {}).get('actual_time', time_str)
        try:
            base_dt = datetime.strptime(time_str, "%H:%M")
            actual_dt = datetime.strptime(actual_time, "%H:%M")
        except ValueError:
            log.error(f"Error parsing time: {time_str}")
            continue
        schedules.append({
            'base_time_24h': time_str,
            'base_time_12h': base_dt.strftime("%I:%M %p").lstrip("0"),
            'actual_time_24h': actual_time,
            'actual_time_12h': actual_dt.strftime("%I:%M %p").lstrip("0"),
            'portion': entry['portion'],
            'is_fixed': entry['is_fixed'],
            'graze': entry['graze'],
            'randomized': actual_time != time_str,
            'sort_key': actual_dt
        })

    schedules.sort(key=lambda x: x['sort_key'])
    return schedules


def get_next_feeding(schedules):
    """Get the next upcoming feeding from the schedule."""
    if not schedules:
        return None

    current_time = datetime.now().strftime("%H:%M")
    for sched in schedules:
        if sched['actual_time_24h'] > current_time:
            return sched

    # If no upcoming feeding today, return the first one (for tomorrow)
    return schedules[0]


def mark_past_feedings(schedules):
    """Mark which feedings are past and which is next."""
    current_time = datetime.now().strftime("%H:%M")
    found_next = False

    for sched in schedules:
        if sched['actual_time_24h'] < current_time:
            sched['is_past'] = True
            sched['is_next'] = False
        elif not found_next:
            sched['is_past'] = False
            sched['is_next'] = True
            found_next = True
        else:
            sched['is_past'] = False
            sched['is_next'] = False

    return schedules


def attach_outcomes(schedules, now=None):
    """Tag each slot with what actually happened: status is 'fed', 'late',
    'failed', 'missed', 'due', or 'upcoming'; late_min for caught-up feeds."""
    now = now or datetime.now()
    today = now.date()
    todays_events, log_lines = todays_records(today)
    for s in schedules:
        slot = datetime.combine(today, s['sort_key'].time())
        status, late_min = slot_outcome(s['actual_time_24h'], today, todays_events, log_lines)
        if status is None:
            if slot > now:
                status = 'upcoming'
            elif now - slot <= timedelta(minutes=DUE_GRACE_MIN):
                status = 'due'
            else:
                status = 'missed'
        until = grazing_until(s['actual_time_24h'], todays_events, now) if status == 'fed' else None
        if until:
            # Display only: to the feeder the slot is handled (slot_outcome)
            status = 'grazing'
            s['grazing_until_12h'] = until.strftime("%I:%M %p").lstrip("0")
        s['status'] = status
        s['late_min'] = late_min
        s['graze_trail'] = graze_trail(s, todays_events) if s.get('graze') else None
    return schedules


def graze_trail(slot, todays_events):
    """How far a graze slot has got: {'pulses', 'landed', 'cut_short'}, or
    None when it fed all at once instead (the midnight fallback).

    The pulse journal is the source: each pulse records itself as it lands,
    and the closing dispense says whether the graze finished or stopped
    early (partial). A failure after some pulses also stopped it early.
    """
    slot_events = [e for e in todays_events if e.get('scheduled_for') == slot['actual_time_24h']]
    pulses = [e for e in slot_events if e.get('event') == 'graze_pulse']
    dispense = next((e for e in slot_events if e.get('event') == 'dispense'), None)
    if dispense and dispense.get('source') != 'graze':
        return None
    # The journal knows how many pulses this graze was cut into, even if the
    # portion was edited since
    count = pulses[-1].get('of') if pulses else None
    count = count or graze_pulse_count(slot['portion'])
    landed = max((e.get('pulse', 0) for e in pulses), default=0)
    if dispense and dispense.get('partial'):
        landed = dispense.get('pulses', landed)
    elif dispense:
        landed = count
    failed = any(e.get('event') == 'failure' for e in slot_events)
    cut_short = bool(dispense and dispense.get('partial')) or (failed and 0 < landed < count)
    return {'pulses': count, 'landed': min(landed, count), 'cut_short': cut_short}


def _trail_px(slot):
    count = graze_pulse_count(slot['portion'])
    return TRAIL_LEAD_PX + TRAIL_GAP_PX * max(0, count - 2)


def grazing_until(actual_time, todays_events, now):
    """When a still-running graze for this slot will finish, or None.

    Running means pulses recorded but no dispense or failure yet. A graze a
    restart cut off gets its dispense from reconcile_grazes at startup, so it
    never reads as running; GRAZING_STALE_MIN past the expected end, a graze
    with no closing record stops showing as running whatever the journal says.
    """
    slot_events = [e for e in todays_events if e.get('scheduled_for') == actual_time]
    if any(e.get('event') in ('dispense', 'failure') for e in slot_events):
        return None
    pulses = [e for e in slot_events if e.get('event') == 'graze_pulse']
    if not pulses:
        return None
    try:
        until = datetime.fromisoformat(pulses[0]['ts']) + timedelta(seconds=GRAZE_SPAN_S)
    except (KeyError, ValueError):
        return None
    return until if now <= until + timedelta(minutes=GRAZING_STALE_MIN) else None


def todays_extras(today=None):
    """Today's manual and Porch feeds, oldest first, for Today's bar:
    [{'key', 'time_minutes', 'time_12h', 'portion', 'source'}]."""
    today = today or date.today()
    extras = []
    for e in events.read_all():
        ts = e.get('ts') or ''
        if (not ts.startswith(today.isoformat()) or e.get('event') != 'dispense'
                or e.get('source') not in EXTRA_SOURCES):
            continue
        try:
            at = datetime.fromisoformat(ts)
        except ValueError:  # a malformed line must not take the dashboard down
            continue
        extras.append({'key': f"extra-{ts}", 'time_minutes': at.hour * 60 + at.minute,
                       'time_12h': at.strftime("%I:%M %p").lstrip("0"),
                       'portion': e.get('portion') or 'small', 'source': e['source']})
    return extras


def build_timeline(schedules, extras=()):
    """Adds time_minutes to each schedule; returns (start, end, hour ticks).
    Extras (todays_extras) widen the range so a manual feed outside the
    scheduled hours still lands on the bar."""
    for s in schedules:
        h, m = s['actual_time_24h'].split(':')
        s['time_minutes'] = int(h) * 60 + int(m)

    if schedules:
        times_min = [s['time_minutes'] for s in schedules] + [x['time_minutes'] for x in extras]
        timeline_start = max(0, min(times_min) - 60)
        timeline_end = min(1439, max(times_min) + 60)
        # A graze's trail must end on the bar: its share of the bar's width
        # (on the narrowest phone) is a share of the minutes it spans. A late
        # graze may run the bar a little past midnight to fit.
        for s in schedules:
            if s.get('graze'):
                share = _trail_px(s) / NARROWEST_BAR_PX
                needed = (s['time_minutes'] - share * timeline_start) / (1 - share)
                timeline_end = max(timeline_end, round(needed) + 5)
    else:
        timeline_start, timeline_end = 360, 1320  # 6AM-10PM default

    timeline_hours = []
    first_hour = ((timeline_start // 60) + 1) * 60
    for mins in range(first_hour, timeline_end, 60):
        h = mins // 60 % 24  # a bar padded past midnight labels 12a, 2a
        pct = ((mins - timeline_start) / (timeline_end - timeline_start)) * 100
        show_label = (h % 2 == 0)
        # "6a", "10p": the 7-day chart's format, and "10AM" crowds a 360px bar
        label = f"{h % 12 or 12}{'a' if h < 12 else 'p'}" if show_label else None
        timeline_hours.append({'label': label, 'pct': pct})

    return timeline_start, timeline_end, timeline_hours


def hopper_card(consumption):
    status = hopper.status(consumption['daily_cups'] if consumption else None)
    try:
        status['last_refill_label'] = datetime.strptime(
            status['last_refill'], "%Y-%m-%d").strftime("%b %d")
    except (ValueError, TypeError):
        status['last_refill_label'] = status['last_refill']
    # A date is easier to act on than a percentage: when it runs dry, and
    # when to refill so the low-food alert never has to fire
    days_left = status.get('days_left')
    if days_left is not None:
        today = date.today()
        empty_on = today + timedelta(days=days_left)
        refill_by = today + timedelta(days=max(0, days_left - hopper.LOW_DAYS))
        status['empty_label'] = f"{empty_on:%b} {empty_on.day}"
        status['refill_by_label'] = ('today' if refill_by == today
                                     else f"{refill_by:%a}, {refill_by:%b} {refill_by.day}")
    # Preselect the "what was left" guess nearest the app's own level estimate,
    # so the form confirms its guess instead of quizzing from scratch
    level = status.get('level')
    status['refill_default'] = (
        min([0, 10, 25, 50, 75], key=lambda c: abs(c - level * 100))
        if level is not None else 10)
    return status


def index_context():
    """Everything index.html renders, minus app-level constants."""
    schedules = attach_outcomes(mark_past_feedings(read_schedules_with_details()))
    recent_activity = parse_recent_activity(days=1, limit=1)
    extras = todays_extras()
    timeline_start, timeline_end, timeline_hours = build_timeline(schedules, extras)

    weekly_stats = parse_weekly_stats()
    all_events = events.read_all()
    consumption = calculate_consumption_rate(
        weekly_stats, cups_per_lb=hopper.cups_per_lb(hopper.load_state()))

    return {
        'schedules': schedules,
        'next_feeding': get_next_feeding(schedules),
        'portion_info': {name: desc for name, (cycles, desc) in PORTION_SIZES.items()},
        'daily_total': calculate_daily_total(schedules),
        'all_fed_today': bool(schedules) and all(s.get('is_past') for s in schedules),
        'last_feeding': recent_activity[0] if recent_activity else None,
        'timeline_start': timeline_start,
        'timeline_end': timeline_end,
        'timeline_extras': extras,
        'timeline_hours': timeline_hours,
        'weekly_stats': weekly_stats,
        'rhythm': rhythm_week(
            all_events, date.today(), schedule_store.read_entries(),
            [s['actual_time_24h'] for s in schedules if s['status'] in ('upcoming', 'due')],
            pulse_count=graze_pulse_count),
        'week_summary': build_week_summary(weekly_stats),
        'consumption': consumption,
        'hopper': hopper_card(consumption),
        'manual': manual_limit.status(),
        # Latest finish if two grazes overlap: the bowl is busy until then
        'grazing_until': max((s for s in schedules if s['status'] == 'grazing'),
                             key=lambda s: s['sort_key'], default={}).get('grazing_until_12h'),
        'on_time': on_time_summary(all_events, date.today()),
    }
