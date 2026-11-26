"""Bowl history: one day of saved pictures, feeds and bowl readings, for the
timeline in the full-picture dialog.

Read-only. It joins three files the camera and the feeder already write:
the 5-minute frames (camera.py), the event journal (feeding_events.jsonl)
and bowl watch's readings (data/bowl_readings.jsonl). Nothing from those
files is passed through whole: each field sent to the page is picked and
checked here, and a line that doesn't fit is skipped, never a 500.

Off the feed path like the rest of the camera code: it takes no lock and
imports nothing from the feed side (a test pins this).
"""

import json
import re
from datetime import date, datetime, timedelta

import bowl_watch
import camera
import events
from DRV8825 import SIMULATION_MODE

DAYS_SHOWN = 7

# The journal events that go on the bar
_FEED_EVENTS = ('dispense', 'graze_pulse', 'failure', 'missed')
_VIEWS = {'clear', 'dark', 'blocked', 'unreadable', 'unknown', 'offline'}
_BOWLS = {'empty', 'food', 'unknown'}
# Where bowl watch says a before reading's picture came from
# ASCII only: \d would also take other scripts' digits
_CLIP_FRAME_RE = re.compile(r'^clips/([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{6})/([0-9]{3})\.jpg$')
_DATE_RE = re.compile(r'^[0-9]{4}-[0-9]{2}-[0-9]{2}$')
_TS_RE = re.compile(r'^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}$')


def parse_day(date_str, today=None):
    """The date for a YYYY-MM-DD in the last DAYS_SHOWN days, else None."""
    if not isinstance(date_str, str) or not _DATE_RE.fullmatch(date_str):
        return None
    try:
        day = date.fromisoformat(date_str)
    except ValueError:
        return None
    today = today or date.today()
    if not today - timedelta(days=DAYS_SHOWN - 1) <= day <= today:
        return None
    return day


def _frames(day):
    prefix = day.isoformat() + 'T'
    kept = []
    for name in camera.frame_names():
        if not name.startswith(prefix):
            continue
        try:
            camera.frame_time(name)
        except ValueError:
            continue   # shaped right but not a real time
        kept.append(name)
    return kept


def _picture(kind, frame):
    """Where a reading's picture is, as {'clip', 'index'} or {'frame'}, or None."""
    if not isinstance(frame, str):
        return None
    if kind == 'before':
        m = _CLIP_FRAME_RE.fullmatch(frame)
        return {'clip': m.group(1), 'index': int(m.group(2))} if m else None
    return {'frame': frame} if camera._NAME_RE.fullmatch(frame) else None


def _reading(line):
    view = line.get('view')
    bowl = line.get('bowl')
    return {'view': view if view in _VIEWS else 'unknown',
            'bowl': bowl if bowl in _BOWLS else 'unknown'}


def _readings(day):
    """(per-frame readings that day, before/after readings by feed id,
    blind-camera alerts that day). The whole file is read: 14 days at a
    line per frame is a few thousand lines."""
    prefix = day.isoformat()
    frames, by_feed, alerts = [], {}, []
    try:
        with open(bowl_watch.READINGS_FILE, encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
    except FileNotFoundError:
        return frames, by_feed, alerts
    for raw in lines:
        try:
            line = json.loads(raw)
        except ValueError:
            continue   # a torn last line from a power cut
        if not isinstance(line, dict) or not isinstance(line.get('ts'), str):
            continue
        kind = line.get('kind')
        same_day = line['ts'].startswith(prefix)
        if kind == 'frame' and same_day:
            picture = _picture('frame', line.get('frame'))
            if picture:
                frames.append({**picture, **_reading(line)})
        elif kind in ('before', 'after') and isinstance(line.get('feed'), str):
            # Matched by feed, not date: a late feed's after reading can land
            # the next day. The last line for a feed wins
            by_feed.setdefault(line['feed'], {})[kind] = {
                **_reading(line), 'picture': _picture(kind, line.get('frame'))}
        elif kind == 'camera' and same_day and line.get('alert') == 'sent' and _TS_RE.fullmatch(line['ts']):
            alerts.append({'ts': line['ts'], 'view': _reading(line)['view']})
    return frames, by_feed, alerts


def _text(value):
    return value if isinstance(value, str) and len(value) <= 40 else None


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _clip_for(event):
    """{'id', 'frames', 'landed'} of the event's saved clip, for the clip
    player, or None."""
    if not camera.clip_window(event):
        return None
    clip_id = camera.clip_id_for(event['ts'])
    info = camera.clip_info(clip_id)
    return {'id': clip_id, **info} if info else None


def _feeds(day, by_feed):
    prefix = day.isoformat()
    feeds = []
    for event in events.read_all():
        if not isinstance(event, dict) or event.get('event') not in _FEED_EVENTS:
            continue
        ts = event.get('ts')
        if not isinstance(ts, str) or not ts.startswith(prefix):
            continue
        # A dev box's simulated feeds never show on a real feeder's history
        if event.get('sim') and not SIMULATION_MODE:
            continue
        try:
            datetime.fromisoformat(ts)
        except ValueError:
            continue
        try:
            feed_id = camera.clip_id_for(ts)
        except ValueError:
            continue
        readings = by_feed.get(feed_id, {})
        feeds.append({
            'ts': ts,
            'event': event['event'],
            'portion': _text(event.get('portion')),
            'source': _text(event.get('source')),
            'scheduled_for': _text(event.get('scheduled_for')),
            'pulse': _count(event.get('pulse')),
            'of': _count(event.get('of')),
            'partial': bool(event.get('partial')),
            'clip': _clip_for(event),
            'before': readings.get('before'),
            'after': readings.get('after'),
        })
    return feeds


def day_view(day):
    """Everything the timeline needs for one day, oldest first throughout."""
    frame_readings, by_feed, alerts = _readings(day)
    return {
        'date': day.isoformat(),
        'frames': _frames(day),
        'feeds': _feeds(day, by_feed),
        'frame_readings': frame_readings,
        'camera_alerts': alerts,
    }
