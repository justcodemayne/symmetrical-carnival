from __future__ import annotations

import math
import struct
import sys
import zlib
from pathlib import Path

SIZE = 1024
BG_TOP = (17, 28, 51)
BG_BOTTOM = (8, 14, 28)
GREEN = (34, 197, 94)
AMBER = (234, 179, 8)
RED = (239, 68, 68)


def write_png(path: Path, pixels: bytearray, size: int = SIZE) -> None:
    raw = bytearray()
    stride = size * 4
    for y in range(size):
        raw.append(0)
        raw.extend(pixels[y * stride:(y + 1) * stride])

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)


def mix(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    t = max(0.0, min(1.0, t))
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def fuel_color(t: float) -> tuple[int, int, int]:
    if t < 0.5:
        return mix(GREEN, AMBER, t / 0.5)
    return mix(AMBER, RED, (t - 0.5) / 0.5)


def drop_sdf(x: float, y: float) -> float:
    radius = 300.0
    cx = SIZE / 2
    cy = SIZE / 2 + 120.0
    head = math.hypot(x - cx, y - (cy - radius * 0.55))
    body_dx = abs(x - cx)
    body_dy = y - (cy - radius * 0.55)
    cone_len = radius * 1.45
    if body_dy < 0:
        return head - radius * 0.42
    if body_dy < cone_len:
        spread = radius * 0.42 * (body_dy / cone_len)
        return body_dx - spread
    circle = math.hypot(x - cx, y - (cy + radius * 0.9))
    return circle - radius * 0.42


def smooth(edge: float, feather: float = 3.0) -> float:
    if feather <= 0:
        return 1.0 if edge <= 0 else 0.0
    return max(0.0, min(1.0, 0.5 - edge / feather))


def rounded_square_sdf(x: float, y: float, inset: float, radius: float) -> float:
    hx = SIZE / 2 - inset - radius
    hy = SIZE / 2 - inset - radius
    dx = abs(x - SIZE / 2) - hx
    dy = abs(y - SIZE / 2) - hy
    ox = max(dx, 0.0)
    oy = max(dy, 0.0)
    outside = math.hypot(ox, oy)
    inside = min(max(dx, dy), 0.0)
    return outside + inside - radius


def build(out_png: Path) -> None:
    pixels = bytearray(SIZE * SIZE * 4)
    for py in range(SIZE):
        for px in range(SIZE):
            x = px + 0.5
            y = py + 0.5
            base = mix(BG_TOP, BG_BOTTOM, y / SIZE)
            alpha = smooth(rounded_square_sdf(x, y, 0.0, 230.0))
            color = base
            drop_alpha = smooth(drop_sdf(x, y))
            if drop_alpha > 0:
                t = max(0.0, min(1.0, (y - 180) / 640))
                drop = fuel_color(t)
                color = tuple(
                    round(color[i] + (drop[i] - color[i]) * drop_alpha) for i in range(3)
                )
            index = (py * SIZE + px) * 4
            pixels[index] = color[0]
            pixels[index + 1] = color[1]
            pixels[index + 2] = color[2]
            pixels[index + 3] = round(255 * alpha)
    write_png(out_png, pixels)


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("build/GasWatch.png")
    build(target)
    print(f"wrote {target}")