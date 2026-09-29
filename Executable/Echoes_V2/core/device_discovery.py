"""
Device discovery for the Echoes editor.

Finds the Echoes hardware on the local network. The device advertises itself
via mDNS as "echoes.local" and serves an HTTP portal on port 80 with a JSON
status endpoint at  GET /api/status
(see Firmware/echoes_of_termina/src/services/wifi_portal.h).

Discovery strategy (first success wins):
  1. Probe the well-known hostname  (echoes.local)  — fastest path.
  2. Probe the last known IP, cached after a previous successful find.
  3. Fall back to a concurrent scan of the local /24 subnet for HTTP servers
     that answer /api/status with a recognizable device payload.

Everything runs in the background (DeviceMonitor QThread); no firmware changes
and no third-party dependencies are required.
"""
from __future__ import annotations

import concurrent.futures
import json
import socket
import urllib.error
import urllib.request
from typing import Optional

from PySide6.QtCore import QThread, Signal

DEFAULT_HOSTNAME = "echoes.local"
DEFAULT_PORT     = 80
STATUS_PATH      = "/api/status"

PROBE_TIMEOUT    = 2.0    # seconds per /api/status HTTP probe
SCAN_CONNECT_MS  = 300    # per-host TCP connect timeout during subnet scan
SCAN_MAX_WORKERS = 40
SCAN_HOST_LIMIT  = 254    # .1 .. .254 of the local /24


# ── Probing ───────────────────────────────────────────────────────────────────

def probe_device(host: str, port: int = DEFAULT_PORT,
                 timeout: float = PROBE_TIMEOUT) -> Optional[dict]:
    """
    GET /api/status on *host* and return the parsed JSON dict, or None when the
    host is unreachable, the endpoint is missing, or the payload does not look
    like an Echoes device (the firmware always reports its "ip").
    """
    url = f"http://{host}:{port}{STATUS_PATH}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            data = json.loads(resp.read().decode("utf-8", "replace"))
            if not isinstance(data, dict) or "ip" not in data:
                return None
            return data
    except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError):
        return None


# ── Subnet scan fallback ──────────────────────────────────────────────────────

def _local_ip() -> Optional[str]:
    """Local IPv4 used to reach the internet (route lookup only, no packets)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def _scan_port_80(prefix: str, timeout_s: float) -> list:
    """Return the /24 addresses under *prefix* with port 80 accepting connects."""
    def _open(host: str) -> Optional[str]:
        try:
            with socket.create_connection((host, DEFAULT_PORT), timeout=timeout_s):
                return host
        except OSError:
            return None

    hosts = [f"{prefix}.{i}" for i in range(1, SCAN_HOST_LIMIT + 1)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=SCAN_MAX_WORKERS) as pool:
        futures = [pool.submit(_open, h) for h in hosts]
        return [f.result() for f in concurrent.futures.as_completed(futures)
                if f.result() is not None]


def _scan_subnet() -> list:
    """Scan the local /24 for port-80 hosts. Empty when no network / no hits."""
    base = _local_ip()
    if not base:
        return []
    prefix = ".".join(base.split(".")[:3])
    try:
        return _scan_port_80(prefix, SCAN_CONNECT_MS / 1000.0)
    except Exception:   # noqa: BLE001 — scanning must never crash the monitor
        return []


# ── Discovery ─────────────────────────────────────────────────────────────────

def _make_info(host: str, status: dict, via: str) -> dict:
    return {
        "host":   host,
        "ip":     status.get("ip") or host,
        "status": status,
        "via":    via,
    }


def discover(hostname: str = DEFAULT_HOSTNAME,
             cached_ip: Optional[str] = None,
             do_scan: bool = True) -> Optional[dict]:
    """
    Try the known hostname, the cached IP, then (optionally) a subnet scan.
    Returns an info dict  {host, ip, status, via}  or None when not found.
    """
    candidates: list = [hostname]
    if cached_ip:
        candidates.append(cached_ip)

    for host in candidates:
        status = probe_device(host)
        if status:
            via = "mDNS" if host == hostname else "ip-cache"
            return _make_info(host, status, via)

    if do_scan:
        for ip in _scan_subnet():
            status = probe_device(ip)
            if status:
                return _make_info(ip, status, "subnet")

    return None


# ── Background monitor ────────────────────────────────────────────────────────

class DeviceMonitor(QThread):
    """
    Periodically probes for the Echoes hardware and emits `state_changed` with
    the discovery info dict, or None when the device is unreachable.

    The hostname is probed every tick; a subnet scan only runs after repeated
    misses, so a found device never triggers scans.
    """

    state_changed = Signal(object)   # info dict | None

    def __init__(self, hostname: str = DEFAULT_HOSTNAME,
                 poll_ms: int = 4000, parent=None) -> None:
        super().__init__(parent)
        self._hostname  = hostname
        self._poll_ms   = poll_ms
        self._cached_ip: Optional[str] = None
        self._stopping  = False
        self._tick      = 0

    def stop(self) -> None:
        self._stopping = True
        self.requestInterruption()

    def run(self) -> None:
        while not self._stopping:
            self._tick += 1
            # Tick 1 probes hostname + cached IP and scans right away if those
            # miss, so the user gets a first answer fast. Later scans only run
            # after repeated misses (a found device never triggers scans).
            if self._tick == 1:
                info = discover(self._hostname, self._cached_ip, do_scan=True)
            else:
                do_scan = (self._cached_ip is None and self._tick % 3 == 0)
                info = discover(self._hostname, self._cached_ip, do_scan=do_scan)
            if info:
                self._cached_ip = info["ip"]
            elif self._cached_ip is not None:
                # Cached IP no longer answers — forget it so the next tick
                # re-tries the hostname (mDNS) path.
                self._cached_ip = None
            self.state_changed.emit(info)

            # Interruptible sleep so stop() takes effect promptly
            remaining = self._poll_ms
            while remaining > 0 and not self._stopping:
                step = min(50, remaining)
                self.msleep(step)
                remaining -= step
