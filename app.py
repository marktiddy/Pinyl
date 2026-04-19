#!/usr/bin/env python3
"""
Pinyl - Turntable to AirPlay streamer
Streams audio from an analog input (e.g. iRig Pro Duo) to AirPlay speakers
"""

import asyncio
import subprocess
import threading
import json
import logging
from flask import Flask, jsonify, render_template, request
import pyatv
import pyatv.const

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# --- State ---
state = {
    "streaming": False,
    "speakers": [],         # discovered AirPlay speakers
    "active_speakers": [],  # currently streaming to
    "input_device": None,   # selected ALSA capture device
    "inputs": [],           # discovered ALSA inputs
    "volume": 50,
    "error": None,
}

stream_process = None
stream_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Audio input discovery
# ---------------------------------------------------------------------------

def discover_inputs():
    """Discover ALSA capture devices."""
    try:
        result = subprocess.run(
            ["arecord", "-l"],
            capture_output=True, text=True
        )
        inputs = []
        for line in result.stdout.splitlines():
            if line.startswith("card "):
                parts = line.split(":")
                card_num = parts[0].replace("card ", "").strip()
                name = parts[1].split("[")[1].split("]")[0].strip() if "[" in parts[1] else parts[1].strip()
                device = f"hw:{card_num},0"
                inputs.append({"id": device, "name": name, "card": card_num})
        state["inputs"] = inputs
        if inputs and not state["input_device"]:
            state["input_device"] = inputs[0]["id"]
        logger.info(f"Discovered inputs: {inputs}")
    except Exception as e:
        logger.error(f"Error discovering inputs: {e}")
        state["inputs"] = []


# ---------------------------------------------------------------------------
# AirPlay speaker discovery
# ---------------------------------------------------------------------------

async def _discover_airplay():
    """Async AirPlay discovery using pyatv."""
    try:
        devices = await pyatv.scan(asyncio.get_event_loop(), timeout=10)
        speakers = []
        for device in devices:
            if pyatv.const.Protocol.AirPlay in [s.protocol for s in device.services]:
                speakers.append({
                    "id": str(device.identifier),
                    "name": device.name,
                    "address": str(device.address),
                })
        state["speakers"] = speakers
        logger.info(f"Discovered speakers: {speakers}")
    except Exception as e:
        logger.error(f"Error discovering AirPlay speakers: {e}")
        state["speakers"] = []


def discover_speakers():
    """Run AirPlay discovery in a thread."""
    loop = asyncio.new_event_loop()
    loop.run_until_complete(_discover_airplay())
    loop.close()


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------

def get_sample_format(device):
    """Probe the device for supported sample format."""
    result = subprocess.run(
        ["arecord", "-D", device, "--dump-hw-params"],
        capture_output=True, text=True, timeout=3
    )
    output = result.stderr + result.stdout
    if "S24_3LE" in output:
        return "S24_3LE"
    return "cd"  # fallback to 16-bit CD quality


def build_ffmpeg_cmd(input_device, sample_fmt):
    """Build the ffmpeg capture command."""
    if sample_fmt == "S24_3LE":
        return [
            "ffmpeg", "-y",
            "-f", "alsa",
            "-i", input_device,
            "-ar", "44100",
            "-ac", "2",
            "-f", "s16le",
            "-acodec", "pcm_s16le",
            "pipe:1"
        ]
    else:
        return [
            "ffmpeg", "-y",
            "-f", "alsa",
            "-f", "cd",
            "-i", input_device,
            "-f", "s16le",
            "pipe:1"
        ]


def start_stream(input_device, speaker_addresses, volume):
    """Start the ffmpeg → shairport-sync stream."""
    global stream_process

    with stream_lock:
        if state["streaming"]:
            stop_stream()

        try:
            sample_fmt = get_sample_format(input_device)
            ffmpeg_cmd = build_ffmpeg_cmd(input_device, sample_fmt)

            # Use shairport-sync piped mode targeting first speaker
            # Multi-speaker support via multiple shairport-sync instances
            shairport_cmd = [
                "shairport-sync",
                "--output=stdout",
                "--name=Pinyl",
            ]

            logger.info(f"Starting stream: {ffmpeg_cmd}")
            ffmpeg_proc = subprocess.Popen(
                ffmpeg_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )

            shairport_proc = subprocess.Popen(
                shairport_cmd,
                stdin=ffmpeg_proc.stdout,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            stream_process = (ffmpeg_proc, shairport_proc)
            state["streaming"] = True
            state["active_speakers"] = speaker_addresses
            state["error"] = None
            logger.info("Stream started successfully")

        except Exception as e:
            logger.error(f"Error starting stream: {e}")
            state["error"] = str(e)
            state["streaming"] = False


def stop_stream():
    """Stop all stream processes."""
    global stream_process

    with stream_lock:
        if stream_process:
            ffmpeg_proc, shairport_proc = stream_process
            try:
                shairport_proc.terminate()
                ffmpeg_proc.terminate()
                shairport_proc.wait(timeout=5)
                ffmpeg_proc.wait(timeout=5)
            except Exception as e:
                logger.error(f"Error stopping stream: {e}")
            stream_process = None

        state["streaming"] = False
        state["active_speakers"] = []
        logger.info("Stream stopped")


# ---------------------------------------------------------------------------
# Routes - Web UI
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    discover_inputs()
    return render_template("index.html", state=state)


# ---------------------------------------------------------------------------
# Routes - REST API
# ---------------------------------------------------------------------------

@app.route("/api/status", methods=["GET"])
def api_status():
    return jsonify({
        "streaming": state["streaming"],
        "active_speakers": state["active_speakers"],
        "input_device": state["input_device"],
        "volume": state["volume"],
        "error": state["error"],
    })


@app.route("/api/discover/speakers", methods=["POST"])
def api_discover_speakers():
    t = threading.Thread(target=discover_speakers)
    t.start()
    t.join(timeout=15)
    return jsonify({"speakers": state["speakers"]})


@app.route("/api/discover/inputs", methods=["POST"])
def api_discover_inputs():
    discover_inputs()
    return jsonify({"inputs": state["inputs"]})


@app.route("/api/stream/start", methods=["POST"])
def api_stream_start():
    data = request.json or {}
    input_device = data.get("input_device", state["input_device"])
    speaker_addresses = data.get("speakers", state["active_speakers"])
    volume = data.get("volume", state["volume"])

    if not input_device:
        return jsonify({"error": "No input device selected"}), 400

    state["input_device"] = input_device
    state["volume"] = volume

    t = threading.Thread(target=start_stream, args=(input_device, speaker_addresses, volume))
    t.start()
    t.join(timeout=10)

    return jsonify({"streaming": state["streaming"], "error": state["error"]})


@app.route("/api/stream/stop", methods=["POST"])
def api_stream_stop():
    stop_stream()
    return jsonify({"streaming": False})


@app.route("/api/volume", methods=["POST"])
def api_volume():
    data = request.json or {}
    volume = data.get("volume", 50)
    state["volume"] = volume
    # TODO: pipe volume to shairport-sync via DACP/DBUS when implemented
    return jsonify({"volume": volume})


# Home Assistant compatibility endpoints
@app.route("/on", methods=["POST"])
def ha_on():
    t = threading.Thread(
        target=start_stream,
        args=(state["input_device"], state["active_speakers"], state["volume"])
    )
    t.start()
    t.join(timeout=10)
    return jsonify({"state": "on" if state["streaming"] else "off"})


@app.route("/off", methods=["POST"])
def ha_off():
    stop_stream()
    return jsonify({"state": "off"})


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logger.info("Starting Pinyl...")
    discover_inputs()
    t = threading.Thread(target=discover_speakers)
    t.start()
    app.run(host="0.0.0.0", port=5000, debug=False)
