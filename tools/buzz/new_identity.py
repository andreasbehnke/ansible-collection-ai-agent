#!/usr/bin/env python3
"""Generate a Nostr identity for the agent and print it for the password store.

The Buzz adapter authenticates as a Nostr keypair. That keypair must be the
agent's OWN - not the relay's, and emphatically not the community owner's:
Hermes deliberately exposes BUZZ_* (including BUZZ_PRIVATE_KEY) to its terminal
tool for buzz sessions, so the agent's key is worth exactly one community
membership, while the owner's key is worth the whole community.

Standard library only, per the collection's tooling convention. The secp256k1
maths lives in bip340.py next to this script - BIP-340 (x-only public keys), the
same construction upstream's plugins/platforms/buzz/nostr_auth.py uses - and is
verified against the BIP-340 test vectors on every run before anything is
printed.

Usage:
    ./new_identity.py                 # generate and print
    ./new_identity.py --nsec          # also print bech32 nsec (for GUI clients)

Nothing is written to disk. Store it yourself, e.g.

    ./new_identity.py | sed -n 's/^private key: //p' \\
      | pass insert -m private/network/buzz/<agent>/private-key

Then enrol the PUBLIC key as a community member, on the relay host:

    docker compose -f /etc/docker/compose/buzz/docker-compose.yml \\
      exec -T relay buzz-admin add-member --pubkey <public key>

A new agent also wants an owner attestation, or Buzz Desktop shows it as a
person rather than as your agent - see attest.py.
"""

import argparse
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bip340  # noqa: E402  (sibling module, path set above)

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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--nsec", action="store_true",
                    help="also print the bech32 nsec form, for GUI clients")
    args = ap.parse_args()

    bip340.self_test()
    sk = secrets.token_hex(32)
    pk = bip340.public_key_hex(sk)
    print(f"private key: {sk}")
    print(f"public key:  {pk}")
    if args.nsec:
        print(f"nsec:        {bech32('nsec', sk)}")
        print(f"npub:        {bech32('npub', pk)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
