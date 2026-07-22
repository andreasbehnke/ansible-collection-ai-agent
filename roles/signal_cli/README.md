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

All three entries are created and refreshed by
[`tools/signal_cli/export_account.py`](../../tools/signal_cli/export_account.py), see
below.

All of it is written read only to `/etc/signal-cli/accounts/<number>/` and restored into
the state directory **only when that account is not there yet**, so a re-run never
overwrites keys, sessions or databases rotated by the running daemon. signal-cli recreates
whatever the snapshot does not contain.

## Tool: export_account.py

[`tools/signal_cli/export_account.py`](../../tools/signal_cli/export_account.py) writes the
three password store entries from a machine that already runs the daemon - after the first
registration, and again whenever the runtime state changed enough to matter. It stops the
daemon while reading, so the sqlite database in the snapshot is consistent, starts it again
afterwards even if the export fails, and pipes the content straight into `pass` without
printing it. Existing entries are overwritten.

```bash
# remote host, sudo password taken from the password store
tools/signal_cli/export_account.py \
    --number +49123456789 \
    --host agent.example.org \
    --ssh-user admin \
    --sudo-pass-entry private/network/admin@agent

# on the machine itself, with passwordless sudo
tools/signal_cli/export_account.py --number +49123456789
```

| Option | Default | Meaning |
|---|---|---|
| `--number` | required | E.164 number of the account to export |
| `--host` | local machine | host running signal-cli, reached over SSH |
| `--ssh-user` | current user | SSH user on that host |
| `--sudo-pass-entry` | none | pass entry with that user's sudo password; omit when sudo needs none |
| `--data-dir` | `/var/lib/signal-cli` | must match `signal_cli_data_dir` |
| `--service` | `signal-cli` | systemd unit to stop and start |
| `--pass-prefix` | `private/network/signal` | must match `signal_cli_account_pass_prefix` |
| `--skip-state` | off | export only the two json entries |
| `--keep-running` | off | do not stop the daemon; the snapshot may be inconsistent |
| `--dry-run` | off | read everything, report sizes, write nothing |

## Tool: register_landline.py

[`tools/signal_cli/register_landline.py`](../../tools/signal_cli/register_landline.py)
registers a **new** Signal account for a **landline** number. A landline cannot receive
the SMS verification code, so registration must use Signal's voice-call verification -
more finicky than SMS (a captcha is required, and the voice request must follow a primed
SMS request), which is why it is scripted. A number that is already registered is restored
from the password store instead, no re-registration needed.

The script drives signal-cli over SSH and walks through the sequence that works for a
voice-only number:

1. check SSH reachability and that `sudo` to the signal user works
2. refuse to run if the number already looks registered (unless `--force`)
3. `systemctl stop signal-cli` - release the account lock
4. `deleteLocalAccountData --ignore-registered` - start from a clean session, stale
   half-sessions are the main cause of failures
5. **you** solve one captcha and paste the `signalcaptcha://…` token
6. `register --captcha <token>` - the SMS attempt is rejected for a landline
   (`InvalidTransportModeException`, expected) but it **primes the session**
7. `register --voice --captcha <token>` - Signal places an automated voice call
8. **you** answer the call and enter the 6-digit code
9. `verify <code>` - immediately, so the session cannot expire
10. `systemctl start signal-cli` and check the daemon is active and
    `/api/v1/check` returns HTTP 200

**Prerequisites**: the host already provisioned by this role; SSH access as a user with
sudo rights (passwordless, or with `--sudo-pass-entry`); a phone that receives calls on
the number; a browser for the captcha; python 3 and `ssh` locally.

```bash
tools/signal_cli/register_landline.py \
    --number +49123456789 \
    --host agent.example.org \
    --ssh-user admin \
    --sudo-pass-entry private/network/admin@agent

# re-register a number that is already registered (WIPES the existing account)
tools/signal_cli/register_landline.py --number +49123456789 --host agent.example.org --force
```

Options beyond those: `--signal-user` (default `signal`), `--service` (default
`signal-cli`), `--data-dir` (default `/var/lib/signal-cli`), `--api-url` (default
`http://127.0.0.1:8080`), `--host-key-checking` (default `accept-new`). They have to match
the role variables when those were overridden.

**Getting the captcha token**: open
<https://signalcaptchas.org/registration/generate.html>, solve the captcha, then
right-click the *Open Signal* link and copy the link address - do **not** click it. It
starts with `signalcaptcha://`. Tokens are single use and short lived, get a fresh one if
the script asks again.

Afterwards export the new account into the password store with
[`export_account.py`](#tool-export_accountpy), otherwise a rebuilt machine has to go
through the registration again.

**Troubleshooting**

| Symptom | Meaning / fix |
|---|---|
| `InvalidTransportModeException` on `register --captcha` | Expected for a landline (no SMS). The session is primed, the script continues to the voice request. |
| `Captcha required` / `AuthorizationFailedException` | The token is missing, expired or already used. Get a fresh one and re-run. |
| `Before requesting voice verification you need to request SMS verification and wait a minute` | The session was not primed. Re-run from a clean state, with a fresh captcha. |
| `verify` returns `StatusCode: 404` | The verification session expired, usually from re-requesting the call or a long delay. Re-run the whole flow and enter the code immediately after the call. |
| No call arrives | Signal rate-limits. Wait a minute and re-run cleanly with a fresh captcha, rather than requesting twice in one session - double requests invalidate the code. |
| Service stays `activating` or crash-loops after start | The account is not registered/verified, or `signal_cli_account` does not match the registered number. Check `journalctl -u signal-cli -n 30`. |

**Manual equivalent**, for reference. `/etc/signal-cli/config.json` points signal-cli at
the state directory, so no `--config` is needed:

```bash
sudo systemctl stop signal-cli
sudo -u signal -H signal-cli -a +49123456789 deleteLocalAccountData --ignore-registered
sudo -u signal -H signal-cli -a +49123456789 register --captcha 'signalcaptcha://…'
sudo -u signal -H signal-cli -a +49123456789 register --voice --captcha 'signalcaptcha://…'
sudo -u signal -H signal-cli -a +49123456789 verify 123456
sudo systemctl start signal-cli
```

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
