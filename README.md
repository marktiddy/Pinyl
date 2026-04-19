# Pinyl 🎵

Stream audio from your turntable (or any analog input) to AirPlay speakers including HomePod, via a Raspberry Pi.

Pinyl captures audio from a USB audio interface, converts it, and streams it to one or more AirPlay 2 devices on your network. It includes a web UI for control and a REST API for Home Assistant integration.

---

## Hardware

- Raspberry Pi (tested on Pi 5)
- USB audio interface with analog input (tested with iRig Pro Duo)
- Turntable with built-in phono preamp (or a separate phono preamp)
- RCA to 3.5mm or RCA to XLR cable depending on your interface
- AirPlay 2 speaker (e.g. HomePod Mini)

---

## Requirements

- Raspberry Pi OS (Lite or Full, 64-bit)
- Python 3.9+
- ffmpeg
- shairport-sync
- avahi-daemon

---

## Installation

### 1. Install system dependencies

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y ffmpeg shairport-sync avahi-daemon avahi-utils python3-pip
```

### 2. Clone the repo

```bash
git clone https://github.com/yourusername/pinyl.git /opt/pinyl
cd /opt/pinyl
```

### 3. Install Python dependencies

```bash
pip3 install -r requirements.txt --break-system-packages
```

### 4. Check your audio interface is detected

Plug in your USB audio interface and run:

```bash
arecord -l
```

You should see your device listed with a card number (e.g. `card 2`).

### 5. Install and start the service

```bash
# Edit the service file to match your username if not 'mark'
nano pinyl.service

# Install
sudo cp pinyl.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable pinyl
sudo systemctl start pinyl
```

### 6. Open the web UI

Navigate to:

```
http://<your-pi-ip>:5000
```

e.g. `http://192.168.68.108:5000`

---

## Usage

1. Open the web UI
2. Click **↻ Scan** to discover AirPlay speakers on your network
3. Select your audio input from the dropdown
4. Click one or more speakers to select them
5. Press **▶ Start Stream**
6. Audio from your turntable will play on the selected speakers

---

## REST API

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/status` | Current stream status |
| POST | `/api/stream/start` | Start streaming |
| POST | `/api/stream/stop` | Stop streaming |
| POST | `/api/discover/speakers` | Scan for AirPlay speakers |
| POST | `/api/discover/inputs` | Scan for audio inputs |
| POST | `/api/volume` | Set volume `{"volume": 0-100}` |
| POST | `/on` | Home Assistant on toggle |
| POST | `/off` | Home Assistant off toggle |

### Example: start stream via curl

```bash
curl -X POST http://192.168.68.108:5000/api/stream/start \
  -H "Content-Type: application/json" \
  -d '{"input_device": "hw:2,0", "speakers": ["Office"], "volume": 70}'
```

---

## Home Assistant Integration

Copy the contents of `ha-configuration.yaml` into your Home Assistant `configuration.yaml`, then restart HA. A **Turntable** switch will appear which you can assign to any room/area.

---

## Troubleshooting

**No audio devices found**
Run `arecord -l` to confirm your interface is detected. Try a different USB port or powered hub.

**Sample format error**
Run `arecord -D hw:X,0 --dump-hw-params` (replace X with your card number) to see supported formats. Pinyl auto-detects S24_3LE and falls back to CD (16-bit).

**No AirPlay speakers found**
Ensure your Pi and speakers are on the same Wi-Fi network. Run `avahi-browse -a | grep AirPlay` to check.

**Service won't start**
Check logs with `sudo journalctl -u pinyl -f`

---

## Notes

- AirPlay streams at 44,100 Hz / 16-bit (ALAC) regardless of input format — this is an AirPlay protocol limitation
- The iRig Pro Duo outputs S24_3LE; Pinyl handles the conversion via ffmpeg automatically
- Volume control via the UI sets the Pi-side gain; speaker volume is controlled independently on the device

---

## License

MIT
