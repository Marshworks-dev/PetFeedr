# PetFeedr

Raspberry Pi-based automated pet feeder. Manages a stepper motor (DRV8825 driver)
to dispense food on a schedule. Features optional +/-30-minute randomization per
feeding to mimic natural patterns, manual trigger support, and a Flask web UI
served as a PWA.

No database — schedules and logs are flat files.

## Stack

- **Language**: Python 3
- **Web framework**: Flask
- **Scheduling**: `schedule` library (in-process)
- **Hardware**: DRV8825 stepper motor driver via `RPi.GPIO`
- **Simulation mode**: auto-enabled when `RPi.GPIO` is unavailable off a Pi (dev on Mac); on a Pi that is a hard error
- **Frontend**: Vanilla JS PWA — `static/js/` (`app.js` entry; `api.js`, `store.js`, `dom.js`, `cards/`…), `static/sw.js`, `static/manifest.json`

## Key Files

```
PetFeedr.py              # Entry point: main schedule loop + Flask daemon thread (one process)
feeder_core.py            # Shared core: logging, STATE_LOCK, feed_pet, randomization, resync
web_interface.py          # Flask app: index, feed (manual cap + cooldown), refill, CSRF Origin guard
schedule_routes.py        # Blueprint: add/delete/toggle/portion edits to the schedule
dashboard.py              # Read-only view-model for the index page
schedule_store.py         # The one parser/writer for feeding_schedules.txt
graze_rules.py            # Where a graze may sit: 40 min clear, ends before midnight (pure)
atomicfile.py             # write_atomic(): temp + fsync + os.replace for every state file
responses.py              # wants_json / error_response shared by route modules
manual_limit.py           # Daily manual-feed cap (half the schedule, floor ¾ cup) + cooldown
feeding_stats.py          # Log parsing + consumption stats (pure stdlib, unit-tested)
hopper.py                 # Hopper level tracking; learns capacity from refill feedback
notify.py                 # Pushover alerts (PUSHOVER_TOKEN/PUSHOVER_USER env; fail-soft)
passcode.py               # Household passcode: hash + unlock tokens in data/passcode.json, growing waits on wrong guesses
lock_routes.py            # Blueprint: /unlock, /lock, /passcode, and the gate in front of every write
camera.py                 # Bowl webcam: one long-running ffmpeg stream; a frame every 5 min to data/frames/,
                          #   a clip of every dispense/graze pulse to data/clips/ (both 14 days)
servo_controller.py       # Motor control: portion sizes, dispense cycles, anti-jam agitation
DRV8825.py                # GPIO abstraction; falls back to mock_gpio.py off-Pi only
mock_gpio.py              # Simulation stub for RPi.GPIO
test_feeder.py            # unittest suite (python3 -m unittest)
feeding_schedules.txt     # Persistent schedule store: "HH:MM,portion[,fixed][,graze]"
feeding_log.txt           # Rotating log, midnight rotation, 14-day retention
todays_schedule.json      # Today's randomized schedule; source of truth for today's jobs
hopper.json               # Hopper counter + learned capacity estimates
deploy.sh                 # rsync-based deploy to Pi with SSH multiplexing
setup-pi.sh               # One-time Pi setup (venv, systemd unit w/ EnvironmentFile)
ops/check_live.py         # ship.sh smoke: refuses a live Pi in simulation mode
```

Key invariants:
- **One process.** The scheduler owns the main thread; Flask runs in a
  daemon thread. Never spawn web_interface separately in production —
  its `__main__` entry is a console-only dev server.
- **All schedule/motor/file mutations hold `feeder_core.STATE_LOCK`.**
  The `schedule` library is not thread-safe.
- **Web mutations call `resync_today()`** so edits take effect
  immediately; `todays_schedule.json` is the source of truth for
  today's jobs.
- **The "Feeding completed" log line is the single dispense record**
  (tagged `, manual` / `, scheduled`). Don't add other countable lines
  — the stats regexes in feeding_stats.py parse only this family.
- **State files are written with `atomicfile.write_atomic`**, never
  `open(path, 'w')` — a power cut mid-write must not leave an empty schedule.
- **`feeder_core.slot_outcome` is the one answer to "what happened to
  this slot"** — the startup catch-up and the dashboard both read it.
- **Scheduled jobs run `feeder_core.scheduled_feed`, never `feed_pet`
  directly** — it skips a slot already handled today. The Pi has no
  battery clock; after a power cut it boots on a stale time and jumps
  forward at NTP sync, which would otherwise re-fire a fed slot. Startup
  also holds the scheduler for that sync (`wait_for_clock_sync`, 10 min cap).
- **On a real Pi, a missing RPi.GPIO is fatal**, not simulation (DRV8825.load_gpio).
- **The camera stays off the feed path.** camera.py never takes
  `STATE_LOCK` or imports feeder_core, and no feed module imports it
  (a test pins both). `/camera/latest.jpg` takes no filename from the request.
  Clips come from camera.py watching `feeding_events.jsonl`, never from a
  call on the feed path. `/camera/clips/<id>/<n>.jpg` reaches a file only
  through `camera.clip_frame_path`, which checks both parts.
- **The camera stream is started once and left running.** This webcam jams
  when a stream starts after an earlier one stopped, so camera.py restarts
  it only when it has died or stalled. Don't add per-shot captures.
- **Requests pass a Host check, then (POSTs) an Origin check, then the
  passcode gate**, in that order (web_interface.py). `own_host` accepts
  `localhost`, `petfeedr`, `petfeedr.local`, any `*.ts.net` tailnet name
  and bare IPs; anything else gets 421 (DNS rebinding). A POST whose
  Origin isn't this host gets 403; no Origin (curl, server-to-server)
  passes.
- **Every write goes through the passcode gate.** `lock_routes.require_unlock`
  runs before every non-GET request once a passcode is set; a test fails if
  a new POST route isn't on its list. One exception: POST /feed with Porch's
  `X-Porch-Key` (`PORCH_FEED_KEY` in the Pi's `.env`; unset = off) gets
  through for one small extra a day, logged as source `porch`. The key opens
  nothing else. Extras are `feeding_stats.EXTRA_SOURCES` (manual, porch). Reads stay open. The feed path
  (feeder_core, servo, PetFeedr.py) never imports passcode: scheduled feeds
  can't be locked out.
- **Log through `logging.getLogger('petfeedr')`**, never the root
  logger (root goes to stderr/journald, not feeding_log.txt).

## Data Model (Flat Files)

**`feeding_schedules.txt`** — one entry per line: `HH:MM,portion[,fixed][,graze]`
- `portion`: `small` | `medium` | `large` (1/2/3 dispense cycles)
- `fixed`: optional; omit to enable randomization
- `graze`: optional; dispense as 50-step pulses over 30 min on a background
  thread (`feeder_core.run_graze`), lock held per pulse only. One
  "Feeding completed" line at the end (`, graze`); each pulse is journaled
  so a restart mid-graze records what came out (`reconcile_grazes`) and
  never re-fires the slot

**`todays_schedule.json`** — generated at startup, rebuilt on new day

**`feeding_log.txt`** — rotated daily, 14 copies retained

## Running

### Development (Mac — no hardware)
```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python3 PetFeedr.py
```
Simulation mode activates automatically. Web UI at `http://localhost:5000`.

### On the Pi (production)
```bash
sudo systemctl start petfeedr.service
```

## Deployment

```bash
./ship.sh            # "ship it": tests → PR → merge → deploy.sh → prod smoke
./ship.sh --check    # preflight + smoke against the running Pi; changes nothing
./deploy.sh          # deploy only (no git), e.g. redeploying the current main
```
ship.sh refuses a dirty tree, failing tests, or a feed due within 3 min, and
fails the smoke on an inactive service, non-200 dashboard, simulation mode, or
any traceback since the restart. deploy.sh backs up the Pi install, syncs
files, updates deps, restarts the service; schedule/log/state files are preserved.

Target: `PI_HOST=user@host`, from the environment or a gitignored `.deploy.env`
(no default). `PI_PATH` defaults to `/home/<user>/PetFeedr`.


## GPIO Pin Mapping (DRV8825)

| Function  | GPIO Pin |
|-----------|----------|
| Direction | 13       |
| Step      | 19       |
| Enable    | 12       |
| Mode 1    | 16       |
| Mode 2    | 17       |
| Mode 3    | 20       |
