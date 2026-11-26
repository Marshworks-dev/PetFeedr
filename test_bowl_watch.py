"""Tests for bowl_watch: reading the bowl, and the alerts built on it.

Run: python3 -m unittest

No camera pictures live in this repo. The readings are checked here on
frames drawn in code; PETFEEDR_REAL_FRAMES can name a private folder of real
crops (file names start with what they show: empty_, food_, few_, cat_,
dark_, blocked_), checked when it is set.
"""

import json
import math
import os
import random
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import bowl_watch as bw

S = bw.SIZE
HOME = (166, 152, 100)   # where the bowl sits in the thumbnail on the real feeder


def scene(bowl=HOME, kibble=0, cat=False, black_share=0.0, seed=1):
    """A thumbnail drawn like the real camera's view: a mottled floor, the
    bowl's bright rim with a shadow outside it, a lighter floor with a
    stamped ring, the feeder's white pipe coming in from below."""
    rnd = random.Random(seed)
    px = bytearray(rnd.randint(95, 125) for _ in range(S * S))   # concrete floor
    if bowl:
        cx, cy, r = bowl
        for y in range(S):
            for x in range(S):
                d = math.hypot(x - cx, y - cy)
                if d < 0.62 * r:
                    v = 185 + rnd.randint(-8, 8)
                    if abs(d - 0.4 * r) < 1:
                        v = 140          # the stamped ring on the bowl floor
                elif d < r - 4:
                    v = 150 + rnd.randint(-8, 8)   # the bowl's sloped wall
                elif d < r + 4:
                    v = 215              # the rim
                elif d < 1.25 * r:
                    v = 70               # its shadow on the floor
                else:
                    continue
                px[y * S + x] = v
        for y in range(cy, S):           # the pipe, in from the bottom edge
            for x in range(cx - int(0.25 * r), cx + int(0.25 * r)):
                px[y * S + x] = 240
        for _ in range(kibble):          # kibble: small dark pieces on the floor
            while True:
                a, d = rnd.uniform(0, 2 * math.pi), rnd.uniform(0, 0.55 * r)
                x, y = int(cx + d * math.cos(a)), int(cy + d * math.sin(a))
                if not (abs(x - cx) < 0.35 * r and y > cy - 0.1 * r):
                    break
            for dy in (0, 1):
                for dx in (0, 1):
                    px[(y + dy) * S + x + dx] = 70
    if cat:                              # a cat's head and back over the bowl
        cx, cy, r = bowl or HOME
        for y in range(S):
            for x in range(S):
                # Head in the bowl, shoulders wider than it: real cats covered
                # the rim and the floor beside it
                if ((x - cx) / (1.4 * r)) ** 2 + ((y - cy + 0.3 * r) / (1.4 * r)) ** 2 < 1:
                    px[y * S + x] = rnd.randint(110, 200)
    if black_share:                      # something over the lens
        for i in range(int(S * S * black_share)):
            px[len(px) - 1 - i] = 0
    return bytes(px)


class TestReadingTheBowl(unittest.TestCase):
    def test_an_empty_bowl_reads_empty(self):
        self.assertEqual(bw.read_thumbnail(scene())['bowl'], 'empty')

    def test_a_meal_reads_food(self):
        got = bw.read_thumbnail(scene(kibble=120))
        self.assertEqual((got['view'], got['bowl']), ('clear', 'food'))

    def test_a_few_stray_kibble_count_as_eaten(self):
        self.assertEqual(bw.read_thumbnail(scene(kibble=8))['bowl'], 'empty')

    def test_a_cat_over_the_bowl_is_unknown_not_empty(self):
        got = bw.read_thumbnail(scene(kibble=120, cat=True))
        self.assertEqual((got['view'], got['bowl']), ('clear', 'unknown'))

    def test_a_nudged_bowl_is_found_where_it_went(self):
        moved = (HOME[0] + 40, HOME[1] - 30, HOME[2])
        self.assertEqual(bw.read_thumbnail(scene(bowl=moved))['bowl'], 'empty')
        self.assertEqual(bw.read_thumbnail(scene(bowl=moved, kibble=120))['bowl'], 'food')

    def test_no_bowl_in_view_is_unknown(self):
        got = bw.read_thumbnail(scene(bowl=None))
        self.assertEqual((got['view'], got['bowl']), ('clear', 'unknown'))

    def test_a_black_frame_is_dark(self):
        got = bw.read_thumbnail(bytes(S * S))
        self.assertEqual((got['view'], got['bowl']), ('dark', 'unknown'))

    def test_a_mostly_covered_lens_is_blocked(self):
        got = bw.read_thumbnail(scene(bowl=None, black_share=0.7))
        self.assertEqual((got['view'], got['bowl']), ('blocked', 'unknown'))

    def test_a_cat_walking_past_the_lens_corner_is_not_blocked(self):
        # A little black at the edge with the bowl still in view
        got = bw.read_thumbnail(scene(black_share=0.15))
        self.assertEqual((got['view'], got['bowl']), ('clear', 'empty'))


@unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg not installed')
class TestDecode(unittest.TestCase):
    def test_a_camera_frame_becomes_the_bowl_regions_thumbnail(self):
        # A 1280x720 frame, white inside the bowl region and black outside
        raw = bytearray(1280 * 720)
        for y in range(bw.REGION_Y, bw.REGION_Y + bw.REGION_SIZE):
            raw[y * 1280 + bw.REGION_X:y * 1280 + bw.REGION_X + bw.REGION_SIZE] = b'\xff' * bw.REGION_SIZE
        jpeg = subprocess.run(['ffmpeg', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'gray',
                               '-s', '1280x720', '-i', '-', '-frames:v', '1', '-f', 'mjpeg', '-'],
                              input=bytes(raw), capture_output=True, check=True).stdout
        px = bw.decode(jpeg)
        self.assertEqual(len(px), S * S)
        self.assertGreater(sum(px) / len(px), 200)   # all of it from inside the region

    def test_garbage_is_a_read_error_not_a_crash(self):
        with self.assertRaises(bw.ReadError):
            bw.decode(b'not a jpeg')


class WatchCase(unittest.TestCase):
    """A Watch with readings scripted per frame and Pushover captured."""

    T0 = datetime(2026, 11, 27, 15, 30, 1).timestamp()

    def setUp(self):
        self._olddir = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(os.chdir, self._olddir)
        self.sent = []
        # A frame's bytes say what it shows: b'empty', b'food', b'cat', b'dark' ...
        self.watch = bw.Watch(read=self._read, send=lambda m, title='': self.sent.append(m),
                              background=False)
        env = patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop('PETFEEDR_MEAL_ALERTS', None)

    @staticmethod
    def _read(jpeg):
        what = jpeg.decode()
        if what == 'garbage':
            raise bw.ReadError('ffmpeg gave no picture')
        views = {'dark': 'dark', 'blocked': 'blocked'}
        bowl = what if what in ('empty', 'food') else 'unknown'
        return {'view': views.get(what, 'clear'), 'bowl': bowl, 'specks': None}

    def readings(self):
        try:
            with open(bw.READINGS_FILE) as f:
                return [json.loads(line) for line in f]
        except FileNotFoundError:
            return []

    def frames(self, what, start_min, end_min):
        """A saved 5-minute frame showing `what` at each mark from start to end."""
        for minute in range(start_min, end_min + 1, 5):
            at = self.T0 + minute * 60
            self.watch.frame_saved(datetime.fromtimestamp(at).strftime('%Y-%m-%dT%H%M%S.jpg'),
                                   what.encode(), at)

    def feed(self, before=('empty',), at=None):
        """A dispense's clip: the frames before the landing, then cats after."""
        landed = self.T0 if at is None else at
        frames = [(landed - 10 + i * 0.5, w.encode()) for i, w in enumerate(before)]
        frames += [(landed + 1, b'cat'), (landed + 2, b'cat')]
        self.watch.clip_saved(datetime.fromtimestamp(landed).strftime('%Y-%m-%dT%H%M%S'),
                              frames, landed)


class TestBlindCamera(WatchCase):
    def test_an_hour_of_dark_frames_sends_one_alert_then_one_when_back(self):
        self.frames('dark', 0, 55)
        self.assertEqual(self.sent, [])
        self.frames('dark', 60, 120)
        self.assertEqual(len(self.sent), 1)
        self.assertIn('dark', self.sent[0])
        self.frames('empty', 125, 130)
        self.assertEqual(len(self.sent), 2)
        self.assertIn('again', self.sent[1])

    def test_a_covered_lens_says_so(self):
        self.frames('blocked', 0, 60)
        self.assertEqual(len(self.sent), 1)
        self.assertIn('covering the lens', self.sent[0])

    def test_an_hour_with_no_frames_at_all_says_no_picture(self):
        for minute in range(0, 61, 5):
            self.watch.frame_missing(self.T0 + minute * 60)
        self.assertEqual(len(self.sent), 1)
        self.assertIn('no picture', self.sent[0])

    def test_a_dark_frame_now_and_then_sends_nothing(self):
        for start in range(0, 180, 30):
            self.frames('dark', start, start + 20)
            self.frames('cat', start + 25, start + 25)   # a cat over the bowl still means it can see
        self.assertEqual(self.sent, [])

    def test_an_unreadable_frame_neither_starts_nor_ends_an_outage(self):
        self.frames('dark', 0, 30)
        self.frames('garbage', 35, 35)
        self.frames('dark', 40, 60)
        self.assertEqual(len(self.sent), 1)

    def test_the_alert_is_in_the_readings(self):
        self.frames('dark', 0, 60)
        alerts = [r for r in self.readings() if r['kind'] == 'camera']
        self.assertEqual(alerts[0]['alert'], 'sent')
        self.assertEqual(alerts[0]['view'], 'dark')


class TestMealReadings(WatchCase):
    def test_every_frame_is_recorded_with_its_name(self):
        self.frames('empty', 0, 5)
        lines = self.readings()
        self.assertEqual([r['frame'] for r in lines], ['2026-11-27T153001.jpg', '2026-11-27T153501.jpg'])
        self.assertEqual(lines[0]['bowl'], 'empty')

    def test_a_meal_still_there_at_30_min_is_read_food_and_held_while_alerts_are_off(self):
        self.feed()
        self.frames('food', 30, 30)
        after = [r for r in self.readings() if r['kind'] == 'after']
        self.assertEqual(len(after), 1)
        self.assertEqual(after[0]['bowl'], 'food')
        self.assertEqual(after[0]['frame'], '2026-11-27T160001.jpg')
        self.assertEqual(after[0]['alert'], 'held (meal alerts off)')
        self.assertEqual(self.sent, [])

    def test_with_meal_alerts_on_an_uneaten_meal_sends_once(self):
        os.environ['PETFEEDR_MEAL_ALERTS'] = 'on'
        self.feed()
        self.frames('food', 0, 90)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("15:30 meal hasn't been eaten", self.sent[0])

    def test_only_on_turns_meal_alerts_on(self):
        for value in ('', 'yes', '1', 'true', 'onn'):
            os.environ['PETFEEDR_MEAL_ALERTS'] = value
            self.assertFalse(bw.meal_alerts_on(), value)
        os.environ['PETFEEDR_MEAL_ALERTS'] = ' On '
        self.assertTrue(bw.meal_alerts_on())

    def test_food_before_30_min_is_not_the_after_reading(self):
        self.feed()
        self.frames('food', 5, 25)
        self.assertEqual([r for r in self.readings() if r['kind'] == 'after'], [])

    def test_a_cat_at_30_min_waits_for_the_next_clear_frame(self):
        os.environ['PETFEEDR_MEAL_ALERTS'] = 'on'
        self.feed()
        self.frames('cat', 30, 30)
        self.frames('empty', 35, 35)
        after = [r for r in self.readings() if r['kind'] == 'after']
        self.assertEqual([(r['bowl'], r['frame']) for r in after], [('empty', '2026-11-27T160501.jpg')])
        self.assertEqual(self.sent, [])

    def test_no_clear_look_within_the_hour_is_unknown_and_never_alerts(self):
        os.environ['PETFEEDR_MEAL_ALERTS'] = 'on'
        self.feed()
        self.frames('cat', 30, 60)
        after = [r for r in self.readings() if r['kind'] == 'after']
        self.assertEqual([r['bowl'] for r in after], ['unknown'])
        self.assertEqual(self.sent, [])

    def test_before_reading_takes_the_newest_readable_frame_before_landing(self):
        self.feed(before=('empty', 'food', 'cat', 'cat'))
        before = [r for r in self.readings() if r['kind'] == 'before']
        self.assertEqual(before[0]['bowl'], 'food')
        self.assertEqual(before[0]['frame'], 'clips/2026-11-27T153001/001.jpg')

    def test_food_left_before_a_feed_alerts(self):
        os.environ['PETFEEDR_MEAL_ALERTS'] = 'on'
        self.feed(before=('food',))
        self.assertEqual(len(self.sent), 1)
        self.assertIn('still in the bowl from the last meal', self.sent[0])

    def test_food_already_reported_at_30_min_isnt_reported_again_at_the_next_feed(self):
        os.environ['PETFEEDR_MEAL_ALERTS'] = 'on'
        self.feed()
        self.frames('food', 30, 30)
        self.feed(before=('food',), at=self.T0 + 6 * 3600)
        self.assertEqual(len(self.sent), 1)
        before = [r for r in self.readings() if r['kind'] == 'before']
        self.assertEqual(before[-1]['alert'], 'covered by the 30-min alert')

    def test_a_bowl_hidden_before_the_feed_is_unknown(self):
        self.feed(before=('cat',) * 8)
        before = [r for r in self.readings() if r['kind'] == 'before']
        self.assertEqual((before[0]['bowl'], before[0]['frame']), ('unknown', None))

    def test_readings_older_than_14_days_are_pruned(self):
        old = self.T0 - 15 * 86400
        self.watch.frame_saved('old.jpg', b'empty', old)
        self.watch._pruned_on = None
        self.frames('empty', 0, 0)
        self.assertEqual([r['frame'] for r in self.readings()], ['2026-11-27T153001.jpg'])


class TestNeverRaises(WatchCase):
    def test_a_bug_in_reading_is_logged_not_raised(self):
        watch = bw.Watch(read=MagicMock(side_effect=RuntimeError('boom')), send=MagicMock(),
                         background=False)
        with self.assertLogs('petfeedr', 'ERROR'):
            watch.frame_saved('x.jpg', b'x', self.T0)
        with self.assertLogs('petfeedr', 'ERROR'):
            watch.clip_saved('2026-11-27T153001', [(self.T0 - 1, b'x')], self.T0)

    def test_a_failing_pushover_is_logged_not_raised(self):
        watch = bw.Watch(read=self._read, send=MagicMock(side_effect=OSError('no network')))
        with self.assertLogs('petfeedr', 'ERROR'):
            for minute in range(0, 61, 5):
                watch.frame_saved('x.jpg', b'dark', self.T0 + minute * 60)


class TestClipReadingsInBackground(WatchCase):
    def test_a_slow_clip_reading_doesnt_hold_up_the_caller(self):
        import threading
        release = threading.Event()
        done = threading.Event()

        def slow_read(jpeg):
            release.wait(5)
            done.set()
            return {'view': 'clear', 'bowl': 'empty', 'specks': None}

        watch = bw.Watch(read=slow_read, send=MagicMock())
        watch.clip_saved('2026-11-27T153001', [(self.T0 - 1, b'x')], self.T0)
        # Back already, with the reading still waiting
        self.assertFalse(done.is_set())
        release.set()
        self.assertTrue(done.wait(5))


class TestCameraHandsOver(unittest.TestCase):
    """camera.py gives bowl watch each saved frame, each miss and each dispense clip."""

    def setUp(self):
        self._olddir = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(os.chdir, self._olddir)
        import camera
        self.camera = camera
        self.watch = MagicMock()
        p = patch.object(camera.bowl_watch, 'WATCH', self.watch)
        p.start()
        self.addCleanup(p.stop)

    def test_a_saved_shot_is_handed_over(self):
        when = datetime(2026, 11, 27, 16, 4, 30)
        with patch.object(self.camera, '_fresh_frame', return_value=b'\xff\xd8 frame'):
            self.assertFalse(self.camera.snap(False, when))
        self.watch.frame_saved.assert_called_once_with('2026-11-27T160430.jpg', b'\xff\xd8 frame',
                                                        when.timestamp())

    def test_a_missed_shot_is_handed_over(self):
        when = datetime(2026, 11, 27, 16, 4, 30)
        with patch.object(self.camera, '_fresh_frame', side_effect=self.camera.CameraError('no camera')):
            self.assertTrue(self.camera.snap(False, when))
        self.watch.frame_missing.assert_called_once_with(when.timestamp())
        self.watch.frame_saved.assert_not_called()

    def test_dispense_clips_are_handed_over_and_graze_pulses_are_not(self):
        frames = [(100.0, b'a'), (101.0, b'b')]
        stream = MagicMock()
        stream.frames_between.return_value = frames
        with patch.object(self.camera, '_stream', stream):
            watcher = self.camera.ClipWatcher()
            self.assertTrue(watcher._cut('2026-11-27T153001', 90, 130, 100.5, dispense=True))
            self.assertTrue(watcher._cut('2026-11-27T160001', 90, 130, 100.5, dispense=False))
        self.watch.clip_saved.assert_called_once_with('2026-11-27T153001', frames, 100.5)


REAL = os.environ.get('PETFEEDR_REAL_FRAMES')


@unittest.skipUnless(REAL and os.path.isdir(REAL) and shutil.which('ffmpeg'),
                     'PETFEEDR_REAL_FRAMES not set (real frames are kept out of the repo)')
class TestRealFrames(unittest.TestCase):
    EXPECT = {'empty': ('clear', 'empty'), 'food': ('clear', 'food'), 'few': ('clear', 'empty'),
              'cat': ('clear', 'unknown'), 'dark': ('dark', 'unknown'), 'blocked': ('blocked', 'unknown')}

    def test_each_real_frame_reads_as_labelled(self):
        names = sorted(n for n in os.listdir(REAL) if n.endswith('.jpg'))
        self.assertTrue(names)
        for name in names:
            label = name.split('_')[0]
            with self.subTest(name), open(os.path.join(REAL, name), 'rb') as f:
                got = bw.read_jpeg(f.read())
                self.assertEqual((got['view'], got['bowl']), self.EXPECT[label])


if __name__ == '__main__':
    unittest.main()
