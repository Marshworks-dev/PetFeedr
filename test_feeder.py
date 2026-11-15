"""Tests for schedule parsing, randomization, and log-parsing stats.

Run: python3 -m unittest
"""

import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta, date
from unittest.mock import MagicMock, patch

import schedule

import feeder_core
import feeding_stats
from feeder_core import apply_random_offset
from schedule_store import parse_line as parse_schedule_line


class TestParseScheduleLine(unittest.TestCase):
    def test_full_line(self):
        self.assertEqual(parse_schedule_line("08:00,medium,fixed"), ("08:00", "medium", True, False))

    def test_time_only_defaults(self):
        self.assertEqual(parse_schedule_line("08:00"), ("08:00", "small", False, False))

    def test_unknown_portion_falls_back(self):
        self.assertEqual(parse_schedule_line("08:00,bogus"), ("08:00", "small", False, False))

    def test_randomized_with_portion(self):
        self.assertEqual(parse_schedule_line("21:30,large"), ("21:30", "large", False, False))

    def test_whitespace_tolerated(self):
        self.assertEqual(parse_schedule_line(" 08:00 , medium , FIXED \n"), ("08:00", "medium", True, False))

    def test_legacy_fixed_in_portion_slot_is_fixed(self):
        # One parser now: the scheduler honors "HH:MM,fixed" the way the UI
        # always displayed it (they used to disagree).
        self.assertEqual(parse_schedule_line("08:00,fixed"), ("08:00", "small", True, False))


class TestApplyRandomOffset(unittest.TestCase):
    def test_offset_applied(self):
        with patch('feeder_core.random.randint', return_value=15):
            self.assertEqual(apply_random_offset("08:00", 30, []), "08:15")

    def test_negative_offset(self):
        with patch('feeder_core.random.randint', return_value=-30):
            self.assertEqual(apply_random_offset("08:00", 30, []), "07:30")

    def test_backward_past_midnight_clamps_to_base(self):
        with patch('feeder_core.random.randint', return_value=-30):
            self.assertEqual(apply_random_offset("00:10", 30, []), "00:10")

    def test_forward_past_midnight_clamps_to_base(self):
        # A forward roll from 23:45 used to land at 00:05, before the day's
        # earlier meals
        with patch('feeder_core.random.randint', return_value=20):
            self.assertEqual(apply_random_offset("23:45", 30, []), "23:45")

    def test_conflict_avoidance_retries(self):
        # First offset lands within 10 min of an existing time, second is clear.
        existing = [(datetime.strptime("08:15", "%H:%M"), False)]
        with patch('feeder_core.random.randint', side_effect=[10, -20]):
            self.assertEqual(apply_random_offset("08:00", 30, existing), "07:40")

    def test_exact_collision_is_a_conflict(self):
        existing = [(datetime.strptime("08:15", "%H:%M"), False)]
        with patch('feeder_core.random.randint', side_effect=[15, -20]):
            self.assertEqual(apply_random_offset("08:00", 30, existing), "07:40")

    def test_gives_up_after_ten_tries(self):
        existing = [(datetime.strptime("08:03", "%H:%M"), False)]
        with patch('feeder_core.random.randint', return_value=5):
            self.assertEqual(apply_random_offset("08:00", 30, existing), "08:00")

    def test_graze_is_busy_for_its_whole_span(self):
        # A graze from 08:00 runs to 08:30: 08:35 is inside its buffer, 08:40 clear
        graze = [(datetime.strptime("08:00", "%H:%M"), True)]
        with patch('feeder_core.random.randint', side_effect=[-25, -20]):
            self.assertEqual(apply_random_offset("09:00", 30, graze), "08:40")

    def test_a_rolling_graze_keeps_clear_ahead(self):
        # This slot is the graze: from 07:55 it would run into an 08:30 meal
        meal = [(datetime.strptime("08:30", "%H:%M"), False)]
        with patch('feeder_core.random.randint', side_effect=[-5, -20]):
            self.assertEqual(apply_random_offset("08:00", 30, meal, graze=True), "07:40")


class TestGrazeRules(unittest.TestCase):
    def test_cutoffs(self):
        import graze_rules
        self.assertIsNone(graze_rules.midnight_problem("23:29", True))
        self.assertIsNone(graze_rules.midnight_problem("22:59", False))
        self.assertIn("before 11:30 PM", graze_rules.midnight_problem("23:30", True))
        self.assertIn("Set this one to Fixed, or move it before 11:00 PM",
                      graze_rules.midnight_problem("23:00", False))
        # Past both cutoffs, Fixed wouldn't help, so it isn't offered
        self.assertNotIn("Fixed", graze_rules.midnight_problem("23:45", False))

    def test_cutoff_matches_the_run_time_midnight_check(self):
        # The editor's limit and the feeder's all-at-once fallback must agree
        import graze_rules
        last = graze_rules.latest_graze_start(True)
        start = datetime(2026, 11, 15, last // 60, last % 60, 59)
        self.assertFalse(feeder_core.graze_crosses_midnight(start))
        self.assertTrue(feeder_core.graze_crosses_midnight(start + timedelta(seconds=1)))

    def test_spacing(self):
        import graze_rules
        entries = [{'time': '17:30', 'is_fixed': False, 'graze': True},
                   {'time': '08:00', 'is_fixed': False, 'graze': False}]
        self.assertIn("Too close to 5:30 PM", graze_rules.spacing_problem("18:05", False, entries))
        self.assertIsNone(graze_rules.spacing_problem("18:10", False, entries))
        # Two plain meals keep the old 10-minute rule, not this one
        self.assertIsNone(graze_rules.spacing_problem("08:20", False, entries))
        self.assertIn("8:00 AM", graze_rules.spacing_problem("08:20", True, entries))

    def test_spacing_wraps_midnight(self):
        import graze_rules
        entries = [{'time': '23:50', 'is_fixed': True, 'graze': False}]
        self.assertIn("11:50 PM", graze_rules.spacing_problem("00:10", True, entries))


# Real log line shapes: every historical format plus the current schema
NEW_MANUAL = "2026-07-22 14:00:00,430 - INFO - Feeding completed in 0.31s (small portion, manual)"
NEW_SCHEDULED = "2026-07-22 06:00:00,101 - INFO - Feeding completed in 0.62s (medium portion, scheduled)"
LEGACY_COMPLETED = "2026-05-10 04:00:00,391 - INFO - Feeding completed in 0.31s (small portion)"
SIM_COMPLETED = "2026-03-22 00:21:34,508 - INFO - [SIM] ✅ Feeding completed in 0.92s (medium portion)"
SIM_NEW = "2026-07-22 15:09:01,195 - INFO - [SIM] ✅ Feeding completed in 0.94s (medium portion, manual)"
PLAIN_COMPLETED = "2026-04-15 07:15:07 - Feeding completed in 0.31s (small portion)"
FAILED = "2026-07-22 08:00:01,001 - ERROR - Feeding failed (large portion, scheduled): jam"
LEGACY_MANUAL_TRIGGERED = "2026-07-11 21:32:23,525 - INFO - Manual feeding triggered (small portion)"
LEGACY_FEEDING_AT = "2026-04-15 07:15:07 - Feeding at 07:15 AM (small portion)"
WERKZEUG = '2026-04-29 12:34:44,239 - INFO - 192.168.1.10 - - [29/Apr/2026 12:34:44] "GET /sw.js HTTP/1.1" 200 -'


class TestLogPatterns(unittest.TestCase):
    def test_completed_matrix(self):
        cases = [
            (NEW_MANUAL, ("2026-07-22", "14:00:00", "small", "manual")),
            (NEW_SCHEDULED, ("2026-07-22", "06:00:00", "medium", "scheduled")),
            (LEGACY_COMPLETED, ("2026-05-10", "04:00:00", "small", None)),
            (SIM_COMPLETED, ("2026-03-22", "00:21:34", "medium", None)),
            (SIM_NEW, ("2026-07-22", "15:09:01", "medium", "manual")),
            (PLAIN_COMPLETED, ("2026-04-15", "07:15:07", "small", None)),
        ]
        for line, expected in cases:
            m = feeding_stats.COMPLETED_RE.match(line)
            self.assertIsNotNone(m, line)
            self.assertEqual((m['date'], m['time'], m['portion'], m['source']), expected, line)

    def test_failed_line_matches_failed_only(self):
        m = feeding_stats.FAILED_RE.match(FAILED)
        self.assertEqual((m['portion'], m['source']), ("large", "scheduled"))
        self.assertIsNone(feeding_stats.COMPLETED_RE.match(FAILED))

    def test_non_dispense_lines_never_match(self):
        # Legacy trigger/announce lines had companion completed lines —
        # counting them would double-count. Werkzeug noise must never match.
        for line in (LEGACY_MANUAL_TRIGGERED, LEGACY_FEEDING_AT, WERKZEUG):
            self.assertIsNone(feeding_stats.COMPLETED_RE.match(line), line)


def _line(days_ago, time_str, kind, portion):
    d = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")
    if kind == 'legacy':
        return f"{d} {time_str},391 - INFO - Feeding completed in 0.31s ({portion} portion)\n"
    return f"{d} {time_str},391 - INFO - Feeding completed in 0.31s ({portion} portion, {kind})\n"


class TestWeeklyStats(unittest.TestCase):
    def test_aggregates_by_day_and_portion(self):
        lines = [
            _line(0, "06:00:00", 'scheduled', 'small'),
            _line(0, "18:00:00", 'scheduled', 'medium'),
            _line(1, "07:00:00", 'manual', 'large'),
        ]
        stats = feeding_stats.parse_weekly_stats(lines=lines)
        self.assertEqual(len(stats), 7)
        today = stats[-1]
        self.assertTrue(today['is_today'])
        self.assertEqual((today['small'], today['medium'], today['total_feedings']), (1, 1, 2))
        self.assertAlmostEqual(today['total_cups'], 0.75)
        yesterday = stats[-2]
        self.assertEqual((yesterday['large'], yesterday['manual_count']), (1, 1))

    def test_legacy_lines_count_as_scheduled(self):
        stats = feeding_stats.parse_weekly_stats(lines=[_line(0, "06:00:00", 'legacy', 'small')])
        today = stats[-1]
        self.assertEqual((today['total_feedings'], today['manual_count']), (1, 0))

    def test_old_lines_ignored(self):
        stats = feeding_stats.parse_weekly_stats(lines=[_line(10, "06:00:00", 'scheduled', 'small')])
        self.assertEqual(sum(d['total_feedings'] for d in stats), 0)


class TestRecentActivity(unittest.TestCase):
    def test_newest_first_with_types(self):
        lines = [
            _line(1, "07:00:00", 'manual', 'large'),
            _line(0, "06:00:00", 'scheduled', 'small'),
        ]
        activity = feeding_stats.parse_recent_activity(days=14, limit=50, lines=lines)
        self.assertEqual(len(activity), 2)
        self.assertEqual((activity[0]['date'], activity[0]['type']), ("Today", 'scheduled'))
        self.assertEqual((activity[1]['date'], activity[1]['type']), ("Yesterday", 'manual'))

    def test_limit_respected(self):
        lines = [_line(0, f"0{h}:00:00", 'scheduled', 'small') for h in range(1, 6)]
        self.assertEqual(len(feeding_stats.parse_recent_activity(limit=2, lines=lines)), 2)


class TestDayFeedings(unittest.TestCase):
    def test_filters_to_requested_date(self):
        target = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        lines = [
            _line(1, "07:00:00", 'manual', 'large'),
            _line(0, "06:00:00", 'scheduled', 'small'),
        ]
        feedings, total = feeding_stats.day_feedings(target, lines=lines)
        self.assertEqual(len(feedings), 1)
        self.assertEqual((feedings[0]['time'], feedings[0]['type']), ("7:00 AM", 'manual'))
        self.assertAlmostEqual(total, 0.75)


class TestTotals(unittest.TestCase):
    def test_daily_total_mixed_portions(self):
        scheds = [{'portion': 'small'}, {'portion': 'medium'}, {'portion': 'large'}, {'portion': 'bogus'}]
        self.assertAlmostEqual(feeding_stats.calculate_daily_total(scheds), 1.75)

    def test_week_summary_variants(self):
        empty = feeding_stats.parse_weekly_stats(lines=["\n"])
        self.assertIsNone(feeding_stats.build_week_summary(empty))
        stats = feeding_stats.parse_weekly_stats(lines=[_line(0, "06:00:00", 'scheduled', 'small')])
        self.assertEqual(feeding_stats.build_week_summary(stats), "All feedings on schedule")
        stats = feeding_stats.parse_weekly_stats(lines=[_line(0, "06:00:00", 'manual', 'small')])
        self.assertEqual(feeding_stats.build_week_summary(stats), "1 manual feed this week")

    def test_consumption_rate(self):
        stats = feeding_stats.parse_weekly_stats(lines=[
            _line(0, "06:00:00", 'scheduled', 'medium'),
            _line(1, "06:00:00", 'scheduled', 'medium'),
        ])
        rate = feeding_stats.calculate_consumption_rate(stats)
        self.assertEqual(rate['daily_cups'], 0.5)
        self.assertEqual(rate['weekly_cups'], 3.5)
        self.assertIsNone(feeding_stats.calculate_consumption_rate(
            feeding_stats.parse_weekly_stats(lines=["\n"])))


def _api_state(web_interface):
    """GET /api/state and return its data payload."""
    resp = web_interface.app.test_client().get('/api/state')
    assert resp.status_code == 200, resp.status_code
    body = resp.get_json()
    assert body['success'], body
    return body['data']


class TempCwd(unittest.TestCase):
    """Run in a scratch directory so tests never touch the repo's runtime files."""

    def setUp(self):
        self._olddir = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)

    def tearDown(self):
        os.chdir(self._olddir)
        self._tmp.cleanup()


class TestFailureHandling(TempCwd):
    def test_notify_unconfigured_is_noop(self):
        import notify
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('PUSHOVER_TOKEN', None)
            os.environ.pop('PUSHOVER_USER', None)
            self.assertFalse(notify.send("test"))  # no exception, no network

    def test_sim_mode_suppresses_even_with_creds(self):
        # A dev box with real Pushover creds in the shell env must never
        # page a phone from a simulation run.
        import notify
        with patch.dict(os.environ, {'PUSHOVER_TOKEN': 'x', 'PUSHOVER_USER': 'y'}), \
             patch('notify.SIMULATION_MODE', True), \
             patch('notify.urllib.request.urlopen') as mock_open:
            os.environ.pop('PETFEEDR_NOTIFY_IN_SIM', None)
            self.assertFalse(notify.send("test"))
            mock_open.assert_not_called()

    def test_sim_override_allows_send(self):
        import notify
        from unittest.mock import MagicMock
        resp = MagicMock()
        resp.__enter__.return_value.status = 200
        with patch.dict(os.environ, {'PUSHOVER_TOKEN': 'x', 'PUSHOVER_USER': 'y',
                                     'PETFEEDR_NOTIFY_IN_SIM': 'true'}), \
             patch('notify.SIMULATION_MODE', True), \
             patch('notify.urllib.request.urlopen', return_value=resp) as mock_open:
            self.assertTrue(notify.send("test"))
            mock_open.assert_called_once()

    def test_feed_pet_failure_returns_false_and_notifies(self):
        with patch('feeder_core.trigger_servo', side_effect=RuntimeError("jam")), \
             patch('feeder_core.notify.send') as mock_send:
            self.assertFalse(feeder_core.feed_pet(portion='large', source='manual'))
            self.assertIn("large portion, manual", mock_send.call_args[0][0])

    def test_feed_pet_success_returns_true_without_notifying(self):
        with patch('feeder_core.trigger_servo', return_value=0.2), \
             patch('feeder_core.notify.send') as mock_send:
            self.assertTrue(feeder_core.feed_pet())
            mock_send.assert_not_called()


class TestResync(unittest.TestCase):
    """Job-registry sync against todays_schedule.json (runs in a temp cwd)."""

    def setUp(self):
        self._olddir = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)
        schedule.clear()

    def tearDown(self):
        schedule.clear()
        os.chdir(self._olddir)
        self._tmp.cleanup()

    @staticmethod
    def _entry(actual_time, portion='small'):
        return {'base_time': actual_time, 'actual_time': actual_time,
                'portion': portion, 'is_fixed': True, 'randomized': False}

    def test_passed_time_registers_for_tomorrow(self):
        # 00:00 has always passed by the time this runs → next_run is
        # tomorrow, so resyncing after a feeding fired never re-fires it.
        feeder_core.save_todays_schedule([self._entry('00:00')])
        feeder_core.resync_today()
        self.assertEqual(len(schedule.jobs), 1)
        self.assertEqual(schedule.jobs[0].next_run.date(), date.today() + timedelta(days=1))

    def test_resync_fires_due_job_before_clearing(self):
        # A job that came due just before a mutation must fire, not vanish.
        fired = []
        job = schedule.every().day.at("00:00").do(lambda: fired.append(1))
        job.next_run = datetime.now() - timedelta(seconds=1)
        feeder_core.resync_today()
        self.assertEqual(fired, [1])

    def test_resync_replaces_registry_from_file(self):
        schedule.every().day.at("00:00").do(lambda: None)  # stale job
        feeder_core.save_todays_schedule([self._entry('00:00'), self._entry('00:01', 'large')])
        feeder_core.resync_today()
        self.assertEqual(len(schedule.jobs), 2)

    def test_resync_with_no_file_leaves_registry_empty(self):
        schedule.every().day.at("00:00").do(lambda: None)
        feeder_core.resync_today()
        self.assertEqual(schedule.jobs, [])

    def test_ensure_today_preserves_current_schedule(self):
        # Restart fix: today's already-rolled times must NOT be re-randomized.
        entries = [self._entry('00:00', 'medium')]
        feeder_core.save_todays_schedule(entries)
        self.assertEqual(feeder_core.ensure_today(), entries)
        self.assertEqual(len(schedule.jobs), 1)

    def test_ensure_today_regenerates_stale_file(self):
        with open('feeding_schedules.txt', 'w') as f:
            f.write("08:00,medium,fixed\n")
        stale = {'date': (date.today() - timedelta(days=1)).isoformat(),
                 'schedule': [self._entry('09:30')]}
        import json
        with open(feeder_core.TODAYS_SCHEDULE_FILE, 'w') as f:
            json.dump(stale, f)
        result = feeder_core.ensure_today()
        self.assertEqual(len(result), 1)
        self.assertEqual((result[0]['actual_time'], result[0]['portion']), ('08:00', 'medium'))
        self.assertEqual(len(schedule.jobs), 1)

    def test_generate_with_empty_schedules_saves_empty_today(self):
        open('feeding_schedules.txt', 'w').close()
        self.assertEqual(feeder_core.generate_todays_schedule(), [])
        self.assertEqual(feeder_core.load_todays_schedule(), [])


class TestHopper(TempCwd):
    """Hopper learning and low-warning logic."""

    def test_fresh_state_is_learning(self):
        import hopper
        info = hopper.status()
        self.assertTrue(info['learning'])
        self.assertEqual(info['cups_since_refill'], 0.0)
        self.assertIsNone(info['days_left'])

    def test_refill_learns_capacity_and_resets_counter(self):
        import hopper
        for _ in range(4):
            hopper.record_dispense(2.5)  # 10 cups total
        state = hopper.record_refill(25)  # 25% left → consumed 75% → ~13.33 cups
        self.assertAlmostEqual(hopper.capacity_cups(state), 13.33, places=2)
        self.assertEqual(state['cups_since_refill'], 0.0)
        info = hopper.status(daily_avg_cups=1.0)
        self.assertFalse(info['learning'])
        self.assertEqual(info['level'], 1.0)
        self.assertEqual(info['days_left'], 13)

    def test_weighed_refill_learns_cups_per_lb(self):
        import hopper
        for _ in range(4):
            hopper.record_dispense(2.5)  # 10 cups total
        state = hopper.record_refill(25, lbs_added=2.5)  # 10 cups / 2.5 lb
        self.assertEqual(hopper.cups_per_lb(state), 4.0)
        self.assertEqual(hopper.status()['cups_per_lb'], 4.0)
        # 2.5 lb refilled 75% of the hopper → holds ~3.3 lb
        self.assertEqual(hopper.status()['capacity_lbs'], 3.3)

    def test_unweighed_refill_leaves_cups_per_lb_unknown(self):
        import hopper
        hopper.record_dispense(10)
        state = hopper.record_refill(25)
        self.assertIsNone(hopper.cups_per_lb(state))
        self.assertIsNone(hopper.capacity_lbs(state))

    def test_index_shows_capacity_in_lb_and_oz_once_weighed(self):
        import hopper
        import web_interface
        hopper.record_dispense(22.96)
        hopper.record_refill(12.5, lbs_added=7)
        card = _api_state(web_interface)['hopper']
        self.assertEqual(card['capacity_label'], 'Holds ~8 lb (128 oz), ~26.24 cups')
        self.assertEqual((card['headline'], card['lbs_label']), ('~100% full', '· ~8 lb'))
        hopper.record_dispense(13.12)  # half of 26.24 cups
        card = _api_state(web_interface)['hopper']
        self.assertEqual((card['headline'], card['lbs_label']), ('~50% full', '· ~4 lb'))

    def test_index_shows_cups_only_when_never_weighed(self):
        import hopper
        import web_interface
        hopper.record_dispense(10)
        hopper.record_refill(25)
        card = _api_state(web_interface)['hopper']
        self.assertEqual(card['capacity_label'], 'Holds ~13.33 cups')
        self.assertIsNone(card['lbs_label'])

    def test_weighed_refill_after_trivial_consumption_learns_nothing(self):
        import hopper
        hopper.record_dispense(0.5)
        state = hopper.record_refill(10, lbs_added=7)
        self.assertEqual(state['cups_per_lb_estimates'], [])

    def test_refill_without_dispenses_learns_nothing(self):
        import hopper
        state = hopper.record_refill(50)
        self.assertIsNone(hopper.capacity_cups(state))

    def test_refill_after_trivial_consumption_resets_but_learns_nothing(self):
        import hopper
        hopper.record_dispense(0.5)  # below MIN_LEARN_CUPS
        state = hopper.record_refill(10)
        self.assertIsNone(hopper.capacity_cups(state))
        self.assertEqual(state['cups_since_refill'], 0.0)

    def test_refill_at_learning_floor_still_learns(self):
        import hopper
        hopper.record_dispense(hopper.MIN_LEARN_CUPS)
        state = hopper.record_refill(0)  # emptied → capacity = cups dispensed
        self.assertAlmostEqual(hopper.capacity_cups(state), hopper.MIN_LEARN_CUPS)

    def test_capacity_is_median_of_recent_estimates(self):
        import hopper
        for est_source in [(7.5, 25), (10.0, 0), (20.0, 0)]:  # → 10, 10, 20
            cups, pct = est_source
            hopper.record_dispense(cups)
            hopper.record_refill(pct)
        self.assertAlmostEqual(hopper.capacity_cups(hopper.load_state()), 10.0)

    def test_low_warning_fires_once_per_cycle(self):
        import hopper
        hopper.record_dispense(9.0)
        hopper.record_refill(10)  # capacity ~10 cups
        hopper.record_dispense(9.0)  # 10% left → low
        self.assertIsNotNone(hopper.check_low(daily_avg_cups=1.0))
        self.assertIsNone(hopper.check_low(daily_avg_cups=1.0))  # already notified
        hopper.record_refill(10)  # reset re-arms the warning
        hopper.record_dispense(9.5)
        self.assertIsNotNone(hopper.check_low(daily_avg_cups=1.0))

    def test_corrupt_state_file_resets(self):
        import hopper
        with open(hopper.HOPPER_FILE, 'w') as f:
            f.write("not json{")
        info = hopper.status()
        self.assertTrue(info['learning'])

    def test_index_preselects_refill_guess_near_estimated_level(self):
        import hopper
        import web_interface
        hopper.record_dispense(9.0)
        hopper.record_refill(10)     # capacity ~10 cups
        hopper.record_dispense(4.0)  # level 0.6 → nearest choice is 50
        self.assertEqual(_api_state(web_interface)['hopper']['refill_default'], 50)

    def test_index_refill_guess_defaults_to_10_while_learning(self):
        import web_interface
        self.assertEqual(_api_state(web_interface)['hopper']['refill_default'], 10)

    def test_refill_route_validates_percentage(self):
        import web_interface
        client = web_interface.app.test_client()
        for bad in ('abc', '', '120', '-5'):
            r = client.post('/refill', data={'remaining_pct': bad},
                            headers={'Accept': 'application/json'})
            self.assertEqual(r.status_code, 400, bad)
            self.assertFalse(r.get_json()['success'])
        r = client.post('/refill', data={'remaining_pct': '25'},
                        headers={'Accept': 'application/json'})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()['success'])

    def test_refill_route_validates_lbs_added(self):
        import hopper
        import web_interface
        client = web_interface.app.test_client()
        hopper.record_dispense(10)
        for bad in ('abc', '0', '-2', '700', 'nan', 'inf'):
            r = client.post('/refill', data={'remaining_pct': '25', 'lbs_added': bad},
                            headers={'Accept': 'application/json'})
            self.assertEqual(r.status_code, 400, bad)
        # A rejected weight must not have recorded the refill
        self.assertEqual(hopper.load_state()['cups_since_refill'], 10)
        r = client.post('/refill', data={'remaining_pct': '25', 'lbs_added': ' 2.5 '},
                        headers={'Accept': 'application/json'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(hopper.cups_per_lb(hopper.load_state()), 4.0)

    def test_refill_route_treats_blank_lbs_as_unweighed(self):
        import hopper
        import web_interface
        client = web_interface.app.test_client()
        hopper.record_dispense(10)
        r = client.post('/refill', data={'remaining_pct': '25', 'lbs_added': ''},
                        headers={'Accept': 'application/json'})
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(hopper.cups_per_lb(hopper.load_state()))


if __name__ == '__main__':
    unittest.main()


class TestEventJournal(TempCwd):
    """feeding_events.jsonl: the durable per-event record."""

    def test_dispense_event_carries_numbers_and_hopper_counter(self):
        import events
        with patch('feeder_core.trigger_servo', return_value=0.31), \
             patch('feeder_core.notify.send'):
            feeder_core.feed_pet(portion='medium', base_time='07:00', scheduled_for='07:28')
        (ev,) = events.read_all()
        self.assertEqual(ev['event'], 'dispense')
        self.assertEqual(ev['portion'], 'medium')
        self.assertEqual(ev['cups'], feeding_stats.PORTION_CUPS['medium'])
        self.assertEqual(ev['source'], 'scheduled')
        self.assertEqual(ev['duration_s'], 0.31)
        self.assertEqual((ev['base_time'], ev['scheduled_for']), ('07:00', '07:28'))
        self.assertEqual(ev['hopper_cups'], feeding_stats.PORTION_CUPS['medium'])
        self.assertTrue(ev['sim'])  # test box has no GPIO — analysis must be able to filter this
        datetime.fromisoformat(ev['ts'])

    def test_manual_dispense_has_no_schedule_times(self):
        import events
        with patch('feeder_core.trigger_servo', return_value=0.2), \
             patch('feeder_core.notify.send'):
            feeder_core.feed_pet(portion='small', source='manual')
        (ev,) = events.read_all()
        self.assertEqual(ev['source'], 'manual')
        self.assertIsNone(ev['base_time'])
        self.assertIsNone(ev['scheduled_for'])

    def test_failure_event_records_error(self):
        import events
        with patch('feeder_core.trigger_servo', side_effect=RuntimeError("jam")), \
             patch('feeder_core.notify.send'):
            feeder_core.feed_pet(portion='large', source='manual')
        (ev,) = events.read_all()
        self.assertEqual(ev['event'], 'failure')
        self.assertEqual(ev['error'], 'jam')
        self.assertEqual(ev['portion'], 'large')

    def test_refill_event_records_estimate(self):
        import events, hopper
        for _ in range(4):
            hopper.record_dispense(2.5)
        hopper.record_refill(25)
        ev = events.read_all()[-1]
        self.assertEqual(ev['event'], 'refill')
        self.assertEqual(ev['remaining_pct'], 25)
        self.assertEqual(ev['cups_before'], 10.0)
        self.assertEqual(ev['capacity_estimate'], 13.33)
        self.assertEqual(ev['capacity'], 13.33)

    def test_refill_after_trivial_consumption_records_no_estimate(self):
        import events, hopper
        hopper.record_dispense(0.5)
        hopper.record_refill(50)
        ev = events.read_all()[-1]
        self.assertIsNone(ev['capacity_estimate'])
        self.assertIsNone(ev['capacity'])

    def test_low_event_written_once_per_cycle(self):
        import events, hopper
        with open(hopper.HOPPER_FILE, 'w') as f:
            # 7 of 8 cups gone → level 0.125 (exact in binary, so days_left is a clean 2)
            json.dump({'last_refill': '2026-01-01', 'cups_since_refill': 7.0,
                       'capacity_estimates': [8.0], 'low_notified': False}, f)
        self.assertIsNotNone(hopper.check_low(daily_avg_cups=0.5))
        self.assertIsNone(hopper.check_low(daily_avg_cups=0.5))
        lows = [e for e in events.read_all() if e['event'] == 'hopper_low']
        self.assertEqual(len(lows), 1)
        self.assertEqual(lows[0]['level'], 0.12)
        self.assertEqual(lows[0]['days_left'], 2)

    def test_write_failure_does_not_break_feeding(self):
        import events
        with patch('events.open', side_effect=OSError("disk full")), \
             patch('feeder_core.trigger_servo', return_value=0.2), \
             patch('feeder_core.notify.send'):
            self.assertTrue(feeder_core.feed_pet())

    def test_read_all_skips_malformed_lines(self):
        import events
        events.record('dispense', portion='small')
        with open(events.EVENTS_FILE, 'a') as f:
            f.write('not json\n')
        events.record('refill', remaining_pct=10)
        self.assertEqual([e['event'] for e in events.read_all()], ['dispense', 'refill'])

    def test_missing_file_reads_empty(self):
        import events
        self.assertEqual(events.read_all(), [])

    def test_unserializable_field_does_not_break_feeding(self):
        import events
        with patch('feeder_core.trigger_servo', return_value=object()), \
             patch('feeder_core.notify.send'):
            self.assertTrue(feeder_core.feed_pet())
        (ev,) = events.read_all()
        self.assertIsNone(ev['duration_s'])


class TestScheduleStore(TempCwd):
    """feeding_schedules.txt round-trips and crash-safe writes."""

    def test_round_trip_normalizes_legacy_lines(self):
        import schedule_store
        with open('feeding_schedules.txt', 'w') as f:
            f.write("08:00,fixed\n\n 12:00 , large \n")
        schedule_store.write_entries(schedule_store.read_entries())
        with open('feeding_schedules.txt') as f:
            self.assertEqual(f.read(), "08:00,small,fixed\n12:00,large\n")

    def test_missing_file_reads_empty(self):
        import schedule_store
        self.assertEqual(schedule_store.read_entries(), [])

    def test_failed_write_leaves_original_and_no_debris(self):
        # The power-cut case: if the swap never happens, the old file survives intact
        import atomicfile
        with open('state.txt', 'w') as f:
            f.write("original")
        with patch('atomicfile.os.replace', side_effect=OSError("power cut")):
            with self.assertRaises(OSError):
                atomicfile.write_atomic('state.txt', "new")
        with open('state.txt') as f:
            self.assertEqual(f.read(), "original")
        self.assertEqual(os.listdir('.'), ['state.txt'])

    def test_missing_schedule_file_pages(self):
        with patch('feeder_core.notify.send') as mock_send:
            self.assertEqual(feeder_core.generate_todays_schedule(), [])
        self.assertIn("No feedings", mock_send.call_args[0][0])
        self.assertTrue(os.path.isfile('feeding_schedules.txt'))

    def test_nonempty_schedule_does_not_page(self):
        with open('feeding_schedules.txt', 'w') as f:
            f.write("08:00,small,fixed\n")
        with patch('feeder_core.notify.send') as mock_send:
            feeder_core.generate_todays_schedule()
        mock_send.assert_not_called()


class TestScheduleRoutes(TempCwd):
    def setUp(self):
        super().setUp()
        schedule.clear()
        import web_interface
        self.client = web_interface.app.test_client()
        self.json = {'Accept': 'application/json'}

    def tearDown(self):
        schedule.clear()
        super().tearDown()

    def _file(self):
        with open('feeding_schedules.txt') as f:
            return f.read()

    def test_add_rejects_malformed_time_without_writing(self):
        for bad in ['7:5', '25:00', 'noon', '']:
            resp = self.client.post('/add', data={'feeding_time': bad}, headers=self.json)
            self.assertEqual(resp.status_code, 400, bad)
        self.assertFalse(os.path.exists('feeding_schedules.txt'))

    def test_add_then_duplicate_conflicts(self):
        resp = self.client.post('/add', data={'feeding_time': '23:58', 'portion': 'large'},
                                headers=self.json)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._file(), "23:58,large,fixed\n")
        resp = self.client.post('/add', data={'feeding_time': '23:58'}, headers=self.json)
        self.assertEqual(resp.status_code, 409)

    def test_toggle_legacy_fixed_line_randomizes_it(self):
        with open('feeding_schedules.txt', 'w') as f:
            f.write("08:00,fixed\n")
        self.client.post('/toggle_fixed', data={'base_time': '08:00'}, headers=self.json)
        self.assertEqual(self._file(), "08:00,small\n")

    def test_toggle_fixed_after_fed_keeps_todays_time(self):
        # Fed early at 17:10; fixing it must not set up a second meal at 17:30
        import events
        with open('feeding_schedules.txt', 'w') as f:
            f.write("17:30,medium\n")
        feeder_core.save_todays_schedule([{'base_time': '17:30', 'actual_time': '17:10', 'portion': 'medium',
                                           'is_fixed': False, 'randomized': True, 'graze': False}])
        events.record('dispense', portion='medium', cups=0.5, source='scheduled',
                      base_time='17:30', scheduled_for='17:10')
        self.client.post('/toggle_fixed', data={'base_time': '17:30'}, headers=self.json)
        self.assertEqual(self._file(), "17:30,medium,fixed\n")
        today = feeder_core.load_todays_schedule()[0]
        self.assertEqual((today['actual_time'], today['is_fixed']), ('17:10', True))

    def test_toggle_unknown_time_is_404(self):
        resp = self.client.post('/toggle_fixed', data={'base_time': '09:00'}, headers=self.json)
        self.assertEqual(resp.status_code, 404)

    def test_update_portion_and_delete(self):
        with open('feeding_schedules.txt', 'w') as f:
            f.write("08:00,small,fixed\n12:00,small\n")
        self.client.post('/update_portion', data={'base_time': '08:00', 'portion': 'large'},
                         headers=self.json)
        self.client.post('/delete', data={'base_time': '12:00'}, headers=self.json)
        self.assertEqual(self._file(), "08:00,large,fixed\n")

    def test_edits_return_fresh_state(self):
        resp = self.client.post('/add', data={'feeding_time': '23:58', 'portion': 'large'},
                                headers=self.json)
        rows = resp.get_json()['state']['schedule']
        self.assertEqual(rows, [{'base_time': '23:58', 'time': '11:58 PM',
                                 'portion': 'large', 'is_fixed': True, 'graze': False,
                                 'graze_problem': 'A graze needs 30 minutes before midnight. '
                                                  'Move it before 11:30 PM.'}])
        resp = self.client.post('/update_portion', data={'base_time': '23:58', 'portion': 'small'},
                                headers=self.json)
        self.assertEqual(resp.get_json()['state']['schedule'][0]['portion'], 'small')
        resp = self.client.post('/delete', data={'base_time': '23:58'}, headers=self.json)
        self.assertEqual(resp.get_json()['state']['schedule'], [])

    def test_edit_still_succeeds_when_state_snapshot_fails(self):
        # The write landed; a broken snapshot must not report it as failed
        with patch('responses.view_state.build', side_effect=RuntimeError('boom')):
            resp = self.client.post('/add', data={'feeding_time': '23:58'}, headers=self.json)
        body = resp.get_json()
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(body['success'])
        self.assertNotIn('state', body)
        self.assertIn('23:58', self._file())

    def test_form_posts_without_json_still_redirect(self):
        resp = self.client.post('/add', data={'feeding_time': '23:58'})
        self.assertEqual(resp.status_code, 302)

    def test_api_state_is_json_safe_and_read_only(self):
        import web_interface
        with open('feeding_schedules.txt', 'w') as f:
            f.write("08:00,small,fixed\n")
        before = self._file()
        state = _api_state(web_interface)
        self.assertEqual(self._file(), before)
        self.assertEqual(state['schedule'][0]['time'], '8:00 AM')
        self.assertEqual([p['letter'] for p in state['portions']], ['S', 'M', 'L'])
        self.assertEqual(state['asset_version'], web_interface.view_state.ASSET_VERSION)

    def test_index_embeds_initial_state_and_sw_is_version_stamped(self):
        import web_interface
        html = self.client.get('/').get_data(as_text=True)
        self.assertIn('id="initial-state"', html)
        sw = self.client.get('/sw.js').get_data(as_text=True)
        self.assertIn(web_interface.view_state.ASSET_VERSION, sw)
        self.assertNotIn('__ASSET_VERSION__', sw)

    def test_dashboard_loads_nothing_off_box(self):
        # The fonts once came from Google: every dashboard open called out,
        # and the page lost its fonts whenever the internet was down.
        import re
        html = self.client.get('/').get_data(as_text=True)
        css = self.client.get('/static/styles.css').get_data(as_text=True)
        for name, body in (('index.html', html), ('styles.css', css)):
            refs = re.findall(r'(?:src|href)\s*=\s*["\'](https?:)?//|url\(\s*["\']?(?:https?:)?//', body)
            self.assertEqual(refs, [], f'{name} references an outside host')

    def test_every_precached_file_is_served(self):
        # cache.addAll rejects the whole install if one entry 404s, which
        # would leave the phone with no offline copy at all.
        import re
        sw = self.client.get('/sw.js').get_data(as_text=True)
        paths = re.findall(r"^\s*'(/[^']*)',", sw, re.M)
        self.assertIn('/static/fonts/fredoka-latin.woff2', paths)
        for path in paths:
            self.assertEqual(self.client.get(path).status_code, 200, path)


def _theme_tokens(css, selector):
    """The --color-* custom properties set in one theme block of styles.css."""
    import re
    block = re.search(re.escape(selector) + r'\s*\{(.*?)\n\}', css, re.S).group(1)
    return dict(re.findall(r'--(color-[a-z-]+):\s*(#[0-9a-fA-F]{6})\s*;', block))


def _contrast(fg, bg):
    """WCAG contrast ratio of two #rrggbb colours."""
    def luminance(hex_colour):
        channels = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]
    light, dark = sorted((luminance(fg), luminance(bg)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


class TestKibbleLook(unittest.TestCase):
    """The Kibble palette: readable text in both themes, and no teal left over."""

    # (text, background) token pairs the dashboard actually draws
    TEXT_PAIRS = [
        ('color-text', 'color-bg'), ('color-text', 'color-surface'),
        ('color-text-muted', 'color-bg'), ('color-text-muted', 'color-surface'),
        ('color-primary', 'color-surface'), ('color-on-primary', 'color-primary'),
        ('color-on-brand', 'color-brand'), ('color-on-ink', 'color-ink'),
        ('color-brand', 'color-ink'), ('color-accent-dark', 'color-surface'),
        ('color-danger', 'color-surface'), ('color-on-danger', 'color-danger'),
        ('color-text', 'color-brand-soft'),
    ]

    @classmethod
    def setUpClass(cls):
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, 'static', 'styles.css')) as f:
            css = f.read()
        light = _theme_tokens(css, ':root')
        cls.themes = {'light': light, 'dark': {**light, **_theme_tokens(css, '[data-theme="dark"]')}}

    def test_text_pairs_pass_aa_in_both_themes(self):
        for theme, tokens in self.themes.items():
            for fg, bg in self.TEXT_PAIRS:
                ratio = _contrast(tokens[fg], tokens[bg])
                self.assertGreaterEqual(ratio, 4.5, f'{theme}: {fg} on {bg} is {ratio:.2f}:1')

    def test_contrast_check_catches_a_failing_pair(self):
        # Breaker: yellow text on cream is the mistake this guards against
        self.assertLess(_contrast('#f2b632', '#faf4ea'), 4.5)
        self.assertAlmostEqual(_contrast('#000000', '#ffffff'), 21.0, places=1)

    def test_toasts_follow_an_open_dialog(self):
        # A modal <dialog> covers the page and makes it inert: a toast left
        # outside the schedule sheet was neither seen nor announced
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, 'static/js/toast.js')) as f:
            body = f.read()
        self.assertIn("document.querySelector('dialog[open]')", body)
        self.assertIn('host.appendChild(container)', body)

    def test_old_teal_and_coral_are_gone(self):
        here = os.path.dirname(os.path.abspath(__file__))
        old = ('#00838f', '#005f66', '#00acc1', '#00767f', '#f0f4f4', '#0d2224',
               '#f08080', '#e06666', '0,131,143', '240,128,128', '0, 84, 92')
        for rel in ('static/styles.css', 'static/icon.svg', 'static/logo.svg',
                    'static/manifest.json', 'templates/index.html',
                    'static/js/theme.js', 'static/js/cards/next.js'):
            with open(os.path.join(here, rel)) as f:
                body = f.read().lower()
            for colour in old:
                self.assertNotIn(colour, body, f'{rel} still has {colour}')


def _load_check_live():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'check_live', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ops', 'check_live.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestShipLiveCheck(TempCwd):
    """ship.sh's simulation guard, run against real /api/state output — so a
    change to the state shape fails here instead of blinding the smoke test."""

    def setUp(self):
        super().setUp()
        self.check = _load_check_live()

    def _state_body(self, simulation):
        import web_interface
        with patch('view_state.SIMULATION_MODE', simulation):
            return web_interface.app.test_client().get('/api/state').get_data(as_text=True)

    def test_live_state_passes(self):
        ok, reason = self.check.state_is_live(self._state_body(False))
        self.assertTrue(ok, reason)

    def test_simulation_state_fails(self):
        ok, _ = self.check.state_is_live(self._state_body(True))
        self.assertFalse(ok)

    def test_state_fails_closed_on_anything_unproven(self):
        for body in ['', 'not json', '{}', '{"success": true, "data": {}}',
                     '{"success": false, "data": {"app": {"simulation": false}}}',
                     '{"success": true, "data": {"app": {"simulation": "false"}}}',
                     '{"success": true, "data": {"app": {"simulation": null}}}']:
            self.assertFalse(self.check.state_is_live(body)[0], body)

    def test_env_check(self):
        self.assertTrue(self.check.env_is_live('READABLE\n')[0])
        self.assertTrue(self.check.env_is_live('READABLE\nPETFEEDR_SIMULATE=false\n')[0])
        self.assertFalse(self.check.env_is_live('READABLE\nPETFEEDR_SIMULATE=True\n')[0])
        self.assertFalse(self.check.env_is_live('')[0])  # couldn't read: fail closed
        self.assertFalse(self.check.env_is_live('PETFEEDR_SIMULATE=false\n')[0])


class TestLearnedCupsPerLb(unittest.TestCase):
    def test_consumption_uses_learned_ratio(self):
        week = [{'total_cups': 2.0, 'total_feedings': 4}]
        self.assertEqual(feeding_stats.calculate_consumption_rate(week)['daily_lbs'], 0.5)
        self.assertEqual(feeding_stats.calculate_consumption_rate(
            week, cups_per_lb=5.0)['daily_lbs'], 0.4)


class TestManualFeedGuards(TempCwd):
    def setUp(self):
        super().setUp()
        import web_interface
        self.wi = web_interface
        web_interface._last_manual_feed = None
        self.client = web_interface.app.test_client()
        self.json = {'Accept': 'application/json'}
        with open('feeding_schedules.txt', 'w') as f:
            f.write("08:00,medium\n17:00,medium\n20:00,small\n")  # 1.25 cups → allowance 0.75 (floor)

    def _log_manual(self, portion, n):
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open('feeding_log.txt', 'a') as f:
            for _ in range(n):
                f.write(f"{stamp},100 - INFO - Feeding completed in 0.31s ({portion} portion, manual)\n")

    def test_allowance_is_half_schedule_with_floor(self):
        import manual_limit
        self.assertEqual(manual_limit.allowance_cups(), 0.75)
        with open('feeding_schedules.txt', 'w') as f:
            f.write("06:00,large\n12:00,large\n18:00,large\n")  # 2.25 cups
        self.assertEqual(manual_limit.allowance_cups(), 1.125)

    def test_feed_within_allowance_dispenses(self):
        with patch('web_interface.feed_pet', return_value=True) as mock_feed:
            resp = self.client.post('/feed', data={'portion': 'small'}, headers=self.json)
        self.assertEqual(resp.status_code, 200)
        mock_feed.assert_called_once()
        self.assertIn('feed', resp.get_json()['state'])  # UI updates in place from this

    def test_failed_dispense_is_an_error_without_state(self):
        with patch('web_interface.feed_pet', return_value=False):
            resp = self.client.post('/feed', data={'portion': 'small'}, headers=self.json)
        self.assertEqual(resp.status_code, 500)
        self.assertFalse(resp.get_json()['success'])
        self.assertNotIn('state', resp.get_json())

    def test_feed_over_allowance_refused_without_dispensing(self):
        self._log_manual('medium', 1)  # 0.5 used; a medium would make 1.0 > 0.75
        with patch('web_interface.feed_pet') as mock_feed:
            resp = self.client.post('/feed', data={'portion': 'medium'}, headers=self.json)
        self.assertEqual(resp.status_code, 429)
        self.assertIn('0.5 of 0.75', resp.get_json()['message'])
        mock_feed.assert_not_called()

    def test_scheduled_feeds_do_not_count_toward_manual_cap(self):
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open('feeding_log.txt', 'w') as f:
            f.write(f"{stamp},1 - INFO - Feeding completed in 0.3s (large portion, scheduled)\n" * 3)
        with patch('web_interface.feed_pet', return_value=True):
            resp = self.client.post('/feed', data={'portion': 'small'}, headers=self.json)
        self.assertEqual(resp.status_code, 200)

    def test_second_feed_within_cooldown_refused(self):
        with patch('web_interface.feed_pet', return_value=True) as mock_feed:
            self.client.post('/feed', data={'portion': 'small'}, headers=self.json)
            resp = self.client.post('/feed', data={'portion': 'small'}, headers=self.json)
        self.assertEqual(resp.status_code, 429)
        self.assertEqual(mock_feed.call_count, 1)

    def test_failed_feed_does_not_start_cooldown(self):
        with patch('web_interface.feed_pet', side_effect=[False, True]) as mock_feed:
            self.client.post('/feed', data={'portion': 'small'}, headers=self.json)
            resp = self.client.post('/feed', data={'portion': 'small'}, headers=self.json)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(mock_feed.call_count, 2)

    def test_cross_origin_post_refused(self):
        with patch('web_interface.feed_pet') as mock_feed:
            resp = self.client.post('/feed', data={'portion': 'small'},
                                    headers={**self.json, 'Origin': 'https://evil.example'})
        self.assertEqual(resp.status_code, 403)
        mock_feed.assert_not_called()

    def test_same_origin_and_originless_posts_allowed(self):
        with patch('web_interface.feed_pet', return_value=True):
            resp = self.client.post('/feed', data={'portion': 'small'},
                                    headers={**self.json, 'Origin': 'http://localhost'})
        self.assertEqual(resp.status_code, 200)
        resp = self.client.post('/add', data={'feeding_time': '23:59'}, headers=self.json)
        self.assertEqual(resp.status_code, 200)

    def test_rebound_host_refused(self):
        # DNS rebinding: the attacker's name, same-origin, still refused.
        with patch('web_interface.feed_pet') as mock_feed:
            resp = self.client.post('/feed', data={'portion': 'small'},
                                    headers={**self.json, 'Host': 'evil.example:5000',
                                             'Origin': 'http://evil.example:5000'})
        self.assertEqual(resp.status_code, 421)
        mock_feed.assert_not_called()
        self.assertEqual(self.client.get('/api/state', headers={'Host': 'evil.example:5000'}).status_code, 421)

    def test_own_hosts_allowed(self):
        from web_interface import own_host
        for host in ('localhost:5000', 'petfeedr:5000', 'petfeedr.local:5000',
                     'petfeedr.tail0000.ts.net', '192.168.1.40:5000', '100.64.0.9:5000', '[::1]:5000'):
            self.assertTrue(own_host(host), host)
        for host in ('evil.example', 'petfeedr.evil.example', 'ts.net.evil.example', '[::1', ''):
            self.assertFalse(own_host(host), host)


class TestGpioLoading(unittest.TestCase):
    def test_off_pi_missing_gpio_simulates(self):
        import DRV8825
        _, sim = DRV8825.load_gpio(force_simulate=False, is_pi=False)
        self.assertTrue(sim)  # RPi.GPIO isn't installed on the dev box

    def test_on_pi_missing_gpio_refuses_to_simulate(self):
        import DRV8825
        with self.assertRaises(ImportError):
            DRV8825.load_gpio(force_simulate=False, is_pi=True)

    def test_forced_simulation_wins_on_pi(self):
        import DRV8825
        _, sim = DRV8825.load_gpio(force_simulate=True, is_pi=True)
        self.assertTrue(sim)


class TestCatchUp(TempCwd):
    """Startup catch-up of feedings that came due while the process was down."""
    NOW = datetime.combine(date.today(), datetime.strptime("14:20", "%H:%M").time())

    def setUp(self):
        super().setUp()
        feeder_core.save_todays_schedule([
            {'base_time': t, 'actual_time': t, 'portion': 'small', 'is_fixed': True, 'randomized': False}
            for t in ('06:00', '14:00', '18:00')])

    def _journal(self, *entries):
        with open('feeding_events.jsonl', 'w') as f:
            for e in entries:
                f.write(json.dumps({'ts': f"{date.today().isoformat()}T{e[1]}:01", 'event': e[0],
                                    'scheduled_for': e[1]}) + '\n')

    def _run(self):
        with patch('feeder_core.feed_pet', return_value=True) as mock_feed, \
             patch('feeder_core.notify.send') as mock_send:
            result = feeder_core.catch_up_missed(now=self.NOW)
        return result, mock_feed, mock_send

    def test_recent_miss_is_caught_up_once(self):
        self._journal(('dispense', '06:00'))
        result, mock_feed, mock_send = self._run()
        self.assertEqual(result, ['14:00'])
        self.assertEqual(mock_feed.call_args.kwargs,
                         {'portion': 'small', 'base_time': '14:00', 'scheduled_for': '14:00',
                          'late_by_min': 20})
        self.assertEqual(mock_send.call_args.kwargs, {'priority': -1})

    def test_already_dispensed_is_not_refed(self):
        self._journal(('dispense', '06:00'), ('dispense', '14:00'))
        result, mock_feed, _ = self._run()
        self.assertEqual(result, [])
        mock_feed.assert_not_called()

    def test_log_line_counts_when_journal_write_failed(self):
        # The journal is fail-soft; a lost event must not become a second meal
        self._journal(('dispense', '06:00'))
        with open('feeding_log.txt', 'w') as f:
            f.write(f"{date.today().isoformat()} 14:00:01,277 - INFO - "
                    "Feeding completed in 0.31s (small portion, scheduled)\n")
        result, mock_feed, _ = self._run()
        self.assertEqual(result, [])

    def test_manual_feed_near_slot_does_not_count(self):
        self._journal(('dispense', '06:00'))
        with open('feeding_log.txt', 'w') as f:
            f.write(f"{date.today().isoformat()} 14:00:30,1 - INFO - "
                    "Feeding completed in 0.31s (small portion, manual)\n")
        result, _, _ = self._run()
        self.assertEqual(result, ['14:00'])

    def test_failure_is_not_retried(self):
        self._journal(('dispense', '06:00'), ('failure', '14:00'))
        result, mock_feed, _ = self._run()
        mock_feed.assert_not_called()

    def test_old_miss_is_left_to_watchdog(self):
        # 06:00 is 8h late and unfed: too late to dispense automatically
        self._journal(('dispense', '14:00'))
        result, mock_feed, _ = self._run()
        self.assertEqual(result, [])
        mock_feed.assert_not_called()

    def test_failed_catch_up_does_not_notify_success(self):
        self._journal(('dispense', '06:00'))
        with patch('feeder_core.feed_pet', return_value=False), \
             patch('feeder_core.notify.send') as mock_send:
            self.assertEqual(feeder_core.catch_up_missed(now=self.NOW), [])
        mock_send.assert_not_called()

    def test_real_dispense_records_lateness_in_journal(self):
        self._journal(('dispense', '06:00'))
        with patch('feeder_core.trigger_servo', return_value=0.3), \
             patch('feeder_core.notify.send'):
            feeder_core.catch_up_missed(now=self.NOW)
        with open('feeding_events.jsonl') as f:
            last = json.loads(f.readlines()[-1])
        self.assertEqual((last['scheduled_for'], last['late_by_min']), ('14:00', 20))

    def test_second_restart_does_not_refeed_a_caught_up_slot(self):
        # Journal lost, but the log shows the catch-up already ran (its
        # "completed" line is late, outside the normal match window)
        self._journal(('dispense', '06:00'))
        with open('feeding_log.txt', 'w') as f:
            f.write(f"{date.today().isoformat()} 14:19:58,1 - INFO - Catching up missed 14:00 feeding (19 min late)\n"
                    f"{date.today().isoformat()} 14:19:59,1 - INFO - Feeding completed in 0.31s (small portion, scheduled)\n")
        result, mock_feed, _ = self._run()
        mock_feed.assert_not_called()

    def test_sim_journal_events_still_count(self):
        with open('feeding_events.jsonl', 'w') as f:
            for t in ('06:00', '14:00'):
                f.write(json.dumps({'ts': f"{date.today().isoformat()}T14:19:00", 'event': 'dispense',
                                    'scheduled_for': t, 'sim': True}) + '\n')
        result, mock_feed, _ = self._run()
        mock_feed.assert_not_called()


class TestSlotOutcome(unittest.TestCase):
    TODAY = date(2026, 9, 29)

    def _ev(self, kind, slot, **kw):
        return {'ts': f'2026-09-29T{slot}:01', 'event': kind, 'scheduled_for': slot, **kw}

    def test_journal_outcomes(self):
        so = feeder_core.slot_outcome
        self.assertEqual(so('06:00', self.TODAY, [self._ev('dispense', '06:00')], []), ('fed', None))
        self.assertEqual(so('06:00', self.TODAY, [self._ev('dispense', '06:00', late_by_min=12)], []),
                         ('late', 12))
        self.assertEqual(so('06:00', self.TODAY, [self._ev('failure', '06:00')], []), ('failed', None))
        self.assertEqual(so('06:00', self.TODAY, [self._ev('missed', '06:00', late_by_min=90)], []),
                         ('missed', 90))
        self.assertEqual(so('06:00', self.TODAY, [], []), (None, None))

    def test_dispense_wins_over_failure(self):
        evs = [self._ev('failure', '06:00'), self._ev('dispense', '06:00')]
        self.assertEqual(feeder_core.slot_outcome('06:00', self.TODAY, evs, [])[0], 'fed')

    def test_log_fallbacks(self):
        catch = ["2026-09-29 06:20:00,1 - INFO - Catching up missed 06:00 feeding (20 min late)\n"]
        self.assertEqual(feeder_core.slot_outcome('06:00', self.TODAY, [], catch), ('late', 20))
        done = ["2026-09-29 06:00:02,1 - INFO - Feeding completed in 0.3s (small portion, scheduled)\n"]
        self.assertEqual(feeder_core.slot_outcome('06:00', self.TODAY, [], done), ('fed', None))


class TestCatchUpRecordsMisses(TempCwd):
    def test_too_late_slot_is_journaled_as_missed_once(self):
        feeder_core.save_todays_schedule([{'base_time': '06:00', 'actual_time': '06:00',
                                           'portion': 'small', 'is_fixed': True, 'randomized': False}])
        noon = datetime.combine(date.today(), datetime.strptime("12:00", "%H:%M").time())
        with patch('feeder_core.feed_pet') as mock_feed:
            feeder_core.catch_up_missed(now=noon)
            feeder_core.catch_up_missed(now=noon)  # second restart: already recorded
        mock_feed.assert_not_called()
        import events
        missed = [e for e in events.read_all() if e['event'] == 'missed']
        self.assertEqual(len(missed), 1)
        self.assertEqual((missed[0]['scheduled_for'], missed[0]['late_by_min']), ('06:00', 360))


class TestOnTimeSummary(unittest.TestCase):
    TODAY = date(2026, 9, 29)

    @staticmethod
    def _ev(day, kind='dispense', **kw):
        return {'ts': f'2026-09-{day:02d}T06:00:01', 'event': kind, 'scheduled_for': '06:00', **kw}

    def test_streak_counts_back_from_today(self):
        evs = [self._ev(d) for d in (26, 27, 28, 29)]
        s = feeding_stats.on_time_summary(evs, self.TODAY)
        self.assertEqual((s['streak_days'], s['week_on_time'], s['week_total']), (4, 4, 4))

    def test_empty_today_neither_extends_nor_breaks(self):
        s = feeding_stats.on_time_summary([self._ev(d) for d in (27, 28)], self.TODAY)
        self.assertEqual(s['streak_days'], 2)

    def test_late_missed_or_failed_breaks_streak(self):
        for bad in (self._ev(28, late_by_min=10), self._ev(28, 'missed'), self._ev(28, 'failure')):
            evs = [self._ev(27), self._ev(28), bad, self._ev(29)]
            s = feeding_stats.on_time_summary(evs, self.TODAY)
            self.assertEqual(s['streak_days'], 1, bad)
            self.assertEqual((s['week_on_time'], s['week_total']), (3, 4))

    def test_manual_feeds_are_ignored(self):
        manual = {'ts': '2026-09-29T12:00:00', 'event': 'dispense', 'source': 'manual', 'scheduled_for': None}
        s = feeding_stats.on_time_summary([manual], self.TODAY)
        self.assertEqual((s['streak_days'], s['week_total']), (0, 0))


class TestRhythmWeek(unittest.TestCase):
    TODAY = date(2026, 9, 29)
    ENTRIES = [{'time': '06:00', 'portion': 'small', 'is_fixed': True},
               {'time': '18:00', 'portion': 'medium', 'is_fixed': False}]

    @staticmethod
    def _ev(ts, kind='dispense', **kw):
        return {'ts': ts, 'event': kind, **kw}

    def test_graze_sits_at_its_start_with_a_trail(self):
        from servo_controller import graze_pulse_count
        evs = [self._ev('2026-09-29T18:30:05', portion='medium', cups=0.5, source='graze',
                        scheduled_for='18:00'),
               self._ev('2026-09-28T18:20:00', portion='medium', cups=0.375, source='graze',
                        scheduled_for='18:00', partial=True, pulses=3)]
        r = feeding_stats.rhythm_week(evs, self.TODAY, self.ENTRIES, pulse_count=graze_pulse_count)
        (done,) = r['days'][-1]['marks']
        self.assertEqual((done['kind'], done['pct']), ('grazed', feeding_stats._day_pct('18:00')))
        self.assertEqual((done['pulses'], done['landed'], done['cut_short']), (4, 4, False))
        self.assertEqual(done['label'], '6:00 PM · medium · grazed')
        (stopped,) = r['days'][-2]['marks']
        self.assertEqual((stopped['landed'], stopped['cut_short']), (3, True))
        self.assertIn('stopped after 3 of 4', stopped['label'])

    def test_late_graze_keeps_its_tail(self):
        from servo_controller import graze_pulse_count
        evs = [self._ev('2026-09-29T19:10:00', portion='small', cups=0.25, source='graze',
                        scheduled_for='18:00', late_by_min=40)]
        (mark,) = feeding_stats.rhythm_week(evs, self.TODAY, self.ENTRIES,
                                            pulse_count=graze_pulse_count)['days'][-1]['marks']
        self.assertEqual(mark['pct'], feeding_stats._day_pct('18:40'))
        self.assertEqual(mark['from_pct'], feeding_stats._day_pct('18:00'))

    def test_graze_without_a_pulse_count_draws_as_fed(self):
        evs = [self._ev('2026-09-29T18:30:05', portion='medium', cups=0.5, source='graze',
                        scheduled_for='18:00')]
        (mark,) = feeding_stats.rhythm_week(evs, self.TODAY, self.ENTRIES)['days'][-1]['marks']
        self.assertEqual(mark['kind'], 'fed')

    def test_marks_land_on_the_right_day_and_time(self):
        evs = [self._ev('2026-09-29T06:00:01', portion='small', cups=0.25, source='scheduled'),
               self._ev('2026-09-28T12:00:00', portion='large', cups=0.75, source='manual'),
               self._ev('2026-09-22T06:00:00', portion='small', cups=0.25)]  # 7 days ago: off the chart
        r = feeding_stats.rhythm_week(evs, self.TODAY, self.ENTRIES)
        self.assertEqual([d['date'] for d in r['days']][0], '2026-09-23')
        today, yesterday = r['days'][-1], r['days'][-2]
        self.assertEqual((today['day_label'], today['total_cups']), ('Today', 0.25))
        self.assertEqual([(m['kind'], m['pct']) for m in today['marks']], [('fed', 25.0)])
        self.assertEqual([(m['kind'], m['portion']) for m in yesterday['marks']], [('manual', 'large')])
        self.assertEqual(sum(d['total_cups'] for d in r['days']), 1.0)

    def test_late_missed_failed_and_upcoming(self):
        evs = [self._ev('2026-09-29T06:30:00', portion='small', cups=0.25, scheduled_for='06:00', late_by_min=30),
               self._ev('2026-09-28T07:00:00', 'missed', scheduled_for='06:00'),
               self._ev('2026-09-27T06:00:00', 'failure', portion='small')]
        r = feeding_stats.rhythm_week(evs, self.TODAY, self.ENTRIES, upcoming_times=['18:00'])
        late, upcoming = r['days'][-1]['marks']
        self.assertEqual((late['kind'], late['from_pct'], late['pct']), ('late', 25.0, 27.08))
        self.assertEqual(upcoming['kind'], 'upcoming')
        self.assertEqual(r['days'][-2]['marks'][0]['kind'], 'missed')
        self.assertEqual(r['days'][-2]['marks'][0]['pct'], 25.0)  # at the slot, not when it was noticed
        self.assertEqual((r['days'][-3]['marks'][0]['kind'], r['days'][-3]['total_cups']), ('failed', 0))

    def test_guides_widen_for_randomized_slots(self):
        g = feeding_stats.rhythm_week([], self.TODAY, self.ENTRIES)['guides']
        self.assertEqual([(x['pct'], x['half_width']) for x in g], [(25.0, 0), (75.0, 2.08)])

    def test_junk_events_are_skipped_not_raised(self):
        evs = [{'event': 'dispense'}, {'ts': '2026-09-29Tbroken', 'event': 'dispense'},
               self._ev('2026-09-29T08:00:00', 'refill')]
        r = feeding_stats.rhythm_week(evs, self.TODAY, [])
        self.assertTrue(all(not d['marks'] for d in r['days']))


class TestDashboardExtras(TempCwd):
    def test_manual_status_marks_portions_that_no_longer_fit(self):
        import manual_limit
        with open('feeding_schedules.txt', 'w') as f:
            f.write("08:00,small\n")  # allowance hits the 0.75 floor
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open('feeding_log.txt', 'w') as f:
            f.write(f"{stamp},1 - INFO - Feeding completed in 0.3s (small portion, manual)\n")
        st = manual_limit.status()
        self.assertEqual((st['used'], st['remaining']), (0.25, 0.5))
        self.assertEqual(st['fits'], {'small': True, 'medium': True, 'large': False})

    def test_cups_formatter(self):
        import view_state
        f = view_state.cups
        self.assertEqual([f(0), f(0.25), f(0.5), f(1), f(1.25), f(2)],
                         ['0 cups', '¼ cup', '½ cup', '1 cup', '1¼ cups', '2 cups'])

    def test_refill_by_date_leaves_low_alert_margin(self):
        import dashboard
        import hopper
        with patch('dashboard.hopper.status', return_value={
                'last_refill': '2026-09-17', 'level': 0.5, 'days_left': 10}):
            card = dashboard.hopper_card({'daily_cups': 1.25})
        refill_by = date.today() + timedelta(days=10 - hopper.LOW_DAYS)
        self.assertIn(f"{refill_by:%b} {refill_by.day}", card['refill_by_label'])

    def test_refill_by_today_when_nearly_empty(self):
        import dashboard
        with patch('dashboard.hopper.status', return_value={
                'last_refill': '2026-09-17', 'level': 0.05, 'days_left': 1}):
            self.assertEqual(dashboard.hopper_card({'daily_cups': 1.25})['refill_by_label'], 'today')

    def test_index_renders_every_slot_status(self):
        import web_interface
        slots = ['00:01', '00:02', '00:03', '00:04', '23:59']
        with open('feeding_schedules.txt', 'w') as f:
            f.writelines(f"{s},small,fixed\n" for s in slots)
        feeder_core.save_todays_schedule([{'base_time': s, 'actual_time': s, 'portion': 'small',
                                           'is_fixed': True, 'randomized': False} for s in slots])
        t = date.today().isoformat()
        with open('feeding_events.jsonl', 'w') as f:
            for kind, slot, extra in (('dispense', '00:01', {}), ('dispense', '00:02', {'late_by_min': 7}),
                                      ('failure', '00:03', {})):
                f.write(json.dumps({'ts': f'{t}T{slot}:01', 'event': kind, 'scheduled_for': slot, **extra}) + '\n')
        state = _api_state(web_interface)
        slots_by_key = {s['key']: s for s in state['timeline']['slots']}
        self.assertEqual([slots_by_key[k]['status'] for k in slots],
                         ['fed', 'late', 'failed', 'missed', 'upcoming'])
        self.assertEqual(slots_by_key['00:02']['badge'], '+7m')
        self.assertEqual(slots_by_key['00:03']['status_text'], 'Failed — check for a jam')
        today_marks = {m['kind'] for m in state['stats']['days'][-1]['marks']}
        self.assertEqual(today_marks, {'fed', 'late', 'failed', 'upcoming'})
        self.assertEqual(len(state['stats']['guides']), len(slots))


class TestGrazeTrail(unittest.TestCase):
    SLOT = {'actual_time_24h': '17:30', 'portion': 'medium'}

    @staticmethod
    def _ev(kind, **kw):
        return {'ts': '2026-11-15T17:30:00', 'event': kind, 'scheduled_for': '17:30', **kw}

    def _trail(self, *evs, slot=None):
        import dashboard
        return dashboard.graze_trail(slot or self.SLOT, list(evs))

    def test_upcoming_running_and_finished(self):
        self.assertEqual(self._trail(), {'pulses': 4, 'landed': 0, 'cut_short': False})
        pulses = [self._ev('graze_pulse', pulse=i, of=4) for i in (1, 2)]
        self.assertEqual(self._trail(*pulses)['landed'], 2)
        done = self._trail(*pulses, self._ev('dispense', source='graze'))
        self.assertEqual(done, {'pulses': 4, 'landed': 4, 'cut_short': False})

    def test_stopped_early(self):
        pulses = [self._ev('graze_pulse', pulse=i, of=4) for i in (1, 2, 3)]
        partial = self._ev('dispense', source='graze', partial=True, pulses=3)
        self.assertEqual(self._trail(*pulses, partial), {'pulses': 4, 'landed': 3, 'cut_short': True})
        failed = self._trail(pulses[0], self._ev('failure', source='graze'))
        self.assertTrue(failed['cut_short'])
        # Failing on the first pulse is a failed slot, not a cut-short trail
        self.assertFalse(self._trail(self._ev('failure', source='graze'))['cut_short'])

    def test_fed_all_at_once_has_no_trail(self):
        # The midnight fallback feeds a graze slot whole
        self.assertIsNone(self._trail(self._ev('dispense', source='scheduled')))

    def test_journal_count_wins_over_an_edited_portion(self):
        pulses = [self._ev('graze_pulse', pulse=1, of=6)]
        self.assertEqual(self._trail(*pulses, slot={**self.SLOT, 'portion': 'small'})['pulses'], 6)

    def test_other_slots_events_are_ignored(self):
        other = {**self._ev('graze_pulse', pulse=3, of=4), 'scheduled_for': '08:00'}
        self.assertEqual(self._trail(other)['landed'], 0)

    def test_bar_end_pads_for_a_late_graze(self):
        import dashboard
        def slots(graze, portion='large'):
            return [{'actual_time_24h': '06:00', 'graze': False, 'portion': 'small'},
                    {'actual_time_24h': '20:00', 'graze': graze, 'portion': portion}]
        _, plain_end, _ = dashboard.build_timeline(slots(False))
        self.assertEqual(plain_end, 21 * 60)
        start, end, _ = dashboard.build_timeline(slots(True))
        # The large trail's px on the narrowest bar fit between the slot and the end
        trail_share = (dashboard.TRAIL_LEAD_PX + 4 * dashboard.TRAIL_GAP_PX) / dashboard.NARROWEST_BAR_PX
        self.assertGreaterEqual((end - 20 * 60) / (end - start), trail_share)
        # A late graze runs the bar past midnight rather than off its end,
        # and the hour labels wrap
        late = [{'actual_time_24h': '06:00', 'graze': False, 'portion': 'small'},
                {'actual_time_24h': '23:20', 'graze': True, 'portion': 'large'}]
        start, end, hours = dashboard.build_timeline(late)
        self.assertGreaterEqual((end - (23 * 60 + 20)) / (end - start), trail_share)
        self.assertIn('12AM', [h['label'] for h in hours])
        # A plain last slot keeps the old cap
        _, plain_late, _ = dashboard.build_timeline([{'actual_time_24h': '23:20', 'graze': False,
                                                      'portion': 'large'}])
        self.assertEqual(plain_late, 1439)


def _element_html(page, element_id, tag=None):
    """The markup inside one element, found by id (or the first `tag`),
    following real nesting: a stray close tag ends it where a browser would."""
    from html.parser import HTMLParser
    voids = {'meta', 'link', 'img', 'input', 'br', 'hr', 'source'}

    class Finder(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=False)
            self.depth = None
            self.start = self.end = None

        def handle_starttag(self, name, attrs):
            if name in voids:
                return
            if self.depth is None and (dict(attrs).get('id') == element_id if element_id else name == tag):
                self.depth, self.start = 0, self.getpos()
            if self.depth is not None and self.end is None:
                self.depth += 1

        def handle_endtag(self, name):
            if name in voids or self.depth is None or self.end is not None:
                return
            self.depth -= 1
            if self.depth == 0:
                self.end = self.getpos()

    finder = Finder()
    finder.feed(page)
    lines = page.split('\n')
    (l1, c1), (l2, c2) = finder.start, finder.end
    if l1 == l2:
        return lines[l1 - 1][c1:c2]
    return '\n'.join([lines[l1 - 1][c1:]] + lines[l1:l2 - 1] + [lines[l2 - 1][:c2]])


class TestShipGrazeGuard(unittest.TestCase):
    def test_guard_covers_the_whole_graze(self):
        # It was hardcoded to 21 minutes; grazes went to 30 and the guard didn't follow
        here = os.path.dirname(os.path.abspath(__file__))
        script = os.path.join(here, 'ship.sh')
        with open(script) as f:
            body = f.read()
        self.assertNotRegex(body, r'GRAZE_GUARD_MIN=\d')
        guard = body[body.index('GRAZE_SPAN_S=$('):body.index('GRAZE_GUARD_MIN=$((')]
        import subprocess
        # $0 is "bash" here, so the script's dirname is the working directory
        out = subprocess.run(['bash', '-c', guard + 'echo "$GRAZE_SPAN_S"'], cwd=here,
                             capture_output=True, text=True)
        from servo_controller import GRAZE_SPAN_S
        self.assertEqual(out.stdout.strip(), str(GRAZE_SPAN_S))


class TestHopperPanel(TempCwd):
    """The hopper in the header is the feed button; Today's bar shows extras."""

    def setUp(self):
        super().setUp()
        import web_interface
        self.web = web_interface
        self.client = web_interface.app.test_client()

    def _events(self, *evs):
        with open('feeding_events.jsonl', 'w') as f:
            f.writelines(json.dumps(e) + '\n' for e in evs)

    def test_extras_show_on_todays_bar(self):
        with open('feeding_schedules.txt', 'w') as f:
            f.write("08:00,small,fixed\n18:00,small,fixed\n")
        today = date.today().isoformat()
        self._events({'ts': f'{today}T12:05:00', 'event': 'dispense', 'source': 'manual', 'portion': 'small'},
                     {'ts': f'{today}T13:40:00', 'event': 'dispense', 'source': 'porch', 'portion': 'small'},
                     {'ts': f'{today}T08:00:02', 'event': 'dispense', 'source': 'scheduled', 'portion': 'small'},
                     {'ts': '2001-01-01T12:00:00', 'event': 'dispense', 'source': 'manual', 'portion': 'large'},
                     {'ts': 'not a time', 'event': 'dispense', 'source': 'manual'})
        timeline = _api_state(self.web)['timeline']
        self.assertEqual([x['label'] for x in timeline['extras']],
                         ['12:05 PM, extra small feed', '1:40 PM, extra small feed from the porch'])
        self.assertTrue(all(0 < x['pct'] < 100 for x in timeline['extras']))
        self.assertEqual(len({x['key'] for x in timeline['extras']}), 2)

    def test_an_extra_outside_the_schedule_widens_the_bar(self):
        with open('feeding_schedules.txt', 'w') as f:
            f.write("12:00,small,fixed\n")
        self._events({'ts': f'{date.today().isoformat()}T05:30:00', 'event': 'dispense',
                      'source': 'manual', 'portion': 'small'})
        timeline = _api_state(self.web)['timeline']
        self.assertLessEqual(timeline['start'], 5 * 60 + 30)
        self.assertGreaterEqual(timeline['extras'][0]['pct'], 0)

    def test_no_extras_is_an_empty_list(self):
        with open('feeding_schedules.txt', 'w') as f:
            f.write("12:00,small,fixed\n")
        self.assertEqual(_api_state(self.web)['timeline']['extras'], [])

    def test_feed_and_hopper_cards_moved_into_the_panel(self):
        page = self.client.get('/').get_data(as_text=True)
        self.assertNotIn('feed-card', page)
        self.assertNotIn('hopper-card', page)
        self.assertIn('aria-controls="feed-panel"', page)
        self.assertIn('aria-expanded="false"', page)
        panel = _element_html(page, 'feed-panel')
        # It drops from the header, so it lives inside it
        self.assertIn('id="feed-panel"', _element_html(page, None, tag='header'))
        for control in ('feed-circle-btn', 'id="feed-portions"', 'id="extras-chip"', 'id="grazing-note"',
                        'id="hopper-body"', 'refill-form', 'id="refill-toggle"'):
            self.assertIn(control, panel)
        # Hidden until the hopper is tapped; the Feed button stays inert until feed.js loads
        self.assertIn('id="feed-panel" hidden', page)
        self.assertIn('class="feed-circle-btn" disabled', page)

    def test_every_module_is_precached(self):
        # A module the service worker doesn't cache breaks the app offline
        import re
        here = os.path.dirname(os.path.abspath(__file__))
        sw = self.client.get('/sw.js').get_data(as_text=True)
        cached = set(re.findall(r"'(/static/js/[^']+)'", sw))
        for root, _, files in os.walk(os.path.join(here, 'static', 'js')):
            for name in files:
                rel = '/' + os.path.relpath(os.path.join(root, name), here).replace(os.sep, '/')
                self.assertIn(rel, cached, f'{rel} is not precached')

    def test_learning_hopper_shows_no_bare_question_mark(self):
        # Before the first refill there's no level; a lone "?" told nobody anything
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, 'static/js/cards/hopper.js')) as f:
            body = f.read()
        self.assertIn('badge.hidden = Boolean(hopper.learning)', body)
        self.assertNotIn("'?'", body)

    def test_pour_only_follows_a_change_and_respects_reduced_motion(self):
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, 'static/js/pour.js')) as f:
            body = f.read()
        # The first state is the baseline: nothing pours on load
        self.assertIn('if (previous) {', body)
        self.assertIn('reducedMotion() || document.hidden', body)


class TestGrazingDisplay(TempCwd):
    """A running graze reads as grazing on the dashboard; the feeder still sees it handled."""

    def setUp(self):
        super().setUp()
        self.now = datetime.now().replace(second=0, microsecond=0)
        self.slot_dt = self.now - timedelta(minutes=10)
        # The graze runs 30 minutes from its slot; that must end today too
        if self.slot_dt.date() != (self.slot_dt + timedelta(minutes=30)).date():
            self.skipTest("too close to midnight")
        self.slot = self.slot_dt.strftime("%H:%M")
        with open('feeding_schedules.txt', 'w') as f:
            f.write(f"{self.slot},medium,fixed,graze\n")
        feeder_core.save_todays_schedule([{'base_time': self.slot, 'actual_time': self.slot,
                                           'portion': 'medium', 'is_fixed': True,
                                           'randomized': False, 'graze': True}])

    def _ev(self, kind, at, **kw):
        return {'ts': at.isoformat(timespec='seconds'), 'event': kind, 'scheduled_for': self.slot, **kw}

    def _write(self, *evs):
        with open('feeding_events.jsonl', 'w') as f:
            f.writelines(json.dumps(e) + '\n' for e in evs)

    def _until_12h(self):
        return (self.slot_dt + timedelta(minutes=30)).strftime("%I:%M %p").lstrip("0")

    def test_running_graze_shows_grazing_on_timeline_and_feed_card(self):
        import web_interface
        self._write(self._ev('graze_pulse', self.slot_dt, pulse=1, of=4))
        state = _api_state(web_interface)
        slot = state['timeline']['slots'][0]
        self.assertEqual(slot['status'], 'grazing')
        self.assertEqual(slot['status_text'], f"Grazing · until {self._until_12h()}")
        self.assertEqual(state['feed']['grazing_label'], f"Grazing now, done at {self._until_12h()}")
        # Display only: the feeder's answer is untouched, so no catch-up or re-fire
        today = self.now.date()
        self.assertEqual(feeder_core.slot_outcome(self.slot, today, *feeder_core.todays_records(today)),
                         ('fed', None))

    def test_trail_reads_on_the_dot(self):
        import web_interface
        self._write(self._ev('graze_pulse', self.slot_dt, pulse=1, of=4),
                    self._ev('graze_pulse', self.slot_dt, pulse=2, of=4))
        slot = _api_state(web_interface)['timeline']['slots'][0]
        self.assertEqual(slot['trail'], {'pulses': 4, 'landed': 2, 'cut_short': False})
        self.assertEqual(slot['trail_text'], 'graze, 2 of 4 pulses so far')

    def test_finished_graze_reads_fed_and_clears_the_note(self):
        import web_interface
        self._write(self._ev('graze_pulse', self.slot_dt, pulse=1, of=4),
                    self._ev('dispense', self.slot_dt + timedelta(minutes=30), source='graze', cups=0.5))
        state = _api_state(web_interface)
        self.assertEqual(state['timeline']['slots'][0]['status'], 'fed')
        self.assertEqual(state['feed']['grazing_label'], '')

    def test_cut_short_or_failed_graze_never_shows_grazing(self):
        import dashboard
        pulse = self._ev('graze_pulse', self.slot_dt, pulse=2, of=4)
        partial = self._ev('dispense', self.slot_dt, source='graze', partial=True, cups=0.25)
        failed = self._ev('failure', self.slot_dt, source='graze')
        for closing in (partial, failed):
            self.assertIsNone(dashboard.grazing_until(self.slot, [pulse, closing], self.now))
        self.assertIsNone(dashboard.grazing_until(self.slot, [], self.now))

    def test_graze_with_no_closing_record_stops_showing_after_its_span(self):
        import dashboard
        pulse = self._ev('graze_pulse', self.slot_dt, pulse=1, of=4)
        end = self.slot_dt + timedelta(minutes=30)
        self.assertEqual(dashboard.grazing_until(self.slot, [pulse], end), end)
        late = end + timedelta(minutes=dashboard.GRAZING_STALE_MIN + 1)
        self.assertIsNone(dashboard.grazing_until(self.slot, [pulse], late))


class TestScheduledFeedIdempotent(TempCwd):
    def setUp(self):
        super().setUp()
        schedule.clear()

    def tearDown(self):
        schedule.clear()
        super().tearDown()

    def test_unfed_slot_dispenses(self):
        with patch('feeder_core.feed_pet', return_value=True) as mock_feed:
            self.assertTrue(feeder_core.scheduled_feed('small', '03:00', '03:00'))
        mock_feed.assert_called_once_with(portion='small', base_time='03:00', scheduled_for='03:00')

    def test_slot_already_fed_today_is_skipped(self):
        with open('feeding_events.jsonl', 'w') as f:
            f.write(json.dumps({'ts': f'{date.today().isoformat()}T03:00:01', 'event': 'dispense',
                                'scheduled_for': '03:00'}) + '\n')
        with patch('feeder_core.feed_pet') as mock_feed:
            self.assertFalse(feeder_core.scheduled_feed('small', '03:00', '03:00'))
        mock_feed.assert_not_called()

    def test_yesterdays_feed_does_not_block_today(self):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        with open('feeding_events.jsonl', 'w') as f:
            f.write(json.dumps({'ts': f'{yesterday}T03:00:01', 'event': 'dispense',
                                'scheduled_for': '03:00'}) + '\n')
        with patch('feeder_core.feed_pet', return_value=True) as mock_feed:
            feeder_core.scheduled_feed('small', '03:00', '03:00')
        mock_feed.assert_called_once()

    def test_power_cut_replay_does_not_double_feed(self):
        # 2026-09-30: fed at 03:00, power cut, Pi boots on a clock 38 min slow.
        # 03:00 looks upcoming again, gets registered, and fires when NTP jumps
        # the clock forward. The motor must run once, not twice.
        feeder_core.save_todays_schedule([{'base_time': '03:00', 'actual_time': '03:00',
                                           'portion': 'small', 'is_fixed': True, 'randomized': False}])
        with patch('feeder_core.trigger_servo', return_value=0.3) as motor, \
             patch('feeder_core.notify.send'):
            feeder_core.resync_today()
            schedule.jobs[0].run()   # the real 03:00 feed, before the outage
            feeder_core.resync_today()  # reboot on the stale clock re-registers it
            schedule.jobs[0].run()   # clock jumps; the overdue job fires
        self.assertEqual(motor.call_count, 1)


class TestClockWait(unittest.TestCase):
    def test_off_pi_returns_immediately(self):
        sleeps = []
        self.assertTrue(feeder_core.wait_for_clock_sync(is_pi=False, flag='/nonexistent', sleep=sleeps.append))
        self.assertEqual(sleeps, [])

    def test_already_synced_returns_immediately(self):
        with tempfile.NamedTemporaryFile() as flag:
            self.assertTrue(feeder_core.wait_for_clock_sync(is_pi=True, flag=flag.name,
                                                            sleep=lambda s: self.fail("slept")))

    def test_waits_until_flag_appears(self):
        with tempfile.TemporaryDirectory() as d:
            flag = os.path.join(d, 'synchronized')
            ticks = []

            def fake_sleep(seconds):
                ticks.append(seconds)
                if len(ticks) == 3:
                    open(flag, 'w').close()

            self.assertTrue(feeder_core.wait_for_clock_sync(
                is_pi=True, flag=flag, sleep=fake_sleep, monotonic=lambda: len(ticks) * 5))
            self.assertEqual(len(ticks), 3)

    def test_timeout_proceeds_and_alerts(self):
        ticks = []
        with patch('feeder_core.notify.send') as mock_send:
            synced = feeder_core.wait_for_clock_sync(
                timeout_s=20, is_pi=True, flag='/nonexistent/flag',
                sleep=ticks.append, monotonic=lambda: len(ticks) * 5)
        self.assertFalse(synced)  # feeds anyway — no network must not mean no food
        self.assertEqual(len(ticks), 4)
        self.assertIn("without a synced clock", mock_send.call_args[0][0])


class TestGraze(TempCwd):
    """Graze: a portion dispensed as pulses spread over GRAZE_SPAN_S."""

    def setUp(self):
        super().setUp()
        schedule.clear()
        self.today = date.today()

    def tearDown(self):
        schedule.clear()
        super().tearDown()

    def _slot(self, slot='17:30'):
        return feeder_core.slot_outcome(slot, self.today, *feeder_core.todays_records(self.today))

    def test_schedule_line_round_trips_graze_flag(self):
        import schedule_store
        self.assertEqual(parse_schedule_line("17:30,medium,graze"), ("17:30", "medium", False, True))
        self.assertEqual(parse_schedule_line("17:30,medium,graze,fixed"), ("17:30", "medium", True, True))
        self.assertEqual(parse_schedule_line("08:00,fixed,graze"), ("08:00", "small", True, True))
        with open('feeding_schedules.txt', 'w') as f:
            f.write("07:00,medium\n17:30,medium,graze,fixed\n")
        schedule_store.write_entries(schedule_store.read_entries())
        with open('feeding_schedules.txt') as f:
            self.assertEqual(f.read(), "07:00,medium\n17:30,medium,fixed,graze\n")

    def test_pulses_add_up_to_the_regular_portion(self):
        from servo_controller import graze_pulse_count, GRAZE_PULSE_STEPS, BASE_STEPS, get_portion_cycles
        for portion, pulses in [('small', 2), ('medium', 4), ('large', 6)]:
            self.assertEqual(graze_pulse_count(portion), pulses)
            self.assertEqual(pulses * GRAZE_PULSE_STEPS, get_portion_cycles(portion) * BASE_STEPS)

    def test_full_graze_spreads_pulses_and_records_once(self):
        import events
        jiggles, sleeps, lock_free = [], [], []

        def fake_sleep(seconds):
            sleeps.append(seconds)
            # Between pulses another thread must be able to take the lock
            # (a manual feed, a schedule edit) — it's never held across a gap
            from threading import Thread
            t = Thread(target=lambda: lock_free.append(feeder_core.STATE_LOCK.acquire(timeout=1)
                                                       and feeder_core.STATE_LOCK.release() is None))
            t.start()
            t.join()

        with patch('feeder_core.trigger_pulse', side_effect=lambda jiggle: jiggles.append(jiggle)), \
             patch('feeder_core.notify.send'), \
             self.assertLogs('petfeedr', 'INFO') as logs:
            self.assertTrue(feeder_core.run_graze('medium', '17:30', '17:30', sleep=fake_sleep))

        self.assertEqual(jiggles, [True, False, True, False])
        self.assertEqual(sleeps, [600.0, 600.0, 600.0])  # first pulse at the slot, last at +30 min
        self.assertEqual(lock_free, [True, True, True])
        completed = [l for l in logs.output if 'Feeding completed' in l]
        self.assertEqual(len(completed), 1)
        self.assertIn('(medium portion, graze)', completed[0])
        dispenses = [e for e in events.read_all() if e['event'] == 'dispense']
        self.assertEqual(len(dispenses), 1)
        self.assertEqual((dispenses[0]['cups'], dispenses[0]['source']), (0.5, 'graze'))
        self.assertEqual(dispenses[0]['hopper_cups'], 0.5)
        self.assertEqual(len([e for e in events.read_all() if e['event'] == 'graze_pulse']), 4)
        self.assertEqual(self._slot(), ('fed', None))

    def test_completed_line_parses_as_one_scheduled_style_feeding(self):
        line = "2026-11-14 17:40:00,123 - INFO - Feeding completed in 600.12s (medium portion, graze)"
        m = feeding_stats.COMPLETED_RE.match(line)
        self.assertEqual((m['portion'], m['source']), ('medium', 'graze'))

    def test_failed_pulse_stops_graze_and_records_partial(self):
        import events
        with patch('feeder_core.trigger_pulse', side_effect=[0.3, 0.3, RuntimeError("jam")]) as pulse, \
             patch('feeder_core.notify.send') as mock_send:
            self.assertFalse(feeder_core.run_graze('medium', '17:30', '17:30', sleep=lambda s: None))
        self.assertEqual(pulse.call_count, 3)  # no grinding on after the jam
        self.assertIn("FAILED", mock_send.call_args_list[0][0][0])
        kinds = [e['event'] for e in events.read_all()]
        self.assertEqual(kinds, ['graze_pulse', 'graze_pulse', 'failure', 'dispense'])
        partial = events.read_all()[-1]
        self.assertEqual((partial['cups'], partial['partial'], partial['pulses']), (0.25, True, 2))
        self.assertEqual(self._slot(), ('failed', None))

    def test_restart_mid_graze_records_what_came_out_and_never_refires(self):
        import events, hopper
        for n in (1, 2):  # the process died after pulse 2 of 4
            events.record('graze_pulse', portion='medium', base_time='17:30',
                          scheduled_for='17:30', pulse=n, of=4)
        with patch('feeder_core.notify.send'), self.assertLogs('petfeedr', 'WARNING') as logs:
            self.assertEqual(feeder_core.reconcile_grazes(self.today), ['17:30'])
        self.assertIn('stopped after 2/4 pulses', logs.output[0])
        self.assertNotIn('Feeding completed', logs.output[0])  # not a whole portion
        partial = events.read_all()[-1]
        self.assertEqual((partial['event'], partial['cups']), ('dispense', 0.25))
        self.assertEqual(hopper.load_state()['cups_since_refill'], 0.25)
        self.assertEqual(self._slot(), ('fed', None))
        self.assertEqual(feeder_core.reconcile_grazes(self.today), [])  # idempotent

        # The slot's job, if it ran again (clock jump), must not start another graze
        with patch('feeder_core.start_graze') as start:
            self.assertFalse(feeder_core.scheduled_feed('medium', '17:30', '17:30', graze=True))
        start.assert_not_called()

    def test_graze_under_way_counts_as_handled(self):
        import events
        events.record('graze_pulse', portion='small', base_time='17:30',
                      scheduled_for='17:30', pulse=1, of=2)
        self.assertEqual(self._slot(), ('fed', None))

    def test_log_pulse_line_marks_slot_when_journal_is_missing(self):
        stamp = f"{self.today.isoformat()} 17:30:01,100"
        lines = [f"{stamp} - INFO - Graze pulse 1/4 for 17:30 slot\n"]
        self.assertEqual(feeder_core.slot_outcome('17:30', self.today, [], lines), ('fed', None))
        self.assertEqual(feeder_core.slot_outcome('07:00', self.today, [], lines), (None, None))

    def test_graze_finishing_near_another_slot_does_not_claim_it(self):
        # A 17:30 graze completes ~17:50; a 17:50 slot is still unfed
        d = self.today.isoformat()
        lines = [f"{d} 17:30:00,100 - INFO - Graze pulse 1/4 for 17:30 slot\n",
                 f"{d} 17:50:02,100 - INFO - Feeding completed in 1202.00s (medium portion, graze)\n"]
        self.assertEqual(feeder_core.slot_outcome('17:50', self.today, [], lines), (None, None))
        self.assertEqual(feeder_core.slot_outcome('17:30', self.today, [], lines), ('fed', None))

    def test_scheduled_graze_starts_in_background(self):
        # Pinned: the real check reads the clock, and after 23:30 any graze crosses midnight
        with patch('feeder_core.start_graze') as start, patch('feeder_core.feed_pet') as feed, \
             patch('feeder_core.graze_crosses_midnight', return_value=False):
            self.assertTrue(feeder_core.scheduled_feed('large', '17:30', '17:34', graze=True))
        start.assert_called_once_with('large', '17:30', '17:34')
        feed.assert_not_called()

    def test_resync_registers_graze_flag(self):
        feeder_core.save_todays_schedule([{'base_time': '00:00', 'actual_time': '00:00',
                                           'portion': 'small', 'is_fixed': True,
                                           'randomized': False, 'graze': True}])
        feeder_core.resync_today()
        self.assertTrue(schedule.jobs[0].job_func.keywords['graze'])

    def test_catch_up_grazes_a_missed_graze_slot(self):
        slot = (datetime.now() - timedelta(minutes=5)).strftime("%H:%M")
        if slot > datetime.now().strftime("%H:%M"):
            self.skipTest("too close to midnight")
        feeder_core.save_todays_schedule([{'base_time': slot, 'actual_time': slot, 'portion': 'small',
                                           'is_fixed': True, 'randomized': False, 'graze': True}])
        with patch('feeder_core.start_graze') as start, patch('feeder_core.feed_pet') as feed, \
             patch('feeder_core.notify.send'), \
             patch('feeder_core.graze_crosses_midnight', return_value=False):
            self.assertEqual(feeder_core.catch_up_missed(), [slot])
        self.assertEqual(start.call_args.kwargs['late_by_min'], 5)
        feed.assert_not_called()

    def test_graze_crossing_midnight_is_detected(self):
        d = self.today
        self.assertTrue(feeder_core.graze_crosses_midnight(datetime.combine(d, datetime.strptime("23:50", "%H:%M").time())))
        self.assertFalse(feeder_core.graze_crosses_midnight(datetime.combine(d, datetime.strptime("23:29", "%H:%M").time())))

    def test_late_graze_slot_feeds_all_at_once(self):
        # Pulses after midnight would mark tomorrow's 23:50 as fed — every other day unfed
        with patch('feeder_core.graze_crosses_midnight', return_value=True), \
             patch('feeder_core.start_graze') as start, \
             patch('feeder_core.feed_pet', return_value=True) as feed:
            self.assertTrue(feeder_core.scheduled_feed('medium', '23:50', '23:50', graze=True))
        start.assert_not_called()
        feed.assert_called_once_with(portion='medium', base_time='23:50', scheduled_for='23:50')

    def test_late_graze_catch_up_feeds_all_at_once(self):
        feeder_core.save_todays_schedule([{'base_time': '23:50', 'actual_time': '23:50', 'portion': 'small',
                                           'is_fixed': True, 'randomized': False, 'graze': True}])
        now = datetime.combine(self.today, datetime.strptime("23:55", "%H:%M").time())
        with patch('feeder_core.start_graze') as start, \
             patch('feeder_core.feed_pet', return_value=True) as feed, \
             patch('feeder_core.notify.send'):
            self.assertEqual(feeder_core.catch_up_missed(now=now), ['23:50'])
        start.assert_not_called()
        self.assertEqual(feed.call_args.kwargs['late_by_min'], 5)


class TestGrazeRoute(TempCwd):
    def setUp(self):
        super().setUp()
        schedule.clear()
        import web_interface
        self.client = web_interface.app.test_client()
        self.json = {'Accept': 'application/json'}

    def tearDown(self):
        schedule.clear()
        super().tearDown()

    def test_toggle_graze_keeps_todays_time(self):
        with open('feeding_schedules.txt', 'w') as f:
            f.write("17:30,medium\n")
        feeder_core.save_todays_schedule([{'base_time': '17:30', 'actual_time': '17:47',
                                           'portion': 'medium', 'is_fixed': False, 'randomized': True}])
        resp = self.client.post('/toggle_graze', data={'base_time': '17:30'}, headers=self.json)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()['state']['schedule'][0]['graze'])
        with open('feeding_schedules.txt') as f:
            self.assertEqual(f.read(), "17:30,medium,graze\n")
        (today,) = feeder_core.load_todays_schedule()
        self.assertEqual((today['actual_time'], today['graze']), ('17:47', True))
        self.client.post('/toggle_graze', data={'base_time': '17:30'}, headers=self.json)
        with open('feeding_schedules.txt') as f:
            self.assertEqual(f.read(), "17:30,medium\n")

    def test_toggle_fixed_keeps_graze(self):
        with open('feeding_schedules.txt', 'w') as f:
            f.write("17:30,medium,graze\n")
        self.client.post('/toggle_fixed', data={'base_time': '17:30'}, headers=self.json)
        with open('feeding_schedules.txt') as f:
            self.assertEqual(f.read(), "17:30,medium,fixed,graze\n")
        self.assertTrue(feeder_core.load_todays_schedule()[0]['graze'])

    def test_toggle_graze_unknown_time_is_404(self):
        resp = self.client.post('/toggle_graze', data={'base_time': '09:00'}, headers=self.json)
        self.assertEqual(resp.status_code, 404)

    def _write(self, text):
        with open('feeding_schedules.txt', 'w') as f:
            f.write(text)

    def _file(self):
        with open('feeding_schedules.txt') as f:
            return f.read()

    def test_graze_past_midnight_cutoff_refused(self):
        self._write("23:10,medium\n23:30,small,fixed\n")
        for base_time in ('23:10', '23:30'):
            resp = self.client.post('/toggle_graze', data={'base_time': base_time}, headers=self.json)
            self.assertEqual(resp.status_code, 409, base_time)
            self.assertIn("before midnight", resp.get_json()['message'])
        self.assertEqual(self._file(), "23:10,medium\n23:30,small,fixed\n")

    def test_fixed_graze_at_the_cutoff_allowed(self):
        self._write("23:29,small,fixed\n")
        resp = self.client.post('/toggle_graze', data={'base_time': '23:29'}, headers=self.json)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._file(), "23:29,small,fixed,graze\n")

    def test_late_fixed_graze_cant_go_back_to_varying(self):
        self._write("23:15,small,fixed,graze\n")
        resp = self.client.post('/toggle_fixed', data={'base_time': '23:15'}, headers=self.json)
        self.assertEqual(resp.status_code, 409)
        self.assertIn("before 11:00 PM", resp.get_json()['message'])
        self.assertEqual(self._file(), "23:15,small,fixed,graze\n")

    def test_graze_too_close_to_another_slot_refused(self):
        self._write("17:30,medium\n18:00,small\n")
        resp = self.client.post('/toggle_graze', data={'base_time': '17:30'}, headers=self.json)
        self.assertEqual(resp.status_code, 409)
        self.assertIn("Too close to 6:00 PM", resp.get_json()['message'])
        self.assertEqual(self._file(), "17:30,medium\n18:00,small\n")

    def test_add_too_close_to_a_graze_refused(self):
        self._write("17:30,medium,graze\n")
        resp = self.client.post('/add', data={'feeding_time': '17:55'}, headers=self.json)
        self.assertEqual(resp.status_code, 409)
        self.assertIn("Too close to 5:30 PM", resp.get_json()['message'])
        resp = self.client.post('/add', data={'feeding_time': '18:10'}, headers=self.json)
        self.assertEqual(resp.status_code, 200)

    def test_rule_breaking_graze_saved_earlier_still_loads(self):
        import web_interface
        self._write("23:45,medium,graze\n")
        self.assertEqual(self.client.get('/').status_code, 200)
        (row,) = _api_state(web_interface)['schedule']
        self.assertTrue(row['graze'])
        self.assertIn("before midnight", row['graze_problem'])
        self.assertEqual(self._file(), "23:45,medium,graze\n")
        # It can always be turned off
        resp = self.client.post('/toggle_graze', data={'base_time': '23:45'}, headers=self.json)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._file(), "23:45,medium\n")

    def test_editor_rows_say_which_can_graze(self):
        import web_interface
        self._write("08:00,small\n17:30,medium,graze\n17:50,small\n")
        rows = {r['base_time']: r['graze_problem'] for r in _api_state(web_interface)['schedule']}
        self.assertIsNone(rows['08:00'])
        self.assertIn("Too close to 5:50 PM", rows['17:30'])
        self.assertIn("Too close to 5:30 PM", rows['17:50'])

    def test_daily_roll_steers_around_a_later_fixed_slot(self):
        self._write("08:00,small\n08:20,small,fixed\n")
        with patch('feeder_core.random.randint', side_effect=[15, -20]):
            schedule_today = feeder_core.generate_todays_schedule()
        self.assertEqual([s['actual_time'] for s in schedule_today], ['07:40', '08:20'])

    def test_cross_origin_toggle_graze_refused(self):
        with open('feeding_schedules.txt', 'w') as f:
            f.write("17:30,medium\n")
        resp = self.client.post('/toggle_graze', data={'base_time': '17:30'},
                                headers={**self.json, 'Origin': 'https://evil.example'})
        self.assertEqual(resp.status_code, 403)
        with open('feeding_schedules.txt') as f:
            self.assertEqual(f.read(), "17:30,medium\n")


JPEG = b'\xff\xd8\xff\xe0 a frame\xff\xd9'


class FakeCamera:
    """Stands in for the long-running ffmpeg: a pipe of frames.

    `frames` are written up front; the pipe then stays open, like a live
    stream between frames, until kill() or end() closes it."""

    def __init__(self, frames=30, body=JPEG, garbage=b''):
        read_fd, self._write_fd = os.pipe()
        self.stdout = os.fdopen(read_fd, 'rb', buffering=0)
        os.write(self._write_fd, body * frames + garbage)
        self.killed = False

    def end(self):
        if self._write_fd is not None:
            os.close(self._write_fd)
            self._write_fd = None

    def kill(self):
        self.killed = True
        self.end()

    def wait(self, timeout=None):
        return -9

    def close(self):
        self.end()
        self.stdout.close()


class CameraCase(TempCwd):
    """A camera that exists: an ordinary file stands in for /dev/video0."""

    def setUp(self):
        super().setUp()
        import camera
        self.camera = camera
        open('video0', 'w').close()
        self.pause = MagicMock()
        self.spawned = []
        for p in (patch.object(camera, 'DEVICE', os.path.abspath('video0')),
                  patch('camera.on_raspberry_pi', return_value=True),
                  # The wait before a retry; no test sits through it
                  patch.object(camera, '_pause', self.pause),
                  patch.object(camera, '_stream', None),
                  patch.object(camera, '_MIN_FRAME_BYTES', 0),   # the test frame is a few bytes
                  # No test starts a real ffmpeg
                  patch.object(camera, '_spawn', side_effect=AssertionError("no camera set up"))):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: [c.close() for c in self.spawned])

    def _cameras(self, *cameras):
        """Each stream start gets the next of these; an exception is raised instead."""
        queue = list(cameras)

        def spawn():
            nxt = queue.pop(0)
            if isinstance(nxt, BaseException):
                raise nxt
            self.spawned.append(nxt)
            return nxt

        return patch.object(self.camera, '_spawn', side_effect=spawn)

    def _frame(self, when, body=JPEG):
        os.makedirs(self.camera.FRAMES_DIR, exist_ok=True)
        path = os.path.join(self.camera.FRAMES_DIR, when.strftime(self.camera.NAME_FORMAT))
        with open(path, 'wb') as f:
            f.write(body)
        return path


class TestCamera(CameraCase):
    def test_a_shot_saves_the_streams_newest_frame_named_by_local_time(self):
        newest = b'\xff\xd8\xff\xe0 newest\xff\xd9'
        cam = FakeCamera(frames=25)
        os.write(cam._write_fd, newest)
        cam.end()   # so the reader has seen every frame before the shot is taken
        with self._cameras(cam):
            self.assertFalse(self.camera.snap(False, datetime(2026, 11, 14, 16, 4, 30)))
        with open(os.path.join('data', 'frames', '2026-11-14T160430.jpg'), 'rb') as f:
            self.assertEqual(f.read(), newest)
        self.assertEqual(os.listdir(self.camera.FRAMES_DIR), ['2026-11-14T160430.jpg'])  # no .part left

    def test_stream_command_is_a_list_at_720p_from_the_camera(self):
        argv = self.camera._stream_argv()
        self.assertIsInstance(argv, list)   # no shell
        self.assertEqual(argv[0], 'ffmpeg')
        self.assertIn('1280x720', argv)
        self.assertIn(self.camera.DEVICE, argv)
        # Passed through, not re-encoded: that is what keeps it cheap on the Pi
        self.assertEqual(argv[argv.index('-c') + 1], 'copy')

    def test_one_stream_serves_every_shot(self):
        # The webcam jams on stop-then-start, so a healthy stream is never restarted
        with self._cameras(FakeCamera()) as spawn:
            failing = False
            for minute in (4, 9, 14):
                failing = self.camera.snap(failing, datetime(2026, 11, 14, 16, minute, 30))
        self.assertFalse(failing)
        self.assertEqual(spawn.call_count, 1)
        self.assertFalse(self.spawned[0].killed)
        self.assertEqual(len(self.camera.frame_names()), 3)

    def test_no_shot_from_a_stream_that_is_still_warming_up(self):
        # The webcam opens dark; its first frames must not be saved
        with patch.object(self.camera, 'START_WAIT_S', 0.2), \
             self._cameras(FakeCamera(frames=self.camera.WARMUP_FRAMES - 1),
                           FakeCamera(frames=self.camera.WARMUP_FRAMES - 1)), \
             self.assertLogs('petfeedr', 'WARNING'):
            self.assertTrue(self.camera.snap(False))
        self.assertEqual(self.camera.frame_names(), [])

    def test_a_stalled_stream_is_killed_and_restarted(self):
        clock = [1000.0]
        with patch.object(self.camera, '_now', side_effect=lambda: clock[0]), \
             self._cameras(FakeCamera(), FakeCamera()) as spawn:
            self.assertFalse(self.camera.snap(False, datetime(2026, 11, 14, 16, 4, 30)))
            clock[0] += self.camera.STALL_AFTER_S + 1   # frames stopped arriving
            self.assertFalse(self.camera.snap(False, datetime(2026, 11, 14, 16, 9, 30)))
        self.assertEqual(spawn.call_count, 2)
        self.assertTrue(self.spawned[0].killed)     # no stray process holding the webcam
        self.assertFalse(self.spawned[1].killed)
        self.assertEqual(len(self.camera.frame_names()), 2)

    def test_a_stream_that_died_is_restarted(self):
        dead = FakeCamera(frames=0)
        dead.end()
        with self._cameras(dead, FakeCamera()) as spawn, \
             patch.object(self.camera.log, 'warning') as warning:
            self.assertFalse(self.camera.snap(False))
        warning.assert_not_called()
        self.assertEqual(spawn.call_count, 2)
        self.pause.assert_called_once_with(self.camera.RETRY_AFTER_S)   # a pause between tries
        self.assertEqual(len(self.camera.frame_names()), 1)

    def test_junk_on_the_stream_is_never_saved_as_a_frame(self):
        junk = FakeCamera(frames=0, garbage=b'not a jpeg' * 5000)
        junk.end()
        with self._cameras(junk, FakeCamera(frames=0, garbage=b'\xff\xd9\xff\xd8' * 50)), \
             patch.object(self.camera, 'START_WAIT_S', 0.2), \
             patch.object(self.camera, '_MIN_FRAME_BYTES', 2048), \
             self.assertLogs('petfeedr', 'WARNING'):
            self.assertTrue(self.camera.snap(False))
        self.assertEqual(self.camera.frame_names(), [])

    def test_a_shot_every_five_minutes_just_ahead_of_the_mark(self):
        nxt = self.camera.seconds_until_next_shot
        on_the_hour = datetime(2026, 11, 14, 16, 0, 0).timestamp()
        self.assertEqual(nxt(on_the_hour), 270)          # → 16:04:30
        self.assertEqual(nxt(on_the_hour + 269), 1)
        self.assertEqual(nxt(on_the_hour + 270), 300)    # just shot; next is 16:09:30
        self.assertEqual(nxt(on_the_hour + 275), 295)

    def test_loop_shoots_at_start_then_after_each_sleep(self):
        order = []

        def sleep(seconds):
            order.append(('sleep', seconds))
            if len(order) >= 6:
                raise StopIteration

        with patch.object(self.camera, 'snap', side_effect=lambda failing: order.append('snap') or False):
            with self.assertRaises(StopIteration):
                self.camera.run_forever(sleep=sleep, clock=lambda: 0.0)
        self.assertEqual(order[:5], ['snap', ('sleep', 270), 'snap', ('sleep', 270), 'snap'])

    def test_frames_older_than_14_days_are_pruned(self):
        now = datetime(2026, 11, 14, 12, 0, 0)
        old = self._frame(now - timedelta(days=14, minutes=1))
        kept = self._frame(now - timedelta(days=13, hours=23))
        with open(os.path.join(self.camera.FRAMES_DIR, 'notes.txt'), 'w') as f:
            f.write("not a frame")
        self.assertEqual(self.camera.prune(now), 1)
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(kept))
        # Only files named like frames are ever deleted
        self.assertTrue(os.path.exists(os.path.join(self.camera.FRAMES_DIR, 'notes.txt')))

    def test_each_tick_prunes(self):
        now = datetime(2026, 11, 14, 12, 0, 0)
        old = self._frame(now - timedelta(days=20))
        with self._cameras(FakeCamera()):
            self.assertFalse(self.camera.snap(False, now))
        self.assertFalse(os.path.exists(old))
        self.assertEqual(self.camera.frame_names(), ['2026-11-14T120000.jpg'])

    def test_unplugged_camera_warns_once_per_outage_and_recovers(self):
        os.remove('video0')
        with self.assertLogs('petfeedr', 'INFO') as logs:
            failing = False
            for _ in range(3):
                failing = self.camera.snap(failing)   # must not raise, starts nothing
            self.assertTrue(failing)
            open('video0', 'w').close()
            with self._cameras(FakeCamera()):
                failing = self.camera.snap(failing)
        self.assertFalse(failing)
        warnings = [r for r in logs.records if r.levelname == 'WARNING']
        self.assertEqual(len(warnings), 1)
        self.assertIn('Camera offline', warnings[0].getMessage())
        self.assertEqual([r.getMessage() for r in logs.records if r.levelname == 'INFO'], ['Camera is back'])
        self.assertEqual(len(self.camera.frame_names()), 1)

    def test_a_stream_that_wont_start_warns_once_and_saves_nothing(self):
        def dead():
            cam = FakeCamera(frames=0)
            cam.end()
            return cam

        for label, cameras in (("ffmpeg exits at once", [dead() for _ in range(4)]),
                               ("ffmpeg not installed", [FileNotFoundError("ffmpeg")] * 2)):
            with self.subTest(label), self.assertLogs('petfeedr', 'WARNING') as logs:
                with self._cameras(*cameras):
                    failing = self.camera.snap(self.camera.snap(False))
                self.assertTrue(failing)
                self.assertEqual(len(logs.records), 1)
        self.assertEqual(self.camera.frame_names(), [])
        # Nothing left running after a failed start
        self.assertTrue(all(c.killed for c in self.spawned))

    def test_a_bug_in_capture_is_logged_once_and_never_escapes(self):
        with self._cameras(ZeroDivisionError("bug"), ZeroDivisionError("bug")), \
             self.assertLogs('petfeedr', 'ERROR') as logs:
            self.assertTrue(self.camera.snap(self.camera.snap(False)))
        self.assertEqual(len(logs.records), 1)

    def test_feed_runs_on_time_while_the_camera_is_stuck(self):
        import threading
        # A stream that starts and then says nothing: the shot waits on it
        silent = FakeCamera(frames=0)
        with self._cameras(silent, FakeCamera(frames=0)), \
             patch.object(self.camera, 'START_WAIT_S', 3):
            shot = threading.Thread(target=self.camera.snap, args=(False,), daemon=True)
            shot.start()
            try:
                # The waiting camera holds no feeder lock, so a feed goes straight through
                self.assertTrue(feeder_core.STATE_LOCK.acquire(blocking=False))
                feeder_core.STATE_LOCK.release()
                with patch('feeder_core.notify.send'):
                    self.assertTrue(feeder_core.feed_pet(portion='small', source='manual'))
                self.assertTrue(shot.is_alive())
            finally:
                silent.end()
                shot.join(10)

    def test_camera_code_is_apart_from_the_feed_path(self):
        from pathlib import Path
        repo = Path(self.camera.__file__).parent
        source = (repo / 'camera.py').read_text()
        self.assertNotIn('STATE_LOCK', source)
        self.assertNotIn('feeder_core', source)
        for feed_module in ('feeder_core.py', 'servo_controller.py', 'schedule_routes.py', 'hopper.py'):
            self.assertNotIn('camera', (repo / feed_module).read_text(), feed_module)


class TestCameraOffPi(TempCwd):
    def test_no_camera_off_a_pi(self):
        import camera
        import view_state
        env = {k: v for k, v in os.environ.items() if k != 'PETFEEDR_CAMERA'}
        with patch.dict(os.environ, env, clear=True), \
             patch('camera.on_raspberry_pi', return_value=False), \
             patch('camera._spawn') as run:
            self.assertIsNone(camera.start())
            self.assertIsNone(camera.status())
            self.assertIsNone(view_state.build()['camera'])
        run.assert_not_called()
        self.assertFalse(os.path.exists('data'))


class TestCameraDashboard(CameraCase):
    def setUp(self):
        super().setUp()
        import web_interface
        import view_state
        self.view_state = view_state
        self.client = web_interface.app.test_client()

    def test_404_before_the_first_frame(self):
        self.assertEqual(self.client.get('/camera/latest.jpg').status_code, 404)

    def test_serves_the_newest_frame_only(self):
        now = datetime(2026, 11, 14, 16, 0, 0)
        self._frame(now - timedelta(minutes=5), b'\xff\xd8 older')
        self._frame(now, b'\xff\xd8 newest')
        # Sorts after every frame name, but isn't one
        with open(os.path.join(self.camera.FRAMES_DIR, 'zzz.jpg'), 'wb') as f:
            f.write(b'not a frame')
        resp = self.client.get('/camera/latest.jpg')
        self.assertEqual((resp.status_code, resp.mimetype), (200, 'image/jpeg'))
        self.assertEqual(resp.data, b'\xff\xd8 newest')
        self.assertEqual(resp.headers['Cache-Control'], 'no-cache')
        resp.close()

    def test_request_cannot_pick_the_file(self):
        now = datetime(2026, 11, 14, 16, 0, 0)
        older = self._frame(now - timedelta(minutes=5), b'\xff\xd8 older')
        self._frame(now, b'\xff\xd8 newest')
        with open('feeding_schedules.txt', 'w') as f:
            f.write("08:00,small\n")
        for query in ('?t=' + os.path.basename(older), '?t=../../feeding_schedules.txt',
                      '?file=feeding_schedules.txt', '?path=/etc/passwd'):
            resp = self.client.get('/camera/latest.jpg' + query)
            self.assertEqual(resp.data, b'\xff\xd8 newest', query)
            resp.close()
        for path in ('/camera/' + os.path.basename(older), '/camera/zzz.jpg', '/camera/',
                     '/camera/..%2f..%2ffeeding_schedules.txt', '/camera/latest.jpg/../../feeding_schedules.txt',
                     '/data/frames/' + os.path.basename(older)):
            self.assertEqual(self.client.get(path).status_code, 404, path)

    def test_route_is_read_only_and_host_checked(self):
        self._frame(datetime(2026, 11, 14, 16, 0, 0))
        self.assertEqual(self.client.post('/camera/latest.jpg').status_code, 405)
        resp = self.client.get('/camera/latest.jpg', headers={'Host': 'evil.example:5000'})
        self.assertEqual(resp.status_code, 421)

    def test_card_shows_fresh_frame_and_its_time(self):
        now = datetime(2026, 11, 14, 16, 8, 0)
        self._frame(datetime(2026, 11, 14, 16, 4, 30))
        card = self.view_state._camera_card(now)
        self.assertEqual(card, {'online': True, 'src': '/camera/latest.jpg?t=2026-11-14T160430.jpg',
                                'label': '4:04 PM', 'alt': 'Camera view of the feeding bowl, 4:04 PM'})

    def test_card_says_offline_when_frames_stop(self):
        now = datetime(2026, 11, 14, 16, 30, 0)
        self._frame(datetime(2026, 11, 14, 16, 4, 30))
        card = self.view_state._camera_card(now)
        self.assertEqual((card['online'], card['label']), (False, 'Camera offline · last frame 4:04 PM'))
        self.assertTrue(card['src'])
        # Yesterday's frame carries its date
        card = self.view_state._camera_card(now + timedelta(days=1))
        self.assertEqual(card['label'], 'Camera offline · last frame Nov 14, 4:04 PM')

    def test_a_stray_file_with_an_impossible_time_is_not_a_frame(self):
        # Shaped like a frame name and sorts last, but no such time exists
        os.makedirs(self.camera.FRAMES_DIR)
        with open(os.path.join(self.camera.FRAMES_DIR, '2026-13-45T999999.jpg'), 'wb') as f:
            f.write(b'junk')
        self._frame(datetime(2026, 11, 14, 16, 4, 30))
        card = self.view_state._camera_card(datetime(2026, 11, 14, 16, 8, 0))
        self.assertEqual(card['label'], '4:04 PM')
        resp = self.client.get('/camera/latest.jpg')
        self.assertEqual(resp.data, JPEG)
        resp.close()
        self.assertIn('camera', self.view_state.build())

    def test_no_frames_means_no_picture(self):
        # No blank panel: the Feed card is drawn as it was before the camera
        self.assertIsNone(self.view_state._camera_card(datetime(2026, 11, 14, 16, 30, 0)))
        self.assertIsNone(self.view_state.build()['camera'])


class PasscodeCase(TempCwd):
    def setUp(self):
        super().setUp()
        import passcode
        import web_interface
        self.passcode = passcode
        self.web = web_interface
        self.clock = [1000.0]
        for p in (patch.object(passcode, '_failures', 0),
                  patch.object(passcode, '_wait_until', 0.0),
                  patch.object(passcode, '_cache', (None, None)),
                  patch.object(passcode, '_ROUNDS', 10),          # fast hashing for tests
                  patch.object(passcode, '_now', side_effect=lambda: self.clock[0]),
                  patch.object(web_interface, '_last_manual_feed', None)):
            p.start()
            self.addCleanup(p.stop)
        self.json = {'Accept': 'application/json'}
        self.client = web_interface.app.test_client()

    def _writes(self):
        """Every route that changes something, with a body that would succeed."""
        return [('/feed', {'portion': 'small'}),
                ('/refill', {'remaining_pct': '10'}),
                ('/add', {'time': '07:30', 'portion': 'small'}),
                ('/delete', {'time': '07:30'}),
                ('/toggle_fixed', {'time': '07:30'}),
                ('/toggle_graze', {'time': '07:30'}),
                ('/update_portion', {'time': '07:30', 'portion': 'large'})]


class TestPorchKey(PasscodeCase):
    """Porch's feed-only key: one small extra a day, and nothing else."""
    KEY = 'porch-test-key-9f3a'

    def setUp(self):
        super().setUp()
        env = patch.dict(os.environ, {'PORCH_FEED_KEY': self.KEY})
        env.start()
        self.addCleanup(env.stop)
        self.porch = {'Accept': 'application/json', 'X-Porch-Key': self.KEY}

    def _feed(self, data=None, headers=None):
        with patch('web_interface.feed_pet', return_value=True) as feed:
            resp = self.client.post('/feed', data={'portion': 'small'} if data is None else data,
                                    headers=headers or self.porch)
        return resp, feed

    def _log_extra(self, source):
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open('feeding_log.txt', 'a') as f:
            f.write(f"{stamp},1 - INFO - Feeding completed in 0.3s (small portion, {source})\n")

    def test_key_feeds_a_small_portion_past_the_lock_as_porch(self):
        self.passcode.set_passcode('4821')
        resp, feed = self._feed()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(feed.call_args.kwargs, {'portion': 'small', 'source': 'porch'})

    def test_key_opens_no_other_write(self):
        self.passcode.set_passcode('4821')
        with patch('hopper.record_refill') as refill:
            for path, data in self._writes()[1:] + [('/lock', {}), ('/passcode', {'passcode': '1111'})]:
                with self.subTest(path=path):
                    resp = self.client.post(path, data=data, headers=self.porch)
                    self.assertEqual(resp.status_code, 401)
                    self.assertTrue(resp.get_json()['locked'])
        refill.assert_not_called()
        # /unlock stays open to everyone, and the key is no passcode
        resp = self.client.post('/unlock', data={'passcode': self.KEY}, headers=self.porch)
        self.assertEqual(resp.status_code, 401)
        self.assertNotIn('Set-Cookie', resp.headers)

    def test_only_small_and_never_coerced(self):
        for data in ({'portion': 'large'}, {'portion': 'medium'}, {'portion': 'huge'}, {}):
            with self.subTest(data=data):
                resp, feed = self._feed(data)
                self.assertEqual(resp.status_code, 400)
                feed.assert_not_called()

    def test_refused_once_any_extra_was_given_today(self):
        for source in ('manual', 'porch'):
            with self.subTest(source=source):
                with open('feeding_log.txt', 'w'):
                    pass
                self._log_extra(source)
                resp, feed = self._feed()
                self.assertEqual(resp.status_code, 429)
                self.assertIn('already given today', resp.get_json()['message'])
                feed.assert_not_called()

    def test_cooldown_still_applies(self):
        self.web._last_manual_feed = time.monotonic()
        resp, feed = self._feed()
        self.assertEqual(resp.status_code, 429)
        feed.assert_not_called()

    def test_wrong_missing_or_unset_key_is_just_locked(self):
        self.passcode.set_passcode('4821')
        for headers in ({'Accept': 'application/json', 'X-Porch-Key': self.KEY + 'x'},
                        {'Accept': 'application/json', 'X-Porch-Key': ''},
                        self.json):
            with self.subTest(headers=headers):
                resp, feed = self._feed(headers=headers)
                self.assertEqual(resp.status_code, 401)
                feed.assert_not_called()
        with patch.dict(os.environ, {'PORCH_FEED_KEY': ''}):
            resp, feed = self._feed()
        self.assertEqual(resp.status_code, 401)
        feed.assert_not_called()

    def test_with_no_passcode_a_wrong_key_is_a_plain_manual_feed(self):
        # Exactly as today: no passcode means the page's own feeds are open
        resp, feed = self._feed(headers={'Accept': 'application/json', 'X-Porch-Key': 'nope'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(feed.call_args.kwargs['source'], 'manual')
        # ...but a valid key always gets Porch's rules
        resp, feed = self._feed({'portion': 'large'})
        self.assertEqual(resp.status_code, 400)

    def test_host_and_origin_checks_run_first(self):
        resp, feed = self._feed(headers={**self.porch, 'Origin': 'https://evil.example'})
        self.assertEqual(resp.status_code, 403)
        with patch('web_interface.feed_pet') as feed:
            resp = self.client.post('/feed', data={'portion': 'small'}, headers=self.porch,
                                    base_url='http://evil.example:5000')
        self.assertEqual(resp.status_code, 421)
        feed.assert_not_called()

    def test_key_never_reaches_logs_state_or_responses(self):
        self.passcode.set_passcode('4821')
        with self.assertLogs('petfeedr', 'INFO') as logs:
            bodies = [self._feed()[0].get_data(as_text=True)]
            self._log_extra('porch')
            bodies.append(self._feed()[0].get_data(as_text=True))
            bodies.append(self.client.post('/refill', data={'remaining_pct': '10'},
                                           headers=self.porch).get_data(as_text=True))
            bodies.append(self.client.get('/api/state').get_data(as_text=True))
            bodies.append(self.client.get('/').get_data(as_text=True))
            log_text = '\n'.join(logs.output)
        for text in bodies + [log_text]:
            self.assertNotIn(self.KEY, text)

    def test_porch_feed_counts_as_an_extra_everywhere(self):
        import manual_limit
        self._log_extra('porch')
        today = date.today()
        feedings, _ = feeding_stats.day_feedings(today.isoformat())
        self.assertEqual(feedings[0]['type'], 'porch')
        self.assertEqual(manual_limit.used_today_cups(), 0.25)
        self.assertTrue(manual_limit.extra_given_today())
        self.assertEqual(feeding_stats.parse_weekly_stats()[-1]['manual_count'], 1)
        mark = feeding_stats.rhythm_week([{'ts': f"{today.isoformat()}T12:00:00", 'event': 'dispense',
                                           'portion': 'small', 'cups': 0.25, 'source': 'porch'}],
                                         today, [])['days'][-1]['marks'][0]
        self.assertEqual(mark['kind'], 'manual')
        # Never a scheduled slot's meal, however close to the slot it lands
        slot = datetime.now().strftime("%H:%M")
        self.assertEqual(feeder_core.slot_outcome(slot, today, [], feeder_core.read_all_log_lines()),
                         (None, None))


class TestPasscodeStore(PasscodeCase):
    def test_nothing_is_set_until_someone_sets_it(self):
        self.assertFalse(self.passcode.is_set())
        self.assertEqual(self.passcode.try_unlock('1234'), (None, 0))
        self.assertFalse(self.passcode.unlocked('anything'))

    def test_only_a_salted_hash_reaches_the_disk(self):
        token = self.passcode.set_passcode('4821')
        with open(self.passcode.PATH) as f:
            raw = f.read()
        self.assertNotIn('4821', raw)
        self.assertNotIn(token, raw)            # nor a working unlock token
        self.assertEqual(os.stat(self.passcode.PATH).st_mode & 0o777, 0o600)
        self.assertEqual([n for n in os.listdir('data') if n != 'passcode.json'], [])  # no temp left
        # Salted: the same passcode never hashes the same twice
        first = json.loads(raw)['hash']
        self.passcode.set_passcode('4821')
        with open(self.passcode.PATH) as f:
            self.assertNotEqual(json.load(f)['hash'], first)

    def test_passcode_must_be_4_to_12_digits(self):
        for bad in ('', '123', 'abcd', '12 34', '1' * 13, '1234\n', '١٢٣٤', None):
            with self.subTest(bad=bad), self.assertRaises(self.passcode.PasscodeError):
                self.passcode.set_passcode(bad)
        self.assertFalse(self.passcode.is_set())

    def test_right_passcode_unlocks_for_30_days(self):
        self.passcode.set_passcode('4821')
        token, wait = self.passcode.try_unlock('4821')
        self.assertTrue(token)
        self.assertEqual(wait, 0)
        self.assertTrue(self.passcode.unlocked(token))
        day = 86400
        with patch.object(self.passcode, '_wall', return_value=time.time() + 29 * day):
            self.assertTrue(self.passcode.unlocked(token))
        with patch.object(self.passcode, '_wall', return_value=time.time() + 31 * day):
            self.assertFalse(self.passcode.unlocked(token))

    def test_made_up_tokens_never_unlock(self):
        self.passcode.set_passcode('4821')
        for forged in ('', None, 'x', 'A' * 43):
            self.assertFalse(self.passcode.unlocked(forged))

    def test_locking_ends_that_device_only(self):
        self.passcode.set_passcode('4821')
        phone, _ = self.passcode.try_unlock('4821')
        laptop, _ = self.passcode.try_unlock('4821')
        self.passcode.lock(phone)
        self.assertFalse(self.passcode.unlocked(phone))
        self.assertTrue(self.passcode.unlocked(laptop))

    def test_changing_the_passcode_ends_every_other_unlock(self):
        self.passcode.set_passcode('4821')
        old, _ = self.passcode.try_unlock('4821')
        new = self.passcode.set_passcode('9000')
        self.assertFalse(self.passcode.unlocked(old))
        self.assertTrue(self.passcode.unlocked(new))
        self.assertEqual(self.passcode.try_unlock('4821')[0], None)

    def test_wrong_guesses_meet_growing_waits(self):
        self.passcode.set_passcode('4821')
        waits = []
        for _ in range(12):
            token, wait = self.passcode.try_unlock('0000')
            self.assertIsNone(token)
            waits.append(wait)
            self.clock[0] += wait   # the guesser waits it out each time
        self.assertEqual(waits[:4], [0, 0, 0, 0])
        self.assertEqual(waits[4:], [30, 60, 120, 240, 480, 900, 900, 900])

    def test_the_right_passcode_is_refused_during_a_wait(self):
        # Otherwise the wait would not slow a guesser down at all
        self.passcode.set_passcode('4821')
        for _ in range(5):
            self.passcode.try_unlock('0000')
        token, wait = self.passcode.try_unlock('4821')
        self.assertIsNone(token)
        self.assertEqual(wait, 30)
        self.clock[0] += 31
        self.assertTrue(self.passcode.try_unlock('4821')[0])
        # A right passcode clears the count
        self.assertEqual(self.passcode.try_unlock('0000'), (None, 0))

    def test_running_through_every_code_takes_months(self):
        self.passcode.set_passcode('9999')
        started = self.clock[0]
        for guess in range(300):
            _, wait = self.passcode.try_unlock(f'{guess:04d}')
            self.clock[0] += wait
        per_guess = (self.clock[0] - started) / 300
        self.assertGreater(per_guess * 10000 / 86400, 90)   # days for all 10,000

    def test_a_damaged_file_means_locked_not_open(self):
        self.passcode.set_passcode('4821')
        with open(self.passcode.PATH, 'w') as f:
            f.write('{not json')
        self.assertTrue(self.passcode.is_set())
        self.assertEqual(self.passcode.try_unlock('4821')[0], None)
        self.assertEqual(self.passcode.try_unlock('')[0], None)

    def test_deleting_the_file_is_the_reset(self):
        token = self.passcode.set_passcode('4821')
        os.remove(self.passcode.PATH)
        self.assertFalse(self.passcode.is_set())
        self.assertFalse(self.passcode.unlocked(token))

    def test_passcode_file_stays_out_of_git_and_deploys(self):
        from pathlib import Path
        repo = Path(self.passcode.__file__).parent
        self.assertTrue(self.passcode.PATH.startswith('data' + os.sep))
        self.assertIn('data/', (repo / '.gitignore').read_text().splitlines())
        deploy = (repo / 'deploy.sh').read_text()
        self.assertIn("--exclude 'data/'", deploy)
        self.assertIn("--exclude 'data/passcode.json'", deploy)   # the backup copy too


class TestPasscodeGate(PasscodeCase):
    def _unlock(self, code='4821'):
        return self.client.post('/unlock', data={'passcode': code}, headers=self.json)

    def test_with_no_passcode_everything_works_as_before(self):
        with patch('web_interface.feed_pet', return_value=True) as feed:
            resp = self.client.post('/feed', data={'portion': 'small'}, headers=self.json)
        self.assertEqual(resp.status_code, 200)
        feed.assert_called_once()
        self.assertEqual(resp.get_json()['state']['lock'], {'set': False, 'unlocked': False})
        self.assertEqual(self._unlock().status_code, 400)   # nothing to unlock

    def test_every_write_is_refused_while_locked(self):
        self.passcode.set_passcode('4821')
        with patch('web_interface.feed_pet') as feed, patch('hopper.record_refill') as refill:
            for path, data in self._writes():
                with self.subTest(path=path):
                    resp = self.client.post(path, data=data, headers=self.json)
                    self.assertEqual(resp.status_code, 401)
                    self.assertTrue(resp.get_json()['locked'])
                    self.assertEqual(self.client.post(path, data=data).status_code, 401)   # plain form too
        feed.assert_not_called()
        refill.assert_not_called()
        self.assertFalse(os.path.exists('feeding_schedules.txt') and open('feeding_schedules.txt').read().strip())

    def test_the_test_above_covers_every_write_route(self):
        # A new POST route must be added to _writes(), so it can't ship ungated by accident
        posts = {str(r) for r in self.web.app.url_map.iter_rules() if 'POST' in r.methods}
        self.assertEqual(posts - {'/unlock', '/lock', '/passcode'}, {p for p, _ in self._writes()})

    def test_reading_stays_open_while_locked(self):
        self.passcode.set_passcode('4821')
        self.assertEqual(self.client.get('/').status_code, 200)
        state = self.client.get('/api/state').get_json()['data']
        self.assertEqual(state['lock'], {'set': True, 'unlocked': False})
        self.assertEqual(self.client.get('/api/day-detail/2026-11-14').status_code, 200)
        self.assertEqual(self.client.get('/camera/latest.jpg').status_code, 404)   # open; just no frame

    def test_nothing_about_the_passcode_reaches_the_page(self):
        token = self.passcode.set_passcode('4821')
        with open(self.passcode.PATH) as f:
            saved = json.load(f)
        for body in (self.client.get('/').get_data(as_text=True),
                     self.client.get('/api/state').get_data(as_text=True)):
            for secret in ('4821', token, saved['hash'], saved['salt'], *saved['sessions']):
                self.assertNotIn(secret, body)

    def test_right_passcode_unlocks_this_device(self):
        self.passcode.set_passcode('4821')
        resp = self._unlock()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()['state']['lock'], {'set': True, 'unlocked': True})
        cookie = resp.headers['Set-Cookie']
        self.assertIn('HttpOnly', cookie)
        self.assertIn('SameSite=Strict', cookie)
        self.assertIn('Max-Age=2592000', cookie)   # 30 days
        with patch('web_interface.feed_pet', return_value=True) as feed:
            self.assertEqual(self.client.post('/feed', data={'portion': 'small'}, headers=self.json).status_code, 200)
        feed.assert_called_once()
        # Another device, with no cookie, is still locked
        other = self.web.app.test_client()
        self.assertEqual(other.post('/feed', data={'portion': 'small'}, headers=self.json).status_code, 401)

    def test_cookie_is_secure_over_https(self):
        self.passcode.set_passcode('4821')
        resp = self.client.post('/unlock', data={'passcode': '4821'}, headers=self.json,
                                base_url='https://petfeedr.example.ts.net')
        self.assertIn('Secure', resp.headers['Set-Cookie'])

    def test_wrong_passcode_says_only_that(self):
        self.passcode.set_passcode('4821')
        with self.assertLogs('petfeedr', 'WARNING') as logs:
            near = self._unlock('4820')
            far = self._unlock('0000')
        self.assertEqual((near.status_code, far.status_code), (401, 401))
        self.assertEqual(near.get_json(), far.get_json())      # no hint of how close
        self.assertNotIn('Set-Cookie', near.headers)
        for record in logs.records:                            # the guess is never logged
            self.assertNotIn('4820', record.getMessage())
            self.assertNotIn('0000', record.getMessage())

    def test_too_many_wrong_tries_are_told_to_wait(self):
        self.passcode.set_passcode('4821')
        with self.assertLogs('petfeedr', 'WARNING'):
            for _ in range(4):
                self._unlock('0000')
            resp = self._unlock('0000')
            self.assertEqual(resp.status_code, 429)
            self.assertIn('wait 30s', resp.get_json()['message'])
            self.assertEqual(self._unlock('4821').status_code, 429)   # right, but still waiting

    def test_lock_ends_the_unlock_on_the_server(self):
        self.passcode.set_passcode('4821')
        token = self._unlock().headers['Set-Cookie'].split(';')[0].split('=', 1)[1]
        resp = self.client.post('/lock', headers=self.json)
        self.assertEqual(resp.get_json()['state']['lock'], {'set': True, 'unlocked': False})
        # A copy of the old cookie is dead too, not just cleared from this browser
        thief = self.web.app.test_client()
        thief.set_cookie(self.passcode.COOKIE, token)
        self.assertEqual(thief.post('/feed', data={'portion': 'small'}, headers=self.json).status_code, 401)

    def test_first_passcode_is_set_from_the_dashboard_and_unlocks_the_setter(self):
        mismatch = self.client.post('/passcode', data={'passcode': '4821', 'confirm': '4812'}, headers=self.json)
        self.assertEqual(mismatch.status_code, 400)
        short = self.client.post('/passcode', data={'passcode': '12', 'confirm': '12'}, headers=self.json)
        self.assertEqual(short.status_code, 400)
        self.assertFalse(self.passcode.is_set())
        with self.assertLogs('petfeedr', 'INFO') as logs:
            resp = self.client.post('/passcode', data={'passcode': '4821', 'confirm': '4821'}, headers=self.json)
        self.assertEqual(resp.get_json()['state']['lock'], {'set': True, 'unlocked': True})
        self.assertNotIn('4821', ' '.join(r.getMessage() for r in logs.records))
        with patch('web_interface.feed_pet', return_value=True):
            self.assertEqual(self.client.post('/feed', data={'portion': 'small'}, headers=self.json).status_code, 200)

    def test_changing_the_passcode_needs_an_unlocked_device(self):
        self.passcode.set_passcode('4821')
        stranger = self.web.app.test_client()
        resp = stranger.post('/passcode', data={'passcode': '0000', 'confirm': '0000'}, headers=self.json)
        self.assertEqual(resp.status_code, 401)
        self._unlock()
        other = self.web.app.test_client()
        other.post('/unlock', data={'passcode': '4821'}, headers=self.json)
        resp = self.client.post('/passcode', data={'passcode': '7777', 'confirm': '7777'}, headers=self.json)
        self.assertEqual(resp.status_code, 200)
        # The changer stays unlocked; every other device is locked out
        with patch('web_interface.feed_pet', return_value=True):
            self.assertEqual(self.client.post('/feed', data={'portion': 'small'}, headers=self.json).status_code, 200)
        self.assertEqual(other.post('/feed', data={'portion': 'small'}, headers=self.json).status_code, 401)

    def test_cross_site_and_unknown_host_are_refused_before_the_passcode_is_looked_at(self):
        self.passcode.set_passcode('4821')
        for path in ('/unlock', '/lock', '/passcode'):
            data = {'passcode': '4821', 'confirm': '4821'}
            with self.subTest(path=path), self.assertLogs('petfeedr', 'WARNING'):
                cross = self.client.post(path, data=data, headers={**self.json, 'Origin': 'http://evil.example'})
                self.assertEqual(cross.status_code, 403)
                rebound = self.client.post(path, data=data, headers={**self.json, 'Host': 'evil.example:5000'})
                self.assertEqual(rebound.status_code, 421)
        # Five refused cross-site tries used none of the free guesses
        self.assertEqual(self.passcode._failures, 0)

    def test_scheduled_feeds_and_grazes_need_no_unlock(self):
        self.passcode.set_passcode('4821')
        from pathlib import Path
        repo = Path(self.passcode.__file__).parent
        # The feed path never looks at the passcode: nothing to unlock at 4 AM
        for module in ('feeder_core.py', 'servo_controller.py', 'PetFeedr.py', 'hopper.py', 'manual_limit.py'):
            self.assertNotIn('passcode', (repo / module).read_text(), module)
        with patch('feeder_core.notify.send'):
            self.assertTrue(feeder_core.feed_pet(portion='small', source='scheduled'))

    def test_deploy_smoke_only_reads(self):
        from pathlib import Path
        repo = Path(self.passcode.__file__).parent
        for script in ('ship.sh', 'ops/check_live.py'):
            text = (repo / script).read_text()
            self.assertNotIn('-X POST', text, script)
            self.assertNotIn('--data', text, script)


class TestShipWaitsForCI(unittest.TestCase):
    """ship.sh's wait_for_ci, run against a stand-in `gh` on PATH."""

    def _run(self, checks_appear_after):
        import re, subprocess, tempfile
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, 'ship.sh')) as f:
            fn = re.search(r'^wait_for_ci\(\) \{.*?^\}', f.read(), re.S | re.M).group(0)
        import shutil
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        gh = os.path.join(tmp, 'gh')
        with open(gh, 'w') as f:
            # Reports 0 checks until called N times, like a PR whose CI run hasn't registered
            f.write(f'''#!/bin/bash
calls=$(( $(cat "{tmp}/calls" 2>/dev/null || echo 0) + 1 )); echo $calls > "{tmp}/calls"
ready=$([ $calls -gt {checks_appear_after} ] && echo 1 || echo 0)
case "$*" in
  *--watch*) [ $ready = 1 ] || {{ echo "no checks reported" >&2; exit 1; }}; echo watched;;
  *) echo $ready;;
esac
''')
        os.chmod(gh, 0o700)
        env = {**os.environ, 'PATH': tmp + os.pathsep + os.environ['PATH'],
               'CI_POLL_SEC': '0', 'CI_POLL_TRIES': '5'}
        return subprocess.run(['bash', '-c', fn + '\nwait_for_ci kibble'],
                              env=env, capture_output=True, text=True)

    def test_waits_for_a_late_ci_run_then_watches_it(self):
        result = self._run(checks_appear_after=2)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('watched', result.stdout)

    def test_gives_up_when_no_ci_run_ever_appears(self):
        result = self._run(checks_appear_after=99)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('watched', result.stdout)
        self.assertIn('no CI run appeared', result.stderr)


def _private_terms(root):
    """Owner-specific terms (one regex per line) from the PRIVATE_TERMS env
    var (CI secret) or a gitignored .private-terms file. Never in the repo:
    a list of what to hide would itself say it."""
    text = os.environ.get('PRIVATE_TERMS', '')
    path = os.path.join(root, '.private-terms')
    if not text and os.path.exists(path):
        with open(path) as f:
            text = f.read()
    return [line.strip() for line in text.splitlines() if line.strip() and not line.startswith('#')]


SKIP_DIRS = {'.git', '.venv', 'venv', '__pycache__', 'data'}
TEXT_SUFFIXES = {'.py', '.sh', '.md', '.txt', '.html', '.css', '.js', '.json', '.yml', '.yaml', '.svg', ''}


def find_personal_markers(root, private_terms=None):
    """(path, match) for each email address, home path, tailnet host or
    private term in the repo's text files. The repo is public; the owner's
    own setup lives outside it."""
    import re
    shapes = [
        # A real mailbox; user@host deploy targets (pi@petfeedr.local) and
        # example domains are fine
        re.compile(r'\b[\w.+-]+@(?!example\.)[\w-]+\.(?:com|net|org|me|io|dev|city|co|us)\b'),
        re.compile(r'/Users/[a-z]\w*'),
        re.compile(r'/home/(?!pi\b|<)[a-z]\w*'),
        re.compile(r'\b[a-z0-9-]+\.(?!example\.|tail0000\.)[a-z0-9-]+\.ts\.net\b'),
    ]
    terms = _private_terms(root) if private_terms is None else private_terms
    shapes += [re.compile(t, re.I) for t in terms]
    hits = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            path = os.path.join(dirpath, name)
            if (os.path.splitext(name)[1] not in TEXT_SUFFIXES or name.endswith('.env')
                    or name == '.private-terms'):
                continue
            try:
                with open(path, encoding='utf-8') as f:
                    text = f.read()
            except UnicodeDecodeError:
                continue
            rel = os.path.relpath(path, root)
            for shape in shapes:
                hits += [(rel, m.group(0)) for m in shape.finditer(text)]
    return hits


class TestNoPersonalSetup(unittest.TestCase):
    def test_repo_names_no_personal_address_path_or_host(self):
        root = os.path.dirname(os.path.abspath(__file__))
        self.assertEqual(find_personal_markers(root), [])

    def test_scan_catches_planted_markers(self):
        # Breaker: every shape, and a private term, must be seen
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, 'deploy.sh'), 'w') as f:
                # Fake values, split so this file doesn't match its own scan
                f.write('# alice.smith@' + 'gmail.com\nrsync /Us' + 'ers/alice/x /ho' + 'me/alice/y\n'
                        'ssh feeder.tail' + '1234.ts.net\nZebraCorp\n')
            found = [m for _, m in find_personal_markers(tmp, private_terms=['zebracorp'])]
            self.assertEqual(len(found), 5, found)

    def test_scan_allows_generic_examples(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, 'README.md'), 'w') as f:
                f.write('PI_HOST=pi@petfeedr.local /home/pi/PetFeedr /home/<user>/x '
                        'a@example.com petfeedr.example.ts.net\n')
            self.assertEqual(find_personal_markers(tmp, private_terms=[]), [])
