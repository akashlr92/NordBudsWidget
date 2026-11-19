"""Draw the Nord Buds widget icon (standard library only) and write nordbuds.ico + nordbuds.png.

Design: rounded square with a red gradient, a pair of white stem earbuds, subtle highlight.
Rendered with 4x4 supersampling for smooth edges.
"""
import math
import struct
import sys
import zlib

RED_TOP, RED_BOT = (0xFF, 0x3B, 0x4F), (0xC4, 0x00, 0x1E)


def sd_round_rect(x, y, cx, cy, hw, hh, r):
    qx, qy = abs(x - cx) - hw + r, abs(y - cy) - hh + r
    return math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - r


def sd_capsule(x, y, ax, ay, bx, by, r):
    px, py, vx, vy = x - ax, y - ay, bx - ax, by - ay
    t = max(0.0, min(1.0, (px * vx + py * vy) / (vx * vx + vy * vy)))
    return math.hypot(px - vx * t, py - vy * t) - r


def earbud(x, y, mirror):
    """Signed distance to one earbud in a 100x100 design space (right bud; mirrored for left)."""
    if mirror:
        x = 100 - x
    head = math.hypot(x - 67, y - 40) - 12.0
    stem = sd_capsule(x, y, 71, 45, 74, 77, 5.0)
    tip = math.hypot(x - 58.5, y - 36.5) - 5.5   # ear tip
    d = min(head, stem)
    return d, tip


def sample(x, y):
    """Return premultiplied RGBA (0..1) at design coords 0..100."""
    bg = sd_round_rect(x, y, 50, 50, 46, 46, 22)
    if bg > 0:
        return (0, 0, 0, 0)
    t = y / 100
    col = [RED_TOP[i] + (RED_BOT[i] - RED_TOP[i]) * t for i in range(3)]
    # soft top highlight
    hl = max(0.0, 1 - (y - 4) / 40) * 0.10
    col = [c + (255 - c) * hl for c in col]
    for mirror in (False, True):
        d, tip = earbud(x, y, mirror)
        if tip <= 0:
            col = [235, 237, 242]                    # light grey ear tip
        elif d <= 0:
            col = [255, 255, 255]
        elif d <= 2.2 and not mirror or (d <= 2.2 and mirror):
            pass
    return (col[0] / 255, col[1] / 255, col[2] / 255, 1.0)


def render(n, ss=4):
    rows = []
    for py in range(n):
        row = []
        for px in range(n):
            acc = [0.0, 0.0, 0.0, 0.0]
            for sy in range(ss):
                for sx in range(ss):
                    x = (px + (sx + .5) / ss) / n * 100
                    y = (py + (sy + .5) / ss) / n * 100
                    r, g, b, a = sample(x, y)
                    acc[0] += r * a; acc[1] += g * a; acc[2] += b * a; acc[3] += a
            k = ss * ss
            a = acc[3] / k
            if a > 0:
                row.append((int(acc[0] / acc[3] * 255 + .5), int(acc[1] / acc[3] * 255 + .5),
                            int(acc[2] / acc[3] * 255 + .5), int(a * 255 + .5)))
            else:
                row.append((0, 0, 0, 0))
        rows.append(row)
    return rows


def png(rows):
    n = len(rows)
    raw = b"".join(b"\0" + bytes(c for p in r for c in p) for r in rows)
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", n, n, 8, 6, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def bmp_entry(rows):
    n = len(rows)
    xor = b"".join(bytes((p[2], p[1], p[0], p[3])) for r in reversed(rows) for p in r)
    andmask = b"\0" * (((n + 31) // 32) * 4) * n
    return struct.pack("<IiiHHIIiiII", 40, n, n * 2, 1, 32, 0, 0, 0, 0, 0, 0) + xor + andmask


def main(out_dir):
    images = []
    for n in (256, 64, 48, 32, 24, 16):
        rows = render(n, 2 if n == 256 else 4)
        images.append((n, bmp_entry(rows)))          # classic BMP entries: Explorer reads these everywhere
        if n == 256:
            open(f"{out_dir}/nordbuds.png", "wb").write(png(rows))
    ico = struct.pack("<HHH", 0, 1, len(images))
    off = 6 + 16 * len(images)
    for n, data in images:
        ico += struct.pack("<BBBBHHII", n % 256, n % 256, 0, 0, 1, 32, len(data), off)
        off += len(data)
    ico += b"".join(d for _, d in images)
    open(f"{out_dir}/nordbuds.ico", "wb").write(ico)
    print("icon written", len(ico), "bytes")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
