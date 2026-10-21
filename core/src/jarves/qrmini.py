"""Minimal QR Code encoder (byte mode, error correction L, versions 1-6) for the phone pairing link (V5).

No dependency: the pairing URL is short (<= 134 bytes). `matrix(text)` returns rows of booleans (True = dark, no quiet
zone); the dashboard draws them as SVG. Algorithm follows ISO/IEC 18004 (Reed-Solomon over GF(256), poly 0x11D).
"""
from __future__ import annotations

# version -> (total codewords, EC codewords per block, number of blocks); ECC level L
_VER = {1: (26, 7, 1), 2: (44, 10, 1), 3: (70, 15, 1), 4: (100, 20, 1), 5: (134, 26, 1), 6: (172, 18, 2)}
_ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34]}
MAX_BYTES = 134


def _mul(a: int, b: int) -> int:
    r = 0
    while b:
        if b & 1:
            r ^= a
        a <<= 1
        if a & 0x100:
            a ^= 0x11D
        b >>= 1
    return r


def _rs(data: list[int], degree: int) -> list[int]:
    gen = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            gen[j] = _mul(gen[j], root)
            if j + 1 < degree:
                gen[j] ^= gen[j + 1]
        root = _mul(root, 2)
    rem = [0] * degree
    for b in data:
        f = b ^ rem.pop(0)
        rem.append(0)
        for i, c in enumerate(gen):
            rem[i] ^= _mul(c, f)
    return rem


def _codewords(payload: bytes, ver: int) -> list[int]:
    total, ec, blocks = _VER[ver]
    ndata = total - ec * blocks
    bits = "0100" + format(len(payload), "08b") + "".join(format(b, "08b") for b in payload)
    bits += "0" * min(4, ndata * 8 - len(bits))
    bits += "0" * (-len(bits) % 8)
    data = [int(bits[i:i + 8], 2) for i in range(0, len(bits), 8)]
    pad = (0xEC, 0x11)
    while len(data) < ndata:
        data.append(pad[(len(data) - len(bits) // 8) % 2])
    per = ndata // blocks
    chunks = [data[i * per:(i + 1) * per] for i in range(blocks)]
    eccs = [_rs(c, ec) for c in chunks]
    out = [c[i] for i in range(per) for c in chunks]
    out += [e[i] for i in range(ec) for e in eccs]
    return out


def _penalty(m: list[list[bool]]) -> int:
    n, score = len(m), 0
    for grid in (m, [list(r) for r in zip(*m)]):
        for row in grid:
            run, prev = 1, row[0]
            for v in row[1:]:
                if v == prev:
                    run += 1
                else:
                    score += run - 2 if run >= 5 else 0
                    run, prev = 1, v
            score += run - 2 if run >= 5 else 0
            s = "".join("1" if v else "0" for v in row)
            score += 40 * (s.count("10111010000") + s.count("00001011101"))
    for y in range(n - 1):
        for x in range(n - 1):
            if m[y][x] == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                score += 3
    dark = sum(v for r in m for v in r)
    score += 10 * (abs(dark * 20 - n * n * 10) // (n * n))
    return score


def matrix(text: str) -> list[list[bool]]:
    payload = text.encode("utf-8")
    if len(payload) > MAX_BYTES:
        raise ValueError("text too long for the built-in QR encoder")
    ver = next(v for v in _VER if len(payload) <= _VER[v][0] - _VER[v][1] * _VER[v][2] - 2)
    size = 17 + 4 * ver
    mod = [[False] * size for _ in range(size)]
    fn = [[False] * size for _ in range(size)]

    def setf(x: int, y: int, dark: bool) -> None:
        if 0 <= x < size and 0 <= y < size:
            mod[y][x] = dark
            fn[y][x] = True

    for i in range(size):
        setf(6, i, i % 2 == 0)
        setf(i, 6, i % 2 == 0)
    for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                setf(cx + dx, cy + dy, max(abs(dx), abs(dy)) not in (2, 4))
    pos = _ALIGN[ver]
    for i, ax in enumerate(pos):
        for j, ay in enumerate(pos):
            if (i == 0 and j == 0) or (i == 0 and j == len(pos) - 1) or (i == len(pos) - 1 and j == 0):
                continue
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    setf(ax + dx, ay + dy, max(abs(dx), abs(dy)) != 1)

    def format_bits(mask: int) -> None:
        d = (1 << 3) | mask                      # ECC level L = 01
        rem = d
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = ((d << 10) | rem) ^ 0x5412
        b = lambda i: ((bits >> i) & 1) != 0
        for i in range(6):
            setf(8, i, b(i))
        setf(8, 7, b(6))
        setf(8, 8, b(7))
        setf(7, 8, b(8))
        for i in range(9, 15):
            setf(14 - i, 8, b(i))
        for i in range(8):
            setf(size - 1 - i, 8, b(i))
        for i in range(8, 15):
            setf(8, size - 15 + i, b(i))
        setf(8, size - 8, True)

    format_bits(0)                               # reserve the format areas
    cw = _codewords(payload, ver)
    i = 0
    right = size - 1
    while right >= 1:
        if right == 6:
            right = 5
        for vert in range(size):
            for j in range(2):
                x = right - j
                y = size - 1 - vert if ((right + 1) & 2) == 0 else vert
                if not fn[y][x] and i < len(cw) * 8:
                    mod[y][x] = ((cw[i >> 3] >> (7 - (i & 7))) & 1) != 0
                    i += 1
        right -= 2
    masks = (lambda x, y: (x + y) % 2 == 0, lambda x, y: y % 2 == 0, lambda x, y: x % 3 == 0,
             lambda x, y: (x + y) % 3 == 0, lambda x, y: (x // 3 + y // 2) % 2 == 0,
             lambda x, y: x * y % 2 + x * y % 3 == 0, lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
             lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0)
    best, best_score = None, None
    for k, f in enumerate(masks):
        trial = [[mod[y][x] ^ (f(x, y) and not fn[y][x]) for x in range(size)] for y in range(size)]
        save = [r[:] for r in mod]
        mod[:] = trial
        format_bits(k)
        score = _penalty(mod)
        if best_score is None or score < best_score:
            best, best_score = [r[:] for r in mod], score
        mod[:] = save
    return best


def rows(text: str) -> list[str]:
    """The matrix as strings of '1'/'0' (compact JSON for the dashboard)."""
    return ["".join("1" if v else "0" for v in r) for r in matrix(text)]
