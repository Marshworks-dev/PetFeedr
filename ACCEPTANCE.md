# One mark: the P is the hopper

Status: approved

Intent: Merge the P logo and the hopper into one mark that works as the
brand, with a full logo and a short mark the way MarshWorks has them. The
P is drawn as the hopper itself: kibble fills the letter around its
counter, the stem is the chute, the spout sits under the stem (option A on
the mark board). The full logo is that P with "etFeedr" in Fredoka, kerned
in so the e tucks under the curve of the bowl (spacing b). The short mark
is the P alone. In the app, the P keeps the hopper's job: it's the feed
button and it shows the fill level.

## Done when
- [check] The header is the full logo on the left, with lock and theme on
      the right. The logo's P is the live hopper button with the % badge
      under its spout. Only the P is the button, with a tap area at least
      44 px wide. When the hopper is low, the badge's text turns the
      warning colour too. The name stays one size at every width. "PetFeedr" stays the
      page's heading for screen readers. The old P disc is gone. It matches
      layout 1 and spacing b on the boards.
- [test] The P is still the hopper button. It shows the level, turns the
      warning colour when low, and reads half full while capacity is still
      being learned. Tapping it opens the feed panel. Its label still says
      the level and what a tap does.
- [check] A pour still falls from the P's spout, and the P shakes when the
      food drops. With reduced motion on, nothing moves.
- [test] The short mark is the favicon and the manifest icon, plus a new
      180 px PNG `apple-touch-icon`, so iOS shows it on the home screen. The
      icon is static at about 80% full. The full logo exists as its own SVG
      file (light and dark), for the README and the brand kit later. The
      page links all three icons.
- [check] At 320, 360 and 390 px, in light and dark, the header fits on one
      row and the theme button stays fully on screen. The short mark stays
      legible at 16 px.
- [test] All existing tests pass, plus one unhappy-path test: the page still
      renders the logo when the hopper has no data.

## Out of scope
- Any change to the feed panel, the hopper math, the pour path or the feed path
- The wordmark's typeface, or the app's colours beyond the mark itself
- Putting the full logo into the README, marshworks.dev or the brand kit
  (follow-up, once it has lived on the phone); logo.png stays as is
- Animating the home-screen icon
