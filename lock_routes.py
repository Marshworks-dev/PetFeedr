"""Unlock, lock and set-passcode routes, and the gate in front of every write.

Reading the dashboard is open. Once a passcode is set, every request that
changes something needs this device's unlock cookie; the check is here on
the server, whatever the page shows.
"""

import hmac
import os

from flask import Blueprint, g, jsonify, make_response, request

from feeder_core import log
import passcode
from responses import wants_json, error_response, success_response

lock_bp = Blueprint('lock', __name__)

COOKIE = passcode.COOKIE
# The one write a locked device may make: trying the passcode
_OPEN_WHEN_LOCKED = {'/unlock'}
_READS = {'GET', 'HEAD', 'OPTIONS'}
# Porch (a household companion page) feeds with this header instead of an
# unlock. A header, not a cookie: a page on another site can't set it
PORCH_HEADER = 'X-Porch-Key'


def porch_feed():
    """True for a POST /feed carrying Porch's key. The key opens that one
    route only; anywhere else it counts for nothing. PORCH_FEED_KEY unset
    or empty turns the feature off."""
    expected = os.environ.get('PORCH_FEED_KEY', '')
    given = request.headers.get(PORCH_HEADER, '')
    if not expected or request.method != 'POST' or request.path != '/feed':
        return False
    # compare_digest: the time taken doesn't leak how much of a guess matched
    return hmac.compare_digest(given.encode(), expected.encode())


def require_unlock():
    """before_request gate, registered after the Host and Origin checks."""
    if request.method in _READS or request.path in _OPEN_WHEN_LOCKED:
        return None
    if porch_feed():
        return None
    if not passcode.is_set() or passcode.unlocked(request.cookies.get(COOKIE)):
        return None
    if wants_json():
        # 'locked' tells the page to ask for the passcode and try again
        return jsonify({'success': False, 'locked': True, 'message': 'Locked — enter the passcode'}), 401
    return 'Locked — enter the passcode', 401


def _with_unlock(response, token):
    response = make_response(response)
    # HttpOnly: page script can't read it. SameSite=Strict: never sent on a
    # request another site started. Secure whenever the page came over HTTPS
    response.set_cookie(COOKIE, token, max_age=passcode.UNLOCK_DAYS * 86400,
                        httponly=True, samesite='Strict', secure=request.is_secure)
    return response


@lock_bp.route('/unlock', methods=['POST'])
def unlock():
    if not passcode.is_set():
        return error_response('No passcode is set', 400)
    token, wait = passcode.try_unlock(request.form.get('passcode', ''))
    if token:
        g.unlocked = True  # the state sent back must already say so
        log.info(f"Dashboard unlocked from {request.remote_addr}")
        return _with_unlock(success_response('Unlocked'), token)
    # The guess itself is never logged
    log.warning(f"Wrong passcode from {request.remote_addr}" + (f"; tries paused for {wait}s" if wait else ""))
    if wait:
        return error_response(f'Too many wrong tries — wait {_spoken(wait)}', 429)
    return error_response('Wrong passcode', 401)


@lock_bp.route('/lock', methods=['POST'])
def lock():
    passcode.lock(request.cookies.get(COOKIE))
    g.unlocked = False
    response = make_response(success_response('Locked'))
    response.delete_cookie(COOKIE, httponly=True, samesite='Strict')
    return response


@lock_bp.route('/passcode', methods=['POST'])
def set_passcode():
    """Set the first passcode, or change it. Changing gets here only from an
    unlocked device: with one set, the gate above has already checked."""
    code = request.form.get('passcode', '')
    if code != request.form.get('confirm', ''):
        return error_response("The two entries don't match", 400)
    changing = passcode.is_set()
    try:
        token = passcode.set_passcode(code)
    except passcode.PasscodeError as e:
        return error_response(str(e), 400)
    g.unlocked = True
    log.info(f"Passcode {'changed' if changing else 'set'} from {request.remote_addr}")
    return _with_unlock(success_response('Passcode changed' if changing else 'Passcode set'), token)


def _spoken(seconds):
    return f"{seconds}s" if seconds < 90 else f"{round(seconds / 60)} min"
