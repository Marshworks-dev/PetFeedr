# Berry: PetFeedr's own colours

Status: approved

Intent: PetFeedr drops the espresso, cream and yellow it borrowed from
MarshWorks and gets a palette of its own: plum ink, a coral accent and a
soft pink-white, light and dark. The layout and type stay as they are; the
MarshWorks credit in the footer stays as it is.

## Done when

- [test] Every --color-* token in both theme blocks takes its Berry value.
      Text on its background meets WCAG AA (4.5:1 body, 3:1 large text and
      icons) in light and dark. An existing or new contrast test checks this.
- [check] The 72 hard-coded colours in styles.css are each either moved to
      a token or kept on purpose with a comment (the food stays kibble-tan).
      None still reads as espresso or yellow.
- [test] Status colours stay distinct from the accent: "late" (warning) and
      "missed" (danger) don't read as coral. The test checks hue distance,
      not just contrast.
- [test] Small / medium / large portion colours stay distinct from each
      other in both themes.
- [check] The header P mark, icon.svg, logo.svg, the manifest's
      theme_color and theme.js's status-bar colours use Berry, so the home-
      screen icon and browser chrome match the app.
- [check] The bar over the bowl picture stays readable over a real photo
      in light and dark.
- [check] Light and dark at 320/390px; side-by-side shots against Kibble.
- [test] All tests pass.

## Out of scope

- Fonts, layout, the logo/P rebrand (shelved).
- The MarshWorks footer credit (unchanged; the kit's logo carries its own
  plate).
- Small graze accuracy (its own hands-on check in OWED).
