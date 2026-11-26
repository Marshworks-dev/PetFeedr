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
