# claude_operator

Provisions a host for running [Claude Code](https://code.claude.com/docs/) **as the operator** -
interactively over SSH, and from a phone or browser through
[Remote Control](https://code.claude.com/docs/en/remote-control).

It installs a pinned Claude Code, creates one login account per human operator with a workspace root
for long lived working trees, and installs `claude-session`, a small wrapper that runs Claude Code in tmux with its own
`--worktree` / `--remote-control` flags.

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
| `/home/<operator>/.ssh/id_ed25519` | operator | `0600` | The account's own ssh key, for git over ssh. Generated on the host, never copied off it |
| `/home/<operator>/.password-store` | operator | | The password store, cloned when the account has a `pass_repository` |

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
claude-session                               # pick a live session, arrows and Enter
claude-session <project>                     # attach the project's session, or start one
claude-session <project> <worktree>          # new git worktree, in its own tmux session
claude-session --no-remote <project> [...]   # the same, without Remote Control
```

**Remote Control is on by default.** Every session the wrapper starts passes `--remote-control`, so
it can be picked up from claude.ai/code or the Claude app; `--no-remote` starts one without it. The
flag only matters when a session starts - attaching to a running one keeps whatever it was started
with. See [What Remote Control costs](#what-remote-control-costs) before relying on the default.

**Without arguments it is a session picker.** The live tmux sessions are listed; Up and Down (or
`j` / `k`) move, Enter attaches, `q` or Esc leaves. Run from inside tmux it switches the client
instead of nesting a second tmux. With no live session, or without a terminal (`ssh <host>
claude-session`, a pipe), it prints the projects and sessions instead, as before.

New to tmux? [TMUX_CHEAT_SHEET.md](TMUX_CHEAT_SHEET.md) has every key you need - detaching,
switching sessions, scrolling Claude, copy mode, and the one key tmux takes away from Claude.

It is thin on purpose. Claude Code creates the worktree itself (`--worktree <name>`); the wrapper
resolves a project name to a directory, handles the multi repository case, keeps the permission mode
consistent, and runs the tmux session Claude lives in.

It does **not** use Claude Code's own `--tmux`. That runs the tmux client as a child of a Claude
process which outlives a detach: `Ctrl-b d` then leaves a blinking cursor instead of the shell
prompt until you press Ctrl-C. The wrapper execs tmux itself, with the session name
(`<repository>_worktree-<name>`) and environment `--tmux` would use, so sessions started either way
are found again.

**Multi repository projects.** If the project directory is itself a git repository, that is the
session's working directory. If it *contains* repositories, the first is the working directory and
every sibling is passed as `--add-dir`, so one session sees the whole set. `--worktree` branches only
the repository it runs in - git has no cross repository worktree, and the wrapper does not pretend
otherwise.

What the wrapper's `--help` warns about is worth repeating: `--worktree` refuses a project whose own
trust dialog has never been accepted - trust in `~/work` does not count - so start
`claude-session <project>` once and accept it before asking for a worktree. A new worktree is then a
new path and gets its **own** trust dialog, and its **own** session history.

**A failed start stays on screen.** tmux closes a session as soon as its only pane exits, which would
take Claude's error message with it and make the command look as if it simply returned. When Claude
exits non-zero the pane prints the status and waits for Enter; a normal `/exit` closes as before.

### The login banner

The role also installs an `update-motd.d` snippet so anyone arriving over SSH sees what the host is
for. It **runs `claude-session --help`** rather than repeating it, so the banner cannot drift out of
step with the tool; the colouring is applied with `sed` rules that leave a line plain rather than
wrong if one ever stops matching, and the snippet stays silent when the wrapper is not installed.

Two details it has to get right, because `pam_motd` is an unusual place to run anything: the
environment is minimal, so `HOME` is defaulted before calling the wrapper (its `set -u` would
otherwise abort before printing anything), and the file must be executable and root-owned or Ubuntu
skips it.

## Copy and paste

**Short version:** use a terminal that implements OSC 52 (kitty, WezTerm, foot, Alacritty, Ghostty,
iTerm2) and copy and paste work without any X11 forwarding - the one thing you give up is pasting
*images* with Ctrl+V. GNOME Terminal and the other VTE based terminals do not implement OSC 52;
for them the only route is SSH X11 forwarding, which has to be *trusted* on a Wayland desktop and
gives the host considerably more than your clipboard. Both routes are installed by default and
proven on a live host by `tools/claude_operator/check_clipboard.py`.

### What Claude Code does

Claude Code's fullscreen renderer takes the mouse. Dragging selects inside Claude Code and copies
the selection at once (`copyOnSelect`); Ctrl+C on a selection copies it too. Ctrl+V pastes an
**image** from the clipboard. All of it uses the clipboard *of the host Claude Code runs on* - which
a headless host does not have. Three details, read from the binary and confirmed on a live host,
decide how that is bridged:

* **Copy over SSH never uses xclip.** With `SSH_CONNECTION` set, Claude Code skips the native
  clipboard tools even when xclip and a `DISPLAY` are present. Inside tmux it runs
  `tmux load-buffer -w -`, and also writes its own OSC 52 sequence (`ESC ] 52 ; c ; <base64>`) to
  the pane.
* **Ctrl+V is image paste only** (`chat:imagePaste`). It asks `xclip -selection clipboard
  -t TARGETS` whether the host's clipboard holds an image. Claude Code has no text paste action: text
  always comes from the terminal's own paste, which arrives as a bracketed paste and works through
  tmux without any help.
* **Shift+drag** bypasses Claude Code's mouse handling and gives the terminal's own selection,
  limited to what is on screen. That is independent of both routes below.

| | Copy (select / Ctrl+C) | Paste text | Paste image (Ctrl+V) |
|---|---|---|---|
| **OSC 52 route** - kitty, WezTerm, foot, Alacritty, Ghostty, iTerm2 | ✓ tmux forwards it as OSC 52 | ✓ terminal paste | ✗ - copy the file to the host and give Claude Code its path |
| **X11 route** - GNOME Terminal, other VTE terminals, anything without OSC 52 | ✓ tmux hook → xclip → your desktop | ✓ terminal paste | ✓ xclip reads your desktop's clipboard |

### The OSC 52 route - no X11 needed

OSC 52 is an escape sequence by which a program asks the terminal to set the clipboard. It travels
in-band over the SSH connection, like any other output. The terminal decides whether to obey it, and
the host never gets to read your clipboard through it.

What the role does: `/etc/tmux.conf` sets `set-clipboard on`. That setting matters. With tmux's
default (`external`) the terminal only receives the sequence from `load-buffer -w`, which tmux sends
with an **empty** selection parameter (`ESC ] 52 ; ; …`), and terminals disagree about what empty
means - xterm's own reading is "primary selection and cut buffer 0", not the clipboard. With `on`,
tmux also forwards Claude Code's own explicit `ESC ] 52 ; c ; …`, which every OSC 52 terminal writes
to the clipboard.

tmux only sends OSC 52 to a client whose terminal description says it can take it (the `Ms`
capability, or the `clipboard` terminal feature for `xterm*`), so the host needs terminfo for your
`TERM`. ncurses on Debian and Ubuntu ships `foot`, `alacritty`, `wezterm` and `xterm-256color`, all
with `Ms`; the role adds `kitty-terminfo` for kitty's `xterm-kitty`, without which tmux refuses to
attach at all (*"missing or unsuitable terminal: xterm-kitty"*).

What you do, on the workstation:

* **Do not forward X11** to the host. Remove any `ForwardX11` entry for it, or set
  `ForwardX11 no` explicitly - Debian's `ssh_config` does not forward by default.
* Make sure the terminal accepts clipboard **writes** via OSC 52. The defaults at the time of
  writing:
  * kitty - allowed (`clipboard_control` includes `write-clipboard`)
  * WezTerm, foot - allowed
  * Alacritty - allowed (`terminal.osc52 = "OnlyCopy"`)
  * Ghostty - allowed (`clipboard-write = allow`). Its `TERM` is `xterm-ghostty`, which Ubuntu
    24.04 does not ship; enable Ghostty's `ssh-terminfo` (or `ssh-env`) shell integration feature, or
    connect with `TERM=xterm-256color ssh <host>`.
  * iTerm2 - **off**; enable *Settings → General → Selection → Applications in terminal may access
    clipboard*.
* Paste text with the terminal's own paste shortcut. If you bind it to Ctrl+V, Claude Code's image
  paste never sees the key - which costs nothing on this route, since image paste cannot work here
  anyway.

What OSC 52 allows: every program running in a pane on the host can **write** your clipboard, not
read it. The realistic risk is a planted clipboard - copy something, and paste something else - so
look at what you paste into a local shell. Compared with X11 forwarding it is a small grant.

### The X11 route - for terminals without OSC 52

Connect with X11 forwarding, so the host gets a `DISPLAY` that is your own desktop:

```
Host <host>
  ForwardX11 yes
  ForwardX11Trusted yes
  ForwardX11Timeout 0
```

**It has to be trusted forwarding on a Wayland desktop.** Untrusted forwarding needs the X SECURITY
extension, which XWayland does not have, and ssh refuses it with *"untrusted X11 forwarding setup
failed: xauth key data not generated"*. Debian's `/etc/ssh/ssh_config` already defaults to trusted,
so plain `ssh -X` works there. `ForwardX11Timeout 0` keeps forwarding open for the whole connection.

**What trusted forwarding grants.** While you are connected, any process on the host running as
you - Claude Code and every command it runs included - is a full X11 client of your XWayland
server. It can:

* read your clipboard at any moment, not only when you paste;
* watch and inject keystrokes, and capture the windows, of every X11 application on your desktop
  (Wayland-native applications, GNOME Terminal among them, are out of its reach);
* open windows on your screen.

A host that holds a full scope Claude credential and runs an agent with shell access is exactly the
kind of host where that matters - a prompt injection that reaches a shell can reach your clipboard.
Enable it only for a host you trust as much as your desktop, or switch to an OSC 52 terminal.

What the role does on the host:

* A tmux hook, `after-load-buffer`, passes every buffer loaded into tmux - Claude Code's copies
  among them - to `xclip -selection clipboard`; `copy-command` does the same for tmux's own copy
  mode. Claude Code's image paste runs xclip directly.
* A tmux session outlives the SSH connection it was started from, and a reconnect lands on a new
  display number. So `/usr/local/bin/xclip` is a shim that runs the real xclip on the display of
  your **latest** login, which `/etc/profile.d/claude-session-display.sh` and `claude-session` record
  in `~/.cache/claude-session/display`.
* An X11 copy lives as long as the connection: xclip serves it from the host, so a selection copied
  just before disconnecting may be gone afterwards.

Without a forwarded display all of this is inert: xclip fails quietly and nothing else changes. To
drop it entirely on a host meant for OSC 52 terminals only, set `claude_operator_x11_clipboard:
false`.

### Both routes

* The keys for all of this, tmux's copy mode included, are in
  [TMUX_CHEAT_SHEET.md](TMUX_CHEAT_SHEET.md#copy-and-paste).
* `/etc/tmux.conf` is read when a tmux server starts. A server already running before the role
  installed it picks it up with `tmux source-file /etc/tmux.conf`.
* If your terminal binds Paste to Ctrl+V, give Claude Code's image paste another key in
  `~/.claude/keybindings.json`: `{"bindings": [{"context": "Chat", "bindings": {"alt+v":
  "chat:imagePaste"}}]}`.

### Proving it

```console
$ tools/claude_operator/check_clipboard.py <user>@<host>
$ tools/claude_operator/check_clipboard.py <user>@<host> --route osc52 --term xterm-ghostty
```

The check drives a real Claude Code on the host and never submits a prompt. For the X11 route it
starts Claude Code from an `ssh -X` login shell, injects the keys, mouse drags and bracketed pastes a
terminal sends, reconnects on a second display, and compares the result with your local
clipboard. For the OSC 52 route it starts Claude Code with no X11 at all, attaches a real tmux
client over `ssh -tt` for each `TERM`, makes a selection, and asserts that the client receives
`ESC ] 52 ; c ; <base64 of the selection>`. Whether the terminal then honours that sequence is the
terminal's documented behaviour, listed above.

## Password store

An operator entry may carry `pass_repository`, the git URL of that operator's
[pass](https://www.passwordstore.org/) store:

```yaml
claude_operator_users:
  - name: alice
    authorized_keys: [...]
    pass_repository: ssh://alice@git.example.org/srv/git/credentials.git
```

The role then installs `pass`, `gnupg` and `pinentry-curses`, sets `GPG_TTY` (and `PASSWORD_STORE_DIR`, if the
store is not at pass' default `~/.password-store`) for login shells (`/etc/profile.d/claude-operator-pass.sh`) and clones the store as the
operator, over ssh with the account's own key. The git server's host key is trusted on first contact
and a changed one is refused. The clone is never updated afterwards - `pass git pull` is the
operator's.

Two steps belong to someone else, and the role reports them rather than failing:

1. **Authorising the ssh key at the git server.** On the first run the clone is refused; the report
   prints the account's public key. Add it to the server's authorized keys and run the role again.
2. **The secret gpg key.** It is the one secret the store hinges on, so it never passes through
   Ansible. Import it by hand and mark it trusted - `pass insert` refuses to encrypt to a key of
   unknown trust:

```console
$ gpg --export-secret-keys --armor <id> | ssh <operator>@<host> gpg --import
$ ssh -t <operator>@<host> gpg --edit-key <id> trust   # 5, ultimate
```

Claude Code's Bash tool has no terminal, so pinentry cannot ask it for the passphrase. Unlock the key
once from a terminal (`pass show <any entry>` in a tmux window); the sessions then use gpg-agent's
cached passphrase until it expires. gpg-agent's defaults (10 minutes since last use, 2 hours at most) are
short for a working day; set `claude_operator_pass_timeout` (seconds, e.g. `28800` for 8 hours) and the role
writes it as `default-cache-ttl` and `max-cache-ttl` to each account's `~/.gnupg/gpg-agent.conf` and reloads
the agent. The reload drops the cached passphrase, so unlock once more after a changed timeout.

## Variables

| Variable | Default | Description |
|---|---|---|
| `claude_operator_users` | `[]` | Login accounts: a list of `{name, authorized_keys}`, optionally with `pass_repository`. Empty by default because this repository is public - the composing playbook supplies them |
| `claude_operator_ssh_key_type` | `ed25519` | Type of the ssh key generated for every account. An existing key is left alone |
| `claude_operator_pass_packages` | `[pass, gnupg, pinentry-curses]` | Installed when any account has a `pass_repository` |
| `claude_operator_pass_dirname` | `.password-store` | Where the store is cloned, relative to each home. pass' own default; anything else is exported as `PASSWORD_STORE_DIR` |
| `claude_operator_pass_profile_path` | `/etc/profile.d/claude-operator-pass.sh` | Sets `GPG_TTY`, and `PASSWORD_STORE_DIR` for a non default store |
| `claude_operator_pass_timeout` | undefined | Seconds gpg-agent caches the store key's passphrase (`default-cache-ttl` and `max-cache-ttl`). Undefined leaves `gpg-agent.conf` alone |
| `claude_operator_claude_version` | `"2.1.238"` | Pinned Claude Code release. The floor is higher than it looks: reconnect after a stale token needs 2.1.224, effort control from a device needs 2.1.234 |
| `claude_operator_npm_prefix` | `/usr/local` | npm `--prefix` for the global install |
| `claude_operator_bin` | `/usr/local/bin/claude` | The installed launcher |
| `claude_operator_install_node` | `true` | Install node from the NodeSource repository. Set to false to provide it yourself; the role then only asserts the version |
| `claude_operator_node_version` | `"26"` | Required node major version |
| `claude_operator_node_bin` / `claude_operator_npm_bin` | `/usr/bin/node` / `/usr/bin/npm` | Absolute interpreter paths. Bare names resolve through PATH, where `/usr/local/bin` wins and another package's private node can shadow the pinned one |
| `claude_operator_packages` | `[tmux, git, xclip, xauth, kitty-terminfo]` | Host packages the operator's sessions need. `tmux` keeps a session alive across an SSH disconnect, `git` is what `--worktree` needs, `xclip` and `xauth` carry the clipboard over X11 forwarding, `kitty-terminfo` lets a kitty client attach to tmux |
| `claude_operator_x11_clipboard` | `true` | Clipboard over SSH X11 forwarding: the xclip shim, the profile.d snippet and the tmux hook. See [Copy and paste](#copy-and-paste) |
| `claude_operator_xclip_shim_path` / `claude_operator_xclip_bin` | `/usr/local/bin/xclip` / `/usr/bin/xclip` | The shim, which must precede the real xclip on PATH, and the real one it runs |
| `claude_operator_x11_profile_path` | `/etc/profile.d/claude-session-display.sh` | Records every SSH login's forwarded display |
| `claude_operator_tmux_osc52` | `true` | `set-clipboard on` in the system tmux configuration, for terminals with OSC 52 |
| `claude_operator_tmux_conf_path` | `/etc/tmux.conf` | Where that configuration goes |
| `claude_operator_workspace_dirname` | `work` | Workspace root, relative to each home. Must not be empty |
| `claude_operator_projects` | `[]` | Project directories created empty under every workspace root |
| `claude_operator_session_bin` | `/usr/local/bin/claude-session` | Where the wrapper goes. `""` skips it |
| `claude_operator_motd_path` | `/etc/update-motd.d/99-claude-session` | Login banner showing the wrapper's own `--help`, colourised. `""` installs none |
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

That is a data posture decision, not a detail - and since the wrapper makes Remote Control the
default, it is one taken for every session unless you opt out. Work that must not leave the machine
belongs in `claude-session --no-remote <project>`. `disableRemoteControl` in
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
