// node --test test_history.mjs — the bowl history's arithmetic
// (static/js/history-model.js). test_feeder.py runs this file too, so the
// one `python3 -m unittest` covers it.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
    frameMinutes, tsMinutes, nearestFrame, feedMarks, blindStretches,
    valueText, settle, recentDays, clock, pct,
} from './static/js/history-model.js';

const F = hhmm => `2026-11-26T${hhmm}00.jpg`;

test('frame names and journal times become minutes into the day', () => {
    assert.equal(frameMinutes(F('0830')), 510);
    assert.equal(frameMinutes('2026-11-26T083000.jpg.part'), null);
    assert.equal(frameMinutes('../x.jpg'), null);
    assert.equal(frameMinutes(undefined), null);
    assert.equal(tsMinutes('2026-11-26T17:30:30'), 1050.5);
    assert.equal(tsMinutes('garbage'), null);
});

test('a slider position shows the nearest saved frame', () => {
    const frames = [F('0825'), F('0830'), F('1200')];
    assert.equal(nearestFrame(frames, 511), F('0830'));
    assert.equal(nearestFrame(frames, 0), F('0825'));
    assert.equal(nearestFrame(frames, 1439), F('1200'));
    // A gap: the closer side wins
    assert.equal(nearestFrame(frames, 700), F('1200'));
    assert.equal(nearestFrame([], 500), null);
    assert.equal(nearestFrame(['junk'], 500), null);
});

test('feeds become marks; a graze shows its pulses, not its closing dispense', () => {
    const marks = feedMarks([
        { ts: '2026-11-26T08:30:01', event: 'dispense', source: 'scheduled', portion: 'small' },
        { ts: '2026-11-26T10:00:00', event: 'dispense', source: 'porch', portion: 'small' },
        { ts: '2026-11-26T12:00:00', event: 'graze_pulse', pulse: 1, of: 2 },
        { ts: '2026-11-26T12:15:00', event: 'graze_pulse', pulse: 2, of: 2 },
        { ts: '2026-11-26T12:15:01', event: 'dispense', source: 'graze' },
        { ts: '2026-11-26T18:00:00', event: 'missed' },
        { ts: '2026-11-26T19:00:00', event: 'failure' },
        { ts: 'nonsense', event: 'dispense' },
        { ts: '2026-11-26T20:00:00', event: 'refill' },
    ]);
    assert.deepEqual(marks.map(m => m.kind), ['fed', 'manual', 'pulse', 'pulse', 'missed', 'failed']);
    assert.equal(marks[0].pct, pct(510 + 1 / 60));
    assert.deepEqual(feedMarks(undefined), []);
});

test('blind stretches: dark/blocked runs and gaps with no picture', () => {
    const frames = [F('0000'), F('0005'), F('0010'), F('0015'), F('0100'), F('0105')];
    const readings = [
        { frame: F('0005'), view: 'dark' }, { frame: F('0010'), view: 'dark' },
        { frame: F('0015'), view: 'clear' }, { frame: F('0100'), view: 'blocked' },
    ];
    const stretches = blindStretches(frames, readings, { nowMinute: 66 });
    assert.deepEqual(stretches, [
        { from: 2.5, to: 12.5, why: 'dark' },        // two dark frames in a row are one stretch
        { from: 15, to: 60, why: 'offline' },        // 45 min with nothing saved
        { from: 57.5, to: 62.5, why: 'blocked' },
    ]);
    // Today: nothing after "now" counts as blind
    assert.deepEqual(blindStretches([F('0000')], [], { nowMinute: 10 }), []);
    // A past day with no frames at all is one long gap
    assert.deepEqual(blindStretches([], []), [{ from: 0, to: 1440, why: 'offline' }]);
});

test('the slider says the time and the nearest feed', () => {
    const marks = feedMarks([{ ts: '2026-11-26T17:30:00', event: 'dispense', source: 'scheduled' }]);
    assert.equal(valueText('Tue', 1045, marks), 'Tue 5:25 PM, 5 min before the 5:30 PM feed');
    assert.equal(valueText('Tue', 1075, marks), 'Tue 5:55 PM, 25 min after the 5:30 PM feed');
    assert.equal(valueText('Tue', 1050, marks), 'Tue 5:30 PM, at the 5:30 PM feed');
    assert.equal(valueText('Tue', 600, marks), 'Tue 10:00 AM');   // nothing near
    assert.equal(clock(0), '12:00 AM');
    assert.equal(clock(12 * 60 + 5), '12:05 PM');
});

test('a drag fetches one frame, once it settles', () => {
    let now = 0;
    const pending = new Map();
    let next = 1;
    const timers = {
        setTimeout: (fn, ms) => { pending.set(next, { fn, at: now + ms }); return next++; },
        clearTimeout: id => pending.delete(id),
    };
    const advance = ms => {
        now += ms;
        for (const [id, t] of [...pending]) if (t.at <= now) { pending.delete(id); t.fn(); }
    };
    const fetched = [];
    const show = settle(v => fetched.push(v), 150, timers);
    // A drag across 40 positions, 20 ms apart
    for (let i = 0; i < 40; i++) { show(i); advance(20); }
    assert.deepEqual(fetched, []);
    advance(150);
    assert.deepEqual(fetched, [39]);
    // Cancelled (dialog closed mid-drag): nothing fetched
    show(5);
    show.cancel();
    advance(500);
    assert.deepEqual(fetched, [39]);
});

test('day chips: today first, seven days, local dates', () => {
    const days = recentDays(new Date(2026, 2, 1, 0, 30));   // 1 March, just after midnight
    assert.equal(days.length, 7);
    assert.deepEqual(days[0], { date: '2026-03-01', label: 'Today' });
    assert.equal(days[1].date, '2026-02-28');
    assert.equal(days[6].date, '2026-02-23');
});

import {
    nearestFeed, readingWords, mealFlag, beforeTarget, afterTarget, parseClock, feedName,
} from './static/js/history-model.js';

const meal = (before, after, extra = {}) => feedMarks([{
    ts: '2026-11-26T17:30:00', event: 'dispense', source: 'scheduled', portion: 'small',
    clip: { id: '2026-11-26T173000', frames: 80, landed: 20 }, before, after, ...extra,
}])[0];

test('the slider adds what the camera read in that frame', () => {
    const marks = [meal(null, null)];
    assert.equal(valueText('Tue', 1045, marks, { view: 'clear', bowl: 'empty' }),
        'Tue 5:25 PM, 5 min before the 5:30 PM feed, camera read empty');
    assert.equal(valueText('Tue', 600, marks, { view: 'dark', bowl: 'unknown' }), "Tue 10:00 AM, camera couldn't see");
    assert.equal(valueText('Tue', 600, marks, { view: 'clear', bowl: 'unknown' }), 'Tue 10:00 AM');
});

test('a meal says what the camera read before and 30 min after', () => {
    const empty = { view: 'clear', bowl: 'empty', picture: null };
    const food = { view: 'clear', bowl: 'food', picture: null };
    const unknown = { view: 'blocked', bowl: 'unknown', picture: null };
    assert.equal(readingWords(meal(empty, food)), 'Camera read: empty before · food left 30 min after');
    assert.equal(readingWords(meal(food, empty)), 'Camera read: food still there before · empty 30 min after');
    assert.equal(readingWords(meal(unknown, unknown)), "Camera read: couldn't tell before · couldn't tell 30 min after");
    // Fed 10 minutes ago: the after reading is still to come
    assert.equal(readingWords(meal(empty, null), 10), 'Camera read: empty before · 30-min look still to come');
    // Long past with no after reading: say only what there is
    assert.equal(readingWords(meal(empty, null), 300), 'Camera read: empty before');
    assert.equal(readingWords(meal(null, null)), null);
    // A garbled reading reads as couldn't tell, never as a word from the file
    assert.equal(readingWords(meal({ bowl: '<b>' }, null)), "Camera read: couldn't tell before");
    // Graze pulses and missed feeds get no readings
    const [pulse, missed] = feedMarks([{ ts: '2026-11-26T12:00:00', event: 'graze_pulse' },
        { ts: '2026-11-26T13:00:00', event: 'missed' }]);
    assert.equal(readingWords(pulse), null);
    assert.equal(readingWords(missed), null);
});

test('uneaten and leftover meals are flagged on the bar', () => {
    assert.equal(mealFlag(meal(null, { bowl: 'food' })), 'uneaten');
    assert.equal(mealFlag(meal({ bowl: 'food' }, { bowl: 'empty' })), 'leftover');
    assert.equal(mealFlag(meal({ bowl: 'empty' }, { bowl: 'empty' })), null);
    assert.equal(mealFlag(meal({ bowl: 'unknown' }, null)), null);
});

test('Before jumps to the clip frame read, else the last frame ahead of the feed', () => {
    const frames = [F('1720'), F('1725'), F('1735')];
    assert.deepEqual(beforeTarget(meal({ bowl: 'empty', picture: { clip: '2026-11-26T173000', index: 3 } }, null), frames),
        { clip: '2026-11-26T173000', index: 3 });
    assert.deepEqual(beforeTarget(meal(null, null), frames), { frame: F('1725') });
    assert.equal(beforeTarget(meal(null, null), [F('1735')]), null);
});

test('30 min after jumps to the frame read, else the first frame 30 min on', () => {
    const frames = [F('1755'), F('1800'), F('1805')];
    assert.deepEqual(afterTarget(meal(null, { bowl: 'food', picture: { frame: '2026-11-27T000500.jpg' } }), frames),
        { frame: '2026-11-27T000500.jpg' });   // a late feed's after picture is the next day's
    assert.deepEqual(afterTarget(meal(null, null), frames), { frame: F('1800') });
    assert.equal(afterTarget(meal(null, null), [F('1755')]), null);
});

test('the nearest feed to a slider position, and the Last 7 Days times', () => {
    const marks = [meal(null, null)];
    assert.equal(nearestFeed(marks, 1080), marks[0]);
    assert.equal(nearestFeed(marks, 1200), null);
    assert.equal(feedName(marks[0]), '5:30 PM feed');
    assert.equal(parseClock('5:30 PM'), 1050);
    assert.equal(parseClock('12:05 AM'), 5);
    assert.equal(parseClock('12:00 PM'), 720);
    assert.equal(parseClock('nonsense'), null);
});

import { feedActions } from './static/js/history-model.js';

test('each feed offers the right buttons; a feed with no clip says so', () => {
    const frames = [F('1725'), F('1800')];
    const withClip = feedActions(meal({ bowl: 'empty', picture: { clip: '2026-11-26T173000', index: 3 } }, null), frames);
    assert.deepEqual(withClip, { before: { clip: '2026-11-26T173000', index: 3 }, watch: true,
        after: { frame: F('1800') }, noClip: false });
    const noClip = feedActions(meal(null, null, { clip: null }), frames);
    assert.equal(noClip.watch, false);
    assert.equal(noClip.noClip, true);
    const [pulse, bare, missed] = feedMarks([
        { ts: '2026-11-26T12:00:00', event: 'graze_pulse', clip: { id: '2026-11-26T120000', frames: 80, landed: 20 } },
        { ts: '2026-11-26T12:10:00', event: 'graze_pulse', clip: null },
        { ts: '2026-11-26T13:00:00', event: 'missed' }]);
    // A graze pulse: Watch only, never Before or 30 min after
    assert.deepEqual(feedActions(pulse, frames), { before: null, watch: true, after: null, noClip: false });
    assert.deepEqual(feedActions(bare, frames), { before: null, watch: false, after: null, noClip: true });
    // A missed feed dropped nothing: no buttons, and no "no clip" line
    assert.deepEqual(feedActions(missed, frames), { before: null, watch: false, after: null, noClip: false });
});

import { frameLoader } from './static/js/history-model.js';

test('the picture reads as loading until the newest one asked for arrives', () => {
    const loader = frameLoader();
    assert.equal(loader.state, 'shown');
    // A drag asks for A, then B
    loader.want('/a.jpg');
    loader.want('/b.jpg');
    // A arrives late: still waiting for B
    assert.equal(loader.loaded('/a.jpg'), false);
    assert.equal(loader.state, 'loading');
    assert.equal(loader.loaded('/b.jpg'), true);
    assert.equal(loader.state, 'shown');
    // A stale failure doesn't mark the current picture as failed
    loader.want('/c.jpg');
    assert.equal(loader.failed('/b.jpg'), false);
    assert.equal(loader.failed('/c.jpg'), true);
    assert.equal(loader.state, 'failed');
    loader.reset();
    assert.equal(loader.state, 'shown');
});

import { imgStep } from './static/js/history-model.js';

test('a picture that failed is fetched again, not left "loading" forever', () => {
    assert.equal(imgStep('/a.jpg', '/b.jpg', true, 1280), 'set');
    assert.equal(imgStep('/a.jpg', '/a.jpg', true, 1280), 'none');    // already up
    assert.equal(imgStep('/a.jpg', '/a.jpg', false, 0), 'none');      // still on its way
    assert.equal(imgStep('/a.jpg', '/a.jpg', true, 0), 'retry');      // failed: same src again
    assert.equal(imgStep(null, '/a.jpg', false, 0), 'set');
});
