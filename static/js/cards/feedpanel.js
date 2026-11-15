// The feed panel: tapping the hopper in the header drops it open with the
// feed controls and the hopper's level; an outside tap or Escape closes it.
// A disclosure (button + aria-expanded), not a dialog: the page around it
// stays live, so a pour can be seen landing on Today's bar.

import { $, animate } from '../dom.js';

function isOpen() {
    return !$('#feed-panel').hidden;
}

export function openPanel() {
    const panel = $('#feed-panel');
    if (isOpen()) return;
    panel.hidden = false;
    $('#hopper-button').setAttribute('aria-expanded', 'true');
    animate(panel, [{ opacity: 0, transform: 'translateY(-10px) scale(0.97)' },
                    { opacity: 1, transform: 'none' }], { duration: 220 });
}

// returnFocus: false when the close isn't the user's doing (a feed went
// through), so focus isn't yanked somewhere they weren't looking
export function closePanel({ returnFocus = true } = {}) {
    const panel = $('#feed-panel');
    if (!isOpen()) return;
    const hadFocus = panel.contains(document.activeElement);
    panel.hidden = true;
    $('#hopper-button').setAttribute('aria-expanded', 'false');
    if (returnFocus || hadFocus) $('#hopper-button').focus();
}

export function initFeedPanel() {
    const button = $('#hopper-button');
    const panel = $('#feed-panel');
    button.addEventListener('click', () => (isOpen() ? closePanel() : openPanel()));
    document.addEventListener('pointerdown', e => {
        if (isOpen() && !panel.contains(e.target) && !button.contains(e.target)) {
            closePanel({ returnFocus: false });
        }
    });
    document.addEventListener('keydown', e => {
        // The passcode dialog handles its own Escape
        if (e.key === 'Escape' && isOpen() && !document.querySelector('dialog[open]')) closePanel();
    });
}
