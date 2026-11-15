# PetFeedr

An automated pet feeder powered by a Raspberry Pi. Schedule feedings, control portion sizes, and monitor activity through a web interface — all running on your local network.

## Features

- Schedule feeding times with configurable portion sizes (small, medium, large)
- Optional schedule randomization (±30 min) to mimic natural feeding patterns
- Web interface for managing schedules, triggering manual feedings, and viewing activity history
- Stepper motor control (DRV8825) with anti-jam agitation
- Simulation mode for development without hardware
- Systemd service for reliable, always-on operation
- Automatic daily schedule regeneration with fresh randomization

## Hardware

- Raspberry Pi (tested on Pi 3/4/5)
- DRV8825 stepper motor driver
- NEMA 17 stepper motor (or similar)
- Food hopper and dispensing mechanism

### GPIO Pin Mapping

| Function    | GPIO Pin |
|-------------|----------|
| Direction   | 13       |
| Step        | 19       |
| Enable      | 12       |
| Mode 1      | 16       |
| Mode 2      | 17       |
| Mode 3      | 20       |

## Quick Start (Raspberry Pi)

### First-Time Setup

1. Clone the repository on your Mac/PC:
   ```bash
   git clone https://github.com/Marshworks-dev/PetFeedr.git
   cd PetFeedr
   ```

2. Tell the deploy script where your Pi is, in a `.deploy.env` file next to it
   (gitignored) or in your environment. There's no default:
   ```bash
   echo 'PI_HOST=pi@petfeedr.local' > .deploy.env   # your Pi's user@hostname
   ```
   `PI_PATH` defaults to `/home/<user>/PetFeedr`.

3. Deploy to your Pi:
   ```bash
   ./deploy.sh
   ```

4. SSH into your Pi and run the one-time setup:
   ```bash
   ssh pi@your-pi.local
   cd ~/PetFeedr
   PETFEEDR_TZ=America/New_York ./setup-pi.sh   # your timezone; feedings follow the Pi's clock
   ```

5. Access the web interface at `http://your-pi.local:5000`

### Deploying Updates

After making code changes, run:
```bash
./deploy.sh
```

This will back up the current Pi installation, sync changed files, update dependencies, and restart the service. Backups are stored locally (last 10 kept).

## Development (Mac/PC)

For local development without a Pi:
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 PetFeedr.py
```

Simulation mode is enabled automatically when `RPi.GPIO` is not available. You can also force it:
```bash
PETFEEDR_SIMULATE=true python3 PetFeedr.py
```

In simulation mode, no GPIO/hardware is touched — motor movements are logged but not executed, and the web interface works normally.

## Configuration

### Environment Variables

| Variable             | Default              | Description                        |
|----------------------|----------------------|------------------------------------|
| `PI_HOST`            | (none, required)     | SSH target for deploy.sh/ship.sh   |
| `PI_PATH`            | `/home/<user>/PetFeedr` | Install path on Pi              |
| `PETFEEDR_TZ`        | (unchanged)          | Timezone setup-pi.sh sets          |
| `BACKUP_DIR`         | `$HOME/PetFeedr-backups` | Local backup directory         |
| `PETFEEDR_PORT`      | `5000`               | Web interface port                 |
| `PETFEEDR_SIMULATE`  | `false`              | Force simulation mode              |
| `FLASK_SECRET_KEY`   | (random)             | Flask session secret key           |
| `FLASK_DEBUG`        | `false`              | Enable Flask debug mode            |
| `PETFEEDR_CAMERA`    | `/dev/video0`        | Bowl webcam device (needs ffmpeg)  |
| `PUSHOVER_TOKEN`, `PUSHOVER_USER` | (unset = off) | Phone alerts via Pushover |
| `PETFEEDR_NOTIFY_IN_SIM` | `false`          | Send alerts from simulation mode   |
| `PORCH_FEED_KEY`     | (unset = off)        | Lets a companion app give one small extra a day (below) |

On the Pi these go in `~/PetFeedr/.env` (mode 600); the service reads it.

### Companion apps

`PORCH_FEED_KEY` lets another household app (a family dashboard, say) give
one small extra feeding a day without the passcode, by sending the key in an
`X-Porch-Key` header on `POST /feed`. The key opens nothing else.

## Manual Installation

If you prefer manual setup on the Pi:

1. Copy files to your Pi
2. Create a virtual environment:
   ```bash
   cd ~/PetFeedr
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements-pi.txt
   ```

3. Create the systemd service:
   ```bash
   sudo nano /etc/systemd/system/petfeedr.service
   ```

   ```ini
   [Unit]
   Description=PetFeedr Service
   After=multi-user.target

   [Service]
   Type=simple
   User=<your-user>
   WorkingDirectory=/home/<your-user>/PetFeedr
   ExecStart=/bin/bash -c 'source /home/<your-user>/PetFeedr/venv/bin/activate && python /home/<your-user>/PetFeedr/PetFeedr.py'
   Restart=always

   [Install]
   WantedBy=multi-user.target
   ```

4. Enable and start:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable petfeedr.service
   sudo systemctl start petfeedr.service
   ```

## Usage

1. Access the web interface at `http://localhost:5000` (or `http://<pi-hostname>:5000`)
2. Add feeding schedules with time, portion size, and optional randomization
3. Use the manual feed button for on-demand feedings
4. View recent activity in the feeding log

## Security Note

Reading the dashboard is open to anyone who can reach it. Feeding, schedule
changes and refills need the household passcode once one is set (from the
dashboard; each device stays unlocked for 30 days). The server listens on all
interfaces (`0.0.0.0`), refuses requests for host names that aren't its own,
and refuses cross-site form posts. It's built for a trusted home network or a
tailnet. Don't expose it to the public internet: it has no HTTPS of its own.

## Useful Commands

```bash
sudo systemctl status petfeedr.service    # Check status
sudo systemctl restart petfeedr.service   # Restart
sudo journalctl -u petfeedr.service -f    # View logs
```

## License

Copyright 2024-2026 MarshWorks LLC

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) for details.
