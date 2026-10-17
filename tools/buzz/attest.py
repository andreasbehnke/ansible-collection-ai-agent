#!/usr/bin/env python3
"""Mint the owner attestation (NIP-OA auth tag) for an agent's Nostr identity.

Buzz Desktop decides "agent" or "person" from what is on the relay, not from
anything local. An agent it runs itself gets the two records published for it; a
headless agent on another host gets neither, so without this tag the app shows
the agent as an ordinary member, and other agents' sibling-admission gates drop
its mentions silently (block/buzz#5492).

The tag is the owner key vouching for the agent key, per upstream
docs/nips/NIP-OA.md:

    ["auth", "<owner pubkey>", "<conditions>", "<sig>"]

where the signature is BIP-340 over SHA256 of

    "nostr:agent-auth:" || <agent pubkey> || ":" || <conditions>

It is bound to the agent's pubkey alone, so one tag covers every event that
identity ever signs. Conditions default to the empty string: clauses are
conjunctive, and the agent signs many kinds. The result is PUBLIC - an owner
pubkey, the conditions and a signature - so it belongs in plain host_vars, not
in the password store.

The owner private key is read from stdin and never stored or echoed. It is the
key worth the whole community, and it stays an operator step for that reason:

    pass show private/network/service/buzz/owner-private-key \\
      | ./attest.py <agent public key>

Then set it as buzz_auth_tag (hermes_buzz_auth_tag in the composing playbook) so
the buzz role republishes the agent's kind:0 profile carrying the tag.

Standard library only, per the collection's tooling convention. The signature is
deterministic, so re-minting the same pair yields the same tag; the curve
implementation is checked against the BIP-340 vectors before anything is
printed.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bip340  # noqa: E402  (sibling module, path set above)

DOMAIN_SEPARATOR = "nostr:agent-auth:"


def _hex32(value: str, what: str) -> str:
    """A 32-byte hex string, lowercased - events carry pubkeys in lower case, and
    the preimage is signed verbatim, so the case has to match the event's."""
    cleaned = value.strip().lower()
    if len(cleaned) != 64:
        raise SystemExit(f"{what} must be 64 hex characters, got {len(cleaned)}")
    try:
        bytes.fromhex(cleaned)
    except ValueError:
        raise SystemExit(f"{what} is not hex: {value.strip()!r}")
    return cleaned


def auth_tag(owner_private_key_hex: str, agent_pubkey_hex: str,
             conditions: str = "") -> list:
    """The finished NIP-OA auth tag for this agent, signed by this owner key."""
    owner = bip340.public_key_hex(owner_private_key_hex)
    if owner == agent_pubkey_hex:
        raise SystemExit("owner and agent are the same key - NIP-OA rejects "
                         "self-attestation, and the agent must have its own identity")
    preimage = f"{DOMAIN_SEPARATOR}{agent_pubkey_hex}:{conditions}".encode("ascii")
    digest = hashlib.sha256(preimage).digest()
    signature = bip340.sign(owner_private_key_hex, digest)
    if not bip340.verify(owner, digest, signature):
        raise SystemExit("minted signature does not verify - refusing to print it")
    return ["auth", owner, conditions, signature]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("agent_public_key",
                    help="the agent identity being attested, 64 hex characters")
    ap.add_argument("--conditions", default="",
                    help="NIP-OA conditions string, signed verbatim "
                         "(default: empty, meaning no constraints)")
    args = ap.parse_args()

    if args.conditions != args.conditions.strip() or " " in args.conditions:
        raise SystemExit("whitespace is not permitted anywhere in conditions")
    if not args.conditions.isascii():
        raise SystemExit("conditions must be ASCII")

    bip340.self_test()
    agent = _hex32(args.agent_public_key, "the agent public key")

    if sys.stdin.isatty():
        raise SystemExit("the owner private key is read from stdin, e.g.\n"
                         "  pass show private/network/service/buzz/owner-private-key"
                         " | ./attest.py <agent public key>")
    first_line = next((line for line in sys.stdin.read().splitlines() if line.strip()), "")
    owner_private_key = _hex32(first_line, "the owner private key")

    tag = auth_tag(owner_private_key, agent, args.conditions)
    print(json.dumps(tag, separators=(",", ":")))
    print(f"attested agent {agent}\n"
          f"            by owner {tag[1]}\n"
          "set the line above as buzz_auth_tag and re-run the playbook; the buzz "
          "role republishes the\nprofile so the relay carries the tag.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
