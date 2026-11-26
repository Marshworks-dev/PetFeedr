"""Bowl camera: a frame every few minutes, and a short clip of every feed.

A USB webcam over the bowl is a sensor the rest of the feeder lacks —
"Feeding completed" only proves the motor turned. Frames land in
data/frames/, named by local time. The last minute of the stream is also
kept in memory, and when the event journal records a dispense or a graze
pulse, the frames from just before it to just after are saved as a clip in
data/clips/, named by the event's time, so the dashboard can show food
landing.

The webcam is started once and left streaming; each saved frame is the
newest one off that stream. Measured on the Pi: this webcam is steady while
it streams and jams when a stream is started after an earlier one stopped
(about 3 shots in 10 were lost to that), so the stream is only restarted
when it has died or gone quiet.

Deliberately apart from the feed path: nothing here takes the feeder's state
lock and no feed code calls in, so a flaky webcam can never delay or break
a meal.
"""

import collections
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime, timedelta

import bowl_watch
from DRV8825 import on_raspberry_pi
from events import EVENTS_FILE

log = logging.getLogger('petfeedr')

FRAMES_DIR = os.path.join('data', 'frames')
DEVICE = os.environ.get('PETFEEDR_CAMERA', '/dev/video0')
INTERVAL_S = 300
# Shots land this far ahead of each 5-minute mark, so an on-the-dot feed
# gets a "before" frame
LEAD_S = 30
KEEP_DAYS = 14
# 5 a second is the webcam's slowest rate: plenty for a still every 5
# minutes, and passing it through costs under 1% of one core on the Pi 3
STREAM_FPS = 5
# The webcam opens dark and takes a moment to settle its exposure
WARMUP_FRAMES = 20
# How long a new stream gets to deliver its first settled frame
START_WAIT_S = 15
# A stream whose newest frame is older than this has stalled
STALL_AFTER_S = 20
# A stream that won't start is tried once more after this long. When the
# webcam jams, the Pi's USB reset brings it back within about 6 seconds
RETRY_AFTER_S = 8
# Two missed shots and the dashboard calls the camera offline
STALE_AFTER_S = 2 * INTERVAL_S + 60

CLIPS_DIR = os.path.join('data', 'clips')
# A clip runs from this long before a dispense starts to this long after it ends
CLIP_BEFORE_S = 10
CLIP_AFTER_S = 30
# 2 a second from the 5 a second stream: food landing reads, at about 5 MB a clip
CLIP_STEP_S = 0.5
# Memory holds enough stream for a clip's whole window plus the watcher's lag:
# about 8 MB of frames
BUFFER_S = 60
# How often the watcher reads the journal for new events
WATCH_EVERY_S = 1
# Only these journal events get a clip. A graze's closing dispense is left
# out: each of its pulses already has one, and it lands in the same second
# as the last pulse
_CLIP_EVENTS = ('dispense', 'graze_pulse')
CLIP_ID_FORMAT = '%Y-%m-%dT%H%M%S'
_CLIP_ID_RE = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{6}$')

# Sortable, so the newest frame is the last name and pruning is a comparison
NAME_FORMAT = '%Y-%m-%dT%H%M%S.jpg'
_NAME_RE = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{6}\.jpg$')
_JPEG_MAGIC = b'\xff\xd8'
# One frame's end marker straight into the next frame's start
_FRAME_BREAK = b'\xff\xd9\xff\xd8'
# Real frames are about 70 KB. Anything far outside that is a broken stream,
# not a picture
_MIN_FRAME_BYTES = 2048
_MAX_FRAME_BYTES = 4 * 1024 * 1024

_pause = time.sleep  # names of their own, so tests can skip the waits
_now = time.monotonic
_wall = time.time  # clips are matched to journal times, which are wall-clock


class CameraError(Exception):
    """No frame to save; the message is the short reason."""


def enabled():
    """Only a Pi has the camera. PETFEEDR_CAMERA names a device to try anywhere."""
    return 'PETFEEDR_CAMERA' in os.environ or on_raspberry_pi()


def _stream_argv():
    # A list, never a shell string. The webcam's own MJPEG frames are passed
    # straight through (-c copy), nothing is decoded; mjpeg2jpeg adds the
    # tables webcams leave out, so each frame is a complete JPEG
    return ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin',
            '-f', 'v4l2', '-input_format', 'mjpeg', '-video_size', '1280x720',
            '-framerate', str(STREAM_FPS), '-i', DEVICE,
            '-c', 'copy', '-bsf:v', 'mjpeg2jpeg', '-f', 'mjpeg', '-']


def _spawn():
    # Unbuffered: frames are read as they arrive. ffmpeg's own errors go to
    # the service journal
    return subprocess.Popen(_stream_argv(), stdout=subprocess.PIPE, bufsize=0)


class Stream:
    """The one long-running capture process and the newest frame off it."""

    def __init__(self):
        self._proc = _spawn()
        # Guards the newest frame only; never held together with any other lock
        self._lock = threading.Lock()
        self._frame = None
        self._at = 0.0
        self._wall_at = 0.0
        self._count = 0
        # (wall time, frame) every CLIP_STEP_S, oldest first, for clips
        self._recent = collections.deque(maxlen=int(BUFFER_S / CLIP_STEP_S))
        self._ready = threading.Event()
        threading.Thread(target=self._read, name='camera-reader', daemon=True).start()

    def _read(self):
        buf = b''
        try:
            while True:
                chunk = self._proc.stdout.read(65536)
                if not chunk:
                    break
                buf += chunk
                while True:
                    cut = buf.find(_FRAME_BREAK)
                    if cut < 0:
                        break
                    self._keep(buf[:cut + 2])
                    buf = buf[cut + 2:]
                if len(buf) > _MAX_FRAME_BYTES:
                    buf = b''
            if buf.endswith(_FRAME_BREAK[:2]):
                self._keep(buf)
        except (OSError, ValueError):
            pass  # the pipe went away under us: same as the stream ending
        finally:
            self._ready.set()  # nobody waits out the timeout on a dead stream

    def _keep(self, frame):
        if not frame.startswith(_JPEG_MAGIC) or len(frame) < _MIN_FRAME_BYTES:
            return
        with self._lock:
            self._frame = frame
            self._at = _now()
            self._wall_at = _wall()
            self._count += 1
            settled = self._count >= WARMUP_FRAMES
            if settled and (not self._recent or self._wall_at - self._recent[-1][0] >= CLIP_STEP_S):
                self._recent.append((self._wall_at, frame))
        if settled:
            self._ready.set()

    def latest(self):
        """The newest settled frame, or None while warming up or once stalled."""
        with self._lock:
            if self._count < WARMUP_FRAMES or _now() - self._at > STALL_AFTER_S:
                return None
            return self._frame

    def latest_with_time(self):
        """The newest settled frame and its wall-clock time, or None."""
        with self._lock:
            if self._count < WARMUP_FRAMES or _now() - self._at > STALL_AFTER_S:
                return None
            return self._frame, self._wall_at

    def frames_between(self, start, end):
        """Buffered (wall time, frame) pairs from start to end, oldest first."""
        with self._lock:
            return [(at, f) for at, f in self._recent if start <= at <= end]

    def wait_for_frame(self):
        self._ready.wait(START_WAIT_S)
        return self.latest()

    def stop(self):
        # Killed, never left behind: a stray ffmpeg would hold the webcam
        self._proc.kill()
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


_stream = None


def _stop_stream():
    global _stream
    if _stream:
        _stream.stop()
    _stream = None


def _fresh_frame():
    """The newest frame off the stream, starting it when it is dead or silent."""
    global _stream
    frame = _stream.latest() if _stream else None
    if frame:
        return frame
    for attempt in (1, 2):
        _stop_stream()
        if os.path.exists(DEVICE):
            _stream = Stream()
            frame = _stream.wait_for_frame()
            if frame:
                return frame
            reason = "the camera stream gave no frame"
        else:
            reason = f"no camera at {DEVICE}"
        if attempt == 1:
            _pause(RETRY_AFTER_S)
    _stop_stream()
    raise CameraError(reason)


def save_frame(frame, now=None):
    """Write one frame to the frames folder and return its path."""
    os.makedirs(FRAMES_DIR, exist_ok=True)
    path = os.path.join(FRAMES_DIR, (now or datetime.now()).strftime(NAME_FORMAT))
    # Temp + rename: the dashboard never serves a half-written frame
    tmp = path + '.part'
    with open(tmp, 'wb') as f:
        f.write(frame)
    os.replace(tmp, path)
    return path


def frame_names():
    """Frame file names, oldest first. Anything else in the folder is ignored."""
    try:
        names = os.listdir(FRAMES_DIR)
    except FileNotFoundError:
        return []
    return sorted(n for n in names if _NAME_RE.match(n))


def latest_frame():
    # The pattern alone would pass "2026-13-45T999999.jpg"; a name that isn't
    # a real time must not become the latest frame and break the dashboard.
    # Checked newest-first, so a normal call parses one name, not two weeks' worth
    for name in reversed(frame_names()):
        try:
            frame_time(name)
        except ValueError:
            continue
        return os.path.join(FRAMES_DIR, name)
    return None


def frame_time(path):
    return datetime.strptime(os.path.basename(path), NAME_FORMAT)


def prune(now=None):
    """Delete frames and clips older than KEEP_DAYS. Returns how many went."""
    cutoff = ((now or datetime.now()) - timedelta(days=KEEP_DAYS)).strftime(NAME_FORMAT)
    old = [n for n in frame_names() if n < cutoff]
    for name in old:
        os.remove(os.path.join(FRAMES_DIR, name))
    # A clip's name is a frame name without the .jpg, so the same cutoff sorts it
    old_clips = [c for c in clip_ids() if c < cutoff]
    for clip_id in old_clips:
        shutil.rmtree(os.path.join(CLIPS_DIR, clip_id))
    return len(old) + len(old_clips)


def snap(failing, now=None):
    """One timer tick. Returns whether the camera is failing now.

    An unplugged webcam fails every 5 minutes; the log gets one warning when
    the outage starts and one line when it ends, not 288 a day.
    """
    now = now or datetime.now()
    try:
        frame = _fresh_frame()
        path = save_frame(frame, now)
        prune(now)
    except (CameraError, OSError) as e:
        if not failing:
            log.warning(f"Camera offline: {e} — feeding is unaffected; retrying quietly")
        bowl_watch.WATCH.frame_missing(now.timestamp())
        return True
    except Exception:
        # A bug, not an outage: keep the traceback, still only once
        if not failing:
            log.exception("Camera capture failed unexpectedly — feeding is unaffected")
        bowl_watch.WATCH.frame_missing(now.timestamp())
        return True
    if failing:
        log.info("Camera is back")
    # Never raises: a misread bowl can't stop the next shot
    bowl_watch.WATCH.frame_saved(os.path.basename(path), frame, now.timestamp())
    return False


def seconds_until_next_shot(epoch_s):
    # Epoch time lines up with the clock's 5-minute marks in any timezone a
    # whole number of minutes from UTC
    return (INTERVAL_S - LEAD_S - epoch_s % INTERVAL_S) % INTERVAL_S or INTERVAL_S


def run_forever(sleep=time.sleep, clock=time.time):
    failing = snap(False)  # one right away, so a restart shows a fresh frame
    while True:
        sleep(seconds_until_next_shot(clock()))
        failing = snap(failing)


# ----- Feed clips -----

def clip_ids():
    """Saved clip names, oldest first. Anything else in the folder is ignored."""
    try:
        names = os.listdir(CLIPS_DIR)
    except FileNotFoundError:
        return []
    return sorted(n for n in names if _CLIP_ID_RE.fullmatch(n))


def clip_id_for(ts):
    """The clip name for a journal event's ts ("2026-11-15T15:30:01")."""
    return datetime.fromisoformat(ts).strftime(CLIP_ID_FORMAT)


def _valid_clip_id(clip_id):
    if not isinstance(clip_id, str) or not _CLIP_ID_RE.fullmatch(clip_id):
        return False
    try:
        datetime.strptime(clip_id, CLIP_ID_FORMAT)
    except ValueError:
        return False
    return True


def clip_info(clip_id):
    """A saved clip's frame count and the frame where the feed has landed, or
    None when there is no such clip."""
    if not _valid_clip_id(clip_id):
        return None
    try:
        with open(os.path.join(CLIPS_DIR, clip_id, 'clip.json')) as f:
            meta = json.load(f)
        return {'frames': int(meta['frames']), 'landed': int(meta['landed'])}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def clip_frame_path(clip_id, index):
    """The file for one frame of a clip, or None. Both parts are checked here,
    so nothing from a request reaches the filesystem unchecked."""
    info = clip_info(clip_id)
    if info is None or not isinstance(index, int) or not 0 <= index < info['frames']:
        return None
    return os.path.join(CLIPS_DIR, clip_id, f"{index:03d}.jpg")


def clip_window(event):
    """(start, end, landed_at) in wall-clock seconds for a journal event, or
    None when it gets no clip. A dispense is journaled as it finishes, so it
    started duration_s earlier."""
    if event.get('event') not in _CLIP_EVENTS:
        return None
    if event.get('event') == 'dispense' and event.get('source') == 'graze':
        return None
    try:
        finished = datetime.fromisoformat(event['ts']).timestamp()
        duration = float(event.get('duration_s') or 0)
    except (KeyError, TypeError, ValueError):
        return None
    return finished - duration - CLIP_BEFORE_S, finished + CLIP_AFTER_S, finished


def save_clip(clip_id, frames, landed_at):
    """Write a clip's frames and clip.json, all or nothing. Returns its path,
    or None when a clip by that name already exists."""
    final = os.path.join(CLIPS_DIR, clip_id)
    if os.path.exists(final):
        return None
    os.makedirs(CLIPS_DIR, exist_ok=True)
    tmp = os.path.join(CLIPS_DIR, f".{clip_id}.part")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    for i, (_, frame) in enumerate(frames):
        with open(os.path.join(tmp, f"{i:03d}.jpg"), 'wb') as f:
            f.write(frame)
    # The first frame taken after the food landed; the player opens here
    # when motion is reduced
    landed = next((i for i, (at, _) in enumerate(frames) if at >= landed_at), len(frames) - 1)
    with open(os.path.join(tmp, 'clip.json'), 'w') as f:
        json.dump({'frames': len(frames), 'landed': landed}, f)
    # Rename: the dashboard never sees a half-written clip
    os.replace(tmp, final)
    return final


class ClipWatcher:
    """Reads new journal lines and cuts a clip once each event's window has passed.

    Starts at the journal's end: events from before the camera started get no
    clip, because their frames were never in memory."""

    def __init__(self, path=EVENTS_FILE):
        self.path = path
        try:
            self._offset = os.path.getsize(path)
        except OSError:
            self._offset = 0
        self._pending = []   # (due wall time, clip id, window)
        self._failing = False

    def _new_events(self):
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return []
        if size < self._offset:   # replaced or cut down: start over at its end
            self._offset = size
            return []
        if size == self._offset:
            return []
        with open(self.path, 'rb') as f:
            f.seek(self._offset)
            data = f.read(size - self._offset)
        # A line still being written waits for the next read
        complete = data[:data.rfind(b'\n') + 1]
        self._offset += len(complete)
        found = []
        for line in complete.decode('utf-8', 'replace').splitlines():
            try:
                found.append(json.loads(line))
            except ValueError:
                continue
        return found

    def poll(self, now=None):
        """One look at the journal; cuts any clip now due. Returns clips saved."""
        now = _wall() if now is None else now
        for event in self._new_events():
            window = clip_window(event)
            if window:
                self._pending.append((window[1] + 1, clip_id_for(event['ts']), window,
                                      event.get('event') == 'dispense'))
        due = [p for p in self._pending if p[0] <= now]
        self._pending = [p for p in self._pending if p[0] > now]
        saved = []
        for _, clip_id, (start, end, landed_at), dispense in due:
            if self._cut(clip_id, start, end, landed_at, dispense):
                saved.append(clip_id)
        return saved

    def _cut(self, clip_id, start, end, landed_at, dispense=False):
        stream = _stream
        frames = stream.frames_between(start, end) if stream else []
        try:
            if len(frames) < 2:
                raise CameraError("the camera had no frames from then")
            save_clip(clip_id, frames, landed_at)
        except (CameraError, OSError) as e:
            # One line per outage, like the shots
            if not self._failing:
                log.warning(f"No feed clip for {clip_id}: {e} — feeding is unaffected")
            self._failing = True
            return False
        self._failing = False
        # A dispense's clip holds the bowl just before the food landed: the
        # "before" reading. Graze pulses get none (a graze is one meal over
        # 30 min, not one per pulse). Never raises
        if dispense:
            bowl_watch.WATCH.clip_saved(clip_id, frames, landed_at)
        return True


def watch_forever(watcher=None, sleep=time.sleep):
    watcher = watcher or ClipWatcher()
    while True:
        try:
            watcher.poll()
        except Exception:
            # A bug, not an outage: keep the traceback and keep watching
            log.exception("Feed clip watcher failed unexpectedly — feeding is unaffected")
        sleep(WATCH_EVERY_S)


def start():
    """Start the capture and clip threads; None where there is no camera to run."""
    if not enabled():
        return None
    thread = threading.Thread(target=run_forever, name='camera', daemon=True)
    thread.start()
    # Made here, not in the thread: the journal's end is taken at startup
    watcher = ClipWatcher()
    threading.Thread(target=watch_forever, args=(watcher,), name='camera-clips', daemon=True).start()
    return thread


def live_frame():
    """The stream's newest frame and when it was taken, or None when the
    stream isn't delivering. Read by the web thread; the stream guards it."""
    stream = _stream
    got = stream.latest_with_time() if stream else None
    if not got:
        return None
    frame, at = got
    return frame, datetime.fromtimestamp(at)


def status(now=None):
    """Newest picture and whether it is fresh, for the dashboard. None where
    the feeder has no camera. While the stream runs the picture is seconds
    old; otherwise it is the last saved frame."""
    if not enabled():
        return None
    live = live_frame()
    if live:
        taken = live[1].replace(microsecond=0)
        return {'online': True, 'frame': taken.strftime(NAME_FORMAT), 'taken': taken}
    latest = latest_frame()
    if not latest:
        return {'online': False, 'frame': None, 'taken': None}
    taken = frame_time(latest)
    age_s = ((now or datetime.now()) - taken).total_seconds()
    return {'online': age_s <= STALE_AFTER_S, 'frame': os.path.basename(latest), 'taken': taken}
