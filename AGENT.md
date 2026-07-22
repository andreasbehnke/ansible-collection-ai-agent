# AI Agent Provisioning Collection

Ansible collection `andreasbehnke.ai_agent`, holding the roles which provision AI agents
(agent runtimes, their chat tools and dependencies) on Debian/Ubuntu machines.

This is a public repository. Never commit secrets, real phone numbers, host names or
anything else from the private infrastructure it is used from - examples use placeholders.

## Folder Structure

* galaxy.yml - collection metadata, bump `version` when the collection changes
* meta/runtime.yml - minimum supported ansible version
* README.md - user facing documentation: setup conventions and the list of roles
* roles - the roles of this collection, one directory per role, standard role layout
  * signal_cli - installs signal-cli, runs its JSON-RPC/REST daemon as a systemd service
    under the unprivileged `signal` user; read only configuration in /etc/signal-cli,
    writable state in /var/lib/signal-cli, accounts restored from the password store

Role directories must be named lowercase with underscores; a role is referenced as
`andreasbehnke.ai_agent.<role_name>`.

## Conventions

These are binding for every role in this collection. They are also documented for users in
`README.md`, keep both in sync when they change.

* **One service, one system user** - each service gets its own unprivileged system user
  with `nologin` shell, never a shared or login account.
* **Read only configuration** - everything ansible manages lives root owned in
  `/etc/<service>`, readable by the service group only (`0750` / `0640`), and is not
  writable by the service itself.
* **Separate writable state** - runtime state lives in `/var/lib/<service>`, owned by the
  service user (`0700`). Where an upstream tool insists on a writable "config" directory,
  keep the ansible managed material in `/etc` and seed it into the state directory once.
* **Seed once, never overwrite** - restored state is only written when it is absent, so a
  re-run never overwrites keys, sessions or databases the running service has rotated.
  Guard with a `stat` on the target, not with `force: no` alone.
* **Local exposure only** - service APIs bind to the loopback interface, and the role
  asserts it instead of trusting the variable.
* **Pinned upstream versions** - pin releases in `defaults/main.yml` instead of tracking
  "latest", so provisioning is reproducible.
* **Dependencies stay with the caller** - shared runtimes (java, python, docker) are
  installed by the calling playbook. Roles only assert that a suitable version is present,
  so several roles can share one runtime without fighting over it.
* **No secrets in git** - secrets come from the password store at run time, and every task
  touching secret material sets `no_log: true`.
* **Idempotence** - a second run must report zero changes; check mode must not fail. Never
  let a task depend on a file another task wrote in the same run (it does not exist in
  check mode) - pass the content through a fact instead.
* **Variable naming** - every variable is prefixed with the role name and has a documented
  default in `defaults/main.yml`.

### Password store (pass)

Secrets are read on the controller with
`lookup('community.general.passwordstore', '<entry path>', returnall=true)`:

* one entry per file, named after the file it reproduces, below a per service prefix
  `<prefix>/<identity>/<file>`; the prefix is a role variable
* entries hold the complete file content verbatim (`pass insert -m`), which is why
  `returnall=true` is mandatory - without it only the first line is returned
* optional entries are read with `missing='empty'` and their tasks are skipped when the
  lookup returns nothing; required entries fail the run
* binary state (databases, archives) is stored base64 encoded and unpacked by the role
* the store is the source of truth for a rebuilt machine: whenever a manual step creates
  new secret material, document the export command in the role's README

## Adding a role

Create `roles/<role_name>/` using the standard role layout (`defaults/`, `tasks/`,
`handlers/`, `templates/`, `meta/`) plus a `README.md` documenting its variables, the paths
it creates and its password store entries. Then:

* follow the conventions above
* add a one sentence entry with a link to the role README under "Roles of this collection"
  in `README.md` - the role details belong in the role README, not in the collection one
* add the role to the folder structure list above
* bump `version` in `galaxy.yml`
