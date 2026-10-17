"""BIP-340 Schnorr over secp256k1, the signature scheme Nostr is built on.

Shared by the tools in this directory: `new_identity.py` needs x-only public
keys, `attest.py` needs signing. Standard library only, per the collection's
tooling convention, which is why the maths is here in full rather than pulled
from a dependency.

A wrong curve implementation fails silently and expensively - it would mint an
identity whose public key does not match its private key, or an attestation the
relay rejects without saying why - so `self_test()` checks this code against the
published BIP-340 vectors, and every caller runs it before printing anything.

    python3 bip340.py        # run the self-test alone
"""

import hashlib

P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
G = (0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
     0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8)


def _add(a, b):
    if a is None:
        return b
    if b is None:
        return a
    if a[0] == b[0] and (a[1] + b[1]) % P == 0:
        return None
    if a == b:
        lam = 3 * a[0] * a[0] * pow(2 * a[1], P - 2, P) % P
    else:
        lam = (b[1] - a[1]) * pow(b[0] - a[0], P - 2, P) % P
    x = (lam * lam - a[0] - b[0]) % P
    return (x, (lam * (a[0] - x) - a[1]) % P)


def _mul(k, point=G):
    r = None
    while k:
        if k & 1:
            r = _add(r, point)
        point = _add(point, point)
        k >>= 1
    return r


def _lift_x(x: int):
    """The even-y point with this x coordinate, or None if x is not on the curve."""
    if not 0 < x < P:
        return None
    y = pow(x * x * x + 7, (P + 1) // 4, P)
    if pow(y, 2, P) != (x * x * x + 7) % P:
        return None
    return (x, y if y % 2 == 0 else P - y)


def _tagged_hash(tag: str, msg: bytes) -> bytes:
    t = hashlib.sha256(tag.encode()).digest()
    return hashlib.sha256(t + t + msg).digest()


def public_key_hex(private_key_hex: str) -> str:
    """x-only (BIP-340) public key for a 32-byte private key."""
    d = int(private_key_hex, 16)
    if not 1 <= d < N:
        raise ValueError("private key out of range")
    return f"{_mul(d)[0]:064x}"


def sign(private_key_hex: str, message: bytes, aux_rand: bytes = bytes(32)) -> str:
    """Sign a 32-byte message, returning the 64-byte signature as hex.

    The signature is verified before it is returned: a caller that gets a value
    back got one this implementation agrees with.
    """
    if len(message) != 32:
        raise ValueError("BIP-340 signs a 32-byte message")
    if len(aux_rand) != 32:
        raise ValueError("aux_rand must be 32 bytes")
    d0 = int(private_key_hex, 16)
    if not 1 <= d0 < N:
        raise ValueError("private key out of range")
    point = _mul(d0)
    # x-only public keys carry no sign, so sign with whichever of d0 / -d0
    # belongs to the even-y point.
    d = d0 if point[1] % 2 == 0 else N - d0
    t = d ^ int.from_bytes(_tagged_hash("BIP0340/aux", aux_rand), "big")
    rand = _tagged_hash("BIP0340/nonce",
                        t.to_bytes(32, "big") + point[0].to_bytes(32, "big") + message)
    k0 = int.from_bytes(rand, "big") % N
    if k0 == 0:
        raise RuntimeError("nonce is zero, which cannot happen by chance")
    nonce_point = _mul(k0)
    k = k0 if nonce_point[1] % 2 == 0 else N - k0
    e = int.from_bytes(_tagged_hash("BIP0340/challenge",
                                    nonce_point[0].to_bytes(32, "big")
                                    + point[0].to_bytes(32, "big") + message),
                       "big") % N
    sig = (nonce_point[0].to_bytes(32, "big") + ((k + e * d) % N).to_bytes(32, "big")).hex()
    if not verify(f"{point[0]:064x}", message, sig):
        raise RuntimeError("signature does not verify against its own public key")
    return sig


def verify(public_key_hex_: str, message: bytes, signature_hex: str) -> bool:
    """True when the signature is a valid BIP-340 signature for this x-only key."""
    point = _lift_x(int(public_key_hex_, 16))
    if point is None or len(message) != 32:
        return False
    sig = bytes.fromhex(signature_hex)
    if len(sig) != 64:
        return False
    r = int.from_bytes(sig[:32], "big")
    s = int.from_bytes(sig[32:], "big")
    if r >= P or s >= N:
        return False
    e = int.from_bytes(_tagged_hash("BIP0340/challenge",
                                   sig[:32] + point[0].to_bytes(32, "big") + message),
                       "big") % N
    nonce_point = _add(_mul(s), _mul(N - e, point))
    return (nonce_point is not None
            and nonce_point[1] % 2 == 0
            and nonce_point[0] == r)


# BIP-340 test vectors 0 and 1, with the aux_rand each was generated with.
_VECTORS = (
    ("0000000000000000000000000000000000000000000000000000000000000003",
     "F9308A019258C31049344F85F89D5229B531C845836F99B08601F113BCE036F9",
     "0000000000000000000000000000000000000000000000000000000000000000",
     "0000000000000000000000000000000000000000000000000000000000000000",
     "E907831F80848D1069A5371B402410364BDF1C5F8307B0084C55F1CE2DCA8215"
     "25F66A4A85EA8B71E482A74F382D2CE5EBEEE8FDB2172F477DF4900D310536C0"),
    ("B7E151628AED2A6ABF7158809CF4F3C762E7160F38B4DA56A784D9045190CFEF",
     "DFF1D77F2A671C5F36183726DB2341BE58FEAE1DA2DECED843240F7B502BA659",
     "0000000000000000000000000000000000000000000000000000000000000001",
     "243F6A8885A308D313198A2E03707344A4093822299F31D0082EFA98EC4E6C89",
     "6896BD60EEAE296DB48A229FF71DFE071BDE413E6D43F917DC8DCF8C78DE3341"
     "8906D11AC976ABCCB20B091292BFF4EA897EFCB639EA871CFA95F6DE339E4B0A"),
)


def self_test() -> None:
    """Check key derivation, signing and verification against the BIP-340 vectors."""
    for secret, pub, aux, msg, sig in _VECTORS:
        got = public_key_hex(secret).upper()
        if got != pub:
            raise SystemExit(f"self-test failed: public key {got} != {pub}")
        got = sign(secret, bytes.fromhex(msg), bytes.fromhex(aux)).upper()
        if got != sig:
            raise SystemExit(f"self-test failed: signature {got} != {sig}")
        if not verify(pub, bytes.fromhex(msg), sig):
            raise SystemExit("self-test failed: valid signature rejected")
        if verify(pub, bytes.fromhex(msg), sig[:-2] + ("00" if sig[-2:] != "00" else "11")):
            raise SystemExit("self-test failed: tampered signature accepted")


if __name__ == "__main__":
    self_test()
    print("bip340 self-test passed")
