# ansible-collection-ai-agent

Ansible role collection for provisioning AI agents and the tools they talk to, on
Debian and Ubuntu machines.

Fully qualified collection name: `andreasbehnke.ai_agent`

* requires ansible core 2.16 or later (`meta/runtime.yml`)
* requires the `community.general` collection on the controller (password store lookup)
* target systems: Debian / Ubuntu, systemd, `become` via sudo

```yaml
- hosts: agent
  become: yes
  roles:
    - andreasbehnke.ai_agent.<role_name>
```

## My configuration setup

These roles are written for my own private infrastructure and carry its conventions.
They are usable elsewhere, but you should know which assumptions are baked in.

### Conventions

| Convention | What it means for the roles |
|---|---|
| One service, one system user | Each service gets its own unprivileged system user with `nologin` shell, never a shared or login account. |
| Read only configuration | Everything ansible manages lives root owned in `/etc/<service>`, readable by the service group only (`0750` / `0640`), and is not writable by the service itself. |
| Separate writable state | Runtime state lives in `/var/lib/<service>`, owned by the service user (`0700`). Where an upstream tool insists on a writable "config" directory, the ansible managed material is kept in `/etc` and seeded into the state directory once. |
| Seed once, never overwrite | Restored state is only written when it is absent. A re-run never overwrites keys, sessions or databases that the running service has rotated since. |
| Local exposure only | Service APIs bind to the loopback interface. Roles assert this instead of trusting the variable. |
| Pinned upstream versions | Release versions are pinned in `defaults/main.yml` rather than tracking "latest", so provisioning is reproducible. |
| Dependencies stay with the caller | Shared runtimes (java, python, docker) are installed by the calling playbook. Roles only assert that a suitable version is present, so several roles can share one runtime without fighting over it. |
| No secrets in git | Secrets come from the password store at run time (see below). Nothing secret is committed here, and tasks touching secrets run with `no_log: true`. |
| Upstream keys keep their name | A variable carrying the value of an upstream configuration key is named `<role>_<upstream key in lower case>` - `hermes_signal_allowed_users` for Hermes' `SIGNAL_ALLOWED_USERS` - never an invented name. Variables without an upstream counterpart keep the plain role prefix. |
| Cross-role configuration is wired in the playbook | Roles never read each other's variables. A role exposes what it owns and stays unaware of its consumers; the playbook includes the producing role with `public: yes` and passes the values into the consuming role's input variable. |
| Use the upstream policy scope | When the software offers an administrator controlled configuration layer, the role configures through it - root owned, readable by the service group only (`0750` / `0640`) - so the service cannot rewrite its own policy. Tightening upstream's world readable defaults is what allows secrets to live there. |
| Upstream service installer plus drop-in | When upstream generates its own systemd unit, it is installed with the upstream command and left untouched; identity, environment and hardening come from an ansible owned `*.service.d/` drop-in. |
| Manual steps become tools | What a role cannot do itself - registering an account, exporting new secrets into the password store, key rotation - ships as a python script in `tools/<role_name>/` (standard library only) and is documented in that role's README, instead of as shell snippets in the documentation. |

### Password store (pass)

Secrets are kept in the unix [password store](https://www.passwordstore.org/), not in
ansible vault. Lookups are executed **on the controller**, so the store lives on the
machine running ansible and `gpg-agent` has to be unlocked before a run:

```yaml
lookup('community.general.passwordstore', '<entry path>', returnall=true)
```

| Aspect | Convention |
|---|---|
| Entry layout | One entry per file, named after the file it reproduces, below a per service prefix: `<prefix>/<identity>/<file>`. Each role exposes its prefix as a variable, e.g. `signal_cli_account_pass_prefix`. |
| Multi line entries | Entries hold the complete file content verbatim, created with `pass insert -m`. Roles read them with `returnall=true` - without it, only the first line of the entry would be returned. |
| Optional entries | Entries that may be absent are read with `missing='empty'` and the depending tasks are skipped when nothing is found. Required entries fail the run when missing. |
| Binary content | Binary state (databases, archives) is stored base64 encoded, and unpacked by the role. |
| Logging | Every task that reads or writes secret material sets `no_log: true`. |

Because the store is the source of truth for a rebuilt machine, exporting new secrets
back into it after a manual step (registration, linking, key rotation) is part of the
procedure - each role's README documents the export commands.

## Roles of this collection

### [hermes](roles/hermes/README.md)

Hermes is the agent runtime itself, so this role installs it system wide and runs its
messaging gateway as a hardened systemd service, configured through Hermes' managed scope -
see [`roles/hermes/README.md`](roles/hermes/README.md) for its variables, directory layout
and what "managed scope" pins.

### [signal_cli](roles/signal_cli/README.md)

Signal is my chat interface to the agents, so this role installs signal-cli and runs its
JSON-RPC/REST daemon as a hardened systemd service - see
[`roles/signal_cli/README.md`](roles/signal_cli/README.md) for its variables, directory
layout and password store entries.
