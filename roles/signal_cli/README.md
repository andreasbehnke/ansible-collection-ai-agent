# signal_cli

Installs [signal-cli](https://github.com/AsamK/signal-cli) and runs its JSON-RPC/REST
daemon as the systemd service `signal-cli`, under a dedicated unprivileged `signal` user,
bound to the loopback interface only (the HTTP api has no authentication). A registered
Signal account can be reproduced from the password store, so a rebuilt machine does not
have to register its phone number again.

The api endpoints are `/api/v1/rpc` (JSON-RPC), `/api/v1/events` (server sent events) and
`/api/v1/check` (health).

**Requires** a java runtime of `signal_cli_java_min_version` or later on `PATH`. It is
*not* installed by this role, only asserted, so the calling playbook stays in charge of
the shared runtime.

## Directory layout

| Path | Owner | Mode | Purpose |
|---|---|---|---|
| `/opt/signal-cli-<version>` | `root:root` | | unpacked release, symlinked to `/usr/local/bin/signal-cli` |
| `/etc/signal-cli` | `root:signal` | `0750` | ansible managed configuration, read only for the service |
| `/etc/signal-cli/config.json` | `root:signal` | `0640` | system wide signal-cli config, sets `dataDir` and `account` |
| `/etc/signal-cli/accounts/<number>/` | `root:signal` | `0750` | account files from the password store, read only master copy |
| `/var/lib/signal-cli` | `signal:signal` | `0700` | writable state (`--config`), seeded from the configuration directory |
| `/etc/systemd/system/signal-cli.service` | `root:root` | `0644` | daemon unit, hardened with `ProtectSystem=strict` and `ReadWritePaths` on the state directory only |

signal-cli needs full read/write access to its data directory (it rotates prekeys and
keeps sessions in a sqlite database), which is why state and configuration are separate.
Because `config.json` sets `dataDir`, manual invocations use the same state directory
without passing `--config`:

```bash
sudo -u signal signal-cli listAccounts
```

## Fresh install

With `signal_cli_accounts` empty nothing is restored. The service is installed and enabled
but deliberately **not started** - without an account the daemon would only crash loop.
Register or link an account manually, then run the role again to start the service.

## Reproducing an existing account

Set `signal_cli_accounts` and store the signal-cli account files in the password store,
verbatim, one entry each:

| Entry below `pass_path` | Required | Restored to | Content |
|---|---|---|---|
| `accounts.json` | yes | `data/accounts.json` | account registry of that number, names the data file (`path`) of the account |
| `account.json` | yes | `data/<path>` | identity key, registration password, profile key - the account identity, so the number does not have to be registered again |
| `state.tar.gz.b64` | no | `data/<path>.d/` | base64 encoded tar.gz of the runtime state: the sqlite database with **groups**, contacts and sessions |

`pass_path` defaults to `<signal_cli_account_pass_prefix>/<number>`, `<path>` is taken from
the `accounts.json` entry of that number.

Without `state.tar.gz.b64` signal-cli starts with an empty database: sending into a known
group fails with `Group not found` until the group is learned again from an incoming
message. The snapshot ages - refresh it after relevant group or contact changes.

Export everything from a running instance (stop the daemon first, so the sqlite database
is consistent):

```bash
N=+49123456789
ssh admin@host 'sudo systemctl stop signal-cli'
ssh admin@host 'sudo cat /var/lib/signal-cli/data/accounts.json' \
  | pass insert -m private/network/signal/$N/accounts.json
# <path> is the "path" field of that number's entry in accounts.json
ssh admin@host 'sudo cat /var/lib/signal-cli/data/<path>' \
  | pass insert -m private/network/signal/$N/account.json
ssh admin@host 'sudo tar czf - -C /var/lib/signal-cli/data <path>.d | base64 -w0' \
  | pass insert -m private/network/signal/$N/state.tar.gz.b64
ssh admin@host 'sudo systemctl start signal-cli'
```

All of it is written read only to `/etc/signal-cli/accounts/<number>/` and restored into
the state directory **only when that account is not there yet**, so a re-run never
overwrites keys, sessions or databases rotated by the running daemon. signal-cli recreates
whatever the snapshot does not contain.

## Variables

| Variable | Default | Description |
|---|---|---|
| `signal_cli_version` | `0.14.6` | pinned signal-cli release to install |
| `signal_cli_install_dir` | `/opt` | where the release archive is unpacked |
| `signal_cli_bin_link` | `/usr/local/bin/signal-cli` | symlink placed into `PATH` |
| `signal_cli_java_min_version` | `25` | minimum java major version, asserted, not installed |
| `signal_cli_user` | `signal` | service user, created as a system user without login shell |
| `signal_cli_group` | `signal` | service group, also the group of the read only configuration |
| `signal_cli_service_name` | `signal-cli` | systemd unit name |
| `signal_cli_config_dir` | `/etc/signal-cli` | read only configuration directory |
| `signal_cli_data_dir` | `/var/lib/signal-cli` | writable state directory, passed as `--config` |
| `signal_cli_http_host` | `127.0.0.1` | REST api address, asserted to be a loopback address |
| `signal_cli_http_port` | `8080` | REST api port |
| `signal_cli_receive_mode` | `on-start` | signal-cli `daemon --receive-mode`, one of `on-start`, `on-connection`, `manual` |
| `signal_cli_ignore_attachments` | `false` | when true, incoming attachments are not downloaded |
| `signal_cli_account` | `""` | E.164 number the daemon serves; may stay empty if exactly one account exists in the state directory |
| `signal_cli_accounts` | `[]` | existing accounts to reproduce, entries `{number: "+49...", pass_path: "<optional>"}`; empty means a fresh install |
| `signal_cli_account_pass_prefix` | `private/network/signal` | password store prefix, `pass_path` defaults to `<prefix>/<number>` |

## Example

```yaml
- hosts: agent
  become: yes
  tasks:
    # java is installed by the playbook, not by the role
    - ansible.builtin.include_role:
        name: andreasbehnke.ai_agent.signal_cli
      vars:
        signal_cli_account: "+49123456789"
        signal_cli_accounts:
          - number: "+49123456789"
```
