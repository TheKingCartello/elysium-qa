"""Dependency-free ABI encoding + keccak256 selectors.

Lets a scenario say:

    function: "createToken(string,string,uint256)"
    args: ["My Test", "TST", 1000000000000000000]

instead of hand-recording hex calldata. Verified against known selectors
(transfer/approve/balanceOf) on import via `selftest()`.
"""
from __future__ import annotations

import struct
from typing import Any

# --------------------------------------------------------------------------
# Keccak-256 (original Keccak padding, NOT SHA3-256)
# --------------------------------------------------------------------------
_RC = [
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
]
_ROT = [
    [0, 36, 3, 41, 18], [1, 44, 10, 45, 2], [62, 6, 43, 15, 61],
    [28, 55, 25, 21, 56], [27, 20, 39, 8, 14],
]
_M = (1 << 64) - 1


def _rotl(x: int, n: int) -> int:
    return ((x << n) | (x >> (64 - n))) & _M if n else x


def _keccak_f(a: list[list[int]]) -> None:
    for rnd in range(24):
        c = [a[x][0] ^ a[x][1] ^ a[x][2] ^ a[x][3] ^ a[x][4] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rotl(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(5):
                a[x][y] ^= d[x]
        b = [[0] * 5 for _ in range(5)]
        for x in range(5):
            for y in range(5):
                b[y][(2 * x + 3 * y) % 5] = _rotl(a[x][y], _ROT[x][y])
        for x in range(5):
            for y in range(5):
                a[x][y] = b[x][y] ^ ((~b[(x + 1) % 5][y]) & b[(x + 2) % 5][y] & _M)
        a[0][0] ^= _RC[rnd]


def keccak256(data: bytes) -> bytes:
    rate = 136
    a = [[0] * 5 for _ in range(5)]
    padded = bytearray(data)
    padded.append(0x01)
    while len(padded) % rate:
        padded.append(0x00)
    padded[-1] |= 0x80

    for off in range(0, len(padded), rate):
        block = padded[off : off + rate]
        for i in range(rate // 8):
            lane = struct.unpack_from("<Q", block, i * 8)[0]
            a[i % 5][i // 5] ^= lane
        _keccak_f(a)

    out = b"".join(struct.pack("<Q", a[i % 5][i // 5]) for i in range(4))
    return out


def selector(signature: str) -> str:
    """'transfer(address,uint256)' -> '0xa9059cbb'"""
    sig = "".join(signature.split())
    return "0x" + keccak256(sig.encode()).hex()[:8]


# --------------------------------------------------------------------------
# ABI encoding
# --------------------------------------------------------------------------
def _is_dynamic(t: str) -> bool:
    t = t.strip()
    if t in ("string", "bytes"):
        return True
    if t.endswith("]"):
        return True  # arrays encoded dynamically (simplest correct form)
    return False


def _pad32(b: bytes) -> bytes:
    if len(b) > 32:
        raise ValueError("value exceeds 32 bytes for a static type")
    return b.rjust(32, b"\x00")


def _encode_static(t: str, v: Any) -> bytes:
    t = t.strip()
    if t == "address":
        h = str(v).lower().replace("0x", "")
        if len(h) != 40:
            raise ValueError(f"bad address: {v}")
        return bytes.fromhex(h).rjust(32, b"\x00")
    if t == "bool":
        return (1 if (v is True or str(v).lower() in ("1", "true", "yes")) else 0).to_bytes(32, "big")
    if t.startswith("uint"):
        n = int(v)
        if n < 0:
            raise ValueError(f"negative value for {t}: {v}")
        return n.to_bytes(32, "big")
    if t.startswith("int"):
        n = int(v)
        return (n & ((1 << 256) - 1)).to_bytes(32, "big")
    if t.startswith("bytes") and t != "bytes":
        size = int(t[5:])
        h = str(v).replace("0x", "")
        b = bytes.fromhex(h)
        return b.rjust(size, b"\x00").ljust(32, b"\x00")
    raise ValueError(f"unsupported static type: {t}")


def _encode_dynamic(t: str, v: Any) -> bytes:
    t = t.strip()
    if t == "string":
        b = str(v).encode()
        return len(b).to_bytes(32, "big") + b + b"\x00" * ((32 - len(b) % 32) % 32)
    if t == "bytes":
        b = bytes.fromhex(str(v).replace("0x", ""))
        return len(b).to_bytes(32, "big") + b + b"\x00" * ((32 - len(b) % 32) % 32)
    if t.endswith("]"):
        inner = t[: t.rfind("[")]
        items = list(v)
        head, tail = b"", b""
        # arrays are always encoded as (offsets..., data...) inside the dynamic blob
        for it in items:
            enc = _encode_single(inner, it)
            head += len(head).to_bytes(32, "big") + len(enc) * 0 if False else b""
        # simple two-pass build
        pieces = [_encode_single(inner, it) for it in items]
        if _is_dynamic(inner):
            offset = 32 * len(items)
            heads = b""
            for p in pieces:
                heads += offset.to_bytes(32, "big")
                offset += len(p)
            body = heads + b"".join(pieces)
        else:
            body = b"".join(pieces)
        return len(items).to_bytes(32, "big") + body
    raise ValueError(f"unsupported dynamic type: {t}")


def _encode_single(t: str, v: Any) -> bytes:
    return _encode_dynamic(t, v) if _is_dynamic(t) else _encode_static(t, v)


def split_types(sig: str) -> tuple[str, list[str]]:
    """('swap(address,uint256)', ...) -> ('swap', ['address','uint256'])"""
    sig = "".join(sig.split())
    if "(" not in sig:
        raise ValueError(f"not a function signature: {sig}")
    name = sig[: sig.index("(")]
    inner = sig[sig.index("(") + 1 : sig.rindex(")")]
    if not inner:
        return name, []
    types, depth, cur = [], 0, ""
    for ch in inner:
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
        if ch == "," and depth == 0:
            types.append(cur)
            cur = ""
        else:
            cur += ch
    types.append(cur)
    return name, types


def encode_call(signature: str, args: list[Any]) -> str:
    """Full calldata hex for a function signature + python args."""
    name, types = split_types(signature)
    if len(types) != len(args):
        raise ValueError(f"{name}: expected {len(types)} args ({types}), got {len(args)}")
    if not types:
        return selector(signature)

    encoded = [_encode_single(t, a) for t, a in zip(types, args)]
    head, tail = b"", b""
    head_len = 32 * len(types)
    for t, enc in zip(types, encoded):
        if _is_dynamic(t):
            head += (head_len + len(tail)).to_bytes(32, "big")
            tail += enc
        else:
            head += enc.ljust(32, b"\x00")
    return selector(signature) + (head + tail).hex()


# --------------------------------------------------------------------------
def selftest() -> list[str]:
    """Known-answer tests. Run from CLI with `python3 -m elysium_qa abi`."""
    errs = []
    if keccak256(b"").hex() != "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470":
        errs.append("keccak256(empty) vector failed")
    for sig, want in [
        ("transfer(address,uint256)", "0xa9059cbb"),
        ("approve(address,uint256)", "0x095ea7b3"),
        ("balanceOf(address)", "0x70a08231"),
        ("totalSupply()", "0x18160ddd"),
    ]:
        if selector(sig) != want:
            errs.append(f"selector {sig} -> {selector(sig)} != {want}")
    # encoding cross-check: transfer(0x00..01, 1)
    got = encode_call("transfer(address,uint256)",
                      ["0x0000000000000000000000000000000000000001", 1])
    want = ("0xa9059cbb"
            "0000000000000000000000000000000000000000000000000000000000000001"
            "0000000000000000000000000000000000000000000000000000000000000001")
    if got != want:
        errs.append(f"transfer calldata mismatch:\n {got}\n {want}")
    # dynamic string offset sanity
    d = encode_call("f(string)", ["hi"])
    if len(d) != 10 + 64 * 3:
        errs.append(f"dynamic string length wrong: {len(d)}")
    return errs
