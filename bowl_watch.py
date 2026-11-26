"""Bowl watch: what the bowl camera can actually tell us, sent by Pushover.

A review of real feed clips showed the cats get their heads over the bowl
within a second of food landing, so the camera can't see food land or
measure a portion. What it can see:

1. That it can't see. A frame that is near-black (dark) or mostly covered
   with no bowl in it (blocked) for an hour straight, or no frame at all
   for an hour, sends one alert; a clear frame afterwards sends one "back"
   message.
2. Whether a meal was eaten. Each dispense gets a "before" reading from the
   clip frames just before the food lands, and an "after" reading from the
   first readable 5-minute frame 30-60 min later: empty, food or unknown.
   Food after 30 min means the meal wasn't eaten; food before a feed means
   the last one wasn't finished. Unknown never alerts.

Every reading goes to data/bowl_readings.jsonl (14 days) with the frame it
came from, so any alert can be checked against its picture. Meal alerts are
off unless PETFEEDR_MEAL_ALERTS=on: they ship quiet, recording only, until a
week of readings has been checked against the pictures.

Like camera.py, this is off the feed path: it reads saved frames and clips
handed over by the camera threads, never takes the feeder's state lock, and
nothing it does can raise into the camera (every entry point logs instead).

Pictures are decoded by the ffmpeg already on the Pi into a small greyscale
thumbnail; the arithmetic is plain Python, so the Pi needs no image library.
State (an alert already sent, meals waiting for their 30-min look) lives in
memory: a restart forgets it, which at worst repeats one alert or skips one
reading.
"""

import json
import logging
import math
import os
import subprocess
import threading
from datetime import datetime, timedelta

import notify
from atomicfile import write_atomic

log = logging.getLogger('petfeedr')

READINGS_FILE = os.path.join('data', 'bowl_readings.jsonl')
KEEP_DAYS = 14

# The region of the 1280x720 frame the bowl sits in, decoded at half scale.
# The thresholds below were measured at that scale on real frames.
REGION_X, REGION_Y, REGION_SIZE = 480, 0, 640
SIZE = REGION_SIZE // 2   # the thumbnail is SIZE x SIZE grey levels

# Near-black pixels: the feeder's light is meant to stay on, so a dark frame
# means a covered lens or a dead light, not night
DARK_LEVEL = 25
DARK_SHARE = 0.95      # this much near-black: 'dark'
BLOCKED_SHARE = 0.45   # this much near-black and no bowl: 'blocked'

# Where to look for the bowl's rim: a bright ring with darker floor outside.
# It gets nudged by the cats, so it is searched for, never assumed
BOWL_X = (110, 222)
BOWL_Y = (96, 208)
BOWL_R = (84, 116)
OUTSIDE = 1.15            # the floor ring sits this far out from the rim
RIM_STEP = 25             # rim brighter than the floor by this much counts as seen
RIM_SCORE_MIN = 75        # empty bowls scored 84-109, a cat over it 68 or less
RIM_SEEN_MIN = 0.65       # share of the rim seen: bowls 0.71-0.98, cats 0.52 or less
# The feeder's pipe enters the bowl from below; the rim there is skipped
PIPE_SKIP = math.radians(35)

# Kibble: specks much darker than their 9x9 surroundings on the bowl floor.
# The empty bowl's stamped pattern gives 7-9 per thousand floor pixels, a
# meal 16-32, a few stray kibble about 12. A few kibble count as eaten
FLOOR_SHARE = 0.62
SPECK_BLUR = 4
SPECK_STEP = 45
FOOD_PER_MILLE = 14

BLIND_ALERT_AFTER_S = 3600
AFTER_FROM_S = 30 * 60
AFTER_UNTIL_S = 60 * 60
# Clip frames tried for the "before" reading, newest first: a cat waiting
# beside the bowl can cover the last one or two
BEFORE_TRIES = 6


class ReadError(Exception):
    """A frame that couldn't be decoded."""


def meal_alerts_on():
    # Off unless switched on: a missing or misspelt setting stays quiet
    return os.environ.get('PETFEEDR_MEAL_ALERTS', '').strip().lower() == 'on'


# ----- Reading one picture -----

def decode(jpeg):
    """The bowl region of a JPEG frame as SIZE*SIZE grey bytes."""
    argv = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin',
            '-f', 'mjpeg', '-i', '-',
            '-vf', f'crop={REGION_SIZE}:{REGION_SIZE}:{REGION_X}:{REGION_Y},scale={SIZE}:{SIZE}',
            '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'gray', '-']
    try:
        out = subprocess.run(argv, input=jpeg, capture_output=True, timeout=20).stdout
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ReadError(f"ffmpeg couldn't run: {e}") from e
    if len(out) != SIZE * SIZE:
        raise ReadError("ffmpeg gave no picture")
    return out


def _rim_offsets(r):
    """(dx, dy) pairs around the rim at radius r and just outside it."""
    inner, outer = [], []
    for i in range(72):
        a = 2 * math.pi * i / 72
        # Angles pointing down (+y) run into the pipe
        if abs((a - math.pi / 2 + math.pi) % (2 * math.pi) - math.pi) < PIPE_SKIP:
            continue
        inner.append((round(r * math.cos(a)), round(r * math.sin(a))))
        outer.append((round(r * OUTSIDE * math.cos(a)), round(r * OUTSIDE * math.sin(a))))
    return inner, outer


_RIMS = {r: _rim_offsets(r) for r in range(BOWL_R[0] - 8, BOWL_R[1] + 9)}


def _rim(px, cx, cy, r):
    """(score, share seen) for a rim centred at cx, cy with radius r."""
    inner, outer = _RIMS[r]
    last = SIZE - 1
    total = seen = 0
    for (ix, iy), (ox, oy) in zip(inner, outer):
        a = px[min(max(cy + iy, 0), last) * SIZE + min(max(cx + ix, 0), last)]
        b = px[min(max(cy + oy, 0), last) * SIZE + min(max(cx + ox, 0), last)]
        total += a - b
        seen += a - b > RIM_STEP
    return total / len(inner), seen / len(inner)


def find_bowl(px):
    """(cx, cy, r) of the bowl in a thumbnail, or None when it can't be seen
    (a cat over it, a covered lens, or knocked out of view)."""
    # Coarse then fine: the Pi 3 is slow, and the rim is a smooth target
    best = (-1e9, None)
    for r in range(BOWL_R[0], BOWL_R[1] + 1, 8):
        for cy in range(BOWL_Y[0], BOWL_Y[1] + 1, 8):
            for cx in range(BOWL_X[0], BOWL_X[1] + 1, 8):
                score = _rim(px, cx, cy, r)[0]
                if score > best[0]:
                    best = (score, (cx, cy, r))
    _, (bx, by, br) = best
    for r in range(br - 6, br + 7, 2):
        for cy in range(by - 6, by + 7, 2):
            for cx in range(bx - 6, bx + 7, 2):
                score = _rim(px, cx, cy, r)[0]
                if score > best[0]:
                    best = (score, (cx, cy, r))
    score, (cx, cy, r) = best
    if score < RIM_SCORE_MIN or _rim(px, cx, cy, r)[1] < RIM_SEEN_MIN:
        return None
    return cx, cy, r


def food_specks(px, bowl):
    """Dark specks per thousand pixels of the bowl floor (pipe left out)."""
    cx, cy, r = bowl
    floor_r = FLOOR_SHARE * r
    k = SPECK_BLUR
    x0, x1 = max(int(cx - floor_r) - k, 0), min(int(cx + floor_r) + k + 1, SIZE)
    y0, y1 = max(int(cy - floor_r) - k, 0), min(int(cy + floor_r) + k + 1, SIZE)
    w = x1 - x0
    # Summed-area table over the floor's box, for the 9x9 local average
    table = [[0] * (w + 1) for _ in range(y1 - y0 + 1)]
    for j in range(y1 - y0):
        row = px[(y0 + j) * SIZE + x0:(y0 + j) * SIZE + x1]
        run = 0
        above, here = table[j], table[j + 1]
        for i in range(w):
            run += row[i]
            here[i + 1] = above[i + 1] + run
    area = specks = 0
    for y in range(y0 + k, y1 - k):
        for x in range(x0 + k, x1 - k):
            if (x - cx) ** 2 + (y - cy) ** 2 >= floor_r ** 2:
                continue
            if abs(x - cx) < 0.32 * r and y > cy - 0.05 * r:
                continue   # the pipe
            a, b = x - x0 - k, y - y0 - k
            c, d = a + 2 * k + 1, b + 2 * k + 1
            local = (table[d][c] - table[b][c] - table[d][a] + table[b][a]) / (2 * k + 1) ** 2
            area += 1
            specks += local - px[y * SIZE + x] > SPECK_STEP
    return 1000 * specks / area if area else 0.0


def read_thumbnail(px):
    """{'view': clear|dark|blocked, 'bowl': empty|food|unknown, 'specks'}."""
    dark = sum(1 for p in px if p < DARK_LEVEL) / len(px)
    if dark >= DARK_SHARE:
        return {'view': 'dark', 'bowl': 'unknown', 'specks': None}
    bowl = find_bowl(px)
    if bowl is None:
        view = 'blocked' if dark >= BLOCKED_SHARE else 'clear'
        return {'view': view, 'bowl': 'unknown', 'specks': None}
    specks = food_specks(px, bowl)
    return {'view': 'clear', 'bowl': 'food' if specks >= FOOD_PER_MILLE else 'empty',
            'specks': round(specks, 1)}


def read_jpeg(jpeg):
    return read_thumbnail(decode(jpeg))


# ----- Watching over time -----

def _hhmm(epoch):
    return datetime.fromtimestamp(epoch).strftime('%H:%M')


class Watch:
    """The blind-camera alert and the meal readings. Called from the camera's
    two threads, so its state sits behind its own lock; pictures are read
    outside it."""

    def __init__(self, read=read_jpeg, send=notify.send, clock=None):
        self._read = read
        self._send = send
        self._lock = threading.Lock()
        self._blind_since = None     # epoch of the first bad frame in a row
        self._blind_alerted = False
        self._waiting = []           # (landed epoch, clip id) awaiting the 30-min look
        self._last_after = None      # the most recent after-reading's bowl
        self._pruned_on = None

    # The camera calls these. None of them raises.

    def frame_saved(self, name, jpeg, now):
        try:
            reading = self._safe_read(jpeg)
            self._record({'kind': 'frame', 'frame': name, **reading}, now)
            self._blind(reading['view'], now)
            self._after(name, reading, now)
            self._prune(now)
        except Exception:
            log.exception("Bowl watch failed on a frame — feeding is unaffected")

    def frame_missing(self, now):
        try:
            self._blind('offline', now)
            self._after(None, None, now)
        except Exception:
            log.exception("Bowl watch failed — feeding is unaffected")

    def clip_saved(self, clip_id, frames, landed_at):
        """A dispense's clip: frames are (epoch, jpeg), oldest first."""
        try:
            self._before(clip_id, frames, landed_at)
        except Exception:
            log.exception("Bowl watch failed on a clip — feeding is unaffected")

    # Internals

    def _safe_read(self, jpeg):
        try:
            return self._read(jpeg)
        except ReadError as e:
            log.warning(f"Bowl watch couldn't read a frame: {e}")
            return {'view': 'unreadable', 'bowl': 'unknown', 'specks': None}

    def _blind(self, view, now):
        if view in ('dark', 'blocked', 'offline'):
            with self._lock:
                self._blind_since = self._blind_since or now
                due = not self._blind_alerted and now - self._blind_since >= BLIND_ALERT_AFTER_S
                if due:
                    self._blind_alerted = True
            if due:
                why = {'dark': "every picture has been dark",
                       'blocked': "something has been covering the lens",
                       'offline': "no picture has come from it"}[view]
                message = f"The bowl camera can't see: {why} for an hour. Feeding carries on."
                self._send(message, title='PetFeedr camera')
                self._record({'kind': 'camera', 'view': view, 'alert': 'sent'}, now)
            return
        if view == 'unreadable':
            return   # a decode hiccup says nothing either way
        with self._lock:
            was_alerted = self._blind_alerted
            self._blind_since = None
            self._blind_alerted = False
        if was_alerted:
            self._send("The bowl camera can see the bowl again.", title='PetFeedr camera')
            self._record({'kind': 'camera', 'view': view, 'alert': 'sent'}, now)

    def _before(self, clip_id, frames, landed_at):
        before = [(at, jpeg) for at, jpeg in frames if at < landed_at]
        reading, used = {'view': 'unknown', 'bowl': 'unknown', 'specks': None}, None
        for i in range(len(before) - 1, max(len(before) - 1 - BEFORE_TRIES, -1), -1):
            reading = self._safe_read(before[i][1])
            if reading['bowl'] != 'unknown':
                used = i
                break
        with self._lock:
            covered = self._last_after == 'food'
            self._waiting.append((landed_at, clip_id))
        line = {'kind': 'before', 'feed': clip_id,
                'frame': None if used is None else f"clips/{clip_id}/{used:03d}.jpg", **reading}
        if reading['bowl'] == 'food':
            if covered:
                line['alert'] = 'covered by the 30-min alert'
            else:
                line['alert'] = self._meal_alert(
                    f"Food was still in the bowl from the last meal when the "
                    f"{_hhmm(landed_at)} feeding came.")
        self._record(line, landed_at)

    def _after(self, name, reading, now):
        with self._lock:
            due = [w for w in self._waiting if now - w[0] >= AFTER_FROM_S]
            done = []
            for landed_at, clip_id in due:
                if reading and reading['bowl'] != 'unknown':
                    done.append((landed_at, clip_id, reading))
                elif now - landed_at >= AFTER_UNTIL_S:
                    done.append((landed_at, clip_id, None))
            for landed_at, clip_id, _ in done:
                self._waiting.remove((landed_at, clip_id))
            if done:
                self._last_after = done[-1][2]['bowl'] if done[-1][2] else 'unknown'
        for landed_at, clip_id, got in done:
            got = got or {'view': 'unknown', 'bowl': 'unknown', 'specks': None}
            line = {'kind': 'after', 'feed': clip_id, 'frame': name if got['bowl'] != 'unknown' else None, **got}
            if got['bowl'] == 'food':
                line['alert'] = self._meal_alert(
                    f"The {_hhmm(landed_at)} meal hasn't been eaten: food is still in the bowl 30 minutes later.")
            self._record(line, now)

    def _meal_alert(self, message):
        if not meal_alerts_on():
            return 'held (meal alerts off)'
        self._send(message, title='PetFeedr bowl')
        return 'sent'

    def _record(self, fields, at):
        line = {'ts': datetime.fromtimestamp(at).isoformat(timespec='seconds'), **fields}
        try:
            os.makedirs(os.path.dirname(READINGS_FILE), exist_ok=True)
            with open(READINGS_FILE, 'a') as f:
                f.write(json.dumps(line) + '\n')
        except OSError as e:
            log.warning(f"Bowl reading not saved: {e}")

    def _prune(self, now):
        today = datetime.fromtimestamp(now).date()
        if self._pruned_on == today:
            return
        self._pruned_on = today
        cutoff = (datetime.fromtimestamp(now) - timedelta(days=KEEP_DAYS)).isoformat(timespec='seconds')
        try:
            with open(READINGS_FILE) as f:
                lines = f.readlines()
        except FileNotFoundError:
            return
        kept = []
        for line in lines:
            try:
                if json.loads(line)['ts'] >= cutoff:
                    kept.append(line)
            except (ValueError, KeyError, TypeError):
                continue
        if len(kept) != len(lines):
            write_atomic(READINGS_FILE, ''.join(kept))


WATCH = Watch()
