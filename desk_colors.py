"""Sample the screen colours at given screen points.

Copies a small box around each point from the screen into an off-screen bitmap and reads the
pixels from it. Standard library only.
"""
import ctypes
import ctypes.wintypes as wt

_user32 = ctypes.WinDLL("user32")
_gdi32 = ctypes.WinDLL("gdi32")
_user32.GetDC.restype = wt.HDC
_user32.GetDC.argtypes = [wt.HWND]
_user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
_gdi32.BitBlt.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                          wt.HDC, ctypes.c_int, ctypes.c_int, wt.DWORD]
_gdi32.CreateCompatibleDC.restype = wt.HDC
_gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
_gdi32.CreateCompatibleBitmap.restype = wt.HBITMAP
_gdi32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
_gdi32.SelectObject.restype = wt.HGDIOBJ
_gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
_gdi32.GetPixel.restype = wt.DWORD
_gdi32.GetPixel.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
_gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
_gdi32.DeleteDC.argtypes = [wt.HDC]


def sample(points, avg=2):
    """Return ['#rrggbb', ...] for each (x, y) screen point (averaged over a small box)."""
    side = 2 * avg + 1
    screen = _user32.GetDC(None)
    mem = _gdi32.CreateCompatibleDC(screen)
    bmp = _gdi32.CreateCompatibleBitmap(screen, side, side)
    old = _gdi32.SelectObject(mem, bmp)
    out = []
    try:
        for (x, y) in points:
            rs = gs = bs = n = 0
            if _gdi32.BitBlt(mem, 0, 0, side, side, screen, x - avg, y - avg, 0x00CC0020):   # SRCCOPY
                for py in range(side):
                    for px in range(side):
                        c = _gdi32.GetPixel(mem, px, py)
                        if c != 0xFFFFFFFF:
                            rs += c & 0xFF; gs += (c >> 8) & 0xFF; bs += (c >> 16) & 0xFF; n += 1
            out.append("#%02x%02x%02x" % (rs // n, gs // n, bs // n) if n else None)
    finally:
        _gdi32.SelectObject(mem, old)
        _gdi32.DeleteObject(bmp)
        _gdi32.DeleteDC(mem)
        _user32.ReleaseDC(None, screen)
    return out


if __name__ == "__main__":
    import sys
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    pts = [(int(a), int(b)) for a, b in (p.split(",") for p in sys.argv[1:])] or [(10, 10), (1300, 30), (600, 400)]
    print(sample(pts))
