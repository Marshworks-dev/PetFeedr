# Grazes get room: spacing and a midnight cutoff

Status: shipped

Intent: A graze needs its whole window to itself, and that window has to
end before midnight. The window is the 30-minute graze plus the slot's
±30 variation when it has one. The schedule editor refuses a setup that
breaks this and says why in plain words, instead of the feeder quietly
feeding all at once or stacking two meals in the bowl.

## Done when
- [test] A graze's set time must be before 23:30 when Fixed and before
      23:00 when ±30 (its last pulse lands 30 minutes after the start and
      must still be dated today). Turning Graze on, or switching a graze
      from Fixed to ±30, past that time is refused (409) with a message
      that names the cutoff. (Adds can't create a graze, so they only
      face the spacing rule.)
- [test] When either slot is a graze, two set times must be at least
      40 minutes apart (30 for the graze plus the existing 10-minute
      buffer). An add or a Graze toggle that breaks this is refused (409)
      with a message naming the slot it clashes with.
- [test] The daily randomizer treats a graze as busy from its start to
      30 minutes later when checking the 10-minute buffer, so two ±30
      slots never roll into each other. When it can't find a clear time,
      it uses the set time, as now.
- [test] A ±30 roll never crosses midnight in either direction. A 23:45
      slot can't become 00:05. Today only backward rolls are clamped
      (feeder_core.py:246).
- [check] In the schedule editor, the Graze chip on a row that can't
      graze reads as unavailable. Tapping it shows the reason ("A graze
      needs 30 minutes before midnight. Set this one to Fixed, or move
      it before 11:00 PM."), and its aria-label says the same.
      A refused add shows the server's message as the toast.
- [test] A schedule already saved with a rule-breaking graze still loads
      and runs: past the cutoff it feeds all at once (the existing
      fallback stays), and the editor shows the row with its reason.
      Nothing is rewritten on disk.
- [test] At least one unhappy-path test per rule above. All tests pass.

## Out of scope
- Showing each slot's window as a band in the editor (later, if the
  messages aren't enough)
- The graze trail on the timeline and 7-day chart (its own change)
- The hopper redesign (its own change)
- Changing the ±30 range, the 30-minute graze span, or the manual feed
  cap and cooldown
- Blocking a manual Feed while a graze runs (the existing note stays)
