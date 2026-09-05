#!/usr/bin/env python3
"""Generate a Nostr identity for the agent and print it for the password store.

The Buzz adapter authenticates as a Nostr keypair. That keypair must be the
agent's OWN - not the relay's, and emphatically not the community owner's:
Hermes deliberately exposes BUZZ_* (including BUZZ_PRIVATE_KEY) to its terminal
tool for buzz sessions, so the agent's key is worth exactly one community
membership, while the owner's key is worth the whole community.

Standard library only, per the collection's tooling convention. The secp256k1
maths is BIP-340 (x-only public keys), the same construction upstream's
plugins/platforms/buzz/nostr_auth.py uses; this script is verified against the
BIP-340 test vector on every run before it prints anything.

Usage:
    ./new_identity.py                 # generate and print
    ./new_identity.py --nsec          # also print bech32 nsec (for GUI clients)

Nothing is written to disk. Store it yourself, e.g.

    ./new_identity.py | sed -n 's/^private key: //p' \\
      | pass insert -m private/network/buzz/<agent>/private-key

Then enrol the PUBLIC key as a community member, on the relay host:

    docker compose -f /etc/docker/compose/buzz/docker-compose.yml \\
      exec -T relay buzz-admin add-member <public key>
"""

import argparse
import secrets

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


def public_key_hex(private_key_hex: str) -> str:
    """x-only (BIP-340) public key for a 32-byte private key."""
    d = int(private_key_hex, 16)
    if not 1 <= d < N:
        raise ValueError("private key out of range")
    return f"{_mul(d)[0]:064x}"


_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def _polymod(values):
    gen = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]
    chk = 1
    for v in values:
        top = chk >> 25
        chk = ((chk & 0x1FFFFFF) << 5) ^ v
        for i in range(5):
            if (top >> i) & 1:
                chk ^= gen[i]
    return chk


def bech32(hrp: str, data_hex: str) -> str:
    acc = bits = 0
    data = []
    for byte in bytes.fromhex(data_hex):
        acc = (acc << 8) | byte
        bits += 8
        while bits >= 5:
            bits -= 5
            data.append((acc >> bits) & 31)
    if bits:
        data.append((acc << (5 - bits)) & 31)
    values = ([ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]
              + data + [0] * 6)
    poly = _polymod(values) ^ 1
    checksum = "".join(_CHARSET[(poly >> 5 * (5 - i)) & 31] for i in range(6))
    return hrp + "1" + "".join(_CHARSET[d] for d in data) + checksum


def _self_test() -> None:
    """BIP-340 vector 1. A wrong curve implementation would mint an identity
    whose public key does not match its private key - unusable, and only
    discovered after it had been stored and enrolled."""
    got = public_key_hex("00" * 31 + "03").upper()
    want = "F9308A019258C31049344F85F89D5229B531C845836F99B08601F113BCE036F9"
    if got != want:
        raise SystemExit(f"self-test failed: {got} != {want}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--nsec", action="store_true",
                    help="also print the bech32 nsec form, for GUI clients")
    args = ap.parse_args()

    _self_test()
    sk = secrets.token_hex(32)
    pk = public_key_hex(sk)
    print(f"private key: {sk}")
    print(f"public key:  {pk}")
    if args.nsec:
        print(f"nsec:        {bech32('nsec', sk)}")
        print(f"npub:        {bech32('npub', pk)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
