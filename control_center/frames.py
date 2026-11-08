"""Tiny stdlib PNG writer and an overhead-table sketch for the demo stream.

Real sessions save the actual 320x176 frame they upload; this only draws a
stand-in so the demo has something honest-looking to show (it is labelled as
a demo in the UI).
"""
import math
import struct
import zlib

WIDTH, HEIGHT = 320, 176


def png_bytes(width, height, rgb):
    """rgb: bytearray of width*height*3."""
    raw = bytearray()
    row = width * 3
    for y in range(height):
        raw.append(0)
        raw.extend(rgb[y * row:(y + 1) * row])

    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff)

    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(bytes(raw), 6)) + chunk(b'IEND', b''))


class Canvas:
    def __init__(self, width=WIDTH, height=HEIGHT, color=(30, 32, 36)):
        self.w, self.h = width, height
        self.px = bytearray(color) * (width * height)

    def rect(self, x0, y0, x1, y1, color):
        for y in range(max(0, int(y0)), min(self.h, int(y1))):
            start = (y * self.w + max(0, int(x0))) * 3
            end = (y * self.w + min(self.w, int(x1))) * 3
            if end > start:
                self.px[start:end] = bytes(color) * ((end - start) // 3)

    def disc(self, cx, cy, r, color, ring=None):
        for y in range(max(0, int(cy - r)), min(self.h, int(cy + r) + 1)):
            for x in range(max(0, int(cx - r)), min(self.w, int(cx + r) + 1)):
                d = math.hypot(x - cx, y - cy)
                if d <= r:
                    c = ring if ring and d > r - 2 else color
                    i = (y * self.w + x) * 3
                    self.px[i:i + 3] = bytes(c)

    def line(self, x0, y0, x1, y1, width, color):
        steps = int(max(abs(x1 - x0), abs(y1 - y0))) + 1
        for s in range(steps + 1):
            t = s / steps
            self.disc(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, width / 2, color)

    def png(self, swap_red_blue=False):
        px = self.px
        if swap_red_blue:
            px = bytearray(px)
            px[0::3], px[2::3] = self.px[2::3], self.px[0::3]
        return png_bytes(self.w, self.h, px)


def overhead(tcp_xy, grip_open, cups, held=None, lift=0.0):
    """Head-camera sketch. tcp_xy and cups are 0..1 table coordinates."""
    c = Canvas()
    c.rect(16, 14, 304, 162, (196, 170, 132))          # tabletop
    c.rect(16, 14, 304, 18, (170, 146, 112))
    for x in range(16, 304, 24):                        # wood grain
        c.rect(x, 18, x + 1, 162, (186, 160, 124))
    base = (40, 150)
    for i, (cx, cy) in enumerate(cups):
        if held == i:
            continue
        c.disc(16 + cx * 288, 14 + cy * 148, 11, (232, 236, 240), ring=(150, 160, 172))
    tx, ty = 16 + tcp_xy[0] * 288, 14 + tcp_xy[1] * 148
    elbow = ((base[0] + tx) / 2 - 18, (base[1] + ty) / 2 + 10)
    c.line(base[0], base[1], elbow[0], elbow[1], 12, (64, 70, 82))
    c.line(elbow[0], elbow[1], tx, ty, 10, (78, 86, 100))
    c.disc(base[0], base[1], 14, (44, 48, 56))
    if held is not None:
        r = 11 + lift * 18                                # a lifted cup looks bigger from above
        c.disc(tx + 6, ty - 4, r, (240, 244, 248), ring=(150, 160, 172))
    spread = 12 if grip_open else 7
    ang = math.atan2(ty - elbow[1], tx - elbow[0]) + math.pi / 2
    for side in (-1, 1):
        fx, fy = tx + math.cos(ang) * spread * side, ty + math.sin(ang) * spread * side
        c.line(tx, ty, fx, fy, 5, (232, 104, 52))
    return c
