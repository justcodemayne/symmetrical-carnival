from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path


def decode(path: Path):
    data = path.read_bytes()
    pos = 8
    idat = b""
    width = height = 0
    while pos < len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        tag = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        if tag == b"IHDR":
            width, height = struct.unpack(">II", body[:8])
        elif tag == b"IDAT":
            idat += body
        pos += 12 + length
    raw = zlib.decompress(idat)
    stride = width * 4
    rows: list[bytes] = []
    prev = bytearray(stride)
    offset = 0
    for _ in range(height):
        filt = raw[offset]
        offset += 1
        line = bytearray(raw[offset:offset + stride])
        offset += stride
        for x in range(stride):
            a = line[x - 4] if x >= 4 else 0
            b = prev[x]
            c = prev[x - 4] if x >= 4 else 0
            if filt == 1:
                line[x] = (line[x] + a) & 255
            elif filt == 2:
                line[x] = (line[x] + b) & 255
            elif filt == 3:
                line[x] = (line[x] + (a + b) // 2) & 255
            elif filt == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[x] = (line[x] + pred) & 255
        rows.append(bytes(line))
        prev = line

    def px(x: int, y: int):
        base = x * 4
        row = rows[y]
        return row[base], row[base + 1], row[base + 2], row[base + 3]

    return width, height, px


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "build/GasWatch.png")
    width, height, px = decode(path)
    print(f"size {width}x{height}")
    checks = [
        ("corner", (2, 2), 0),
        ("edge midpoint", (512, 1), None),
        ("square interior", (150, 512), 255),
        ("centre", (512, 512), 255),
        ("outside top-left", (0, 0), 0),
    ]
    failures = 0
    for label, point, expected in checks:
        value = px(*point)
        status = "ok"
        if expected is not None and value[3] != expected:
            status = f"FAIL expected alpha {expected}"
            failures += 1
        print(f"  {label:<20} rgba={value}  {status}")
    for label, point in [("drop top", (512, 330)), ("drop mid", (512, 600)), ("drop bottom", (512, 780))]:
        print(f"  {label:<20} rgba={px(*point)}")
    step = 8
    grid_y = list(range(0, height, step))
    grid_x = list(range(0, width, step))
    total = len(grid_x) * len(grid_y)
    transparent = sum(1 for y in grid_y for x in grid_x if px(x, y)[3] < 10)
    opaque = sum(1 for y in grid_y for x in grid_x if px(x, y)[3] > 245)
    print(f"  transparent {transparent / total:.1%}  opaque {opaque / total:.1%}")
    top = px(512, 330)
    bottom = px(512, 780)
    green_top = top[1] > top[0] and top[3] > 200
    red_bottom = bottom[0] > bottom[1] and bottom[3] > 200
    print(f"  gradient green top {green_top}   red bottom {red_bottom}")
    if transparent / total > 0.25:
        print("  FAIL most of the canvas is transparent")
        failures += 1
    if opaque / total < 0.55:
        print("  FAIL rounded square is too small")
        failures += 1
    if not green_top or not red_bottom:
        print("  FAIL drop gradient colours missing")
        failures += 1
    print("ICON OK" if not failures else f"ICON PROBLEMS: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())