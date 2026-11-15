// The header lock and the passcode prompt. Looking is always open; feeding,
// schedule changes and refills need this device unlocked. The server checks
// every write itself — this only asks for the passcode at the right moment.

import { $ } from '../dom.js';
import { post } from '../api.js';
import { apply, getState, setUnlocker } from '../store.js';
import { showToast } from '../toast.js';

const TEXT = {
    unlock: { title: 'Enter passcode', note: 'Feeding and changes need the passcode.', submit: 'Unlock' },
    set: { title: 'Set a passcode', note: 'From now on feeding, schedule changes and refills will need it. Use 4 to 12 digits.', submit: 'Set passcode' },
    change: { title: 'Change passcode', note: 'Every other device will be locked. Use 4 to 12 digits.', submit: 'Change passcode' },
    menu: { title: 'This device is unlocked', note: '' },
};

let mode = 'unlock';
// An action waiting on the prompt (a tapped Feed, a refused change): told
// true once unlocked, false if the prompt is closed without unlocking
let waiting = null;

const lockState = () => getState()?.lock;
const isLocked = () => { const lock = lockState(); return Boolean(lock && lock.set && !lock.unlocked); };

function show(nextMode) {
    mode = nextMode;
    const text = TEXT[mode];
    $('#lock-title').textContent = text.title;
    $('#lock-note').textContent = text.note;
    $('#lock-note').hidden = !text.note;
    $('#lock-error').textContent = '';
    $('#lock-entry').hidden = mode === 'menu';
    $('#lock-menu').hidden = mode !== 'menu';
    $('#lock-confirm-row').hidden = mode === 'unlock';
    const code = $('#lock-code');
    const confirm = $('#lock-confirm');
    code.value = '';
    confirm.value = '';
    // Lets a password manager offer the right thing
    code.autocomplete = mode === 'unlock' ? 'current-password' : 'new-password';
    if (mode !== 'menu') $('#lock-submit').textContent = text.submit;
    const dialog = $('#lock-dialog');
    // Native <dialog>: focus trap, Esc, and focus back where it came from
    if (!dialog.open) dialog.showModal();
    (mode === 'menu' ? $('#lock-now') : code).focus();
}

function fail(message) {
    $('#lock-error').textContent = message;   // role="alert": announced
    const code = $('#lock-code');
    code.value = '';
    $('#lock-confirm').value = '';
    code.focus();
}

async function submit() {
    const code = $('#lock-code').value;
    const fields = { passcode: code };
    if (mode !== 'unlock') {
        if (!/^[0-9]{4,12}$/.test(code)) return fail('Use 4 to 12 digits');
        fields.confirm = $('#lock-confirm').value;
        if (fields.confirm !== code) return fail("The two entries don't match");
    } else if (!code) {
        return fail('Enter the passcode');
    }
    const button = $('#lock-submit');
    button.disabled = true;
    const result = await post(mode === 'unlock' ? '/unlock' : '/passcode', fields);
    button.disabled = false;
    if (!result.ok) return fail(result.message);
    if (result.state) apply(result.state);
    $('#lock-dialog').close();
    showToast(result.message, 'success');
}

async function lockNow() {
    const result = await post('/lock', {});
    if (result.state) apply(result.state);
    $('#lock-dialog').close();
    showToast(result.ok ? 'Locked' : result.message, result.ok ? 'success' : 'error');
}

// Resolves true once this device is unlocked. Opens the prompt when it isn't.
// `refused` means the feeder just turned a change away as locked: whatever
// this page believed (an unlock that expired, or was ended elsewhere) is stale.
export function requestUnlock(refused = false) {
    const state = getState();
    if (refused && state?.lock) apply({ ...state, lock: { set: true, unlocked: false } });
    if (!isLocked()) return Promise.resolve(true);
    return new Promise(resolve => {
        waiting = resolve;
        show('unlock');
    });
}

// A locked device's tap on a gated control opens the prompt instead; `resume`
// carries the tap on once unlocked. Registered before the control's own
// listeners (initLock runs first), so those never see the locked tap.
function gate(el, types, resume) {
    for (const type of types) {
        el.addEventListener(type, e => {
            if (!isLocked()) return;
            if (type === 'keydown' && e.key !== ' ' && e.key !== 'Enter') return;
            e.preventDefault();
            e.stopImmediatePropagation();
            requestUnlock().then(unlocked => { if (unlocked && resume) resume(); });
        }, { capture: true });
    }
}

export function renderLock(lock) {
    const button = $('#lock-toggle');
    const state = !lock.set ? 'unset' : lock.unlocked ? 'unlocked' : 'locked';
    button.dataset.state = state;
    button.setAttribute('aria-label', {
        unset: 'No passcode set. Set one.',
        locked: 'Locked. Enter the passcode to feed or make changes.',
        unlocked: 'Unlocked. Lock this device or change the passcode.',
    }[state]);
}

export function initLock() {
    const dialog = $('#lock-dialog');
    $('#lock-toggle').addEventListener('click', () => {
        const lock = lockState();
        show(!lock?.set ? 'set' : lock.unlocked ? 'menu' : 'unlock');
    });
    $('#lock-form').addEventListener('submit', e => {
        e.preventDefault();
        if (mode !== 'menu') submit();
    });
    $('#lock-cancel').addEventListener('click', () => dialog.close());
    $('#lock-close').addEventListener('click', () => dialog.close());
    $('#lock-now').addEventListener('click', lockNow);
    $('#lock-change').addEventListener('click', () => show('change'));
    // A tap on the backdrop lands on the dialog element itself
    dialog.addEventListener('click', e => { if (e.target === dialog) dialog.close(); });
    dialog.addEventListener('close', () => {
        if (waiting) waiting(!isLocked());
        waiting = null;
    });

    // Feed is held, not tapped: after unlocking, the hold is still the user's to do
    gate($('.feed-circle-btn'), ['pointerdown', 'keydown'], null);
    gate($('#edit-schedule-btn'), ['click'], () => $('#edit-schedule-btn').click());
    gate($('#refill-toggle'), ['click'], () => $('#refill-toggle').click());
    // Anything else the server refuses as locked asks here and is sent again
    setUnlocker(() => requestUnlock(true));
}
