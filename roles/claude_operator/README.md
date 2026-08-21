# claude_operator

Provisions a host for running [Claude Code](https://code.claude.com/docs/) **as the operator** -
interactively over SSH, and from a phone or browser through
[Remote Control](https://code.claude.com/docs/en/remote-control).

It installs a pinned Claude Code, creates one login account per human operator with a workspace root
for long lived working trees, and installs `claude-session`, a small wrapper over Claude Code's own
`--worktree` / `--tmux` / `--remote-control` flags.

It deliberately runs **no service and opens no port**. Remote Control is an outbound HTTPS
connection; the only listener on such a host is `sshd`.

## The one step this role cannot perform

Remote Control accepts exactly one kind of credential: a full scope login from `claude auth login`.
A `claude setup-token` credential is refused by name - *"These tokens can only make model requests,
so they can't establish Remote Control sessions"* - and API keys are not supported at all.

`claude auth login` opens a browser flow. Over SSH it degrades gracefully (press `c`, approve the URL
on a machine that has a browser, paste the code back), but no Ansible module can drive it. So the
role provisions everything up to the sign in and then **reports** which accounts still need it:

```console
$ ssh <operator>@<host>
$ claude auth login                # press c, approve elsewhere, paste back
$ claude auth status --json        # expect a "Login method" row naming the claude.ai account
$ cd ~/work/<project> && claude    # accept the workspace trust dialog, then exit
```

Run the role again afterwards; it is idempotent and the report disappears.

## Nothing may outrank that credential

Claude Code resolves credentials by a seven rank precedence and a `claude auth login` credential is
**rank 7 of 7**, the bottom. Every other source wins, silently. The symptom is not a billing surprise
but a capability loss: Remote Control eligibility is evaluated against whichever credential actually
won, and the error names the credential rather than the environment.

`preflight.yml` therefore asserts that none of these is set - in the environment the playbook runs
from, and in `/etc/environment` on the target:

| Variable | Effect |
|---|---|
| `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN` | Outrank the login. Remote Control refuses API keys outright |
| `CLAUDE_CODE_OAUTH_TOKEN` | Outranks the login, and is inference only |
| `CLAUDE_CODE_USE_BEDROCK` / `_VERTEX` / `_FOUNDRY` | Remote Control is unavailable on those endpoints |
| `ANTHROPIC_BASE_URL` | Anything other than `api.anthropic.com` disqualifies Remote Control |
| `DISABLE_TELEMETRY`, `DO_NOT_TRACK`, `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`, `DISABLE_GROWTHBOOK` | Each disables the feature flag evaluation Remote Control availability depends on |

**Every one of these failures is silent.** The host installs cleanly, sessions start cleanly, and
they simply never appear in the session list. Two places the role cannot check, and you should, after
signing in: the operator's own shell files, and an `env` block in `~/.claude/settings.json`.

## Deviations from this collection's conventions

Three, each deliberate, and each the opposite of what the same convention asks for on a service host.

| Convention | What this role does instead | Why |
|---|---|---|
| **One service, one system user with `nologin`** | Login accounts with a shell, a home and `sudo` if the host grants it | There is no service here. The host *is* the operator's working environment: Claude Code needs their home, their credential at `~/.claude/.credentials.json`, their git configuration and their working trees |
| **Read only configuration in `/etc/<service>`** | No `/etc/claude-operator`, no `.env`, no managed scope | There is nothing for Ansible to deliver. The only secret is `~/.claude/.credentials.json`, created on the host by the operator; it never passes through the password store or through Ansible. That removes this collection's most delicate machinery - `no_log` handling of a token from `pass` - rather than reimplementing it |
| **A hardened systemd unit** | No unit at all; `tmux` plus `claude-session` | Server mode has no local terminal, so it can only be driven from a device. An always running server is also a standing remote execution channel into a host holding a full scope credential. And the interactive form is the more robust one: it retries through a network outage and reconnects by itself, where server mode gives up after about ten minutes |

Two conventions the role does keep, and they matter more here than usual: the Claude Code
installation is **pinned** and **root owned**, so every account shares one known version and no
operator can silently drift the binary.

`DISABLE_AUTOUPDATER` is the one variable worth considering and rejecting deliberately. An unattended
service sets it because a version bump can change tool semantics underneath it. Here a human is
present, and Remote Control's behaviour is version gated in ways that favour being current - so the
pin is the package, and the absence of the variable is a choice rather than an oversight.

Root ownership is what makes that safe, and it has been observed doing its job: on a host pinned to
2.1.238 the autoupdater attempted 2.1.239 and recorded
`{"path":"npm-global","outcome":"failed","status":"no_permissions"}` in
`~/.claude/.last-update-result.json`. The operator's session keeps running the pinned binary and
nothing drifts into `~/.local`. Expect that line in the log; it is the design working, not a fault.
Raise the pin in `claude_operator_claude_version` when you want the new version.

## Directory layout

| Path | Owner | Mode | Purpose |
|---|---|---|---|
| `/usr/local/bin/claude` | `root:root` | `0755` | Claude Code, pinned, root owned so no operator can drift it |
| `/usr/local/lib/node_modules/@anthropic-ai/claude-code` | `root:root` | | The package itself |
| `/usr/local/bin/claude-session` | `root:root` | `0755` | The session wrapper |
| `/home/<operator>` | operator | `0700` | Home. Several operators have no business reading each other's |
| `/home/<operator>/.claude` | operator | `0700` | **The credential and every transcript.** Created by Claude Code with the login user's umask (so it lands `0775`); the role re-asserts `0700` once it exists, but never creates it — its existence is what tells the role the account has signed in |
| `/home/<operator>/work` | operator | `0755` | Workspace root, one directory per project |

The workspace root is deliberately **not** the home directory. The startup trust dialog never saves
trust for a home directory, so a session started there can never satisfy the workspace trust
precondition Remote Control requires - and a session in `$HOME` has `~/.claude`, credential included,
in scope.

Session history is keyed to the working directory
(`~/.claude/projects/<path with every non alphanumeric character replaced by ->`), so moving a
working tree loses `claude --continue` in the new location even though `claude --resume <id>` still
finds it. Choose paths you will not want to change, and back up `~/.claude`.

## claude-session

```console
claude-session                        # list projects and live sessions
claude-session <project>              # attach the project's session, or start one
claude-session <project> <worktree>   # new git worktree, in its own tmux session
claude-session -r <project> [...]     # the same, with Remote Control enabled
```

It is thin on purpose. Claude Code already creates the worktree and the tmux session itself
(`--worktree <name> --tmux=classic`); the wrapper resolves a project name to a directory, handles the
multi repository case, and keeps the permission mode consistent.

**Multi repository projects.** If the project directory is itself a git repository, that is the
session's working directory. If it *contains* repositories, the first is the working directory and
every sibling is passed as `--add-dir`, so one session sees the whole set. `--worktree` branches only
the repository it runs in - git has no cross repository worktree, and the wrapper does not pretend
otherwise.

Both things the wrapper's `--help` warns about are worth repeating: a new worktree is a new path and
gets its **own** trust dialog, and its **own** session history.

## Variables

| Variable | Default | Description |
|---|---|---|
| `claude_operator_users` | `[]` | Login accounts: a list of `{name, authorized_keys}`. Empty by default because this repository is public - the composing playbook supplies them |
| `claude_operator_claude_version` | `"2.1.238"` | Pinned Claude Code release. The floor is higher than it looks: reconnect after a stale token needs 2.1.224, effort control from a device needs 2.1.234 |
| `claude_operator_npm_prefix` | `/usr/local` | npm `--prefix` for the global install |
| `claude_operator_bin` | `/usr/local/bin/claude` | The installed launcher |
| `claude_operator_install_node` | `true` | Install node from the NodeSource repository. Set to false to provide it yourself; the role then only asserts the version |
| `claude_operator_node_version` | `"22"` | Required node major version |
| `claude_operator_packages` | `[tmux, git]` | Host packages the operator's sessions need. `tmux` keeps a session alive across an SSH disconnect, `git` is what `--worktree` needs |
| `claude_operator_workspace_dirname` | `work` | Workspace root, relative to each home. Must not be empty |
| `claude_operator_projects` | `[]` | Project directories created empty under every workspace root |
| `claude_operator_session_bin` | `/usr/local/bin/claude-session` | Where the wrapper goes. `""` skips it |
| `claude_operator_permission_mode` | `acceptEdits` | Permission mode the wrapper starts sessions with. `dontAsk` is refused by `preflight.yml` |

### Why `dontAsk` is refused

`preflight.yml` asserts a *judgement* rather than a fact, which is unusual for this collection. It is
there because `dontAsk` is the **correct** value for an unattended agent host, where nobody can
answer a prompt - and copying a value between two roles that look alike is exactly how it would
arrive here. On this host somebody is present, at the terminal or on a connected device, where
permission prompts and `AskUserQuestion` are forwarded and stay open until answered. `dontAsk` would
deny silently instead of asking them.

`bypassPermissions` is not refused, but think twice: the "the machine holds nothing" argument that
justifies it on a scratch host does not apply to one holding a full scope credential.

## What Remote Control costs

While Remote Control is connected, **the session transcript is stored on Anthropic servers** -
messages, responses and tool activity - so the conversation stays in sync across devices and the
session can reconnect after a network drop. Execution and filesystem access stay local.

That is a data posture decision, not a detail. Work that must not leave the machine belongs in a
plain `claude-session <project>`, not the `-r` form. `disableRemoteControl` in
`~/.claude/settings.json` turns the feature off entirely for a host that should never use it.

## Example

```yaml
- hosts: claude_operator
  become: yes
  roles:
    - role: andreasbehnke.ai_agent.claude_operator
      vars:
        claude_operator_users:
          - name: alice
            authorized_keys:
              - "{{ lookup('file', 'public-keys/alice.pub') }}"
        claude_operator_projects:
          - platform
```

Network placement is the playbook's business, not this role's: it works the same on any segment. The
host holds an account credential, so put it somewhere that reflects that.
