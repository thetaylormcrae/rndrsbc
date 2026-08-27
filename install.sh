#!/bin/bash
# rndrSBC 1-Command Installation Script for Raspberry Pi OS

set -e

echo "=== Installing rndrSBC Native E-Paper Platform ==="

sudo apt update
sudo apt install -y python3-pip python3-pil python3-requests python3-spidev python3-rpi.gpio fonts-dejavu

echo "=== Installing Python dependencies ==="
pip3 install -r requirements.txt --break-system-packages || pip3 install -r requirements.txt

echo "=== Creating systemd service ==="
sudo bash -c "cat << 'SERVICE' > /etc/systemd/system/rndrsbc.service
[Unit]
Description=rndrSBC Native E-Paper Daemon
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$(pwd)
ExecStart=/usr/bin/python3 $(pwd)/main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
SERVICE"

sudo systemctl daemon-reload
sudo systemctl enable rndrsbc.service

echo "=== Installation Complete! ==="
echo "Start daemon now with: sudo systemctl start rndrsbc"
