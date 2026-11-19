"""
Connect / disconnect a Bluetooth audio device from this PC without admin rights.

Uses the Windows audio stack (Core Audio -> device topology -> IKsControl) and the
KSPROPSETID_BtAudio "one-shot reconnect/disconnect" properties, the same mechanism the
Windows sound settings use. Standard library only (ctypes COM).
"""
import ctypes
import ctypes.wintypes as wt
import uuid

_ole32 = ctypes.WinDLL("ole32")
HRESULT = ctypes.c_long


class GUID(ctypes.Structure):
    _fields_ = [("d1", wt.DWORD), ("d2", wt.WORD), ("d3", wt.WORD), ("d4", ctypes.c_ubyte * 8)]

    @classmethod
    def of(cls, s):
        g = cls()
        ctypes.memmove(ctypes.byref(g), uuid.UUID(s).bytes_le, 16)
        return g


CLSID_MMDeviceEnumerator = GUID.of("BCDE0395-E52F-467C-8E3D-C4579291692E")
IID_IMMDeviceEnumerator = GUID.of("A95664D2-9614-4F35-A746-DE8DB63617E6")
IID_IDeviceTopology = GUID.of("2A07407E-6497-4A18-9787-32F79BD0D98F")
IID_IPart = GUID.of("AE2DE0E4-5BCA-4F2D-AA46-5D13F8FDB3A9")
IID_IKsControl = GUID.of("28F54685-06FD-11D2-B27A-00A0C9223196")
KSPROPSETID_BtAudio = GUID.of("7FA06C40-B8F6-4C7E-8556-E8C33A12E54D")
KSPROPERTY_ONESHOT_RECONNECT, KSPROPERTY_ONESHOT_DISCONNECT = 0, 1


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", wt.DWORD)]


PKEY_Device_FriendlyName = PROPERTYKEY(GUID.of("a45c254e-df1c-4efd-8020-67d146a850e0"), 14)
PKEY_Device_InstanceId = PROPERTYKEY(GUID.of("78c34fc8-104a-4aca-9ea4-524d52996e57"), 256)


class PROPVARIANT(ctypes.Structure):
    _fields_ = [("vt", wt.USHORT), ("r1", wt.USHORT), ("r2", wt.USHORT), ("r3", wt.USHORT),
                ("val", ctypes.c_void_p), ("pad", ctypes.c_void_p)]


class KSPROPERTY(ctypes.Structure):
    _fields_ = [("Set", GUID), ("Id", wt.ULONG), ("Flags", wt.ULONG)]


def _call(obj, index, *args, argtypes=()):
    """Call vtable method `index` on COM pointer `obj` (a c_void_p)."""
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    fn = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, *argtypes)(vtbl[index])
    return fn(obj, *args)


def _release(obj):
    if obj:
        _call(obj, 2)


def _prop_string(store, key):
    pv = PROPVARIANT()
    if _call(store, 5, ctypes.byref(key), ctypes.byref(pv), argtypes=(ctypes.c_void_p, ctypes.c_void_p)) != 0:
        return ""
    s = ctypes.wstring_at(pv.val) if pv.vt == 31 and pv.val else ""     # VT_LPWSTR
    _ole32.PropVariantClear(ctypes.byref(pv))
    return s


def _ks_controls(match):
    """Yield (endpoint name, IKsControl ptr) for every Bluetooth audio endpoint whose name contains `match`."""
    _ole32.CoInitializeEx(None, 0x2)
    enum = ctypes.c_void_p()
    hr = _ole32.CoCreateInstance(ctypes.byref(CLSID_MMDeviceEnumerator), None, 0x17,
                                 ctypes.byref(IID_IMMDeviceEnumerator), ctypes.byref(enum))
    if hr != 0:
        return
    coll = ctypes.c_void_p()
    # EnumAudioEndpoints(eAll, DEVICE_STATE_ACTIVE|UNPLUGGED|NOTPRESENT... = all)
    _call(enum, 3, 2, 0xF, ctypes.byref(coll), argtypes=(ctypes.c_int, wt.DWORD, ctypes.c_void_p))
    count = wt.UINT()
    _call(coll, 3, ctypes.byref(count), argtypes=(ctypes.c_void_p,))
    for i in range(count.value):
        dev = ctypes.c_void_p()
        _call(coll, 4, i, ctypes.byref(dev), argtypes=(wt.UINT, ctypes.c_void_p))
        store = ctypes.c_void_p()
        _call(dev, 4, 0, ctypes.byref(store), argtypes=(wt.DWORD, ctypes.c_void_p))
        name = _prop_string(store, PKEY_Device_FriendlyName) if store else ""
        _release(store)
        if match.lower() not in name.lower():
            _release(dev)
            continue
        topo, conn, other, part, topo2, filt, ks = (ctypes.c_void_p() for _ in range(7))
        dev_id = ctypes.c_wchar_p()
        try:
            if _call(dev, 3, ctypes.byref(IID_IDeviceTopology), 0x17, None, ctypes.byref(topo),
                     argtypes=(ctypes.c_void_p, wt.DWORD, ctypes.c_void_p, ctypes.c_void_p)) != 0:
                continue
            if _call(topo, 4, 0, ctypes.byref(conn), argtypes=(wt.UINT, ctypes.c_void_p)) != 0:
                continue
            if _call(conn, 8, ctypes.byref(other), argtypes=(ctypes.c_void_p,)) != 0:     # GetConnectedTo
                continue
            if _call(other, 0, ctypes.byref(IID_IPart), ctypes.byref(part),
                     argtypes=(ctypes.c_void_p, ctypes.c_void_p)) != 0:
                continue
            if _call(part, 12, ctypes.byref(topo2), argtypes=(ctypes.c_void_p,)) != 0:    # GetTopologyObject
                continue
            if _call(topo2, 8, ctypes.byref(dev_id), argtypes=(ctypes.c_void_p,)) != 0:   # GetDeviceId
                continue
            if _call(enum, 5, dev_id, ctypes.byref(filt), argtypes=(ctypes.c_wchar_p, ctypes.c_void_p)) != 0:
                continue                                                                 # GetDevice
            if _call(filt, 3, ctypes.byref(IID_IKsControl), 0x17, None, ctypes.byref(ks),
                     argtypes=(ctypes.c_void_p, wt.DWORD, ctypes.c_void_p, ctypes.c_void_p)) != 0:
                continue
            yield name, ks
        finally:
            if dev_id:
                _ole32.CoTaskMemFree(dev_id)
            for p in (ks, filt, topo2, part, other, conn, topo, dev):
                _release(p)
    _release(coll)
    _release(enum)


def _oneshot(match, prop_id):
    done = []
    for name, ks in _ks_controls(match):
        prop = KSPROPERTY(KSPROPSETID_BtAudio, prop_id, 1)        # KSPROPERTY_TYPE_GET
        returned = wt.ULONG()
        hr = _call(ks, 3, ctypes.byref(prop), ctypes.sizeof(prop), None, 0, ctypes.byref(returned),
                   argtypes=(ctypes.c_void_p, wt.ULONG, ctypes.c_void_p, wt.ULONG, ctypes.c_void_p))
        done.append((name, hr & 0xFFFFFFFF))
    return done


def disconnect(match="Nord Buds"):
    """Disconnect this PC's audio links (music + calls) to the device. Returns [(endpoint, hresult)]."""
    return _oneshot(match, KSPROPERTY_ONESHOT_DISCONNECT)


def reconnect(match="Nord Buds"):
    """Ask Windows to reconnect the device's audio links. Returns [(endpoint, hresult)]."""
    return _oneshot(match, KSPROPERTY_ONESHOT_RECONNECT)


def endpoints(match="Nord Buds"):
    return [name for name, _ in _ks_controls(match)]


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    print({"list": endpoints, "disconnect": disconnect, "reconnect": reconnect}[cmd]())
