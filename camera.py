"""Bowl camera: saves a frame every few minutes. Nothing interprets them yet.

A USB webcam over the bowl is a sensor the rest of the feeder lacks —
"Feeding completed" only proves the motor turned. This step just collects:
frames land in data/frames/, named by local time, so a later step can pair
them with feeds from the event journal by timestamp.

The webcam is started once and left streaming; each saved frame is the
newest one off that stream. Measured on the Pi: this webcam is steady while
it streams and jams when a stream is started after an earlier one stopped
(about 3 shots in 10 were lost to that), so the stream is only restarted
when it has died or gone quiet.

Deliberately apart from the feed path: nothing here takes the feeder's state
lock and no feed code calls in, so a flaky webcam can never delay or break
a meal.
"""

import logging
import os
import re
import subprocess
import threading
import time
from datetime import datetime, timedelta

from DRV8825 import on_raspberry_pi

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
        self._count = 0
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
            self._count += 1
            settled = self._count >= WARMUP_FRAMES
        if settled:
            self._ready.set()

    def latest(self):
        """The newest settled frame, or None while warming up or once stalled."""
        with self._lock:
            if self._count < WARMUP_FRAMES or _now() - self._at > STALL_AFTER_S:
                return None
            return self._frame

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
    """Delete frames older than KEEP_DAYS. Returns how many went."""
    cutoff = ((now or datetime.now()) - timedelta(days=KEEP_DAYS)).strftime(NAME_FORMAT)
    old = [n for n in frame_names() if n < cutoff]
    for name in old:
        os.remove(os.path.join(FRAMES_DIR, name))
    return len(old)


def snap(failing, now=None):
    """One timer tick. Returns whether the camera is failing now.

    An unplugged webcam fails every 5 minutes; the log gets one warning when
    the outage starts and one line when it ends, not 288 a day.
    """
    try:
        save_frame(_fresh_frame(), now)
        prune(now)
    except (CameraError, OSError) as e:
        if not failing:
            log.warning(f"Camera offline: {e} — feeding is unaffected; retrying quietly")
        return True
    except Exception:
        # A bug, not an outage: keep the traceback, still only once
        if not failing:
            log.exception("Camera capture failed unexpectedly — feeding is unaffected")
        return True
    if failing:
        log.info("Camera is back")
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


def start():
    """Start the capture thread; None where there is no camera to run."""
    if not enabled():
        return None
    thread = threading.Thread(target=run_forever, name='camera', daemon=True)
    thread.start()
    return thread


def status(now=None):
    """Newest frame and whether it is fresh, for the dashboard. None where
    the feeder has no camera."""
    if not enabled():
        return None
    latest = latest_frame()
    if not latest:
        return {'online': False, 'frame': None, 'taken': None}
    taken = frame_time(latest)
    age_s = ((now or datetime.now()) - taken).total_seconds()
    return {'online': age_s <= STALE_AFTER_S, 'frame': os.path.basename(latest), 'taken': taken}
