# buzz

Installs the `buzz` CLI that the Hermes **Buzz platform adapter** shells out to.

[Buzz](https://github.com/block/buzz) is Block's Nostr-based human+agent
workspace. One relay is one community, and the relay URL *is* the workspace. The
adapter (`plugins/platforms/buzz` in hermes-agent) joins that community as a Nostr
identity and treats its channels and DMs as another chat surface — structurally
the same job the `signal_cli` role does for Signal.

This role installs a binary and nothing else. It runs no service and holds no
state, because the CLI is stateless: it takes its identity, relay and auth tag
from the environment on each invocation. Everything else is wired by the
composing playbook.

## What it does

Fetches the pinned Buzz release, verifies its checksum, extracts **only**
`usr/bin/buzz`, and installs it root owned at `buzz_bin`.

Upstream publishes no standalone CLI artifact — the `buzz` binary ships inside
the Buzz Desktop `.deb`, and the only other route is building the Rust workspace
with cargo. Extracting is the lesser evil: `buzz` links just libc, libm and
libgcc (no GTK, no WebKit), so it stands alone on a headless host, and pulling a
desktop package's dependency tree onto a hardened agent host would not.

## Variables

| Variable | Default | Meaning |
|---|---|---|
| `buzz_version` | `desktop-v0.5.28` | pinned upstream release carrying the CLI |
| `buzz_deb_sha256` | *(see defaults)* | checksum of that release's `.deb`; not optional |
| `buzz_deb_url` | derived from `buzz_version` | download location |
| `buzz_bin` | `/usr/local/bin/buzz` | where the CLI is installed, root owned |
| `buzz_download_dir` | `/usr/local/src` | staging for the download and extraction |
| `buzz_packages` | `[dpkg]` | needed to unpack the release |
| `buzz_profile` | `{}` | `name` (and optional `about`) to publish for the identity; empty = install only |
| `buzz_relay_url` | `""` | relay to publish the profile on; required when `buzz_profile` is set |
| `buzz_private_key_entry` | `""` | **pass entry path** of the identity's hex private key; required when `buzz_profile` is set |
| `buzz_auth_tag` | `""` | the owner's NIP-OA attestation for this identity, minted by `tools/buzz/attest.py`; public, and published with the profile |

## The profile is not optional for the adapter

The adapter connects only after `buzz users get` returns its own profile. An
identity that never published one gets `[]` back, and the adapter fails with
*"'users get' returned no profile — is the key a member of this community?"* —
misleadingly, since membership is fine. The display name also drives the
adapter's mention gating, so a nameless agent cannot be @-mentioned in a channel.

Setting `buzz_profile` makes the role publish it, like `signal_cli_profile` does
for Signal. Idempotent: it reads what the relay holds first and only runs
`set-profile` when the display name differs, or when `buzz_auth_tag` is set and
the attestation has not landed (changing only `about` therefore does not
republish). Every task that touches the key sets `no_log`, and the key reaches the
CLI through the environment, never argv.

To bump: choose a `desktop-v*` release, set `buzz_version`, and set
`buzz_deb_sha256` to `sha256sum` of the downloaded `.deb`.

## Without an attestation the app shows a person, not an agent

A member with a published name can be direct-messaged and will answer — the
relay fans the event out, the adapter's socket picks it up — but Buzz Desktop
will still list it as an ordinary person, with none of the agent affordances.
That is not a gap in this role's wiring: the app decides *agent or person* from
what the relay holds, and for an agent it runs itself Buzz Desktop publishes two
records that nothing publishes for a headless agent on another host.

The first is the one this role can own. Per upstream's `docs/nips/NIP-OA.md`, the
agent's `kind:0` profile must carry exactly one **owner attestation** — the
community owner's key vouching for the agent's key:

```
["auth", "<owner pubkey>", "<conditions>", "<sig>"]
```

signed BIP-340 over `SHA256("nostr:agent-auth:" || <agent pubkey> || ":" || <conditions>)`.
Without it, upstream's own bug report (block/buzz#5492) describes exactly the
symptom: *"the desktop UI displays the headless agent as a person rather than an
agent"*, and agent-to-agent mentions are dropped by the sibling admission gate
with nothing in the log. The tag is bound to the agent's pubkey alone, so one tag
covers every event that identity ever signs, and the conditions are signed
verbatim — ours are empty, since the clauses are conjunctive and the agent signs
many kinds.

Mint it on the controller with the owner key, which never leaves it:

```bash
pass show private/network/service/buzz/owner-private-key \
  | tools/buzz/attest.py <the agent's public key>
```

Then set the printed line as `buzz_auth_tag`. The CLI takes it as
`--auth-tag` / `BUZZ_AUTH_TAG` and "injects it into every signed event", so the
republished profile carries it, and the relay starts answering `users get` with
`owner_pubkey` set — which is how the role knows the attestation landed, and what
makes the app render the agent as managed by you.

Pass the same tag into the adapter's environment from the composing playbook (see
below), or only the profile is attested while every message the agent sends is
not.

The second record, an owner-authored `kind:30177` policy coordinate (`d` = the
agent's pubkey), is what seeds the agent list and the `@`-mention picker
independently of channel membership. It is deliberately **not** handled here: it
must be signed by the owner key, the relay accepts only events authored by the
key that authenticated, and no released CLI can publish it — upstream's
`buzz agents attest` and `buzz agents register` are still an open pull request
(block/buzz#7652). When it lands, bumping `buzz_version` and running
`buzz agents register` as the owner is all it should take. Nor will the agent
report presence (`buzz-acp` publishes that, and Hermes is not `buzz-acp`), so
expect it to show without a status indicator.

## Why the path is pinned, not just the binary

The adapter resolves its CLI as `BUZZ_CLI_PATH` → `which("buzz")` → `~/bin/buzz`.
That last fallback is **inside the agent's own writable state directory**
(`HERMES_HOME`), so an agent able to drop a file there would be choosing the
binary it then executes. The composing playbook must therefore pass
`BUZZ_CLI_PATH={{ buzz_bin }}` explicitly — installing root owned in `/usr/local`
is only half the protection.

## Wiring it into Hermes

Per the collection's convention, cross-role configuration lives in the playbook,
not in the roles. Include this role with `public: yes` and pass the values into
the hermes role:

```yaml
- name: Install the buzz CLI
  ansible.builtin.include_role:
    name: andreasbehnke.ai_agent.buzz
    public: yes
  vars:
    buzz_relay_url: "https://buzz.example.com"
    buzz_private_key_entry: "private/network/buzz/<agent>/private-key"
    buzz_profile:
      name: "Agent"
    # public, so a plain value - see the attestation section above
    buzz_auth_tag: '["auth","<owner pubkey>","","<sig>"]'

- name: Provision the Hermes agent
  ansible.builtin.include_role:
    name: andreasbehnke.ai_agent.hermes
  vars:
    hermes_managed_env:
      BUZZ_RELAY_URL: "https://buzz.example.com"
      BUZZ_CLI_PATH: "{{ buzz_bin }}"
      # the same tag the profile was published with, so the agent's own
      # messages are attested too and other agents accept its mentions
      BUZZ_AUTH_TAG: '["auth","<owner pubkey>","","<sig>"]'
    # a pass ENTRY PATH - the hermes role looks it up itself, with no_log
    hermes_managed_env_secrets:
      BUZZ_PRIVATE_KEY: "private/network/buzz/<agent>/private-key"
    hermes_managed_config:
      gateway:
        platforms:
          buzz:
            enabled: true
            extra:
              relay_url: "https://buzz.example.com"
              require_mention: true
```

The relay must be reachable over **https/wss with a publicly trusted
certificate**, and not merely a working one. Two independent reasons:

- The adapter only trusts attachment origins whose scheme is `https`/`wss`
  (`_attachment_origin`). Over plain `http` the chat works while every inbound
  image, audio file and PDF is dropped silently.
- Buzz Desktop's WebSocket client is rustls with Mozilla's roots compiled in. It
  ignores the system trust store and `SSL_CERT_FILE`, so a private CA is unusable
  by the desktop app even when `curl` and this CLI accept it.

## The agent's identity

Three keys exist and none may be reused for another's purpose:

| | Held by | Worth |
|---|---|---|
| relay | the relay deployment | the community's own signing identity |
| owner | a human, in a desktop keyring and `pass` | administering the community, and attesting its agents |
| **agent** | `pass`, injected as `BUZZ_PRIVATE_KEY` | one community membership |

Hermes deliberately exempts `BUZZ_*` from its terminal-tool environment scrub
(`_TERMINAL_FIRST_PARTY_ENV_PREFIXES`), so for a Buzz session the agent's own
shell can read `BUZZ_PRIVATE_KEY` and call `buzz` directly. That is upstream's
intent, and it is exactly why the agent gets its own key: treat it as compromised
if the agent ever is.

Generate one with the tool, which self-tests its curve implementation against the
BIP-340 vectors before printing anything:

```bash
tools/buzz/new_identity.py
tools/buzz/new_identity.py --nsec     # bech32 forms too, for GUI clients
```

Store the private half and enrol the public half as a community member — closed
relays (`BUZZ_REQUIRE_RELAY_MEMBERSHIP=true`) admit no one else:

```bash
# on the controller
tools/buzz/new_identity.py | sed -n 's/^private key: //p' \
  | pass insert -m private/network/buzz/<agent>/private-key

# on the relay host
docker compose -f /etc/docker/compose/buzz/docker-compose.yml \
  exec -T relay buzz-admin add-member --pubkey <public key>
docker compose -f /etc/docker/compose/buzz/docker-compose.yml \
  exec -T relay buzz-admin list-members
```

Membership makes the identity admissible; the owner attestation is what makes it
an *agent*. Mint it on the controller too, and set the result as
`buzz_auth_tag`:

```bash
pass show private/network/service/buzz/owner-private-key \
  | tools/buzz/attest.py <public key>
```

## Connecting Buzz Desktop

Humans reach the same community through Buzz Desktop on their own workstation.
This role does not install it — it is the full `.deb` the CLI is extracted from,
GTK and WebKit included — so on a Debian family desktop install it by hand, from
the same release the agent's CLI is pinned to:

```bash
v=0.5.28    # match buzz_version
curl -fLO "https://github.com/block/buzz/releases/download/desktop-v$v/Buzz_${v}_amd64.deb"
sha256sum "Buzz_${v}_amd64.deb"    # compare with buzz_deb_sha256
sudo apt install "./Buzz_${v}_amd64.deb"
```

It pulls in `libwebkit2gtk-4.1-0` and `libgtk-3-0`, and besides `buzz-desktop`
installs `buzz`, `buzz-agent`, `buzz-acp`, `buzz-dev-mcp`,
`buzz-backend-kubernetes` and `git-credential-nostr` into `/usr/bin`.

Then give it an identity and a relay:

1. **Identity.** Use the owner key from the table above, never the agent's. The
   app can generate one, or import one generated here with
   `tools/buzz/new_identity.py --nsec` (it takes the `nsec` form). It keeps the
   key in the desktop keyring through the Secret Service
   (`org.freedesktop.secrets`), so a session keyring such as gnome-keyring or
   KeePassXC must be running and unlocked.
2. **Membership.** On a closed relay the key must be enrolled before the app can
   connect — `buzz-admin add-member --pubkey <hex public key>` on the relay host,
   exactly as for the agent.
3. **Relay.** Add the community by its relay URL (`wss://buzz.example.com`). The
   certificate must be publicly trusted — see above; a private CA fails in the
   app alone.

The app keeps its state under `~/.local/share/xyz.block.buzz.app` and
`~/.config/xyz.block.buzz.app`. The `.deb` also puts a `buzz` CLI on the
desktop's `PATH`, which is handy for checking what the agent sees (the key is
read from the terminal, so it stays out of shell history):

```bash
read -rs BUZZ_PRIVATE_KEY && export BUZZ_PRIVATE_KEY
BUZZ_RELAY_URL=https://buzz.example.com buzz users get
```

## Paths

| Path | Owner | Purpose |
|---|---|---|
| `{{ buzz_bin }}` | `root:root` `0755` | the CLI |
| `{{ buzz_download_dir }}/buzz-<version>.deb` | `root:root` `0600` | verified download |
| `{{ buzz_download_dir }}/buzz-<version>/` | `root:root` `0700` | extraction staging |

No service, no `/etc` scope and no state directory — the role owns a binary, and
the adapter that uses it lives in the hermes role's process.
