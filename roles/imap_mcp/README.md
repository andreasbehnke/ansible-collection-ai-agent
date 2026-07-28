# imap_mcp

Runs [`nikolausm/imap-mcp-server`](https://github.com/nikolausm/imap-mcp-server) as a hardened
systemd service that gives an agent **read-only IMAP access plus draft writing** - it can list,
search and read mail and save drafts, but **cannot send, delete, move or flag** anything, and
cannot manage accounts.

The server is installed so the agent (a different unprivileged user, e.g. the Hermes `hermes`
user) **cannot modify the executables** and **cannot reach the mailbox credentials**:

- the code is cloned and built **as root** and left **root owned** - the service only executes it;
- it runs under its **own** unprivileged `imap-mcp` user, which holds the credentials;
- only a **loopback** MCP endpoint is exposed; the agent connects to that and nothing else.

`imap-mcp-server` speaks stdio only, so [`mcp-proxy`](https://github.com/sparfenyuk/mcp-proxy)
bridges it to a streamable-HTTP endpoint on `127.0.0.1` (chosen over supergateway, which only
binds `0.0.0.0`).

## Directory layout

| Path | Owner | Mode | Purpose |
|---|---|---|---|
| `/usr/local/lib/imap-mcp-server` | `root:root` | `0755`/`0644` | code + `node_modules` + `dist`, built by root - **immutable** to the service and the agent |
| `/usr/local/lib/mcp-proxy` | `root:root` | | the stdio→HTTP bridge (python venv), immutable too |
| `/var/lib/imap-mcp` | `imap-mcp:imap-mcp` | `0700` | `HOME` / state directory |
| `/var/lib/imap-mcp/.imap-mcp/accounts.json` | `imap-mcp:imap-mcp` | `0600` | AES-encrypted account store - **not readable by the agent** |
| `/var/lib/imap-mcp/.imap-mcp/.key` | `imap-mcp:imap-mcp` | `0600` | its encryption key |

The endpoint is `http://{{ imap_mcp_http_host }}:{{ imap_mcp_port }}{{ imap_mcp_proxy_path }}`
(`http://127.0.0.1:9000/mcp` by default). It has **no authentication**, so it is asserted to a
loopback address and must never be exposed off host.

## Immutability

Everything under `/usr/local/lib` is built and owned by `root`; the role also strips group/other
write and re-asserts root ownership on every run. The `imap-mcp` service user (and any agent user)
can execute the server but cannot change `dist/`, `node_modules/` or the bridge. Check with:

```bash
stat -c '%a %U:%G' /usr/local/lib/imap-mcp-server/dist/index.js   # 644 root:root
sudo -u hermes touch /usr/local/lib/imap-mcp-server/dist/index.js # Permission denied
```

## Tool policy (read-only + drafts)

`imap_mcp_enabled_tools` is passed to the server as `IMAP_MCP_ENABLED_TOOLS`, and the server
registers **only** those tools. The default set is the read tools plus `imap_save_draft`; there is
no `imap_send_email`, `imap_delete_email`, `imap_move_email`, flag or `imap_*_account` tool exposed
at all. The restriction is enforced **server side** - the agent cannot call a tool that is not
registered, whatever it asks for.

## One-time account setup

`imap-mcp-server` stores its accounts AES-encrypted in `~/.imap-mcp/accounts.json` with the key in
`~/.imap-mcp/.key`; there is no plain credential env var and no add-account CLI. Add the account
**once** with the collection's helper (it drives the server's `imap_add_account` tool), then carry
those two files in the password store - the role restores them on every run (seed once / restore,
like `signal_cli`):

```bash
# 1. add the account on the host (writes ~/.imap-mcp/accounts.json + .key, checks the connection)
tools/imap_mcp/init_account.py --account agent@example.com \
    --imap-host imap.example.com --imap-user agent@example.com \
    --imap-password-entry private/network/imap/agent@example.com/password \
    --host agent.example.org --sudo-pass-entry private/network/admin@agent
# 2. copy both files into the password store
tools/imap_mcp/export_account.py --account agent@example.com --host agent.example.org \
    --sudo-pass-entry private/network/admin@agent
```

That writes `<prefix>/agent@example.com/accounts.json` and `<prefix>/agent@example.com/key` into
`pass`. Point the role at it with `imap_mcp_account` (+ `imap_mcp_account_pass_prefix`); on a fresh
host with no account the service is installed and enabled but **not started**. See the platform
operator doc (`doc/imap-mcp/README.md`) for the full walk-through.

## Agent integration

The role knows nothing about the agent. The composing playbook registers the loopback endpoint
with the MCP client. For Hermes that is an `mcp_servers` entry, best pinned in the managed scope so
the agent cannot change it:

```yaml
      - ansible.builtin.include_role:
          name: andreasbehnke.ai_agent.imap_mcp
        vars:
          imap_mcp_account: "agent@example.com"

      - ansible.builtin.include_role:
          name: andreasbehnke.ai_agent.hermes
        vars:
          hermes_managed_config:
            mcp_servers:
              imap:
                url: "http://127.0.0.1:9000/mcp"
```

Because the credentials live with the `imap-mcp` user and only the restricted loopback endpoint is
reachable, the agent gets read + draft and can neither exfiltrate the mailbox password nor bypass
the tool policy. For defence in depth you can also deny the agent's own service egress to the IMAP
host.

## Tools

[`tools/imap_mcp/init_account.py`](../../tools/imap_mcp/init_account.py) adds the mailbox account
for the first time. It runs a one-shot MCP session against the installed server (as the `imap-mcp`
user, over SSH `--host` or locally), calls `imap_add_account` with the connection details, and -
unless `--no-test` - `imap_test_account` to confirm the mailbox is reachable. The password comes
from a `pass` entry (`--imap-password-entry`) or an interactive prompt, never the command line.
`--dry-run` shows the calls (password redacted).

[`tools/imap_mcp/export_account.py`](../../tools/imap_mcp/export_account.py) copies the two account
files from a host that has been set up into the password store, over SSH (`--host`) or locally. It
overwrites existing entries; run it again to refresh. `--dry-run` shows what it would store without
touching `pass`.

## Variables

| Variable | Default | Description |
|---|---|---|
| `imap_mcp_repo` / `imap_mcp_version` | upstream / `v1.5.2` | git source and pinned ref of imap-mcp-server |
| `imap_mcp_code_dir` | `/usr/local/lib/imap-mcp-server` | root-owned code path |
| `imap_mcp_proxy_version` | `0.12.0` | pinned `mcp-proxy` version (in a root venv) |
| `imap_mcp_proxy_dir` | `/usr/local/lib/mcp-proxy` | bridge venv path |
| `imap_mcp_proxy_path` | `/mcp` | streamable-HTTP endpoint path |
| `imap_mcp_install_node` / `imap_mcp_node_version` | `true` / `20` | install Node from NodeSource, or verify only |
| `imap_mcp_user` / `imap_mcp_group` | `imap-mcp` | service identity |
| `imap_mcp_service_name` | `imap-mcp` | systemd unit name |
| `imap_mcp_home` | `/var/lib/imap-mcp` | `HOME` / state directory holding the credentials |
| `imap_mcp_http_host` | `127.0.0.1` | endpoint address, asserted to be loopback |
| `imap_mcp_port` | `9000` | endpoint port |
| `imap_mcp_enabled_tools` | read set + `imap_save_draft` | server-side tool allowlist |
| `imap_mcp_account` | `""` | account id / mailbox to restore; empty = fresh install (not started) |
| `imap_mcp_account_pass_prefix` | `private/network/imap` | password store prefix, `<prefix>/<account>/{accounts.json,key}` |

## Example

```yaml
- hosts: agent
  become: yes
  tasks:
    - ansible.builtin.include_role:
        name: andreasbehnke.ai_agent.imap_mcp
      vars:
        imap_mcp_account: "agent@example.com"
```
