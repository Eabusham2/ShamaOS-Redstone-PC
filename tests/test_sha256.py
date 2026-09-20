import hashlib

from shamaos.sha256 import (
    bsig0, bsig1, ch, double_sha256, hash_meets_target, maj, sha256, ssig0, ssig1
)


def test_sha256_known_vectors():
    for data in (b"", b"abc", b"a" * 1000):
        assert sha256(data) == hashlib.sha256(data).digest()


def test_double_sha256():
    data = bytes(range(80))
    expected = hashlib.sha256(hashlib.sha256(data).digest()).digest()
    assert double_sha256(data) == expected


def test_boolean_primitives_are_32_bit():
    x, y, z = 0x12345678, 0xA5A5A5A5, 0x0F0F0F0F
    for value in (ch(x,y,z), maj(x,y,z), bsig0(x), bsig1(x), ssig0(x), ssig1(x)):
        assert 0 <= value <= 0xFFFFFFFF


def test_target_compare():
    assert hash_meets_target(bytes(32), b"\xff" * 32)
    assert not hash_meets_target(b"\xff" * 32, bytes(32))
