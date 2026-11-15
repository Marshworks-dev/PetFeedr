# Graze trail, and the hopper becomes the feed button

Status: shipped

Intent: A graze reads as what it is, a meal spread out over time, on Today's
bar and the 7-day chart. The hopper moves to the top of the screen and
becomes the way you feed by hand. When food comes out while the app is open,
you see it fall from the hopper and land on Today's bar, then the right node
fills. Two commits: the trail first, then the hopper.

## Done when

Trail (commit 1)
- [test] Today's state gives each graze slot its pulse count (small 2,
      medium 4, large 6), how many have landed, and whether it was cut
      short. These come from the pulse journal.
- [check] On Today's bar, a graze is its head node followed by one crumb
      for each pulse after the first. A crumb is filled once its pulse has
      landed and hollow while it's still to come. A graze cut short ends
      in a ✕. Crumbs are spaced evenly, not to scale, so the trail's
      length shows the pulse count. This matches the trail mock
      (2Tg2UFai…).
- [test] The 7-day chart puts a graze at its slot's start, not at the time
      it finished, and draws a smaller trail of crumbs. The legend gains a
      "grazed" entry.
- [test] A large graze in the day's last slot doesn't run off the end of
      Today's bar. The bar's end is padded to fit it.
- [test] Every node and crumb has a text equivalent ("5:30 PM graze, 2 of
      4 pulses so far").

Hopper (commit 2)
- [check] The hopper sits top center in the header, on every view. Its
      badge shows the level. Tapping it opens a panel with the controls
      that today live in the Feed and Hopper cards: S/M/L, hold to feed,
      extras left, pounds left, the refill-by date and Refilled. The panel
      closes on an outside tap or Escape. It matches the pour mock
      (UpTVab…).
- [test] The Feed card and the Hopper card are gone. Their controls keep
      their behavior: hold to feed, the daily cap, the cooldown, the
      passcode gate and the refill form all work as now, and their
      existing tests still pass.
- [test] A manual or Porch feed shows on Today's bar as an extra node with
      an orange ring, at the time it happened.
- [check] While the app is open, a new dispense pours: food falls from the
      hopper over the camera and sprinkles along Today's bar, the bar
      charges up, and the charge converges into the node, which fills with
      a burst. This covers a manual feed, a scheduled meal and each graze
      pulse; a pulse is a lighter sprinkle into its crumb.
- [test] The pour is triggered only by a change in state. A feed that
      happened while the app was closed shows as already filled, with no
      pour. A refresh or a second tab never pours the same feed twice.
- [check] With reduced motion on, nothing falls or moves: the node simply
      fills.
- [check] Light and dark at phone width. The hopper button is at least
      44 px, its label says the level and what tapping it does, and the
      panel's focus moves in and back out correctly.
- [test] At least one unhappy-path test for each new data field. All
      tests pass.

## Out of scope
- Live push (SSE or websockets). A pour shows up on the next state refresh,
  as the page updates now.
- Any change to the feed path, the manual cap or cooldown, the refill math,
  or the camera
- Pours on the 7-day chart
- Sound or haptics
