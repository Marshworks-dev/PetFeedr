# Bowl history: past pictures on a timeline with the feeds and what the camera read

Status: shipped

Intent: The bowl picture stops being only "now". In the full-picture
dialog you can scrub back through the day and step back up to 7 days.
The feeds sit on the same bar, each with its clip and the bowl as the
camera read it before and 30 minutes after, and the stretches when the
camera couldn't see are shaded. So you can see how a meal went, and
check any bowl reading against its own picture (the 12-03 bowl-watch
review becomes a phone task). Past pictures, clips and readings are
private to unlocked devices.

## Done when

Server
- [test] `GET /api/camera/day/<YYYY-MM-DD>` returns, for that day:
      frame times (oldest first); feeds from feeding_events.jsonl
      (dispense, graze_pulse, failure, missed; sim events dropped on a Pi),
      each with its saved clip id when there is one; and the bowl
      readings for that day from data/bowl_readings.jsonl (frame view,
      before/after bowl per feed, blind-camera alerts). A bad date, or
      one outside the last 7 days, is a 400, not a traceback. A torn or
      malformed readings line is skipped, never a 500.
- [test] `GET /camera/frame/<name>` serves a frame only when the name is
      exactly one of `camera.frame_names()`; it's never joined into a path.
      `../`, encoded slashes, `.part` files and well-formed names with no
      file all get 404.
- [test] Once a passcode is set, the day route, past frames and
      `/camera/clips/…` need the unlock (401 without it). With no
      passcode they're open, like everything else. `/camera/latest.jpg`
      stays open. A test fails if a new `/camera/` or `/api/camera/` GET
      isn't on the gated list.
- [test] The camera stays off the feed path: the existing import test
      still passes, the new routes take no lock, and nothing here writes
      to the readings file or the frames folder.

Dashboard
- [check] The full-picture dialog gains a 24h scrub bar for the shown day.
      Dragging or arrow keys move the picture to the nearest frame, and its
      time label follows. Feed marks sit on the bar in the 7-day card's
      colours; a graze shows its pulses.
- [check] Under the bar, a thin strip shades the stretches where the
      camera read dark, blocked or got no picture, and marks where a
      blind-camera alert went out.
- [test] Each dispense offers: **Before** (the clip frame the before
      reading used, or the last 5-minute frame before the feed when there
      is no reading), **▶ Watch** (the existing clip player), and **30 min
      after** (the frame the after reading used, or the first frame 30 min
      on). Each graze pulse offers ▶ Watch. A feed with no clip says so.
- [test] Each dispense shows its readings as words: "empty before · food
      left 30 min after", "couldn't tell", or nothing yet while the after
      reading is pending. Food left after 30 min, or food still there
      before a feed, marks the feed on the bar. The labels say "camera
      read", because the readings haven't been checked yet.
- [check] Day chips step back through the last 7 days; today is the
      default and opens at the newest frame, as the dialog does now.
- [check] A tapped day in the Last 7 Days card lists "see the bowl" per
      feed, which opens the dialog at that feed's Before frame.
- [check] Locked device: the dialog shows the latest picture as now, with
      one line "Unlock to see earlier pictures" that opens the unlock
      dialog. No scrub bar, no frame or day requests. The Watch buttons
      on Today's bar say "Unlock to watch" and open the same dialog.
- [test] Scrubbing fetches one full frame at a time, only once the drag
      settles (about 150 ms), not every frame passed over.
- [test] The scrub bar is a slider with a text value ("Tue 5:25 PM, 5 min
      before the 5:30 feed, camera read empty"); Before, Watch and 30 min
      after say which feed.
- [test] At least one unhappy-path test per new route. All tests pass.
- [check] Light and dark, 320–390 px, on the phone (not yet run at ship:
      checked in a 320/390 px browser and on the tailnet test server)

## Out of scope

- Correcting a reading from the page ("misread" button). It's a write and
  a new data file; next if the 12-03 review wants it
- Any change to bowl_watch's thresholds, or switching meal alerts on
- Thumbnails or a filmstrip (comes next if scrubbing feels slow)
- Live view, HTTPS (next on the list, HTTPS first)
- Extra shots timed to feeds
- Going back past 7 days, though 14 are kept
- Downloading or sharing frames or clips
