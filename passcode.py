"""The household passcode: looking is open, doing needs an unlock.

One short passcode for everyone in the house, against a guest or a stray
device on the home network — not against someone determined. With only
10,000 four-digit codes the stored hash protects little by itself; what
keeps the passcode safe is the growing wait after wrong guesses.

Until a passcode is set, nothing is locked. Scheduled feeds never come
through here.
"""

import hashlib
import hmac
import json
import math
import os
import re
import secrets
import threading
import time

from atomicfile import write_atomic

# In data/: never in git, never touched by a deploy. Deleting this file over
# SSH is the reset for a forgotten passcode
PATH = os.path.join('data', 'passcode.json')
UNLOCK_DAYS = 30
# The cookie that carries a device's unlock token
COOKIE = 'petfeedr_unlock'
# fullmatch on ASCII digits: '$' would let a trailing newline through
_CODE_RE = re.compile(r'[0-9]{4,12}')
_ROUNDS = 100_000
# Wrong guesses allowed before the waits begin, then 30 s doubling to 15 min.
# Counted for the whole feeder, not per address: a guesser can change its
# address, and at one try per 15 minutes 10,000 codes take over 100 days
FREE_TRIES = 5
BASE_WAIT_S = 30
MAX_WAIT_S = 15 * 60

# Guards the file and the guess counters. Never held with the feeder's lock
_lock = threading.Lock()
_failures = 0
_wait_until = 0.0
_cache = (None, None)  # (file mtime, parsed state)

_now = time.monotonic  # names of their own, so tests can move the clocks
_wall = time.time


class PasscodeError(ValueError):
    """A passcode that can't be used; the message is safe to show."""


def _load():
    """The saved state, or None when no passcode is set. Re-read when the
    file changes, so deleting it over SSH takes effect at once."""
    global _cache
    try:
        mtime = os.stat(PATH).st_mtime_ns
    except FileNotFoundError:
        _cache = (None, None)
        return None
    if _cache[0] != mtime:
        try:
            with open(PATH) as f:
                state = json.load(f)
            if not all(k in state for k in ('salt', 'hash', 'sessions')):
                raise ValueError("missing fields")
        except (OSError, ValueError):
            # Unreadable means locked, never open: a damaged file must not
            # quietly remove the passcode
            state = {'salt': '', 'hash': '', 'sessions': {}}
        _cache = (mtime, state)
    return _cache[1]


def _save(state):
    global _cache
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    write_atomic(PATH, json.dumps(state))
    os.chmod(PATH, 0o600)
    _cache = (os.stat(PATH).st_mtime_ns, state)


def _hash(code, salt):
    return hashlib.pbkdf2_hmac('sha256', code.encode(), bytes.fromhex(salt), _ROUNDS).hex()


def _session_key(token):
    # Only a digest of each unlock token is kept: reading the file hands
    # nobody a working cookie
    return hashlib.sha256(token.encode()).hexdigest()


def _new_session(state):
    token = secrets.token_urlsafe(32)
    now = _wall()
    state['sessions'] = {k: exp for k, exp in state['sessions'].items() if exp > now}
    state['sessions'][_session_key(token)] = now + UNLOCK_DAYS * 86400
    return token


def is_set():
    with _lock:
        return _load() is not None


def set_passcode(code):
    """Set or replace the passcode. Every device's unlock ends; the caller
    gets a fresh one. Returns that unlock token."""
    if not isinstance(code, str) or not _CODE_RE.fullmatch(code):
        raise PasscodeError("Use 4 to 12 digits")
    global _failures, _wait_until
    with _lock:
        salt = secrets.token_hex(16)
        state = {'salt': salt, 'hash': _hash(code, salt), 'sessions': {}}
        token = _new_session(state)
        _save(state)
        _failures, _wait_until = 0, 0.0
    return token


def try_unlock(code):
    """(token, 0) for the right passcode, else (None, seconds to wait before
    the next try is looked at; 0 when another try is allowed now)."""
    global _failures, _wait_until
    with _lock:
        state = _load()
        if state is None:
            return None, 0
        waiting = _wait_until - _now()
        if waiting > 0:
            # Not even looked at: a right guess during the wait must not get in
            return None, math.ceil(waiting)
        # Anything that couldn't be a passcode is a wrong guess, unhashed
        right = (bool(state['hash']) and isinstance(code, str) and bool(_CODE_RE.fullmatch(code))
                 and hmac.compare_digest(_hash(code, state['salt']), state['hash']))
        if right:
            _failures, _wait_until = 0, 0.0
            token = _new_session(state)
            _save(state)
            return token, 0
        _failures += 1
        if _failures < FREE_TRIES:
            return None, 0
        wait = min(BASE_WAIT_S * 2 ** (_failures - FREE_TRIES), MAX_WAIT_S)
        _wait_until = _now() + wait
        return None, wait


def unlocked(token):
    """Whether this unlock token is one of ours and still in date."""
    if not token:
        return False
    with _lock:
        state = _load()
        if state is None:
            return False
        return state['sessions'].get(_session_key(token), 0) > _wall()


def lock(token):
    """End one device's unlock."""
    if not token:
        return
    with _lock:
        state = _load()
        if state and state['sessions'].pop(_session_key(token), None) is not None:
            _save(state)
