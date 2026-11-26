"""Make timeline previews for frames saved before previews existed.

One-off, run from the install folder on the Pi:
    venv/bin/python backfill_previews.py
Frames that already have a preview are skipped, so running it again is
harmless. It only adds files to data/previews/; frames, clips and readings
are never touched.
"""
import logging
import os
import sys

import camera


def main():
    # Lowest CPU priority, so the feeder and the live camera come first.
    # Best effort: some sandboxes refuse it, and the previews are made anyway
    try:
        os.nice(19)
    except OSError as e:
        print(f"Running at normal priority ({e})")
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    made, failed = camera.backfill_previews()
    print(f"Previews made: {made}, failed: {failed}, total now: {len(camera.preview_names())}")
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
