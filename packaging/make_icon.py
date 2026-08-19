"""
Generate packaging/walkman-bridge.ico — a cassette glyph in the app's palette.

Pure stdlib (zlib + struct): no Pillow dependency, and the icon stays
reproducible from source instead of being an opaque binary in the repo.

    python packaging/make_icon.py
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

SIZES = (16, 32, 48, 64, 128, 256)

# Dashboard palette: near-black shell, amber body, cyan reels.
BG = (10, 11, 13, 255)
AMBER = (245, 158, 11, 255)
AMBER_DIM = (146, 94, 7, 255)
CYAN = (34, 211, 238, 255)
INK = (10, 11, 13, 255)
TRANSPARENT = (0, 0, 0, 0)


def render(size: int) -> list[list[tuple[int, int, int, int]]]:
    """Draw the cassette at `size`, supersampled 4x for smooth edges."""
    ss = 4
    n = size * ss
    hi = [[TRANSPARENT for _ in range(n)] for _ in range(n)]

    def fill(x0: float, y0: float, x1: float, y1: float, color, radius: float = 0.0):
        for y in range(max(0, int(y0)), min(n, int(y1) + 1)):
            for x in range(max(0, int(x0)), min(n, int(x1) + 1)):
                if radius:
                    # rounded-rect: check corner circles
                    cx = min(max(x, x0 + radius), x1 - radius)
                    cy = min(max(y, y0 + radius), y1 - radius)
                    if (x - cx) ** 2 + (y - cy) ** 2 > radius * radius:
                        continue
                hi[y][x] = color

    def disc(cx: float, cy: float, r: float, color):
        for y in range(max(0, int(cy - r)), min(n, int(cy + r) + 1)):
            for x in range(max(0, int(cx - r)), min(n, int(cx + r) + 1)):
                if (x - cx) ** 2 + (y - cy) ** 2 <= r * r:
                    hi[y][x] = color

    u = n / 32.0  # design grid: 32x32 units

    # Shell
    fill(2 * u, 6 * u, 30 * u, 26 * u, BG, radius=2.5 * u)
    fill(2.8 * u, 6.8 * u, 29.2 * u, 25.2 * u, AMBER, radius=2.0 * u)
    # Label area
    fill(5 * u, 9 * u, 27 * u, 18 * u, INK, radius=1.0 * u)
    # Reel windows
    disc(11 * u, 13.5 * u, 3.2 * u, AMBER_DIM)
    disc(21 * u, 13.5 * u, 3.2 * u, AMBER_DIM)
    disc(11 * u, 13.5 * u, 1.6 * u, CYAN)
    disc(21 * u, 13.5 * u, 1.6 * u, CYAN)
    # Tape window between the reels
    fill(14.5 * u, 12.5 * u, 17.5 * u, 14.5 * u, AMBER_DIM)
    # Bottom deck detail
    fill(9 * u, 20.5 * u, 23 * u, 22.5 * u, INK, radius=0.8 * u)

    # Downsample (box filter) for anti-aliasing
    out = []
    for y in range(size):
        row = []
        for x in range(size):
            r = g = b = a = 0
            for dy in range(ss):
                for dx in range(ss):
                    pr, pg, pb, pa = hi[y * ss + dy][x * ss + dx]
                    r += pr * pa
                    g += pg * pa
                    b += pb * pa
                    a += pa
            if a:
                row.append((r // a, g // a, b // a, a // (ss * ss)))
            else:
                row.append(TRANSPARENT)
        out.append(row)
    return out


def to_png(pixels: list[list[tuple[int, int, int, int]]]) -> bytes:
    height = len(pixels)
    width = len(pixels[0])
    raw = b"".join(
        b"\x00" + b"".join(struct.pack("BBBB", *px) for px in row) for row in pixels
    )

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def main() -> None:
    images = [(size, to_png(render(size))) for size in SIZES]

    # ICO: header + one directory entry per image, then the PNG payloads.
    # PNG-in-ICO is supported from Windows Vista onward.
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries, payloads = b"", b""
    for size, png in images:
        dim = 0 if size >= 256 else size  # 0 means 256 in the ICO format
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset)
        payloads += png
        offset += len(png)

    out = Path(__file__).with_name("walkman-bridge.ico")
    out.write_bytes(header + entries + payloads)
    print(f"wrote {out} ({out.stat().st_size:,} bytes, sizes: {list(SIZES)})")


if __name__ == "__main__":
    main()
