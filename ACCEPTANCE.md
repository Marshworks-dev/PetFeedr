# Bowl history: small previews so dragging shows a picture straight away

Status: approved

Intent: When you drag the bowl history timeline, the picture for that spot
shows straight away instead of after a wait. A small preview of every saved
frame is kept, the day's previews load in the background when you open a
day, and the full picture replaces the preview once the drag stops.

## Done when

- [test] Each saved 5-minute frame also gets a preview: 320 px wide and a
      fraction of the frame's size, written next to it the same way the
      frame is (temp file, then rename). A preview that fails to make is
      logged once and never stops the frame being saved or the bowl
      reading. Previews are pruned along with their frames.
- [test] `GET /camera/preview/<name>` serves a preview only when the name
      is exactly a listed frame that has a preview. Otherwise it's a 404
      and nothing is joined into a path. It needs the unlock once a
      passcode is set, like past frames. The GET writes nothing.
- [test] A one-off backfill script makes previews for frames already on
      the Pi. It skips ones that exist, so running it twice is harmless.
- [check] Opening a day loads that day's previews in the background.
      While you drag, the picture for the spot shows at once, with
      "Loading 10:30 AM…" still up. When the drag stops, the full picture
      replaces it and the label clears. A frame with no preview falls
      back to today's behaviour (full picture after the drag settles).
- [test] Only the full picture ends the "Loading" wait. A preview never
      makes the picture look final.
- [test] At least one unhappy path per new route or script. All tests pass.

## Out of scope

- Clips: they already load only when you tap Watch
- A filmstrip of previews under the bar
- Changing how often frames are taken or how long they're kept
- HTTPS (still next on the list)
