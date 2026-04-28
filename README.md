# Pinyl 🎵

Stream audio from your turntable (or any analog input) to AirPlay speakers including HomePod, via a Raspberry Pi.

Pinyl captures audio from a USB audio interface, converts it, and streams it to one or more AirPlay 2 devices on your network. It includes a web UI for control and a REST API for Home Assistant integration.

---

## Hardware

- [Raspberry Pi 5](https://geni.us/CvALl)
- [Behringer UCA-202](https://geni.us/oZM6) USB audio interface
- Turntable with built-in phono preamp (or a separate phono preamp)
- RCA to 3.5mm or RCA to XLR cable depending on your interface
- AirPlay 2 speaker (e.g. HomePod Mini)

---

## Requirements

- Raspberry Pi OS (Lite or Full, 64-bit)
- Python 3.9+
- ffmpeg
- avahi-daemon

---

## Installation

### 1. Install system dependencies

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y ffmpeg avahi-daemon avahi-utils python3-pip
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

The repo includes `pinyl.service`, a systemd unit that starts Pinyl automatically on boot.

Open it and change the `User=` line to match your Pi username if it isn't `mark`:

```bash
nano /opt/pinyl/pinyl.service
```

Then install and enable it:

```bash
sudo cp /opt/pinyl/pinyl.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable pinyl
sudo systemctl start pinyl
```

Confirm it's running:

```bash
sudo systemctl status pinyl
```

### 6. Open the web UI

Navigate to:

```
http://<your-pi-ip>:5050
```

e.g. `http://192.168.68.108:5050`

---

## Managing the Service

| Task            | Command                        |
| --------------- | ------------------------------ |
| Check status    | `sudo systemctl status pinyl`  |
| Start           | `sudo systemctl start pinyl`   |
| Stop            | `sudo systemctl stop pinyl`    |
| Restart         | `sudo systemctl restart pinyl` |
| View live logs  | `sudo journalctl -u pinyl -f`  |
| Enable on boot  | `sudo systemctl enable pinyl`  |
| Disable on boot | `sudo systemctl disable pinyl` |

After editing `app.py` or any project file, restart the service for changes to take effect:

```bash
sudo systemctl restart pinyl
```

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

| Method | Endpoint                 | Description                    |
| ------ | ------------------------ | ------------------------------ |
| GET    | `/api/status`            | Current stream status          |
| POST   | `/api/stream/start`      | Start streaming                |
| POST   | `/api/stream/stop`       | Stop streaming                 |
| POST   | `/api/discover/speakers` | Scan for AirPlay speakers      |
| POST   | `/api/discover/inputs`   | Scan for audio inputs          |
| POST   | `/api/volume`            | Set volume `{"volume": 0-100}` |
| POST   | `/on`                    | Home Assistant on toggle       |
| POST   | `/off`                   | Home Assistant off toggle      |

### Example: start stream via curl

```bash
curl -X POST http://192.168.68.108:5050/api/stream/start \
  -H "Content-Type: application/json" \
  -d '{"input_device": "plughw:2,0", "speakers": ["<device-id>"], "volume": 70}'
```

Device IDs are returned by `GET /api/status` or `POST /api/discover/speakers`.

---

## Home Assistant Integration

Copy the contents of `ha-configuration.yaml` into your Home Assistant `configuration.yaml`, then restart HA. A **Turntable** switch will appear which you can assign to any room/area.

---

## Troubleshooting

**No audio devices found**
Run `arecord -l` to confirm your interface is detected. Try a different USB port or powered hub.

**ffmpeg exits immediately**
Check the Pinyl logs (`sudo journalctl -u pinyl -f`) for the ffmpeg error output. Run `arecord -D plughw:X,0 -f S16_LE -r 48000 -c 2 /tmp/test.wav` (replace X with your card number) to confirm the device records correctly outside of Pinyl.

**No AirPlay speakers found**
Ensure your Pi and speakers are on the same Wi-Fi network. Run `avahi-browse -a | grep AirPlay` to check.

**Pi appears as an AirPlay speaker on the network**
This is caused by `shairport-sync` running as a service. Pinyl doesn't need it — disable it:

```bash
sudo systemctl disable --now shairport-sync
```

**Service won't start**
Check logs with `sudo journalctl -u pinyl -f`

---

## Notes

- Audio is captured at 48 kHz stereo and streamed as FLAC (compression level 0) via the RAOP protocol
- ffmpeg applies a 4× input gain boost to compensate for the low output level of phono preamps — adjust the `volume=4.0` filter in `app.py` if your input is too loud or too quiet
- Volume control via the UI adjusts the AirPlay receiver volume; the ffmpeg gain is a separate fixed boost
- Audio device IDs use the `plughw:X,0` format, which allows ALSA to handle sample-rate conversion automatically

---

## Support

If you find this useful, [buy me a coffee](https://buymeacoffee.com/marktiddy) ☕

---

## License

MIT
