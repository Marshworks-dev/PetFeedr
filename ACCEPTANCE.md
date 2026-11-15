# PetFeedr's new look: Kibble

Status: shipped

Intent: PetFeedr looks like a MarshWorks product of its own. It uses the
warm "Kibble" look picked on the canvas: cream and espresso, with the
MarshWorks yellow and orange, rounded type, and a P mark: an espresso P
cut from a yellow disc. The app does exactly what it does today. Only the look, the icon and the
fonts change. Dark mode is drawn to match, not just inverted.

## Done when
- [test] Colours live as tokens on `:root` and under `[data-theme="dark"]`
      in styles.css. Both themes use the Kibble palette (cream #faf4ea /
      espresso #2b1d14 / yellow #f2b632 / orange #e8762c, and the dark
      set from the canvas). The old teal and coral are gone from the
      stylesheet, icons and manifest.
- [test] Every text/background token pair the dashboard uses passes
      4.5:1 in both themes, checked by a test that parses the tokens.
- [test] Fredoka (headings) and Nunito (body) are self-hosted woff2
      latin subsets with their OFL texts, preloaded and precached. Baloo 2
      and DM Sans are removed. The existing "nothing off-box" and
      "every precached file is served" tests still pass.
- [check] Header: the P mark next to a "PetFeedr" wordmark
      in Fredoka, with the lock and theme buttons as 44px round buttons.
- [check] The camera hero matches the canvas: the next-feeding bar sits on
      espresso above the bowl picture, and the "x min ago" chip sits in
      the corner. With no camera frame it falls back to the plain
      next-feeding card in the same style.
- [check] The Feed button is flat yellow with a pale ring, with no raised
      ledge. It sits beside the S/M/L control and the extras line. The
      hold-to-feed ring, the check animation and the disabled-until-JS
      behaviour are unchanged.
- [check] Schedule rows: the next slot gets the soft yellow highlight.
      Portion dots stay distinguishable by lightness, not hue alone.
      The grazing hooks are kept: `.grazing-note`, `#grazing-note`,
      `--dot-fill` on the portion classes, and the `.status-grazing`
      half-fill.
- [check] The P mark (option G on the canvas) is drawn as SVG paths,
      not font text. The header shows the yellow disc on its own. The app
      icon is the disc on an espresso tile, and it replaces `icon.svg` and
      `logo.svg`. The manifest and `theme-color` (light and dark) follow
      the new ground colours, and the 16px favicon stays legible.
- [test] All tests pass. Playwright screenshots at 390px in light and
      dark, with and without a camera frame, compared against the canvas
      and shown to Jason before shipping.
- [check] Jason's phone check of the installed app in both themes
      (OWED line).

## Out of scope
- Any behaviour, route, API or data change, including the passcode,
  Porch and feed logic
- The "Made by MarshWorks" block (its own change, from the brand kit)
- Making PetFeedr reachable only over the tailnet (separate change)
- The MarshWorks checklist files (THIRD_PARTY.md, PROMISES.md,
  .marshworks.json)
- Rewriting copy beyond what the new layout needs
