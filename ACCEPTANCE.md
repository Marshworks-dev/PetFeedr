# Made by MarshWorks in the footer

Status: approved

Intent: PetFeedr says who makes it, quietly. The status footer ends with
"Made by" and the MarshWorks logo at about the height of the footer text.
Nothing else about the page changes.

## Done when

- [check] The footer's last item is "Made by" followed by the full
      MarshWorks logo (the espresso version, which carries its own plate,
      so it reads in light and dark). The logo is no taller than about
      1.4× the footer text. At 320px it wraps onto its own line rather
      than squeezing the other items.
- [test] The logo is the kit's file, copied verbatim to
      static/brand/marshworks-logo-espresso.svg. It's served from
      PetFeedr itself and precached by sw.js, so it works offline and
      loads nothing from marshworks.dev.
- [test] A screen reader hears "Made by MarshWorks" once: the image's
      alt is "MarshWorks", and nothing else in the footer repeats it.
- [test] It's plain, not a link. Tapping it goes nowhere.
- [test] Unhappy path: the footer still renders, with the credit,
      before /status data arrives or while offline.
- [check] Light and dark at phone width.
- [test] All tests pass.

## Out of scope

- The logo/P rebrand (shelved) and the app's colours.
- A sync script. Re-copying the one SVG is a single `cp`. It gets a
  script when the kit changes often enough to need one.
- THIRD_PARTY.md, PROMISES.md, .marshworks.json (the checklist pass).
