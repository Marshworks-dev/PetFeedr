// The feed panel: tapping the hopper in the header drops it open with the
// feed controls and the hopper's level; an outside tap or Escape closes it.
// A disclosure (button + aria-expanded), not a dialog: the page around it
// stays live, so a pour can be seen landing on Today's bar.

import { $, animate } from '../dom.js';

// Open and close are the same motion, one played backwards
const TUCKED = { opacity: 0, transform: 'translateY(-10px) scale(0.97)' };
const SHOWN = { opacity: 1, transform: 'none' };

// Closing counts as closed: the panel is on its way out
function isOpen() {
    const panel = $('#feed-panel');
    return !panel.hidden && !panel.dataset.closing;
}

export function openPanel() {
    const panel = $('#feed-panel');
    if (isOpen()) return;
    // Reopened mid-close: pick up from wherever the close got to
    panel.getAnimations().forEach(a => a.cancel());
    delete panel.dataset.closing;
    panel.hidden = false;
    $('#hopper-button').setAttribute('aria-expanded', 'true');
    animate(panel, [TUCKED, SHOWN], { duration: 220 });
}

// returnFocus: false when the close isn't the user's doing (a feed went
// through), so focus isn't yanked somewhere they weren't looking
export function closePanel({ returnFocus = true } = {}) {
    const panel = $('#feed-panel');
    if (!isOpen()) return;
    const hadFocus = panel.contains(document.activeElement);
    panel.dataset.closing = 'true';
    $('#hopper-button').setAttribute('aria-expanded', 'false');
    if (returnFocus || hadFocus) $('#hopper-button').focus();
    // Held at the end (fill) so it can't flash back in before it's hidden;
    // with reduced motion animate() resolves at once and it just goes
    animate(panel, [SHOWN, TUCKED], { duration: 180, easing: 'ease-in', fill: 'forwards' }).then(() => {
        if (!panel.dataset.closing) return;  // reopened meanwhile
        panel.hidden = true;
        delete panel.dataset.closing;
        panel.getAnimations().forEach(a => a.cancel());
    });
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
