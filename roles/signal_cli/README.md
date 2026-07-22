# signal_cli

Installs [signal-cli](https://github.com/AsamK/signal-cli) and runs its JSON-RPC/REST
daemon as the systemd service `signal-cli`, under a dedicated unprivileged `signal` user,
bound to the loopback interface only (the HTTP api has no authentication).

A java runtime is *not* installed by this role, it is only checked for: signal-cli 0.14.x
needs java 25 or later.

## Directory layout

| Path | Owner | Purpose |
|---|---|---|
| `/etc/signal-cli` | `root:signal` `0750` | ansible managed configuration, read only for the service |
| `/etc/signal-cli/config.json` | `root:signal` `0640` | system wide signal-cli config, sets `dataDir` and `account` |
| `/etc/signal-cli/accounts/<number>/` | `root:signal` `0750` | account files from the password store, read only master copy |
| `/var/lib/signal-cli` | `signal:signal` `0700` | writable state (`--config`), seeded from the configuration directory |

signal-cli needs full read/write access to its data directory (it rotates prekeys and
keeps sessions in a sqlite database), which is why state and configuration are separate.
Because `config.json` sets `dataDir`, manual invocations use the same state directory:

```bash
sudo -u signal signal-cli listAccounts
```

## Fresh install

With `signal_cli_accounts` empty nothing is restored. The service is installed and enabled
but deliberately **not started** - without an account the daemon would only crash loop.
Register or link an account manually, then run the role again to start the service.

## Reproducing an existing account

Set `signal_cli_accounts` and store the two signal-cli account files in the password
store, verbatim, one entry each:

```
<signal_cli_account_pass_prefix>/<number>/accounts.json   -> data/accounts.json
<signal_cli_account_pass_prefix>/<number>/account.json    -> data/<path>
```

`<path>` is taken from the `accounts.json` entry of that number. Export them from a
running instance with:

```bash
pass insert -m private/network/signal/+49123456789/accounts.json  # paste data/accounts.json
pass insert -m private/network/signal/+49123456789/account.json   # paste data/<path>
```

The account file holds the identity key, the registration password and the profile key -
that is the account identity, so the number does not have to be registered again. The
sqlite database in `data/<path>.d/` is not restored: signal-cli recreates it and re-fetches
group memberships and sessions from the server on first start.

Both files are seeded into the state directory **only when the account is not there yet**,
so a re-run never overwrites keys rotated by the running daemon.

## Variables

See `defaults/main.yml`. Most relevant:

| Variable | Default | Meaning |
|---|---|---|
| `signal_cli_version` | `0.14.6` | pinned release, unpacked to `/opt/signal-cli-<version>` |
| `signal_cli_user` / `signal_cli_group` | `signal` | service identity |
| `signal_cli_account` | `""` | E.164 number the daemon serves |
| `signal_cli_accounts` | `[]` | existing accounts to reproduce, `{number, pass_path}` |
| `signal_cli_account_pass_prefix` | `private/network/signal` | password store location |
| `signal_cli_http_host` / `_port` | `127.0.0.1` / `8080` | REST api endpoint, loopback is asserted |
| `signal_cli_receive_mode` | `on-start` | signal-cli `daemon --receive-mode` |

## Example

```yaml
- hosts: agent
  tasks:
    - ansible.builtin.include_role:
        name: andreasbehnke.ai_agent.signal_cli
      vars:
        signal_cli_account: "+49123456789"
        signal_cli_accounts:
          - number: "+49123456789"
```
