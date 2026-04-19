#!/usr/bin/env python3
"""
Pinyl - Turntable to AirPlay streamer
Streams audio from an analog input (e.g. iRig Pro Duo) to AirPlay speakers
via pyatv RAOP protocol.
"""

import asyncio
import subprocess
import threading
import logging
import os
from flask import Flask, jsonify, render_template, request
import pyatv
import pyatv.const

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__, static_url_path="", static_folder="static")

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
state = {
    "streaming": False,
    "speakers": [],
    "active_speakers": [],
    "input_device": None,
    "inputs": [],
    "volume": 50,
    "error": None,
}

stream_tasks = {}  # device_id -> asyncio.Task
stream_atvs = {}   # device_id -> active atv connection
stream_lock = threading.Lock()

# Single shared event loop running in background thread
loop = asyncio.new_event_loop()

def run_loop():
    loop.run_forever()

loop_thread = threading.Thread(target=run_loop, daemon=True)
loop_thread.start()


def run_async(coro, timeout=30):
    """Run a coroutine in the background event loop and wait for result."""
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result(timeout=timeout)


def credentials_path(device_id):
    base = os.path.expanduser("~/.config/pinyl")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, f"{device_id.replace(':', '_')}.creds")


def is_paired(device_id):
    return os.path.exists(credentials_path(device_id))


# ---------------------------------------------------------------------------
# Audio input discovery
# ---------------------------------------------------------------------------

def discover_inputs():
    try:
        result = subprocess.run(["arecord", "-l"], capture_output=True, text=True)
        inputs = []
        for line in result.stdout.splitlines():
            if line.startswith("card "):
                parts = line.split(":")
                card_num = parts[0].replace("card ", "").strip()
                name = parts[1].split("[")[1].split("]")[0].strip() if "[" in parts[1] else parts[1].strip()
                device = f"plughw:{card_num},0"
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
    try:
        devices = await pyatv.scan(loop, timeout=10)
        speakers = []
        for device in devices:
            services = [s.protocol for s in device.services]
            if pyatv.const.Protocol.RAOP in services:
                raop_service = next(
                    s for s in device.services
                    if s.protocol == pyatv.const.Protocol.RAOP
                )
                requires_pairing = (
                    raop_service.pairing == pyatv.const.PairingRequirement.Mandatory
                )
                speakers.append({
                    "id": str(device.identifier),
                    "name": device.name,
                    "address": str(device.address),
                    "paired": is_paired(str(device.identifier)),
                    "requires_pairing": requires_pairing,
                })
        state["speakers"] = speakers
        logger.info(f"Discovered speakers: {speakers}")
    except Exception as e:
        logger.error(f"Error discovering speakers: {e}")
        state["speakers"] = []


def discover_speakers():
    run_async(_discover_airplay())


# ---------------------------------------------------------------------------
# Pairing
# ---------------------------------------------------------------------------

_pairing_handlers = {}


async def _start_pairing(device_id, protocol_str):
    try:
        devices = await pyatv.scan(loop, timeout=10, identifier=device_id)
        if not devices:
            return {"error": f"Device {device_id} not found on network"}

        device = devices[0]
        protocol = (
            pyatv.const.Protocol.RAOP
            if protocol_str == "raop"
            else pyatv.const.Protocol.AirPlay
        )

        pairing = await pyatv.pair(device, protocol, loop)
        await pairing.begin()

        _pairing_handlers[device_id] = pairing
        logger.info(f"Pairing started for {device.name} via {protocol_str}")
        return {"status": "awaiting_pin", "device_name": device.name}

    except Exception as e:
        logger.error(f"Pairing start error: {e}")
        return {"error": str(e)}


async def _finish_pairing(device_id, pin):
    try:
        pairing = _pairing_handlers.get(device_id)
        if not pairing:
            return {"error": "No active pairing session — please start pairing again"}

        pairing.pin(int(pin))
        await pairing.finish()

        if pairing.has_paired:
            # Persist credentials
            creds = pairing.service.credentials
            with open(credentials_path(device_id), "w") as f:
                f.write(str(creds))

            await pairing.close()
            del _pairing_handlers[device_id]

            # Update in-memory speaker list
            for sp in state["speakers"]:
                if sp["id"] == device_id:
                    sp["paired"] = True

            logger.info(f"Pairing successful for {device_id}")
            return {"status": "paired"}
        else:
            await pairing.close()
            return {"error": "Pairing failed — check the PIN and try again"}

    except Exception as e:
        logger.error(f"Pairing finish error: {e}")
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------

async def _stream_to_speaker(device_id, input_device):
    atv = None
    process = None
    reader = None
    try:
        devices = await pyatv.scan(loop, timeout=10, identifier=device_id)
        if not devices:
            raise Exception(f"Speaker {device_id} not found")

        device = devices[0]

        creds_file = credentials_path(device_id)
        if os.path.exists(creds_file):
            with open(creds_file) as f:
                creds = f.read().strip()
            for service in device.services:
                if service.protocol == pyatv.const.Protocol.RAOP:
                    service.credentials = creds

        atv = await pyatv.connect(device, loop)
        stream_atvs[device_id] = atv
        logger.info(f"Connected to {device.name}")

        try:
            state["volume"] = int(atv.audio.volume)
        except Exception:
            pass

        # OS pipe → pyatv gets a plain blocking BufferedReader (BufferedIOBaseWrapper path),
        # not asyncio StreamReader, avoiding the run_coroutine_threadsafe deadlock.
        # WAV: always built into ffmpeg, no external codec needed, header written immediately.
        read_fd, write_fd = os.pipe()
        process = subprocess.Popen(
            ["ffmpeg", "-y",
            "-f", "alsa", "-ac", "2", "-ar", "48000",
            "-i", input_device,
            "-af", "volume=4.0",
            "-ar", "48000",
            "-ac", "2",
            "-f", "flac",
            "-compression_level", "0",
            "-"],
            stdout=write_fd,
            stderr=subprocess.PIPE,
        )
        os.close(write_fd)
        reader = os.fdopen(read_fd, "rb")

        # Drain ffmpeg stderr in background; log at WARNING so errors are always visible.
        def _log_ffmpeg():
            for line in process.stderr:
                logger.warning("ffmpeg: %s", line.decode(errors="replace").rstrip())
        threading.Thread(target=_log_ffmpeg, daemon=True).start()

        # Give ffmpeg a moment and fail fast if it exits immediately (bad device / codec).
        await asyncio.sleep(0.3)
        if process.poll() is not None:
            raise Exception(f"ffmpeg exited immediately (code {process.returncode}) — see ffmpeg output above")

        await atv.stream.stream_file(reader)

    except asyncio.CancelledError:
        logger.info(f"Stream cancelled for {device_id}")
    except Exception as e:
        logger.error(f"Stream error for {device_id}: {e}")
        state["error"] = str(e)
    finally:
        if process:
            try:
                process.terminate()
                process.wait(timeout=3)
            except Exception:
                pass
        if reader:
            try:
                reader.close()
            except Exception:
                pass
        if atv:
            try:
                await atv.close()
            except Exception:
                pass
        stream_tasks.pop(device_id, None)
        stream_atvs.pop(device_id, None)


async def _stop_all_streams():
    tasks = list(stream_tasks.values())
    stream_tasks.clear()
    for task in tasks:
        if not task.done():
            task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def start_stream(input_device, speaker_ids, volume):
    with stream_lock:
        run_async(_stop_all_streams(), timeout=10)

    state["error"] = None
    state["streaming"] = True
    state["active_speakers"] = list(speaker_ids)
    state["volume"] = volume

    async def _launch():
        for device_id in speaker_ids:
            task = asyncio.ensure_future(_stream_to_speaker(device_id, input_device))
            stream_tasks[device_id] = task

    run_async(_launch(), timeout=5)
    logger.info(f"Stream started → {speaker_ids}")


def _stop_stream():
    run_async(_stop_all_streams(), timeout=10)
    state["streaming"] = False
    state["active_speakers"] = []
    logger.info("Stream stopped")


# ---------------------------------------------------------------------------
# Routes — Web UI
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    discover_inputs()
    return render_template("index.html", state=state)


# ---------------------------------------------------------------------------
# Routes — REST API
# ---------------------------------------------------------------------------

@app.route("/api/status", methods=["GET"])
def api_status():
    for atv in stream_atvs.values():
        try:
            state["volume"] = int(atv.audio.volume)
        except Exception:
            pass
        break
    return jsonify({
        "streaming": state["streaming"],
        "active_speakers": state["active_speakers"],
        "input_device": state["input_device"],
        "volume": state["volume"],
        "error": state["error"],
        "speakers": state["speakers"],
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


@app.route("/api/pair/start", methods=["POST"])
def api_pair_start():
    data = request.json or {}
    device_id = data.get("device_id")
    protocol = data.get("protocol", "raop")
    if not device_id:
        return jsonify({"error": "device_id required"}), 400
    result = run_async(_start_pairing(device_id, protocol))
    return jsonify(result)


@app.route("/api/pair/finish", methods=["POST"])
def api_pair_finish():
    data = request.json or {}
    device_id = data.get("device_id")
    pin = data.get("pin")
    if not device_id or pin is None:
        return jsonify({"error": "device_id and pin required"}), 400
    result = run_async(_finish_pairing(device_id, pin))
    return jsonify(result)


@app.route("/api/stream/start", methods=["POST"])
def api_stream_start():
    data = request.json or {}
    input_device = data.get("input_device", state["input_device"])
    speaker_ids = data.get("speakers", [])
    volume = int(data.get("volume", state["volume"]))

    if not input_device:
        return jsonify({"error": "No input device selected"}), 400
    if not speaker_ids:
        return jsonify({"error": "No speakers selected"}), 400

    t = threading.Thread(target=start_stream, args=(input_device, speaker_ids, volume))
    t.start()

    return jsonify({"streaming": True})


@app.route("/api/stream/stop", methods=["POST"])
def api_stream_stop():
    _stop_stream()
    return jsonify({"streaming": False})


@app.route("/api/volume", methods=["POST"])
def api_volume():
    data = request.json or {}
    volume = int(data.get("volume", 50))
    state["volume"] = volume

    async def _apply():
        for atv in list(stream_atvs.values()):
            try:
                await atv.audio.set_volume(volume)
            except Exception:
                pass

    if stream_atvs:
        run_async(_apply(), timeout=5)

    return jsonify({"volume": volume})


# Home Assistant compatibility
@app.route("/on", methods=["POST"])
def ha_on():
    if state["input_device"] and state["active_speakers"]:
        t = threading.Thread(
            target=start_stream,
            args=(state["input_device"], state["active_speakers"], state["volume"])
        )
        t.start()
    return jsonify({"state": "on" if state["streaming"] else "off"})


@app.route("/off", methods=["POST"])
def ha_off():
    with stream_lock:
        _stop_stream()
    return jsonify({"state": "off"})


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    os.makedirs(os.path.expanduser("~/.config/pinyl"), exist_ok=True)
    logger.info("Starting Pinyl...")
    discover_inputs()
    t = threading.Thread(target=discover_speakers)
    t.start()
    app.run(host="0.0.0.0", port=5050, debug=False)
