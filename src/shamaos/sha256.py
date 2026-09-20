from __future__ import annotations

from dataclasses import dataclass

MASK32 = 0xFFFFFFFF

K = (
    0x428A2F98, 0x71374491, 0xB5C0FBCF, 0xE9B5DBA5,
    0x3956C25B, 0x59F111F1, 0x923F82A4, 0xAB1C5ED5,
    0xD807AA98, 0x12835B01, 0x243185BE, 0x550C7DC3,
    0x72BE5D74, 0x80DEB1FE, 0x9BDC06A7, 0xC19BF174,
    0xE49B69C1, 0xEFBE4786, 0x0FC19DC6, 0x240CA1CC,
    0x2DE92C6F, 0x4A7484AA, 0x5CB0A9DC, 0x76F988DA,
    0x983E5152, 0xA831C66D, 0xB00327C8, 0xBF597FC7,
    0xC6E00BF3, 0xD5A79147, 0x06CA6351, 0x14292967,
    0x27B70A85, 0x2E1B2138, 0x4D2C6DFC, 0x53380D13,
    0x650A7354, 0x766A0ABB, 0x81C2C92E, 0x92722C85,
    0xA2BFE8A1, 0xA81A664B, 0xC24B8B70, 0xC76C51A3,
    0xD192E819, 0xD6990624, 0xF40E3585, 0x106AA070,
    0x19A4C116, 0x1E376C08, 0x2748774C, 0x34B0BCB5,
    0x391C0CB3, 0x4ED8AA4A, 0x5B9CCA4F, 0x682E6FF3,
    0x748F82EE, 0x78A5636F, 0x84C87814, 0x8CC70208,
    0x90BEFFFA, 0xA4506CEB, 0xBEF9A3F7, 0xC67178F2,
)

IV = (
    0x6A09E667, 0xBB67AE85, 0x3C6EF372, 0xA54FF53A,
    0x510E527F, 0x9B05688C, 0x1F83D9AB, 0x5BE0CD19,
)


def rotr(x: int, n: int) -> int:
    n &= 31
    x &= MASK32
    return ((x >> n) | (x << (32 - n))) & MASK32 if n else x


def ch(x: int, y: int, z: int) -> int:
    return ((x & y) ^ ((~x) & z)) & MASK32


def maj(x: int, y: int, z: int) -> int:
    return ((x & y) ^ (x & z) ^ (y & z)) & MASK32


def bsig0(x: int) -> int:
    return rotr(x, 2) ^ rotr(x, 13) ^ rotr(x, 22)


def bsig1(x: int) -> int:
    return rotr(x, 6) ^ rotr(x, 11) ^ rotr(x, 25)


def ssig0(x: int) -> int:
    return rotr(x, 7) ^ rotr(x, 18) ^ ((x & MASK32) >> 3)


def ssig1(x: int) -> int:
    return rotr(x, 17) ^ rotr(x, 19) ^ ((x & MASK32) >> 10)


def _pad(message: bytes) -> bytes:
    bit_len = len(message) * 8
    out = bytearray(message)
    out.append(0x80)
    while (len(out) % 64) != 56:
        out.append(0)
    out.extend(bit_len.to_bytes(8, "big"))
    return bytes(out)


def compress(state: tuple[int, ...], block: bytes) -> tuple[int, ...]:
    if len(block) != 64:
        raise ValueError("SHA-256 block must be exactly 64 bytes")

    w = [0] * 64
    for i in range(16):
        w[i] = int.from_bytes(block[i * 4 : i * 4 + 4], "big")
    for i in range(16, 64):
        w[i] = (ssig1(w[i - 2]) + w[i - 7] + ssig0(w[i - 15]) + w[i - 16]) & MASK32

    a, b, c, d, e, f, g, h = state

    for i in range(64):
        t1 = (h + bsig1(e) + ch(e, f, g) + K[i] + w[i]) & MASK32
        t2 = (bsig0(a) + maj(a, b, c)) & MASK32
        h = g
        g = f
        f = e
        e = (d + t1) & MASK32
        d = c
        c = b
        b = a
        a = (t1 + t2) & MASK32

    working = (a, b, c, d, e, f, g, h)
    return tuple((x + y) & MASK32 for x, y in zip(state, working))


def sha256(message: bytes) -> bytes:
    state = IV
    padded = _pad(message)
    for i in range(0, len(padded), 64):
        state = compress(state, padded[i : i + 64])
    return b"".join(word.to_bytes(4, "big") for word in state)


def double_sha256(message: bytes) -> bytes:
    return sha256(sha256(message))


def hash_meets_target(hash_bytes: bytes, target: bytes, *, byteorder: str = "big") -> bool:
    if len(hash_bytes) != 32 or len(target) != 32:
        raise ValueError("hash and target must both be 32 bytes")
    return int.from_bytes(hash_bytes, byteorder) <= int.from_bytes(target, byteorder)


@dataclass
class MiningState:
    header: bytearray
    target: bytes
    nonce_offset: int = 76
    nonce_byteorder: str = "little"
    attempts: int = 0
    generation: int = 0
    last_hash: bytes = bytes(32)

    def nonce(self) -> int:
        return int.from_bytes(
            self.header[self.nonce_offset : self.nonce_offset + 4],
            self.nonce_byteorder,
        )

    def set_nonce(self, value: int) -> None:
        self.header[self.nonce_offset : self.nonce_offset + 4] = (
            value & MASK32
        ).to_bytes(4, self.nonce_byteorder)

    def step(self) -> bool:
        if len(self.header) != 80:
            raise ValueError("Bitcoin-style header must be 80 bytes")
        self.last_hash = double_sha256(bytes(self.header))
        self.attempts += 1
        ok = hash_meets_target(self.last_hash, self.target)
        if not ok:
            self.set_nonce(self.nonce() + 1)
        return ok
