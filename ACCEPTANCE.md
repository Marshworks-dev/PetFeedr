# Bowl watch: alerts when the camera is blind or a meal goes uneaten

Status: approved

Intent: The camera tells you, by Pushover, the two things it can actually
see: that it can't see (dark, blocked or offline), and that a meal wasn't
eaten. It doesn't try to catch food landing; the cats get there first.
A review of 47 feed clips found the bowl empty before all 36 visible
feeds, a cat usually at the bowl within 1 s of the food landing, and one
stretch of 2.5 days when the lens was blocked and nothing said so.

## Done when

### Camera can't see
- [test] A saved frame is read as `clear`, `dark` (mostly black) or
      `blocked` (most of the picture covered, the bowl not found).
- [test] One hour of `dark` or `blocked` frames in a row sends one
      Pushover. It doesn't repeat while the camera stays blind; one
      "camera can see the bowl again" follows when a clear frame comes back.
- [test] One hour with no frame saved at all (stream dead or stalled)
      sends the same alert, worded for that cause. Today it only logs.
- [test] A single dark or blocked frame (a cat walking past the lens)
      sends nothing.

### Meals
- [test] For each feed, the bowl is read twice. **Before:** from the clip's
      frames before the food lands: `empty`, `food` or `unknown`. **30 min
      after:** from the first clear 5-minute frame between 30 and 60 min
      after the feed: `empty`, `food` or `unknown`.
- [test] The bowl is found in each frame and not assumed to be in a fixed
      spot (it gets nudged; a fixed crop failed in the study). Bowl not
      found, a cat over it, or a dark frame all read as `unknown`.
- [test] `food` at 30 min after a feed sends one Pushover ("the 15:30 meal
      hasn't been eaten"). `food` before a feed sends one ("food left from
      the last meal"), unless the 30-min alert already covered that food.
- [test] `unknown` never sends a meal alert. A blind camera is the camera
      alert's job.
- [test] The readings go to their own file (`data/bowl_readings.jsonl`,
      14 days), one line per reading with the frame it came from. That way
      any alert can be checked against the picture.
- [test] The readings get synthetic frames right, drawn in the test code:
      empty, food, a cat's head over the bowl, dark, blocked, bowl moved.
      No camera pictures are committed to this repo.
- [check] The same readings get real frames right, run locally against
      crops kept outside the repo (`PETFEEDR_REAL_FRAMES`, a private
      folder); the test is skipped when that folder is absent.

### Ships quiet first
- [test] Meal alerts have an off switch. They ship off and write readings
      only. The camera alerts ship on.
- [check] After a week of readings, compare every reading against its frame
      and report the misreads. Meal alerts get switched on only if you
      approve that report.

### Unchanged
- [test] camera.py and anything new still never import feeder_core or
      take STATE_LOCK, and no feed module imports them (existing test).
      Readings come from saved frames and the journal, never from a call
      on the feed path.
- [test] No new Python dependency: frames are decoded by the ffmpeg
      already on the Pi into a small greyscale thumbnail, and plain Python
      does the arithmetic. The webcam stream itself is untouched (no
      second stream).
- [test] All tests pass.

## Out of scope

- Seeing food land, or measuring portion size (that's the bowl scale's job).
- Telling the two cats apart.
- Showing readings on the dashboard.
- Graze mode, and any change to feeding.
- Changing capture rate or clip length.
- Sending pictures anywhere, or LLM/vision-model reading.
- Committing camera frames, clips or readings to this repo.
