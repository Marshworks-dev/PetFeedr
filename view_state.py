"""Display-ready dashboard state, served as JSON to the client-side renderer.

dashboard.index_context() gathers the data; this module turns it into the
exact strings and numbers each card shows, so the browser only places them.
One formatter for "1¼ cups" or "4:00 AM" means the UI can't drift between
a server-rendered and a client-rendered spelling.

Read-only, like dashboard.py.
"""

import hashlib
from datetime import datetime
from pathlib import Path

from DRV8825 import SIMULATION_MODE
from feeding_stats import PORTION_CUPS
from servo_controller import PORTION_SIZES, DEFAULT_PORTION
from flask import g, has_request_context, request

import camera
import dashboard
import graze_rules
import hopper
import passcode

APP_VERSION = "1.5.0"
STATIC_DIR = Path(__file__).parent / 'static'


def cups(value):
    """0.75 → "¾ cup", 1.25 → "1¼ cups" — kitchen fractions read faster than decimals."""
    quarters = int(round(float(value) * 4))
    whole, frac = divmod(quarters, 4)
    text = (str(whole) if whole else '') + ['', '¼', '½', '¾'][frac] or '0'
    return f"{text} cup{'' if quarters <= 4 and quarters != 0 else 's'}"


def compute_asset_version():
    """Short hash of the shipped front-end files. Embedded in the page and in
    every state payload: a long-lived tab that sees a different value knows a
    deploy happened and reloads itself once to pick up the new code."""
    digest = hashlib.sha256()
    for path in sorted(STATIC_DIR.rglob('*')):
        if path.is_file() and path.suffix in ('.js', '.css', '.json', '.svg'):
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


ASSET_VERSION = compute_asset_version()

_STATUS_TEXT = {
    'fed': 'Fed',
    'failed': 'Failed — check for a jam',
    'missed': 'Missed',
    'due': 'Due now',
}


def _status_text(slot):
    if slot['status'] == 'late':
        mins = f"{slot['late_min']} min " if slot.get('late_min') else ''
        return f"Caught up {mins}late"
    if slot['status'] == 'upcoming':
        return 'Next' if slot.get('is_next') else 'Upcoming'
    if slot['status'] == 'grazing':
        return f"Grazing · until {slot['grazing_until_12h']}"
    return _STATUS_TEXT[slot['status']]


def _trail_text(trail):
    """A graze's progress in words, for the dot's label: the crumbs are
    decoration to a screen reader."""
    if not trail:
        return ''
    pulses, landed = trail['pulses'], trail['landed']
    if trail['cut_short']:
        return f"graze stopped after {landed} of {pulses} pulses"
    if landed == pulses:
        return f"grazed in {pulses} pulses"
    if landed:
        return f"graze, {landed} of {pulses} pulses so far"
    return f"graze in {pulses} pulses"


def _status_badge(slot):
    if slot['status'] == 'late':
        return f"+{slot['late_min']}m" if slot.get('late_min') else '+late'
    return {'missed': '!', 'failed': '✕'}.get(slot['status'])


def _next_card(ctx):
    nxt = ctx['next_feeding']
    if not nxt:
        return None
    return {
        'time': nxt['actual_time_12h'],
        'time24': nxt['actual_time_24h'],
        'portion': nxt['portion'],
        'randomized': nxt['randomized'],
        'all_fed': ctx['all_fed_today'],
    }


def _timeline(ctx):
    start, end = ctx['timeline_start'], ctx['timeline_end']
    span = end - start
    slots = []
    for s in ctx['schedules']:
        slots.append({
            'key': s['base_time_24h'],
            'time': s['actual_time_12h'],
            'pct': round((s['time_minutes'] - start) / span * 100, 3) if span > 0 else 50,
            'portion': s['portion'],
            'is_fixed': s['is_fixed'],
            'is_past': s.get('is_past', False),
            'is_next': s.get('is_next', False),
            'status': s['status'],
            'status_text': _status_text(s),
            'badge': _status_badge(s),
            'trail': s.get('graze_trail'),
            'trail_text': _trail_text(s.get('graze_trail')),
            **_clips(s.get('clip_events', []), s['actual_time_12h']),
        })
    # Manual and Porch feeds: their own nodes, so a feed by hand has
    # somewhere to land on the bar
    extras = [{'key': x['key'], 'time': x['time_12h'], 'portion': x['portion'],
               'pct': round((x['time_minutes'] - start) / span * 100, 3) if span > 0 else 50,
               'label': f"{x['time_12h']}, extra {x['portion']} feed"
                        + (' from the porch' if x['source'] == 'porch' else ''),
               **_clips([{'ts': x['ts'], 'event': 'dispense'}], x['time_12h'])}
              for x in ctx.get('timeline_extras', [])]
    return {'start': start, 'end': end, 'hours': ctx['timeline_hours'], 'slots': slots,
            'extras': extras}


def _clips(feed_events, time_12h, now=None):
    """The saved clips of a feed's events, for the timeline:
    {'clips': [{'id', 'title', 'frames', 'landed'}], 'clip_note': str|None}.
    The note says why a feed that happened has no clip to show."""
    if not camera.enabled() or not feed_events:
        return {'clips': [], 'clip_note': None}
    now = now or datetime.now()
    clips, newest = [], None
    for e in feed_events:
        try:
            clip_id = camera.clip_id_for(e['ts'])
            at = datetime.fromisoformat(e['ts'])
        except (KeyError, TypeError, ValueError):
            continue
        newest = max(newest, at) if newest else at
        info = camera.clip_info(clip_id)
        if not info:
            continue
        title = f"{time_12h} feed"
        if e.get('event') == 'graze_pulse' and e.get('of'):
            title = f"{time_12h} graze, pulse {e.get('pulse')} of {e['of']}"
        clips.append({'id': clip_id, 'title': title, **info})
    note = None
    if newest and (now - newest).total_seconds() <= camera.CLIP_AFTER_S + 2 * camera.WATCH_EVERY_S + 5:
        note = 'Saving the clip…'   # its window hasn't closed yet
    elif not clips:
        note = 'No clip for this feed'
    elif len(clips) < len(feed_events):
        note = 'Some pulses have no clip'
    return {'clips': clips, 'clip_note': note}


def _schedule_rows(ctx):
    """The editor lists the times as set, not today's randomized draw."""
    rows = sorted(ctx['schedules'], key=lambda s: s['base_time_24h'])
    entries = [{'time': s['base_time_24h'], 'is_fixed': s['is_fixed'], 'graze': s['graze']}
               for s in rows]
    # Why each row can't graze where it is (None when it can). On a graze
    # saved before the rules it explains why the row may feed all at once
    return [{'base_time': s['base_time_24h'], 'time': s['base_time_12h'],
             'portion': s['portion'], 'is_fixed': s['is_fixed'], 'graze': s['graze'],
             'graze_problem': graze_rules.graze_problem(e, entries)}
            for s, e in zip(rows, entries)]


def _feed_card(ctx):
    manual = ctx['manual']
    no_extras = not manual['fits']['small']
    return {
        'fits': manual['fits'],
        'no_extras': no_extras,
        'extras_label': 'No extras left today' if no_extras
                        else f"{cups(manual['remaining'])} of extras left today",
        'allowance_title': f"Manual feeds are capped at {cups(manual['allowance'])} a day",
        'grazing_label': f"Grazing now, done at {ctx['grazing_until']}" if ctx['grazing_until'] else '',
    }


def _stats_card(ctx):
    rhythm = ctx['rhythm']
    days = []
    for day in rhythm['days']:
        fed = [m for m in day['marks'] if m['kind'] in ('fed', 'manual', 'late', 'grazed')]
        notes = ''.join(f"; {m['label']}" for m in day['marks'] if m['kind'] not in ('fed', 'upcoming'))
        days.append({
            'date': day['date'],
            'day_label': day['day_label'],
            'is_today': day['is_today'],
            'cups_short': cups(day['total_cups']).split(' ')[0],
            'aria': f"{day['day_label']}, {cups(day['total_cups'])}, {len(fed)} feedings{notes}",
            'marks': day['marks'],
        })

    on_time = ctx['on_time']
    if on_time['streak_days'] >= 2:
        streak = {'hot': True, 'text': f"{on_time['streak_days']}-day on-time streak",
                  'title': f"{on_time['week_on_time']} of {on_time['week_total']} scheduled feeds on time this week"}
    elif on_time['week_total']:
        streak = {'hot': False, 'text': f"{on_time['week_on_time']}/{on_time['week_total']} on time", 'title': ''}
    else:
        streak = None

    consumption = ctx['consumption']
    return {
        'has_data': any(d['marks'] for d in rhythm['days']),
        'days': days,
        'guides': rhythm['guides'],
        'streak': streak,
        'week_summary': ctx['week_summary'],
        'consumption_label': (f"≈ {cups(consumption['daily_cups'])}/day · {consumption['monthly_lbs']:g} lbs/mo"
                              if consumption else None),
    }


def _hopper_card(ctx):
    h = ctx['hopper']
    card = {'learning': h['learning'], 'refill_default': h['refill_default']}
    if h['learning']:
        n = h['cups_since_refill']
        card['learning_amount'] = f"{n} cup{'s' if n != 1 else ''}"
        card['last_refill_label'] = h['last_refill_label']
        return card

    pct = int(round(h['level'] * 100))
    capacity = f"~{h['capacity']} cups"
    if h['capacity_lbs']:
        capacity = f"~{h['capacity_lbs']:g} lb ({int(round(h['capacity_lbs'] * 16))} oz), " + capacity
    card.update({
        'level': h['level'],
        'pct': pct,
        'low': h['level'] <= hopper.LOW_LEVEL,
        'headline': f"~{pct}% full",
        'lbs_label': f"· ~{h['lbs_left']:g} lb" if h['lbs_left'] is not None else None,
        'refill_by': f"Refill by {h['refill_by_label']}" if h.get('refill_by_label') else None,
        'refill_urgent': h.get('refill_by_label') == 'today',
        'days_label': (f"~{h['days_left']} day{'s' if h['days_left'] != 1 else ''} of food · "
                       f"empty around {h['empty_label']}") if h.get('refill_by_label') else None,
        'capacity_label': f"Holds {capacity}",
    })
    return card


def _camera_card(now=None):
    """Latest frame and when it was taken, for the picture on the Feed card.
    None means no picture: no camera, or no frame yet."""
    now = now or datetime.now()
    cam = camera.status(now)
    if cam is None or not cam['frame']:
        return None
    taken = cam['taken']
    when = taken.strftime("%I:%M %p").lstrip("0")
    if taken.date() != now.date():
        when = f"{taken:%b} {taken.day}, {when}"
    return {
        'online': cam['online'],
        # ?t= only makes the browser fetch again when a new frame lands; the
        # server ignores it
        'src': f"/camera/latest.jpg?t={cam['frame']}",
        'label': when if cam['online'] else f"Camera offline · last frame {when}",
        'alt': f"Camera view of the feeding bowl, {when}",
    }


def _lock_state():
    """Whether a passcode exists and whether this device is unlocked. Only
    what the page needs to draw the lock; the server checks every write
    itself."""
    is_set = passcode.is_set()
    unlocked = False
    if is_set and has_request_context():
        # A route that just unlocked or locked says so: its cookie is on the
        # way out with this very response
        unlocked = g.get('unlocked')
        if unlocked is None:
            unlocked = passcode.unlocked(request.cookies.get(passcode.COOKIE))
    return {'set': is_set, 'unlocked': bool(unlocked)}


def build():
    """Everything the dashboard shows, as JSON-safe display values."""
    ctx = dashboard.index_context()
    last = ctx['last_feeding']
    return {
        'asset_version': ASSET_VERSION,
        'app': {'version': APP_VERSION, 'simulation': SIMULATION_MODE},
        'portions': [{'name': name, 'letter': name[0].upper(), 'label': name.capitalize(),
                      'desc': desc, 'cups': PORTION_CUPS[name]}
                     for name, (cycles, desc) in PORTION_SIZES.items()],
        'default_portion': DEFAULT_PORTION,
        'next': _next_card(ctx),
        'timeline': _timeline(ctx),
        'daily_total_label': cups(ctx['daily_total']),
        'schedule': _schedule_rows(ctx),
        'feed': _feed_card(ctx),
        'stats': _stats_card(ctx),
        'hopper': _hopper_card(ctx),
        'camera': _camera_card(),
        'lock': _lock_state(),
        'last_fed': f"{last['date']} at {last['time']}" if last else None,
    }
