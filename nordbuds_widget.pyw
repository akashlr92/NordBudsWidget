"""
Nord Buds Widget - control OnePlus Nord Buds 4 from Windows.

Backend: talks to the buds over the Bluetooth RFCOMM service HeyMelody uses
("oppointeraction", UUID 0000079A-D102-11E1-9B23-00025B00A5A5) with the community
reverse-engineered OPPO/OnePlus frame format (see Gadgetbridge and BudsLink):

    AA <len> 00 00 <cmd lo> <cmd hi> <seq> <payload len lo> <payload len hi> <payload...>

Frontend: nordbuds_ui.html, served on 127.0.0.1 (random port + secret token) and shown
in a Microsoft Edge "app" window. Standard library only. Run:  pythonw nordbuds_widget.pyw
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "NordBudsWidget")
INSTANCE_FILE = os.path.join(APP_DIR, "instance.json")
CFG_PATH = os.path.join(APP_DIR, "settings.json")
LOG_PATH = os.path.join(APP_DIR, "widget.log")


def log(msg):
    """Append a line to widget.log (kept small); pythonw has no console, so this is our trace."""
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 200_000:
            os.replace(LOG_PATH, LOG_PATH + ".old")
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + f"[{os.getpid()}] {msg}" + "\n")
    except Exception:
        pass


WINDOW_TITLE = "Nord Buds 4"

# ============================================================================= protocol

SERVICE_UUID = uuid.UUID("0000079A-D102-11E1-9B23-00025B00A5A5")


class C:
    HELLO = 0x0100
    PRODUCT = 0x0103
    FIRMWARE = 0x0105
    BATTERY = 0x0106
    GET_ANC = 0x010C
    GET_FEATURES = 0x010D
    GET_EQ = 0x010F
    GET_DEVICES = 0x0112
    GET_CUSTOM_EQ = 0x0122
    GET_BASS = 0x0124
    REPORT = 0x0204
    FIND_BUDS = 0x0400
    SET_FEATURE = 0x0403
    SET_ANC = 0x0404
    SET_EQ = 0x0406
    DEVICE_ACTION = 0x040B
    SET_CUSTOM_EQ = 0x0418
    SET_BASS = 0x041B
    FEATURE_EVENT = 0x0503
    EQ_EVENT = 0x0504
    CUSTOM_EQ_EVENT = 0x0506
    BASS_EVENT = 0x0508
    DEVICES_EVENT = 0x0512


MODE_OFF, MODE_ANC, MODE_TRANSPARENCY = "off", "anc", "transparency"
SET_MODE_BYTES = {MODE_OFF: 0x01, MODE_ANC: 0x02, MODE_TRANSPARENCY: 0x04}

EQ_PRESETS = {0: "Balanced", 1: "Serenade", 2: "Bass"}       # Nord Buds 4 "Sound Master EQ"

FEATURES = {0x06: "game_mode", 0x04: "in_ear", 0x11: "dual_connection", 0x1D: "dynamic_bass"}
FEATURE_IDS = {v: k for k, v in FEATURES.items()}

ACK_NAMES = {0x8400: "Find earbuds", 0x8403: "Setting", 0x8404: "Noise control", 0x8406: "Equalizer",
             0x840B: "Device", 0x8418: "Custom EQ", 0x841B: "BassWave"}


def s8(v):
    return v - 256 if v > 127 else v


def u8(v):
    return v & 0xFF


def decode_mode(v, hi=0):
    if v in (0x02, 0x10, 0x20, 0x40, 0x80):
        return MODE_ANC
    if v == 0x04 or (v == 0x00 and hi == 0x01):
        return MODE_TRANSPARENCY
    if v in (0x01, 0x08):
        return MODE_OFF
    return None


def make_frame(cmd, seq, payload=b""):
    n = len(payload)
    return bytes([0xAA, (n + 7) & 0xFF, 0, 0, cmd & 0xFF, (cmd >> 8) & 0xFF,
                  seq & 0xFF, n & 0xFF, (n >> 8) & 0xFF]) + bytes(payload)


def split_frames(buf):
    """Pop complete frames from a bytearray; returns list of (cmd, payload)."""
    out = []
    while True:
        start = buf.find(0xAA)
        if start < 0:
            buf.clear()
            return out
        if start:
            del buf[:start]
        if len(buf) < 9:
            return out
        plen = buf[7] | (buf[8] << 8)
        if len(buf) < 9 + plen:
            return out
        out.append((buf[4] | (buf[5] << 8), bytes(buf[9:9 + plen])))
        del buf[:9 + plen]


# ============================================================================= winsock (connect by UUID)

class _GUID(ctypes.Structure):
    _fields_ = [("d1", wt.DWORD), ("d2", wt.WORD), ("d3", wt.WORD), ("d4", ctypes.c_ubyte * 8)]


class _SOCKADDR_BTH(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("family", wt.USHORT), ("btAddr", ctypes.c_ulonglong),
                ("serviceClassId", _GUID), ("port", wt.ULONG)]


_ws2 = ctypes.WinDLL("ws2_32")
_ws2.connect.argtypes = [ctypes.c_size_t, ctypes.c_void_p, ctypes.c_int]


def rfcomm_connect(mac_int):
    """Open an RFCOMM socket to the OPPO service; Windows resolves the channel via SDP."""
    s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
    g = _GUID()
    ctypes.memmove(ctypes.byref(g), SERVICE_UUID.bytes_le, 16)
    addr = _SOCKADDR_BTH(32, mac_int, g, 0)
    if _ws2.connect(s.fileno(), ctypes.byref(addr), ctypes.sizeof(addr)) != 0:
        err = _ws2.WSAGetLastError()
        s.close()
        raise OSError(f"Bluetooth connect failed (WSA {err})")
    return s


class _SYSTEMTIME(ctypes.Structure):
    _fields_ = [(n, wt.WORD) for n in ("y", "m", "dow", "d", "h", "mi", "s", "ms")]


class _BT_DEVICE_INFO(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("Address", ctypes.c_ulonglong), ("ulClassofDevice", wt.ULONG),
                ("fConnected", wt.BOOL), ("fRemembered", wt.BOOL), ("fAuthenticated", wt.BOOL),
                ("stLastSeen", _SYSTEMTIME), ("stLastUsed", _SYSTEMTIME), ("szName", wt.WCHAR * 248)]


try:
    _bthprops = ctypes.WinDLL("bthprops.cpl")
except OSError:
    _bthprops = None


def pc_connected(mac_int):
    """True/False if Windows reports the buds connected to this PC; None if unknown."""
    if not _bthprops:
        return None
    info = _BT_DEVICE_INFO()
    info.dwSize = ctypes.sizeof(info)
    info.Address = mac_int
    if _bthprops.BluetoothGetDeviceInfo(None, ctypes.byref(info)) != 0:
        return None
    return bool(info.fConnected)


def bluetooth_on():
    """False when this PC has no Bluetooth radio switched on, None when that is unknown."""
    if not _bthprops:
        return None
    try:
        size, radio = wt.DWORD(ctypes.sizeof(wt.DWORD)), wt.HANDLE()
        _bthprops.BluetoothFindFirstRadio.restype = wt.HANDLE
        find = _bthprops.BluetoothFindFirstRadio(ctypes.byref(size), ctypes.byref(radio))
        if not find:
            return False
        _bthprops.BluetoothFindRadioClose(wt.HANDLE(find))
        ctypes.WinDLL("kernel32").CloseHandle(radio)
        return True
    except Exception:
        return None


def find_buds():
    """Return the MAC (int) of the first paired device exposing the OPPO service, else None."""
    ps = ("Get-PnpDevice | Where-Object { $_.InstanceId -like "
          "'BTHENUM\\{0000079A-D102-11E1-9B23-00025B00A5A5}*' } | "
          "ForEach-Object { $_.InstanceId }")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True,
                             text=True, timeout=25, creationflags=0x08000000).stdout
    except Exception:
        return None
    for line in out.splitlines():
        mac = line.strip().rsplit("&", 1)[-1].split("_")[0]        # ...&AABBCCDDEEFF_C00000000
        if len(mac) == 12:
            try:
                return int(mac, 16)
            except ValueError:
                pass
    return None


# ============================================================================= link

NO_ANSWER = "no-answer"


def fresh_state():
    return {
        "connected": False, "searching": True, "error": None, "firmware": None,
        "battery": {}, "mode": None,
        "eq": None, "custom_eq": None, "bass": None,
        "features": {}, "devices": [], "finding": False,
        "pc_connected": None, "pc_busy": None, "release_until": None, "corner_colors": None,
        "bt_on": None,
        "toast": None, "toast_id": 0,
    }


class BudsLink(threading.Thread):
    """Keeps a connection to the buds open, parses replies/reports into `state`."""

    def __init__(self):
        super().__init__(daemon=True)
        self.mac = None
        self.sock = None
        self.seq = 0
        self.send_lock = threading.Lock()
        self.state_lock = threading.Lock()
        self.stop_evt = threading.Event()
        self.wake = threading.Event()
        self.state = fresh_state()
        self.version = 0
        self.find_timer = None
        self.last_rx = 0.0
        self.released = False          # user disconnected this PC on purpose: don't auto-reconnect
        self.hold_token = None
        self.silent = False            # the buds accept the link but do not answer
        self.silent_tries = 0

    # ---------------------------------------------------------------- state helpers
    def snapshot(self):
        with self.state_lock:
            return json.loads(json.dumps(self.state))

    def update(self, **kw):
        with self.state_lock:
            self.state.update(kw)
            self.version += 1

    def toast(self, msg):
        with self.state_lock:
            self.state["toast"] = msg
            self.state["toast_id"] += 1

    # ---------------------------------------------------------------- sending
    def send(self, cmd, payload=b""):
        with self.send_lock:
            if not self.sock:
                return False
            self.seq = (self.seq + 1) & 0xFF
            try:
                self.sock.sendall(make_frame(cmd, self.seq, payload))
                time.sleep(0.03)
                return True
            except OSError:
                return False

    def query_all(self):
        self.send(C.BATTERY)
        self.send(C.GET_ANC, b"\x01\x01")
        self.send(C.GET_EQ)
        self.send(C.GET_CUSTOM_EQ)
        self.send(C.GET_BASS)
        ids = list(FEATURES)
        self.send(C.GET_FEATURES, bytes([len(ids)] + ids))
        self.send(C.GET_DEVICES)

    # ---------------------------------------------------------------- actions (from UI)
    def _drop_link(self):
        with self.send_lock:
            if self.sock:
                try:
                    self.sock.close()
                except OSError:
                    pass

    def _pc(self, op):
        """Connect/disconnect this PC's audio links via Windows (frees the buds for other devices)."""
        try:
            import bt_audio
            if op == "disconnect":
                self.released = True
                self._drop_link()
                time.sleep(0.5)
                res = bt_audio.disconnect()
            else:
                self.released = False
                self.hold_token = None
                self.update(release_until=None)
                res = bt_audio.reconnect()
            if not res:
                self.toast("Windows couldn't find the earbuds' audio devices. Are they paired with this PC?")
            elif any(hr != 0 for _, hr in res):
                self.toast(f"Windows reported an error ({res[0][1]:#x}). Try again.")
        except Exception as e:                     # never let a COM error kill the service
            self.toast(f"Couldn't {op} this PC: {e}")
        for _ in range(30):                        # wait up to ~12 s for Windows to settle
            time.sleep(0.4)
            c = pc_connected(self.mac) if self.mac else None
            if c is not None and c == (op != "disconnect"):
                break
        self.update(pc_busy=None, pc_connected=pc_connected(self.mac) if self.mac else None)
        self.wake.set()
        if op == "disconnect":
            self._hold_released()

    HOLD_SECONDS = 120

    def _hold_released(self):
        """The buds re-page remembered devices and come straight back to this PC. Keep pushing
        them off for a while so another device can take the slot; then return to normal."""
        import bt_audio
        token = object()
        self.hold_token = token
        end = time.time() + self.HOLD_SECONDS
        self.update(release_until=end)
        while time.time() < end and self.released and self.hold_token is token:
            time.sleep(1)
            if pc_connected(self.mac):
                try:
                    bt_audio.disconnect()
                except Exception:
                    pass
        if self.hold_token is token:
            self.released = False
            self.update(release_until=None)
            self.wake.set()

    def act(self, a):
        t = a.get("type")
        if t == "pc":
            op = a["op"]
            if op not in ("connect", "disconnect"):
                raise ValueError(op)
            if self.mac is None:
                return {"ok": False, "error": "Earbuds not found"}
            self.update(pc_busy=op)
            threading.Thread(target=self._pc, args=(op,), daemon=True).start()
            return {"ok": True}
        if not self.sock:
            if t == "refresh":                        # retry now instead of waiting
                self.wake.set()
                return {"ok": True}
            return {"ok": False, "error": "Earbuds not connected"}
        if t == "mode":
            m = a["mode"]
            self.send(C.SET_ANC, bytes([0x01, 0x01, SET_MODE_BYTES[m]]))
            self.update(mode=m)
            threading.Timer(0.4, lambda: self.send(C.GET_ANC, b"\x01\x01")).start()
        elif t == "eq":
            pid = int(a["id"])
            self.send(C.SET_EQ, bytes([pid]))
            self.update(eq=pid)
            threading.Timer(0.4, lambda: self.send(C.GET_EQ)).start()
        elif t == "custom_eq":
            ce = self.state.get("custom_eq")
            if not ce:
                return {"ok": False, "error": "Custom EQ not available"}
            if len(a["dbs"]) != len(ce["freqs"]):
                raise ValueError(f"custom EQ needs {len(ce['freqs'])} values")
            dbs = [max(ce["min"], min(ce["max"], int(v))) for v in a["dbs"]]
            name = ce["name"].encode("utf-8")
            p = bytearray([0x02, u8(ce["min"]), u8(ce["max"]), ce["id"], len(name)]) + name
            p.append(len(ce["freqs"]))
            for f, db in zip(ce["freqs"], dbs):
                p += bytes([f & 0xFF, (f >> 8) & 0xFF, u8(db)])
            self.send(C.SET_CUSTOM_EQ, bytes(p))
            with self.state_lock:
                self.state["custom_eq"]["dbs"] = dbs
            if self.state.get("eq") != ce["id"]:
                self.send(C.SET_EQ, bytes([ce["id"]]))
                self.update(eq=ce["id"])
        elif t == "bass":
            b = self.state.get("bass") or {"min": -5, "max": 5}
            lvl = max(b["min"], min(b["max"], int(a["level"])))
            self.send(C.SET_BASS, bytes([u8(b["min"]), u8(b["max"]), u8(lvl)]))
            with self.state_lock:
                self.state["bass"] = {**b, "level": lvl}
        elif t == "feature":
            fid = FEATURE_IDS[a["name"]]
            on = bool(a["on"])
            self.send(C.SET_FEATURE, bytes([fid, 1 if on else 0]))
            with self.state_lock:
                self.state["features"][a["name"]] = on
            ids = list(FEATURES)
            threading.Timer(0.5, lambda: self.send(C.GET_FEATURES, bytes([len(ids)] + ids))).start()
        elif t == "find":
            on = bool(a["on"])
            self.send(C.FIND_BUDS, bytes([1 if on else 0]))
            self.update(finding=on)
            if self.find_timer:
                self.find_timer.cancel()
            if on:                                    # safety: auto-stop after 30 s
                self.find_timer = threading.Timer(30, lambda: self.act({"type": "find", "on": False}))
                self.find_timer.start()
        elif t == "refresh":
            threading.Thread(target=self._refresh_or_reconnect, daemon=True).start()
        else:
            return {"ok": False, "error": "unknown action"}
        return {"ok": True}

    # ---------------------------------------------------------------- parsing
    def _battery(self, items):
        b = self.state["battery"]
        for k in range(0, len(items) - 1, 2):
            who = {1: "left", 2: "right", 3: "case"}.get(items[k])
            if not who:
                continue
            lvl, chg = items[k + 1] & 0x7F, bool(items[k + 1] & 0x80)
            if who == "case" and lvl == 0 and not chg:
                if "case" in b:
                    b["case"]["stale"] = True       # case closed / out of range: keep last known
                continue
            b[who] = {"level": lvl, "charging": chg}

    def _devices(self, p, pos, count):
        devs = []
        for _ in range(count):
            if pos + 10 > len(p):
                break
            mac = ":".join(f"{x:02X}" for x in reversed(p[pos:pos + 6]))
            elen, conn, flag, nlen = p[pos + 6], p[pos + 7], p[pos + 8], p[pos + 9]
            name = p[pos + 10:pos + 10 + nlen].decode("utf-8", "replace").rstrip("\0")
            devs.append({"mac": mac, "name": name or f"Device {mac[-5:]}",
                         "connected": conn in (1, 2), "current": bool(flag & 0x01),
                         "audio": bool(flag & 0x04)})
            pos = pos + 7 + elen if elen else pos + 10 + nlen
        devs.sort(key=lambda d: (not d["current"], not d["connected"], d["name"].lower()))
        self.state["devices"] = devs

    def _features(self, p, pos, count):
        for _ in range(count):
            if pos + 1 >= len(p):
                break
            name = FEATURES.get(p[pos])
            if name:
                self.state["features"][name] = p[pos + 1] == 1
            pos += 2

    def _custom_eq(self, p):
        if not p:
            return
        pos, count = 1, p[0]
        for _ in range(count):
            if pos + 5 > len(p):
                break
            sel, mn, mx, eid, nlen = p[pos], s8(p[pos + 1]), s8(p[pos + 2]), p[pos + 3], p[pos + 4]
            pos += 5
            name = p[pos:pos + nlen].decode("utf-8", "replace").rstrip("\0")
            pos += nlen
            bands = p[pos] if pos < len(p) else 0
            pos += 1
            freqs, dbs = [], []
            for _ in range(bands):
                if pos + 3 > len(p):
                    break
                freqs.append(p[pos] | (p[pos + 1] << 8))
                dbs.append(s8(p[pos + 2]))
                pos += 3
            self.state["custom_eq"] = {"id": eid, "name": name or "Custom", "min": mn, "max": mx,
                                       "freqs": freqs, "dbs": dbs}
            if sel:
                self.state["eq"] = eid
            break                                   # Nord Buds 4 exposes one custom slot

    def _refresh_or_reconnect(self):
        """Re-query everything; if the buds don't answer within 4 s the link is stale, so reconnect."""
        before = self.last_rx
        self.query_all()
        time.sleep(4)
        if self.last_rx <= before:
            self.silent = True
            self._drop_link()

    def _on_frame(self, cmd, p):
        self.last_rx = time.time()
        with self.state_lock:
            st = self.state
            if self.silent and self.sock:             # the buds answer again
                st["connected"], st["error"] = True, None
            self.silent, self.silent_tries = False, 0
            if cmd == 0x8106 and len(p) >= 2 and p[0] == 0:
                self._battery(p[2:2 + p[1] * 2])
            elif cmd == 0x810C and len(p) >= 4 and p[0] == 0 and p[1] == 0x01:
                st["mode"] = decode_mode(p[3], p[4] if len(p) > 4 else 0) or st["mode"]
            elif cmd == 0x8105 and len(p) > 1:
                st["firmware"] = "".join(c for c in p[1:].decode("ascii", "replace") if c.isprintable())
            elif cmd == 0x810F and len(p) >= 2 and p[0] == 0:
                st["eq"] = p[1]
            elif cmd == C.EQ_EVENT and p:
                st["eq"] = p[1] if len(p) >= 2 and p[0] == 0 else p[0]
            elif cmd == 0x8122 and p and p[0] == 0:
                self._custom_eq(p[1:])
            elif cmd == C.CUSTOM_EQ_EVENT:
                self._custom_eq(p)
            elif cmd in (0x8124, C.BASS_EVENT):
                off = 1 if len(p) >= 4 and p[0] == 0 else 0
                if len(p) >= off + 3:
                    st["bass"] = {"min": s8(p[off]), "max": s8(p[off + 1]), "level": s8(p[off + 2])}
            elif cmd == 0x810D and len(p) >= 2 and p[0] == 0:
                self._features(p, 2, p[1])
            elif cmd == C.FEATURE_EVENT and p:
                self._features(p, 1, p[0])
            elif cmd == 0x8112 and len(p) >= 2 and p[0] == 0:
                self._devices(p, 2, p[1])
            elif cmd == C.DEVICES_EVENT and p:
                self._devices(p, 1, p[0])
            elif cmd in (0x840B, 0x050E, 0x0513, 0x0516):
                threading.Timer(0.8, lambda: self.send(C.GET_DEVICES)).start()
                if cmd == 0x840B and p and p[0] != 0:
                    self._toast_locked("The earbuds couldn't switch to that device. Is its Bluetooth on and in range?")
            elif cmd in ACK_NAMES:
                if p and p[0] != 0:
                    self._toast_locked(f"{ACK_NAMES[cmd]}: the earbuds rejected the change (code {p[0]})")
            elif cmd == C.REPORT and p:
                kind = p[0]
                if kind == 0x01 and len(p) >= 2:
                    self._battery(p[2:2 + p[1] * 2])
                elif kind == 0x03 and len(p) >= 4:
                    st["mode"] = decode_mode(p[3], p[4] if len(p) > 4 else 0) or st["mode"]
                elif kind == 0x06 and len(p) >= 2:
                    self._devices(p, 2, p[1])
                elif kind == 0x05:
                    ids = list(FEATURES)
                    threading.Timer(0.2, lambda: self.send(C.GET_FEATURES, bytes([len(ids)] + ids))).start()
            else:
                return
            self.version += 1

    def _toast_locked(self, msg):
        self.state["toast"] = msg
        self.state["toast_id"] += 1

    # ---------------------------------------------------------------- main loop
    def run(self):
        while not self.stop_evt.is_set():
            if self.mac is None:
                self.update(searching=True)
                self.mac = find_buds()
                if self.mac is None:
                    self.update(searching=False, error="Nord Buds 4 aren't paired with this PC.",
                                bt_on=bluetooth_on())
                    self.wake.wait(10)
                    self.wake.clear()
                    continue
            # Only open the control link while Windows has the buds connected; opening it
            # otherwise would pull the buds back to this PC.
            pc = pc_connected(self.mac)
            if self.released or pc is False:
                self.update(connected=False, searching=False, pc_connected=False if self.released else pc, error=None,
                            bt_on=bluetooth_on())
                self.wake.wait(4)
                self.wake.clear()
                continue
            try:
                s = rfcomm_connect(self.mac)
                with self.send_lock:
                    self.sock = s
                self.last_rx = time.time()
                quiet = self.silent and self.silent_tries >= 2    # one slow answer is not reported
                self.update(connected=not quiet, searching=False, pc_connected=True, bt_on=True,
                            error=NO_ANSWER if quiet else None)
                self.send(C.HELLO)
                self.send(C.FIRMWARE)
                self.query_all()
                threading.Thread(target=self._poller, args=(s,), daemon=True).start()
                buf = bytearray()
                while True:
                    chunk = s.recv(2048)
                    if not chunk:
                        break
                    buf.extend(chunk)
                    for cmd, payload in split_frames(buf):
                        self._on_frame(cmd, payload)
            except OSError as e:
                self.update(error=str(e) if not self.silent else NO_ANSWER if self.silent_tries else None)
            with self.send_lock:
                if self.sock:
                    try:
                        self.sock.close()
                    except OSError:
                        pass
                self.sock = None
            self.update(connected=False, searching=False, finding=False,
                        pc_connected=pc_connected(self.mac))
            if self.silent:                           # retry at once, then less often, up to once a minute
                self.silent_tries += 1
            self.wake.wait(max(2, min(60, 10 * (self.silent_tries - 1))) if self.silent else
                           2 if not self.released else 4)
            self.wake.clear()

    def _poller(self, s):
        while not self.stop_evt.is_set() and self.sock is s:
            for _ in range(20):                      # every 20 s; check Windows state every 2 s
                time.sleep(1)
                if self.sock is not s:
                    return
                if _ % 2 == 0 and pc_connected(self.mac) is False:
                    self._drop_link()                # Windows lost the buds: stop pretending
                    return
            sent_at = time.time()
            self.send(C.BATTERY)
            self.send(C.GET_DEVICES)
            time.sleep(5)
            if self.sock is s and self.last_rx < sent_at:
                self.silent = True
                self._drop_link()                    # no answer: stale link, reconnect

    def close(self):
        self.stop_evt.set()
        self.wake.set()
        with self.send_lock:
            if self.sock:
                try:
                    self.sock.close()
                except OSError:
                    pass


# ============================================================================= settings

def load_cfg():
    try:
        with open(CFG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_cfg(cfg):
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        with open(CFG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f)
    except Exception:
        pass


# ============================================================================= desktop widget window (Win32)

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_gdi32 = ctypes.WinDLL("gdi32")
_EnumProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
LONG_PTR = ctypes.c_ssize_t

_user32.GetWindowLongPtrW.restype = LONG_PTR
_user32.GetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
_user32.SetWindowLongPtrW.restype = LONG_PTR
_user32.SetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int, LONG_PTR]
_user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
_user32.GetWindow.restype = wt.HWND
_user32.GetWindow.argtypes = [wt.HWND, wt.UINT]
_user32.FindWindowExW.restype = wt.HWND
_user32.FindWindowExW.argtypes = [wt.HWND, wt.HWND, wt.LPCWSTR, wt.LPCWSTR]
_user32.GetForegroundWindow.restype = wt.HWND
_user32.MonitorFromWindow.restype = wt.HANDLE
_user32.MonitorFromWindow.argtypes = [wt.HWND, wt.DWORD]
_user32.MonitorFromPoint.restype = wt.HANDLE
_user32.MonitorFromPoint.argtypes = [wt.POINT, wt.DWORD]
_user32.GetMonitorInfoW.argtypes = [wt.HANDLE, ctypes.c_void_p]
_user32.SetWindowRgn.argtypes = [wt.HWND, wt.HANDLE, wt.BOOL]
_user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
_gdi32.CreateRoundRectRgn.restype = wt.HANDLE
_gdi32.CreateSolidBrush.restype = wt.HANDLE
_user32.SetParent.restype = wt.HWND
_user32.SetParent.argtypes = [wt.HWND, wt.HWND]
_user32.SetFocus.argtypes = [wt.HWND]
_user32.WindowFromPoint.restype = wt.HWND
_user32.WindowFromPoint.argtypes = [wt.POINT]
_user32.GetAncestor.restype = wt.HWND
_user32.GetAncestor.argtypes = [wt.HWND, wt.UINT]
_user32.DefWindowProcW.restype = LONG_PTR
_user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
_user32.CreateWindowExW.restype = wt.HWND
_user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, wt.HWND, wt.HANDLE, wt.HANDLE, ctypes.c_void_p]
_WNDPROC = ctypes.WINFUNCTYPE(LONG_PTR, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

GWL_STYLE, GWL_EXSTYLE = -16, -20
WS_CAPTION, WS_THICKFRAME, WS_SYSMENU = 0x00C00000, 0x00040000, 0x00080000
WS_MINIMIZEBOX, WS_MAXIMIZEBOX = 0x00020000, 0x00010000
WS_MINIMIZE, WS_MAXIMIZE = 0x20000000, 0x01000000
WS_POPUP, WS_CHILD, WS_CLIPCHILDREN = 0x80000000, 0x40000000, 0x02000000
WS_EX_TOOLWINDOW, WS_EX_APPWINDOW = 0x00000080, 0x00040000
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE, SWP_FRAMECHANGED = 0x1, 0x2, 0x10, 0x20
HWND_TOP = 0

VIEW_SIZES = {"compact": (226, 92), "full": (392, 820)}       # in DIPs (scaled for DPI)
MARGIN = 24
HOST_CLASS = "NordBudsWidgetHost"
CORNER_RADIUS = 16                                            # window clip, DIPs (CSS radius is 18)


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]


class _WNDCLASS(ctypes.Structure):
    _fields_ = [("style", wt.UINT), ("lpfnWndProc", _WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HANDLE), ("hIcon", wt.HANDLE),
                ("hCursor", wt.HANDLE), ("hbrBackground", wt.HANDLE), ("lpszMenuName", wt.LPCWSTR),
                ("lpszClassName", wt.LPCWSTR)]


def _class_name(hwnd):
    buf = ctypes.create_unicode_buffer(128)
    _user32.GetClassNameW(hwnd, buf, 128)
    return buf.value


def find_window():
    found = []

    def cb(hwnd, _):
        if _user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(256)
            _user32.GetWindowTextW(hwnd, buf, 256)
            if buf.value == WINDOW_TITLE and _class_name(hwnd).startswith("Chrome_WidgetWin"):
                found.append(hwnd)
        return True

    _user32.EnumWindows(_EnumProc(cb), 0)
    return found[0] if found else None


def desktop_window():
    """The top-level window hosting the desktop icons (Progman, or a WorkerW on some setups)."""
    found = []

    def cb(hwnd, _):
        if _user32.FindWindowExW(hwnd, None, "SHELLDLL_DefView", None):
            found.append(hwnd)
        return True

    _user32.EnumWindows(_EnumProc(cb), 0)
    return found[0] if found else _user32.FindWindowExW(None, None, "Progman", None)


def browser_path():
    for p in (r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
              r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
              r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
              r"%LocalAppData%\Google\Chrome\Application\chrome.exe"):
        p = os.path.expandvars(p)
        if os.path.exists(p):
            return p
    return None


class DesktopWidget:
    """Shows the Edge app window as a frameless, taskbar-less widget that lives on the desktop
    layer (just above the wallpaper/icons, below every application window). The Edge window is
    hosted as a child of our own borderless window, so only the page is visible."""

    def __init__(self, link=None):
        self.link = link
        self.hwnd = None                              # our host window (top level)
        self.edge = None                              # the Edge app window (child of the host)
        self._wndproc = None
        self.view = "compact"
        self.fg_at_show = None                        # foreground window when we popped up
        self.lock = threading.Lock()
        cfg = load_cfg()
        self.anchor = cfg.get("anchor")               # top-left of the compact widget, physical px
        self.cap = None                               # height of Edge's own title strip, physical px

    # ---------------------------------------------------------------- geometry
    def _scale(self):
        try:
            return _user32.GetDpiForWindow(self.hwnd) / 96.0
        except Exception:
            return 1.0

    def _work_area(self, point=None):
        mon = (_user32.MonitorFromPoint(wt.POINT(*point), 0) if point else None) or \
              _user32.MonitorFromWindow(self.hwnd, 2)
        mi = _MONITORINFO()
        mi.cbSize = ctypes.sizeof(mi)
        _user32.GetMonitorInfoW(mon, ctypes.byref(mi))
        r = mi.rcWork
        return r.left, r.top, r.right, r.bottom

    def _rect(self):
        r = wt.RECT()
        _user32.GetWindowRect(self.hwnd, ctypes.byref(r))
        return r.left, r.top, r.right - r.left, r.bottom - r.top

    def _valid_anchor(self):
        if not self.anchor or len(self.anchor) != 2:
            return None
        x, y = self.anchor
        if x <= -10000 or y <= -10000:                # minimised-window coordinates: ignore
            return None
        if not _user32.MonitorFromPoint(wt.POINT(int(x) + 20, int(y) + 20), 0):   # off every screen
            return None
        return int(x), int(y)

    def _place(self, view, activate=False):
        s = self._scale()
        w, h = (int(v * s) for v in VIEW_SIZES[view])
        anchor = self._valid_anchor()
        if anchor is None:
            l, t, r, b = self._work_area()
            anchor = (r - int(VIEW_SIZES["compact"][0] * s) - int(MARGIN * s), t + int(MARGIN * s))
            self.anchor = list(anchor)
        x, y = anchor
        l, t, r, b = self._work_area((x + 20, y + 20))
        h = min(h, b - t - int(8 * s))                # never taller than the screen
        x = max(l, min(x, r - w))                     # keep the whole widget on screen
        y = max(t, min(y, b - h))
        flags = SWP_FRAMECHANGED | (0 if activate else SWP_NOACTIVATE)
        after = HWND_TOP if activate else self._insert_after()
        if after is None:
            flags |= 0x4                              # SWP_NOZORDER
        _user32.SetWindowPos(self.hwnd, after, x, y, w, h, flags)
        if _user32.IsZoomed(self.edge) or _user32.IsIconic(self.edge):
            _user32.ShowWindow(self.edge, 4)          # back to a normal window, without activating
        # Edge surrounds the page with its own title strip and borders. Shift and size the Edge
        # child so the *page* fills our window exactly; Edge's frame falls outside and is clipped.
        dl, dt, dr, db = self._frame_offsets()
        ew, eh = w + dl + dr, h + dt + db
        for _ in range(4):
            _user32.SetWindowPos(self.edge, None, -dl, -dt, ew, eh, 0x4 | SWP_NOACTIVATE | SWP_FRAMECHANGED)
            time.sleep(0.12)
            now = self._frame_offsets()
            er = wt.RECT()
            _user32.GetWindowRect(self.edge, ctypes.byref(er))
            pw, ph = er.right - er.left - now[0] - now[2], er.bottom - er.top - now[1] - now[3]
            if now[:2] == (dl, dt) and pw > w and ph > h:
                break
            dl, dt = now[:2]                          # Edge paints up to 1 px less than its page area
            ew, eh = ew + max(0, w + 1 - pw), eh + max(0, h + 1 - ph)
        d = int(2 * CORNER_RADIUS * s)
        rgn = _gdi32.CreateRoundRectRgn(0, 0, w + 1, h + 1, d, d)
        _user32.SetWindowRgn(self.hwnd, rgn, True)
        if activate:
            self._force_foreground()
        threading.Thread(target=self._publish_corners, args=(x, y, w, h), daemon=True).start()

    def _force_foreground(self):
        """Windows only lets the active app hand over focus; briefly share its input queue
        (AttachThreadInput) so the request is honoured."""
        h = self.hwnd
        k32 = ctypes.WinDLL("kernel32")
        try:
            fg = _user32.GetForegroundWindow()
            fg_tid = _user32.GetWindowThreadProcessId(fg, None) if fg else 0
            me = k32.GetCurrentThreadId()
            attached = bool(fg_tid and fg_tid != me and _user32.AttachThreadInput(me, fg_tid, True))
            try:
                _user32.ShowWindow(h, 5)                  # SW_SHOW
                _user32.BringWindowToTop(h)
                _user32.SetForegroundWindow(h)
            finally:
                if attached:
                    _user32.AttachThreadInput(me, fg_tid, False)
        except Exception:
            pass

    def _publish_corners(self, x, y, w, h):
        """Tell the page what the screen looks like behind each corner so it can blend the
        anti-aliased rounded corners into it. The sampled pixels lie inside our rectangle but
        outside the rounded clip, so they show what is behind the widget."""
        if not self.link:
            return
        try:
            import desk_colors
            time.sleep(0.2)                           # let the new position reach the screen
            i = 2
            pts = [(x + i, y + i), (x + w - 1 - i, y + i), (x + i, y + h - 1 - i), (x + w - 1 - i, y + h - 1 - i)]
            desk = desktop_window()
            cols = list(self.link.snapshot().get("corner_colors") or [None] * 4)
            for n, pt in enumerate(pts):              # a corner covered by an app keeps its last colour
                if _user32.GetAncestor(_user32.WindowFromPoint(wt.POINT(*pt)), 2) == desk:
                    cols[n] = desk_colors.sample([pt], avg=1)[0] or cols[n]
            if all(cols) and cols != self.link.snapshot().get("corner_colors"):
                self.link.update(corner_colors=cols)
        except Exception:
            pass

    def _page_hwnd(self):
        found = []

        def cb(h, _):
            if _class_name(h) == "Chrome_RenderWidgetHostHWND" and _user32.IsWindowVisible(h):
                found.append(h)
                return False
            return True

        _user32.EnumChildWindows(self.edge, _EnumProc(cb), 0)
        return found[0] if found else None

    def _frame_offsets(self):
        """(left, top, right, bottom) distance between Edge's window edge and the page area."""
        ph = self._page_hwnd()
        if ph:
            wr, pr = wt.RECT(), wt.RECT()
            _user32.GetWindowRect(self.edge, ctypes.byref(wr))
            _user32.GetWindowRect(ph, ctypes.byref(pr))
            offs = (pr.left - wr.left, pr.top - wr.top, wr.right - pr.right, wr.bottom - pr.bottom)
            if all(0 <= o < 300 for o in offs):
                return offs
        return (0, self._cap(), 0, 0)

    def page_rect(self):
        return self._rect()                           # the host window is exactly the page

    def _page_displaced(self):
        """True when Edge has moved or resized its window so the page no longer fills ours."""
        if _user32.IsZoomed(self.edge) or _user32.IsIconic(self.edge):
            return True
        ph = self._page_hwnd()
        if not ph:
            return False
        hr, pr = wt.RECT(), wt.RECT()
        _user32.GetWindowRect(self.hwnd, ctypes.byref(hr))
        _user32.GetWindowRect(ph, ctypes.byref(pr))
        return pr.left > hr.left or pr.top > hr.top or pr.right < hr.right or pr.bottom < hr.bottom

    def _cap(self):
        return self.cap if self.cap is not None else int(32 * self._scale())

    def set_metrics(self, outer, inner, dpr):
        cap = max(0, int(round((float(outer) - float(inner)) * float(dpr))))
        if cap > 200:
            return
        with self.lock:
            if cap != self.cap:
                self.cap = cap
                if self.alive() and not self._page_hwnd():        # only the fallback offsets use it
                    self._place(self.view, activate=False)

    def _insert_after(self):
        """Window to insert ours *below*: the one just above the desktop window."""
        desk = desktop_window()
        if not desk:
            return wt.HWND(1)                         # HWND_BOTTOM
        prev = _user32.GetWindow(desk, 3)             # GW_HWNDPREV
        if prev == self.hwnd:
            return None                               # already sitting right above the desktop
        return prev or wt.HWND(1)

    # ---------------------------------------------------------------- lifecycle
    def launch(self, url):
        exe = browser_path()
        if not exe:
            import webbrowser
            webbrowser.open(url)
            return None
        w, h = VIEW_SIZES["compact"]
        args = [exe, f"--app={url}", f"--user-data-dir={os.path.join(APP_DIR, 'window')}",
                f"--window-size={w},{h}", "--window-position=-3000,-3000",      # appear off-screen,
                "--no-first-run", "--no-default-browser-check",                 # then move into place
                "--disable-gpu", "--disable-gpu-compositing",                   # needed for clipped window
                "--disable-features=Translate,msEdgeSidebarV2,msUndersideButton,CalculateNativeWinOcclusion",
                "--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding",
                "--disable-background-timer-throttling"]
        return subprocess.Popen(args)

    def attach(self, timeout=40):
        end = time.time() + timeout
        while time.time() < end:
            hwnd = find_window()
            if hwnd:
                self.edge = hwnd
                if not self._create_host():
                    return False
                self._style()
                return True
            time.sleep(0.25)
        return False

    def _create_host(self):
        """Create our borderless top-level window (no taskbar button, no Alt+Tab) on its own
        message-loop thread."""
        ready = threading.Event()

        def proc(hwnd, msg, wp, lp):
            if msg == 0x0007 and self.edge:                       # WM_SETFOCUS: keys go to the page
                _user32.SetFocus(self.edge)
                return 0
            if msg == 0x0112 and (wp & 0xFFF0) == 0xF010:         # SC_MOVE: take the mouse from the page
                _user32.ReleaseCapture()
            if msg == 0x0010:                                     # WM_CLOSE
                self.close()
                return 0
            if msg == 0x0002:                                     # WM_DESTROY
                _user32.PostQuitMessage(0)
            return _user32.DefWindowProcW(hwnd, msg, wp, lp)

        def pump():
            self._wndproc = _WNDPROC(proc)
            wc = _WNDCLASS()
            wc.lpfnWndProc = self._wndproc
            wc.lpszClassName = HOST_CLASS
            wc.hbrBackground = _gdi32.CreateSolidBrush(0x003C322B)      # page background (BGR)
            _user32.RegisterClassW(ctypes.byref(wc))
            self.hwnd = _user32.CreateWindowExW(WS_EX_TOOLWINDOW, HOST_CLASS, WINDOW_TITLE,
                                                WS_POPUP | WS_CLIPCHILDREN, -3000, -3000, 10, 10,
                                                None, None, None, None)
            ready.set()
            msg = wt.MSG()
            while self.hwnd and _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                _user32.TranslateMessage(ctypes.byref(msg))
                _user32.DispatchMessageW(ctypes.byref(msg))

        threading.Thread(target=pump, daemon=True).start()
        ready.wait(10)
        if not self.hwnd:
            return False
        try:                                                      # Windows 11: no system border or rounding
            dwm = ctypes.WinDLL("dwmapi")
            for attr, val in ((33, 1), (34, 0xFFFFFFFE)):         # corner preference, border colour
                dwm.DwmSetWindowAttribute(wt.HWND(self.hwnd), attr, ctypes.byref(wt.DWORD(val)), 4)
        except Exception:
            pass
        return True

    def _style(self):
        e = self.edge
        st = _user32.GetWindowLongPtrW(e, GWL_STYLE)
        st &= ~(WS_POPUP | WS_CAPTION | WS_THICKFRAME | WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX |
                WS_MINIMIZE | WS_MAXIMIZE)
        _user32.ShowWindow(e, 0)
        _user32.SetWindowLongPtrW(e, GWL_STYLE, st | WS_CHILD)
        _user32.SetParent(e, self.hwnd)
        _user32.ShowWindow(e, 5)
        h = self.hwnd
        with self.lock:
            self.view = "compact"
            self._place("compact")
        _user32.ShowWindow(h, 4)                                  # SW_SHOWNOACTIVATE
        with self.lock:
            self._place("compact")

    def alive(self):
        return bool(self.hwnd) and bool(_user32.IsWindow(self.hwnd)) and bool(_user32.IsWindow(self.edge))

    # ---------------------------------------------------------------- actions from the page
    def set_view(self, view):
        if view not in VIEW_SIZES or not self.alive():
            return False
        with self.lock:
            if self.view == "compact":
                self._remember_anchor()
            if view == "compact":
                self.fg_at_show = None
            self.view = view
            self._place(view, activate=(view == "full"))
        return True

    def drag(self):
        if self.alive():
            start = self._rect()
            _user32.PostMessageW(self.hwnd, 0x0112, 0xF012, 0)      # WM_SYSCOMMAND, SC_MOVE|HTCAPTION
            threading.Timer(0.6, self._after_drag, args=(start,)).start()

    def _after_drag(self, start):
        # the move loop runs in our window's thread; poll until the window stops moving
        last = None
        for _ in range(100):
            if not self.alive():
                return
            r = self._rect()
            if r == last:
                break
            last = r
            time.sleep(0.15)
        with self.lock:
            if self._rect() == start:                 # not moved: keep the stored position
                return
            x, y, w, h = self.page_rect()
            ax, ay = self.anchor or start[:2]             # move the compact position by the same distance
            self.anchor = [ax + x - start[0], ay + y - start[1]]
            self._save_anchor()
            threading.Thread(target=self._publish_corners, args=(x, y, w, h), daemon=True).start()

    def close(self):
        if self.alive():
            _user32.PostMessageW(self.edge, 0x0010, 0, 0)           # WM_CLOSE

    def show(self):
        """Pop the widget up in front of everything, expanded (launch from a shortcut)."""
        if self.alive():
            self.fg_at_show = _user32.GetForegroundWindow()
            _user32.ShowWindow(self.hwnd, 4)
            self.set_view("full")
            self._focus_page()

    def _focus_page(self):
        """The page takes key presses only after a click in it: click an empty spot of its margin."""
        ph = self._page_hwnd()
        if ph:
            lp = (6 << 16) | 8
            _user32.PostMessageW(ph, 0x0201, 1, lp)                 # WM_LBUTTONDOWN
            _user32.PostMessageW(ph, 0x0202, 0, lp)                 # WM_LBUTTONUP

    def _remember_anchor(self):
        if not self.alive() or _user32.IsIconic(self.hwnd):
            return
        x, y, w, h = self.page_rect()
        if x > -10000 and y > -10000:
            self.anchor = [x, y]

    def _save_anchor(self):
        if self._valid_anchor():
            cfg = load_cfg()
            cfg["anchor"] = self.anchor
            cfg.pop("window_pos", None)
            cfg.pop("topmost", None)
            save_cfg(cfg)

    # ---------------------------------------------------------------- keep it on the desktop layer
    def guard(self):
        """Every 0.5 s: undo minimise (Win+D / Show desktop) and, unless the user is using the
        widget, keep it below all application windows."""
        tick = 0
        while self.alive():
            time.sleep(0.5)
            tick += 1
            try:
                if _user32.IsIconic(self.hwnd):
                    _user32.ShowWindow(self.hwnd, 4)
                fg = _user32.GetForegroundWindow()
                if fg == self.hwnd:
                    self.fg_at_show = None                            # user is in the widget
                elif self.view == "full" and self.fg_at_show and fg == self.fg_at_show:
                    _user32.SetWindowPos(self.hwnd, HWND_TOP, 0, 0, 0, 0,
                                         SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)   # stay visible
                else:
                    with self.lock:
                        if _user32.GetForegroundWindow() == self.hwnd:
                            pass                                      # got the focus while we waited
                        elif self.view == "full":                     # clicked away: collapse
                            self.view = "compact"
                            self.fg_at_show = None
                            self._place("compact")
                        else:
                            after = self._insert_after()
                            if after is not None:
                                _user32.SetWindowPos(self.hwnd, after, 0, 0, 0, 0,
                                                     SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
                with self.lock:
                    if self._page_displaced():
                        self._place(self.view)
                if tick % 2 == 0 and self.view == "compact":
                    threading.Thread(target=self._publish_corners, args=self.page_rect(), daemon=True).start()
                if tick % 20 == 0:
                    with self.lock:
                        if self.view == "compact":
                            self._remember_anchor()
                            self._save_anchor()
            except Exception:
                pass


# ============================================================================= HTTP server

class Handler(BaseHTTPRequestHandler):
    link: BudsLink = None
    widget: DesktopWidget = None
    token = ""

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self):
        return secrets.compare_digest(self.headers.get("X-Token", ""), self.token)

    def do_GET(self):
        if self.path == f"/{self.token}/" or self.path == f"/{self.token}":
            with open(os.path.join(HERE, "nordbuds_ui.html"), "rb") as f:
                html = f.read().replace(b"__TOKEN__", self.token.encode())
            return self._send(200, html, "text/html; charset=utf-8")
        if self.path in ("/favicon.ico", "/icon.png"):
            name = "nordbuds.ico" if self.path.endswith(".ico") else "nordbuds.png"
            try:
                with open(os.path.join(HERE, name), "rb") as f:
                    return self._send(200, f.read(), "image/x-icon" if name.endswith(".ico") else "image/png")
            except OSError:
                return self._send(404, {"error": "no icon"})
        if self.path == "/api/ping":
            return self._send(200, {"ok": True})
        if self.path == "/api/state" and self._authorized():
            st = self.link.snapshot()
            st["view"] = self.widget.view if self.widget else "full"
            return self._send(200, st)
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._authorized() or self.headers.get("Content-Type", "") != "application/json":
            return self._send(403, {"error": "forbidden"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0) or b"{}")
        except ValueError:
            return self._send(400, {"error": "bad json"})
        if self.path == "/api/action":
            try:
                return self._send(200, self.link.act(body))
            except (KeyError, ValueError, TypeError) as e:
                return self._send(400, {"ok": False, "error": f"bad request: {e}"})
        if self.path == "/api/win":
            w, op = self.widget, body.get("op")
            if not w:
                return self._send(200, {"ok": False})
            if op in ("compact", "full"):
                return self._send(200, {"ok": w.set_view(op)})
            if op == "drag":
                w.drag()
                return self._send(200, {"ok": True})
            if op == "close":
                w.close()
                return self._send(200, {"ok": True})
            if op == "metrics":
                w.set_metrics(body.get("outer", 0), body.get("inner", 0), body.get("dpr", 1))
                return self._send(200, {"ok": True})
            if op == "show":
                w.show()
                return self._send(200, {"ok": True})
        self._send(404, {"error": "not found"})


def existing_instance():
    try:
        with open(INSTANCE_FILE, "r", encoding="utf-8") as f:
            inst = json.load(f)
        urllib.request.urlopen(f"http://127.0.0.1:{inst['port']}/api/ping", timeout=1.5)
        return inst
    except Exception:
        return None


def main(serve_only=False, startup=False):
    os.makedirs(APP_DIR, exist_ok=True)
    log(f"start args={sys.argv[1:]} exe={sys.executable}")
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)              # per-monitor DPI aware
    except Exception:
        pass
    inst = None if serve_only else existing_instance()
    if inst:                                       # already running: ask it to show the widget
        log(f"already running on port {inst['port']}: asking it to show")
        try:
            _user32.AllowSetForegroundWindow(-1)   # ASFW_ANY: let the running copy take focus
            req = urllib.request.Request(f"http://127.0.0.1:{inst['port']}/api/win",
                                         data=b'{"op": "show"}', method="POST",
                                         headers={"X-Token": inst["token"], "Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=3)
        except Exception:
            pass
        return

    link = BudsLink()
    link.start()
    Handler.link = link
    Handler.token = secrets.token_urlsafe(24)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    with open(INSTANCE_FILE, "w", encoding="utf-8") as f:
        json.dump({"port": port, "token": Handler.token, "pid": os.getpid()}, f)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    url = f"http://127.0.0.1:{port}/{Handler.token}/"
    if serve_only:                                   # testing: no window, run until killed
        print(url, flush=True)
        while True:
            time.sleep(1)

    widget = DesktopWidget(link)
    Handler.widget = widget
    proc = widget.launch(url)
    log(f"edge launched: {getattr(proc, 'pid', None)}")
    if not widget.attach():
        log("ERROR: widget window never appeared")
    else:
        log(f"window attached hwnd={widget.hwnd}")
        if not startup:                              # opened from a shortcut: pop up in front
            time.sleep(0.8)
            widget.show()
        widget.guard()
        log("window closed")                               # returns when the window is closed
    link.close()
    server.shutdown()
    try:
        os.remove(INSTANCE_FILE)
    except OSError:
        pass


if __name__ == "__main__":
    import traceback
    threading.excepthook = lambda a: log("thread error: " + "".join(traceback.format_exception(a.exc_type, a.exc_value, a.exc_traceback)))
    try:
        main(serve_only="--serve" in sys.argv, startup="--startup" in sys.argv)
    except Exception:
        log("FATAL: " + traceback.format_exc())
        raise
