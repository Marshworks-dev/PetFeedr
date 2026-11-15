# Feed clips: see the bowl before and after every dispense

Status: shipped

Intent: The bowl picture answers "did food come out?" without guesswork.
The camera keeps the last few seconds of its stream in memory. When a
dispense or graze pulse lands it saves a short clip: 10 s before to 30 s
after, at 2 frames a second. Manual and Porch feeds get clips too; the
journal records them as dispenses like any other. Tapping that feed on the timeline plays the
clip as a flipbook. The top panel shows a frame seconds old instead of up
to 5 minutes old. The feed path never knows the camera exists: the camera
watches feeding_events.jsonl, the same journal the feeds already write.

## Done when

- [test] camera.py still never imports feeder_core or takes STATE_LOCK, and
      no feed module imports camera (the existing test still passes).
- [test] A dispense or graze_pulse event in the journal gives one clip in
      data/clips/, named by the event's ts, with frames from about 10 s
      before to 30 s after. Events from before the camera started give none.
- [test] A clip is never cut short by the next event: two pulses close
      together each get their own clip.
- [test] With the webcam unplugged or jammed, feeding and the journal are
      untouched; no clip is made, and the log notes it once.
- [test] Clips older than 14 days are pruned, like frames.
- [test] The clip route takes an event ts, accepts only that exact format,
      and serves only files under data/clips/ (no path from the request
      reaches the filesystem). It sits behind the same Host check as
      /camera/latest.jpg.
- [test] The top panel's picture is the stream's newest frame from memory
      (seconds old), refreshed on each poll. The 5-minute saved frames are
      unchanged.
- [check] A screen left open and untouched for 10 minutes stops fetching
      new pictures and says so on the picture; the next tap brings it back.
- [check] On the phone: tap a past feed on the timeline, the clip plays,
      and food can be seen landing. Feeds with no clip say so plainly.
- [check] The flipbook has a pause control, and with reduced motion it
      opens paused on the "after" frame.
- [test] All tests pass.

## Out of scope

- Live video (decided against: about 1.2 GB an hour per open screen).
- Encoding to MP4/H.264; clips are plain JPEG frames.
- Detecting food or cats in the picture.
- Small graze accuracy (its own hands-on check in OWED).
