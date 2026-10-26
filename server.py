#!/usr/bin/env python3
"""Pip-Boy interface for the machine it is running on.

Serves the phosphor UI and reads the local system. The file browser
stays inside the home directory and never writes. GPS and FM are
reported from whatever is actually plugged in.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import re
import threading
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
WBFM = ROOT / "deploy" / "wbfm"
DATA_DIR = Path.home() / ".local" / "share" / "pipboy"
QUESTS_PATH = DATA_DIR / "quests.json"
QUEST_TEXT = 400
HOST = socket.gethostname().split(".")[0].upper()

SDR_IDS = {
    ("0bda", "2832"),
    ("0bda", "2838"),
    ("1d50", "6089"),
    ("1d50", "60a1"),
    ("1df7", "2500"),
    ("1df7", "3000"),
}
GPS_IDS = {
    ("1546", "01a7"),
    ("1546", "01a8"),
    ("1546", "01a9"),
}

TEXT_EXT = {".txt", ".md", ".log", ".csv", ".json", ".py", ".sh", ".conf", ".cfg", ".toml", ".yml", ".yaml", ".ini", ".service"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
AUDIO_EXT = {".wav", ".mp3", ".ogg", ".flac", ".m4a"}
MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ttf": "font/ttf",
    ".txt": "text/plain; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}


def clamp(value: float, lo: float = 0, hi: float = 100) -> float:
    return max(lo, min(hi, value))


def fmt_bytes(n: float) -> str:
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}TB"


def fmt_uptime(seconds: float) -> str:
    remaining = int(seconds)
    days, remaining = divmod(remaining, 86400)
    hours, remaining = divmod(remaining, 3600)
    minutes, _ = divmod(remaining, 60)
    if days:
        return f"{days}d {hours:02d}h"
    return f"{hours:02d}h {minutes:02d}m"


def read_cpu_times() -> tuple[int, int]:
    parts = Path("/proc/stat").read_text().splitlines()[0].split()[1:]
    nums = [int(part) for part in parts]
    idle = nums[3] + (nums[4] if len(nums) > 4 else 0)
    return sum(nums), idle


def read_net_bytes() -> tuple[int, int]:
    rx = tx = 0
    for line in Path("/proc/net/dev").read_text().splitlines()[2:]:
        name, rest = line.split(":", 1)
        if name.strip() == "lo":
            continue
        cols = rest.split()
        rx += int(cols[0])
        tx += int(cols[8])
    return rx, tx


def has_default_route() -> bool:
    try:
        lines = Path("/proc/net/route").read_text().splitlines()[1:]
    except OSError:
        return False
    return any(line.split()[1] == "00000000" for line in lines if len(line.split()) > 1)


def cpu_freq() -> tuple[float | None, float | None]:
    base = Path("/sys/devices/system/cpu/cpu0/cpufreq")
    current = base / "scaling_cur_freq"
    maximum = base / "cpuinfo_max_freq"
    if not maximum.exists():
        maximum = base / "scaling_max_freq"

    def khz(path: Path) -> float | None:
        try:
            return int(path.read_text().strip()) / 1000
        except (OSError, ValueError):
            return None

    return khz(current), khz(maximum)


def cpu_temp() -> float | None:
    reading = vcgencmd("measure_temp")
    if reading and "temp=" in reading:
        try:
            return float(reading.split("=", 1)[1].split("'")[0])
        except ValueError:
            pass
    hottest = None
    thermal = Path("/sys/class/thermal")
    if not thermal.exists():
        return None
    for zone in thermal.glob("thermal_zone*/temp"):
        try:
            value = int(zone.read_text().strip()) / 1000
        except (OSError, ValueError):
            continue
        hottest = value if hottest is None else max(hottest, value)
    return hottest


def vcgencmd(*args: str) -> str | None:
    if shutil.which("vcgencmd") is None:
        return None
    try:
        return subprocess.check_output(
            ["vcgencmd", *args],
            text=True,
            timeout=0.6,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def throttle_word() -> int:
    reading = vcgencmd("get_throttled")
    if not reading or "=" not in reading:
        return 0
    try:
        return int(reading.split("=", 1)[1], 16)
    except ValueError:
        return 0


def usb_devices() -> list[dict]:
    root = Path("/sys/bus/usb/devices")
    found = []
    if not root.exists():
        return found
    for device in root.iterdir():
        vendor = device / "idVendor"
        product = device / "idProduct"
        if not vendor.exists() or not product.exists():
            continue
        name = ""
        product_name = device / "product"
        if product_name.exists():
            try:
                name = product_name.read_text().strip()
            except OSError:
                name = ""
        found.append(
            {
                "vendor": vendor.read_text().strip().lower(),
                "product": product.read_text().strip().lower(),
                "name": name,
            }
        )
    return found


def classify(device: dict) -> str | None:
    ident = (device["vendor"], device["product"])
    label = device["name"].lower()
    if ident in SDR_IDS or any(word in label for word in ("rtl283", "rtlsdr", "rtl-sdr", "hackrf", "airspy", "sdrplay")):
        return "sdr"
    if ident in GPS_IDS or any(word in label for word in ("gps", "u-blox", "ublox", "gnss")):
        return "gps"
    return None


def first_of(kind: str) -> dict | None:
    for device in usb_devices():
        if classify(device) == kind:
            return device
    return None


def meminfo() -> dict[str, int]:
    info = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, rest = line.split(":", 1)
        info[key] = int(rest.strip().split()[0]) * 1024
    return info


def logged_in_users() -> int:
    try:
        out = subprocess.check_output(["who"], text=True, timeout=0.6)
    except (OSError, subprocess.SubprocessError):
        return 0
    names = {line.split()[0] for line in out.splitlines() if line.strip()}
    return len(names)


def build_status(busy: float, rx: float, tx: float) -> dict:
    memory = meminfo()
    total_mem = memory.get("MemTotal", 0)
    avail_mem = memory.get("MemAvailable", memory.get("MemFree", 0))
    disk = shutil.disk_usage("/")
    temp = cpu_temp()
    cores = os.cpu_count() or 1
    load1 = os.getloadavg()[0]
    uptime = float(Path("/proc/uptime").read_text().split()[0])
    current_mhz, max_mhz = cpu_freq()
    word = throttle_word()
    current_flags = word & 0xF
    history_flags = (word >> 16) & 0xF
    route = has_default_route()

    cpu_health = clamp(100 - busy)
    temp_health = 100.0 if temp is None else clamp((85 - temp) / 45 * 100)
    mem_health = 100.0 if not total_mem else clamp(avail_mem / total_mem * 100)
    disk_health = 100.0 if not disk.total else clamp(disk.free / disk.total * 100)
    load_health = clamp(100 - (load1 / cores) * 50)
    net_health = 100.0 if route else 25.0
    rate = rx + tx

    limbs = [
        {"id": "head", "part": "HEAD", "name": "CPU", "health": round(cpu_health), "readout": f"{busy:.0f}% BUSY"},
        {"id": "torso", "part": "TORSO", "name": "TEMP", "health": round(temp_health), "readout": "—" if temp is None else f"{temp:.0f}°C"},
        {"id": "larm", "part": "L.ARM", "name": "MEM", "health": round(mem_health), "readout": f"{fmt_bytes(total_mem - avail_mem)}/{fmt_bytes(total_mem)}"},
        {"id": "rarm", "part": "R.ARM", "name": "DISK", "health": round(disk_health), "readout": f"{fmt_bytes(disk.free)} FREE"},
        {"id": "lleg", "part": "L.LEG", "name": "NET", "health": round(net_health), "readout": fmt_bytes(rate) + "/s" if route else "NO LINK"},
        {"id": "rleg", "part": "R.LEG", "name": "LOAD", "health": round(load_health), "readout": f"{load1:.2f}"},
    ]
    hp = round(
        0.30 * cpu_health
        + 0.25 * temp_health
        + 0.20 * mem_health
        + 0.15 * disk_health
        + 0.10 * load_health
    )
    if disk_health < 10:
        hp = min(hp, 40)
    if temp_health < 10:
        hp = min(hp, 35)

    effects = []
    if current_flags & 0x1:
        effects.append("UNDERVOLTED")
    if current_flags & 0x2:
        effects.append("FREQUENCY CAPPED")
    if current_flags & 0x4:
        effects.append("THERMAL THROTTLE")
    if current_flags & 0x8:
        effects.append("SOFT TEMP LIMIT")
    if disk_health < 20:
        effects.append("DISK WEARY")
    if mem_health < 20:
        effects.append("MEMORY STRAINED")
    if load_health < 25:
        effects.append("HEAVY LOAD")
    if temp_health < 25:
        effects.append("RUNNING HOT")
    if not route:
        effects.append("NO CARRIER")
    if not effects:
        effects.append("OLD SCARS · OTHERWISE NOMINAL" if history_flags else "NOMINAL")

    if current_flags & 0x1:
        rad_level, rad_label = 8, "UNDERVOLTED"
    elif current_flags & 0x4:
        rad_level, rad_label = 6, "THROTTLED"
    elif current_flags & 0x2:
        rad_level, rad_label = 4, "FREQ CAPPED"
    elif current_flags & 0x8:
        rad_level, rad_label = 3, "TEMP LIMIT"
    elif history_flags:
        rad_level, rad_label = 1, "TRACE RESIDUE"
    else:
        rad_level, rad_label = 0, "THROTTLE CLEAR"

    ram_gb = total_mem / (1024 ** 3)
    if cores >= 8:
        strength = 10
    elif cores >= 6:
        strength = 9
    elif cores >= 4:
        strength = 8
    elif cores >= 2:
        strength = 6
    else:
        strength = 4
    if ram_gb >= 16:
        intelligence = 10
    elif ram_gb >= 8:
        intelligence = 9
    elif ram_gb >= 4:
        intelligence = 8
    elif ram_gb >= 2:
        intelligence = 6
    elif ram_gb >= 1:
        intelligence = 5
    else:
        intelligence = 3
    hours = uptime / 3600
    if hours >= 720:
        endurance = 10
    elif hours >= 168:
        endurance = 9
    elif hours >= 24:
        endurance = 7
    elif hours >= 1:
        endurance = 5
    else:
        endurance = 3
    users = logged_in_users()
    charisma = {0: 2, 1: 5, 2: 7, 3: 8}.get(users, 10)
    agility = 5 if not max_mhz else max(1, min(10, round(max_mhz / 200)))
    if current_flags & 0x1:
        luck = 2
    elif current_flags:
        luck = 4
    elif history_flags:
        luck = 7
    else:
        luck = 10
    perception = 7 if route else 2

    special = [
        {"key": "S", "name": "STRENGTH", "trait": f"{cores} CORES", "score": strength},
        {"key": "P", "name": "PERCEPTION", "trait": "LINK UP" if route else "NO LINK", "score": perception},
        {"key": "E", "name": "ENDURANCE", "trait": fmt_uptime(uptime), "score": endurance},
        {"key": "C", "name": "CHARISMA", "trait": f"{users} SESSION" if users == 1 else f"{users} SESSIONS", "score": charisma},
        {"key": "I", "name": "INTELLIGENCE", "trait": fmt_bytes(total_mem), "score": intelligence},
        {"key": "A", "name": "AGILITY", "trait": "—" if not max_mhz else f"{max_mhz / 1000:.1f} GHZ", "score": agility},
        {"key": "L", "name": "LUCK", "trait": "CLEAN" if luck == 10 else "SCARRED", "score": luck},
    ]
    return {
        "host": HOST,
        "hp": hp,
        "uptime": fmt_uptime(uptime),
        "temp": None if temp is None else round(temp),
        "clock": None if current_mhz is None else f"{current_mhz / 1000:.2f} GHz",
        "limbs": limbs,
        "rad": {"level": rad_level, "label": rad_label},
        "effects": effects,
        "special": special,
    }


class Sampler(threading.Thread):
    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.lock = threading.Lock()
        self.ready = threading.Event()
        self.cpu = read_cpu_times()
        self.net = read_net_bytes()
        self.when = time.monotonic()
        self.snap = build_status(0, 0, 0)

    def run(self) -> None:
        time.sleep(0.6)
        self._once()
        self.ready.set()
        while True:
            time.sleep(1)
            self._once()

    def _once(self) -> None:
        now = time.monotonic()
        cpu = read_cpu_times()
        net = read_net_bytes()
        total = cpu[0] - self.cpu[0]
        idle = cpu[1] - self.cpu[1]
        busy = 0 if total <= 0 else (1 - idle / total) * 100
        dt = max(now - self.when, 0.001)
        rx = max(0, net[0] - self.net[0]) / dt
        tx = max(0, net[1] - self.net[1]) / dt
        self.cpu = cpu
        self.net = net
        self.when = now
        snap = build_status(busy, rx, tx)
        with self.lock:
            self.snap = snap

    def status(self) -> dict:
        with self.lock:
            return json.loads(json.dumps(self.snap))


def safe_under(root: Path, rel: str) -> Path:
    cleaned = rel.strip().lstrip("/")
    parts = Path(cleaned).parts if cleaned else ()
    if any(part in ("", ".", "..") or part.startswith(".") for part in parts):
        raise PermissionError(rel)
    path = (root / cleaned).resolve() if cleaned else root.resolve()
    if not path.is_relative_to(root.resolve()):
        raise PermissionError(rel)
    return path


def file_kind(path: Path, is_dir: bool) -> str:
    if is_dir:
        return "DIR"
    ext = path.suffix.lower()
    if ext in TEXT_EXT or ext == ".pdf":
        return "NOTES"
    if ext in IMAGE_EXT:
        return "PICS"
    if ext in AUDIO_EXT:
        return "HOLO"
    return "OTHER"


def list_dir(root: Path, rel: str) -> dict:
    path = safe_under(root, rel)
    if not path.is_dir():
        raise FileNotFoundError(rel)
    entries = []
    for item in path.iterdir():
        if item.name.startswith("."):
            continue
        try:
            stat = item.stat()
        except OSError:
            continue
        entries.append(
            {
                "name": item.name,
                "dir": item.is_dir(),
                "kind": file_kind(item, item.is_dir()),
                "size": 0 if item.is_dir() else stat.st_size,
                "mtime": time.strftime("%d %b %Y", time.localtime(stat.st_mtime)).upper(),
            }
        )
    entries.sort(key=lambda entry: (not entry["dir"], entry["name"].casefold()))
    truncated = len(entries) > 300
    parent = str(Path(rel).parent) if rel.strip("/") else None
    if parent == ".":
        parent = ""
    return {
        "path": rel.strip("/"),
        "parent": parent,
        "truncated": truncated,
        "entries": entries[:300],
    }


def read_text(root: Path, rel: str) -> dict:
    path = safe_under(root, rel)
    if not path.is_file():
        raise FileNotFoundError(rel)
    if path.suffix.lower() not in TEXT_EXT:
        raise PermissionError(rel)
    data = path.read_bytes()[:8000]
    return {"path": rel.strip("/"), "text": data.decode("utf-8", "replace"), "truncated": path.stat().st_size > 8000}


def load_quests() -> list[dict]:
    try:
        return json.loads(QUESTS_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return []


def save_quests(quests: list[dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    QUESTS_PATH.write_text(json.dumps(quests, indent=2))


def mutate_quests(body: dict) -> list[dict]:
    quests = load_quests()
    op = body.get("op")
    if op == "add":
        name = str(body.get("name", "")).strip()
        if not name or len(name) > QUEST_TEXT:
            raise ValueError("name")
        quests.insert(0, {"id": uuid.uuid4().hex[:8], "name": name, "done": False, "objectives": []})
    elif op == "toggle":
        target = str(body.get("id", ""))
        if not any(quest["id"] == target for quest in quests):
            raise ValueError("id")
        for quest in quests:
            if quest["id"] == target:
                quest["done"] = not quest["done"]
    elif op == "remove":
        target = str(body.get("id", ""))
        match = next((quest for quest in quests if quest["id"] == target), None)
        if match is None:
            raise ValueError("id")
        if not match.get("done"):
            raise ValueError("active")
        quests = [quest for quest in quests if quest["id"] != target]
    elif op == "objective":
        target = str(body.get("id", ""))
        text = str(body.get("text", "")).strip()
        if not text or len(text) > QUEST_TEXT:
            raise ValueError("text")
        found = False
        for quest in quests:
            if quest["id"] == target:
                quest.setdefault("objectives", []).append({"text": text, "done": False})
                found = True
        if not found:
            raise ValueError("id")
    else:
        raise ValueError("op")
    save_quests(quests)
    return quests


def empty_gps() -> dict:
    device = first_of("gps")
    return {
        "receiver": device is not None,
        "device": None if device is None else device["name"] or f"{device['vendor']}:{device['product']}",
        "gpsd": False,
        "mode": 0,
        "lat": None,
        "lon": None,
        "alt_m": None,
        "speed_mps": None,
        "sats": None,
    }


def nmea_ok(line: str) -> bool:
    if not line.startswith("$") or "*" not in line:
        return False
    body, checksum = line[1:].split("*", 1)
    if len(checksum) != 2:
        return False
    try:
        expect = int(checksum, 16)
    except ValueError:
        return False
    got = 0
    for char in body:
        got ^= ord(char)
    return got == expect


def nmea_deg(field: str, hemi: str) -> float | None:
    if not field or "." not in field or hemi not in ("N", "S", "E", "W"):
        return None
    split = field.find(".")
    if split < 2:
        return None
    try:
        degrees = float(field[: split - 2])
        minutes = float(field[split - 2 :])
    except ValueError:
        return None
    value = degrees + minutes / 60
    return -value if hemi in ("S", "W") else value


def gps_port() -> str | None:
    by_id = Path("/dev/serial/by-id")
    if by_id.exists():
        for entry in sorted(by_id.iterdir()):
            label = entry.name.lower()
            if "u-blox" in label or "gps" in label or "gnss" in label:
                return str(entry)
    acm = Path("/dev/ttyACM0")
    return str(acm) if acm.exists() else None


class NmeaReader(threading.Thread):
    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.lock = threading.Lock()
        self.snap = empty_gps()

    def snapshot(self) -> dict:
        with self.lock:
            return dict(self.snap)

    def read(self) -> dict:
        snap = self.snapshot()
        if snap.get("receiver") or snap.get("mode", 0) >= 2:
            return snap
        probed = probe_gps()
        return probed if probed.get("gpsd") else snap

    def run(self) -> None:
        while True:
            port = gps_port()
            if not port:
                with self.lock:
                    self.snap = empty_gps()
                time.sleep(2)
                continue
            try:
                self._follow(port)
            except OSError:
                time.sleep(2)

    def _follow(self, port: str) -> None:
        base = empty_gps()
        base["receiver"] = True
        if not base["device"]:
            base["device"] = "u-blox"
        with self.lock:
            self.snap = base
        with open(port, "rb", buffering=0) as handle:
            buf = b""
            while True:
                chunk = handle.read(512)
                if not chunk:
                    raise OSError("eof")
                buf += chunk
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    self._consume(raw.decode("ascii", "ignore").strip())
                if len(buf) > 8192:
                    buf = buf[-1024:]

    def _consume(self, line: str) -> None:
        if not nmea_ok(line):
            return
        fields = line[1:].split("*", 1)[0].split(",")
        kind = fields[0][-3:]
        if kind == "GGA" and len(fields) > 9:
            lat = nmea_deg(fields[2], fields[3])
            lon = nmea_deg(fields[4], fields[5])
            try:
                quality = int(fields[6] or "0")
                sats = int(fields[7]) if fields[7] else None
                alt = float(fields[9]) if fields[9] else None
            except ValueError:
                return
            with self.lock:
                self.snap["receiver"] = True
                self.snap["sats"] = sats
                if quality >= 1 and lat is not None and lon is not None:
                    self.snap["mode"] = 3 if alt is not None else 2
                    self.snap["lat"] = lat
                    self.snap["lon"] = lon
                    self.snap["alt_m"] = alt
                else:
                    self.snap["mode"] = 1
        elif kind == "RMC" and len(fields) > 7 and fields[2] == "A":
            try:
                knots = float(fields[7]) if fields[7] else 0.0
            except ValueError:
                return
            with self.lock:
                self.snap["speed_mps"] = knots * 0.514444


def probe_gps() -> dict:
    device = first_of("gps")
    base = {
        "receiver": device is not None,
        "device": None if device is None else device["name"] or f"{device['vendor']}:{device['product']}",
        "gpsd": False,
        "mode": 0,
        "lat": None,
        "lon": None,
        "alt_m": None,
        "speed_mps": None,
        "sats": None,
    }
    try:
        sock = socket.create_connection(("127.0.0.1", 2947), 0.3)
    except OSError:
        return base
    base["gpsd"] = True
    sock.settimeout(0.25)
    try:
        sock.sendall(b'?WATCH={"enable":true,"json":true}\n')
        buf = b""
        deadline = time.monotonic() + 0.7
        while time.monotonic() < deadline:
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                break
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                try:
                    msg = json.loads(raw.decode("utf-8", "replace"))
                except json.JSONDecodeError:
                    continue
                if msg.get("class") == "TPV":
                    base["mode"] = int(msg.get("mode") or 0)
                    if base["mode"] >= 2:
                        base["lat"] = msg.get("lat")
                        base["lon"] = msg.get("lon")
                        base["speed_mps"] = msg.get("speed")
                    if base["mode"] >= 3:
                        base["alt_m"] = msg.get("alt")
                elif msg.get("class") == "SKY":
                    used = [sat for sat in msg.get("satellites") or [] if sat.get("used")]
                    base["sats"] = len(used)
    finally:
        sock.close()
    return base


def usb_playback() -> list[str]:
    command = ["aplay", "-q", "-f", "S16_LE", "-r", "48000", "-c", "1", "-t", "raw"]
    try:
        cards = Path("/proc/asound/cards").read_text()
    except OSError:
        return command
    for line in cards.splitlines():
        if "USB-Audio" not in line or "Sound Blaster" not in line:
            continue
        start = line.find("[")
        end = line.find("]", start + 1)
        if start < 0 or end < 0:
            continue
        card = line[start + 1:end].strip()
        command[2:2] = ["-D", f"plughw:CARD={card},DEV=0"]
        break
    return command


def kernel_sdr() -> bool:
    return os.access("/dev/swradio0", os.R_OK) and os.access(WBFM, os.X_OK)


class Radio:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.freq = 98.5
        self.fm: subprocess.Popen | None = None
        self.play: subprocess.Popen | None = None
        self.error: str | None = None

    def state(self) -> dict:
        device = first_of("sdr")
        with self.lock:
            playing = self._alive()
            return {
                "present": device is not None,
                "device": None if device is None else device["name"] or f"{device['vendor']}:{device['product']}",
                "decoder": kernel_sdr() or shutil.which("rtl_fm") is not None,
                "audio": shutil.which("aplay") is not None,
                "freq": self.freq,
                "playing": playing,
                "error": self.error,
            }

    def _alive(self) -> bool:
        return self.fm is not None and self.fm.poll() is None and self.play is not None and self.play.poll() is None

    def _stop(self) -> None:
        for proc in (self.play, self.fm):
            if proc and proc.poll() is None:
                proc.terminate()
        for proc in (self.play, self.fm):
            if proc and proc.poll() is None:
                try:
                    proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    proc.kill()
        self.fm = None
        self.play = None

    def command(self, freq: float, play: bool) -> dict:
        if not 87.5 <= freq <= 108.0:
            raise ValueError("freq")
        with self.lock:
            self.freq = round(freq, 1)
            self._stop()
            self.error = None
            if play:
                missing = []
                if first_of("sdr") is None and not os.access("/dev/swradio0", os.R_OK):
                    missing.append("no receiver")
                if os.access("/dev/swradio0", os.R_OK):
                    if not os.access(WBFM, os.X_OK):
                        missing.append("fm decoder is not built")
                elif shutil.which("rtl_fm") is None:
                    missing.append("rtl_fm is not installed")
                if shutil.which("aplay") is None:
                    missing.append("aplay is not installed")
                if missing:
                    self.error = ", ".join(missing)
                else:
                    if kernel_sdr():
                        tune = [str(WBFM), f"{self.freq:.1f}"]
                    else:
                        tune = ["rtl_fm", "-f", f"{self.freq:.1f}M", "-M", "wbfm", "-s", "200000", "-r", "48000", "-E", "deemp"]
                    self.fm = subprocess.Popen(
                        tune,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                    )
                    self.play = subprocess.Popen(
                        usb_playback(),
                        stdin=self.fm.stdout,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    if self.fm.stdout:
                        self.fm.stdout.close()
                    time.sleep(0.4)
                    if not self._alive():
                        self.error = "receiver did not start"
                        self._stop()
            return self.state()


TILE_ROOT = Path.home() / ".cache" / "pipboy" / "tiles"
TILE_UPSTREAM = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"


def tile_bytes(z: int, x: int, y: int) -> bytes:
    if not 0 <= z <= 19:
        raise ValueError("zoom")
    limit = 2 ** z
    if not 0 <= x < limit or not 0 <= y < limit:
        raise ValueError("tile")
    path = TILE_ROOT / str(z) / str(x) / f"{y}.png"
    if path.exists() and path.stat().st_size > 64:
        return path.read_bytes()
    request = urllib.request.Request(
        TILE_UPSTREAM.format(z=z, x=x, y=y),
        headers={"User-Agent": "PIP-OS/0.1 (personal pip-boy)"},
    )
    with urllib.request.urlopen(request, timeout=8) as response:
        data = response.read()
    if not data.startswith(b"\x89PNG"):
        raise ValueError("tile")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    tmp.write_bytes(data)
    tmp.replace(path)
    return data


def apply_gps(snap: dict, gps: dict) -> None:
    for trait in snap.get("special", []):
        if trait.get("key") != "P":
            continue
        if gps.get("mode", 0) >= 3 and gps.get("sats"):
            trait["score"] = 10
            trait["trait"] = f"{gps['sats']} SATS"
        elif gps.get("mode", 0) >= 2:
            trait["score"] = 9
            trait["trait"] = "GPS FIX"


SAMPLER = Sampler()
GPS = NmeaReader()
RADIO = Radio()
FILE_ROOT = Path.home()


class Handler(BaseHTTPRequestHandler):
    server_version = "PIP-OS/0.1"

    def log_message(self, fmt: str, *args) -> None:
        print(f"[pip] {self.address_string()} {fmt % args}")

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        query = urllib.parse.parse_qs(parsed.query)
        try:
            if path == "/api/status":
                snap = SAMPLER.status()
                apply_gps(snap, GPS.snapshot())
                self._json(snap)
            elif (tile := re.fullmatch(r"/tiles/(\d+)/(\d+)/(\d+)\.png", path)):
                self._tile(int(tile.group(1)), int(tile.group(2)), int(tile.group(3)))
            elif path == "/api/files":
                self._json(list_dir(FILE_ROOT, query.get("path", [""])[0]))
            elif path == "/api/files/text":
                self._json(read_text(FILE_ROOT, query.get("path", [""])[0]))
            elif path == "/api/files/image":
                self._image(query.get("path", [""])[0])
            elif path == "/api/quests":
                self._json({"quests": load_quests()})
            elif path == "/api/gps":
                self._json(GPS.read())
            elif path == "/api/radio":
                self._json(RADIO.state())
            else:
                self._static(path)
        except PermissionError:
            self._json({"error": "blocked"}, 403)
        except FileNotFoundError:
            self._json({"error": "missing"}, 404)
        except (OSError, ValueError) as exc:
            self._json({"error": str(exc)}, 400)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        try:
            body = self._body()
            if parsed.path == "/api/quests":
                self._json({"quests": mutate_quests(body)})
            elif parsed.path == "/api/radio":
                freq = float(body.get("freq", RADIO.freq))
                play = bool(body.get("play"))
                self._json(RADIO.command(freq, play))
            else:
                self._json({"error": "missing"}, 404)
        except ValueError:
            self._json({"error": "bad request"}, 400)
        except (OSError, TypeError):
            self._json({"error": "bad request"}, 400)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 65536:
            raise ValueError("too large")
        raw = self.rfile.read(length) if length else b"{}"
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("object")
        return data

    def _json(self, payload: dict, code: int = 200) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(raw)

    def _tile(self, z: int, x: int, y: int) -> None:
        try:
            data = tile_bytes(z, x, y)
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            self.send_response(502)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        self.wfile.write(data)

    def _image(self, rel: str) -> None:
        path = safe_under(FILE_ROOT, rel)
        if not path.is_file() or path.suffix.lower() not in IMAGE_EXT:
            raise PermissionError(rel)
        if path.stat().st_size > 12_000_000:
            raise ValueError("too large")
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", MIME[path.suffix.lower()])
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _static(self, path: str) -> None:
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        file = safe_under(STATIC, rel)
        if not file.is_file():
            raise FileNotFoundError(rel)
        data = file.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(file.suffix.lower(), "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        if file.suffix.lower() in {".html", ".css", ".js"}:
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    global FILE_ROOT
    parser = argparse.ArgumentParser(description="Pip-Boy interface")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--root", default=str(Path.home()))
    args = parser.parse_args()
    FILE_ROOT = Path(args.root).expanduser().resolve()
    if not FILE_ROOT.is_dir():
        raise SystemExit(f"not a directory: {FILE_ROOT}")
    SAMPLER.start()
    GPS.start()
    SAMPLER.ready.wait(timeout=3)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"PIP-OS on http://{args.host}:{args.port}  files: {FILE_ROOT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        RADIO.command(RADIO.freq, False)
        server.server_close()


if __name__ == "__main__":
    main()
