#!/bin/bash
# One-time setup for PetFeedr on Raspberry Pi
# Run this ON THE PI after first deploy: ./setup-pi.sh

set -e

PI_USER="${PI_USER:-$(whoami)}"
PI_PATH="${PI_PATH:-/home/$PI_USER/PetFeedr}"
SERVICE_FILE="/etc/systemd/system/petfeedr.service"

echo "🐱 PetFeedr Pi Setup"
echo "===================="

# Feedings fire on the system clock — a wrong timezone shifts every meal.
# OS images often default to a zone that isn't yours.
if [ -n "${PETFEEDR_TZ:-}" ]; then
    echo "🕐 Setting timezone to $PETFEEDR_TZ..."
    sudo timedatectl set-timezone "$PETFEEDR_TZ"
else
    echo "🕐 Timezone is $(timedatectl show -p Timezone --value)."
    echo "   Wrong? Re-run with PETFEEDR_TZ=Region/City (e.g. America/New_York)."
fi

# The bowl camera shells out to ffmpeg for each frame (camera.py); without
# it the feeder still runs and logs one "Camera offline" warning
echo "📷 Installing ffmpeg for the bowl camera..."
# Not fatal: a failed install must not stop the feeder being set up
sudo apt-get install -y ffmpeg || echo "   ⚠️  ffmpeg didn't install — the feeder will run without the camera"

# The webcam's microphone stays off: no USB audio driver, ever
# (ops/modprobe/petfeedr-no-usb-audio.conf says why)
echo "🔇 Disabling the webcam microphone..."
sudo install -m 644 "$PI_PATH/ops/modprobe/petfeedr-no-usb-audio.conf" /etc/modprobe.d/
# Already loaded (the webcam was plugged in before this ran): unplug the
# driver now. A desktop audio service may hold it; the reboot clears it then
sudo modprobe -r snd_usb_audio 2>/dev/null || echo "   ⚠️  USB audio is in use; the mic is gone after the next reboot"

# Create the systemd service file
echo "📝 Creating systemd service..."
sudo tee "$SERVICE_FILE" > /dev/null << EOF
[Unit]
Description=PetFeedr Service
After=multi-user.target

[Service]
Type=simple
User=$PI_USER
WorkingDirectory=$PI_PATH
# Optional secrets (Pushover creds) — '-' tolerates a missing file
EnvironmentFile=-$PI_PATH/.env
ExecStart=/bin/bash -c 'source $PI_PATH/venv/bin/activate && python $PI_PATH/PetFeedr.py'
Restart=always
# Nothing the feeder runs can gain privileges (no sudo, no setuid), so a
# compromised web app can't load the mic's driver back in
NoNewPrivileges=yes

[Install]
WantedBy=multi-user.target
EOF

# Reload systemd and enable service
echo "🔄 Enabling service..."
sudo systemctl daemon-reload
sudo systemctl enable petfeedr.service

# Restart (not start) so re-running this script applies unit changes
# to an already-running service
echo "▶️  Starting service..."
sudo systemctl restart petfeedr.service

# Check status
echo ""
echo "✅ Setup complete!"
sudo systemctl status petfeedr.service --no-pager

echo ""
echo "🌐 Web interface: http://petfeedr.local:5000"
echo ""
echo "Useful commands:"
echo "  sudo systemctl status petfeedr.service   # Check status"
echo "  sudo systemctl restart petfeedr.service  # Restart"
echo "  sudo journalctl -u petfeedr.service -f   # View logs"
