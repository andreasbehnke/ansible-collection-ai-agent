# tmux cheat sheet for claude_operator hosts

The keys you need to live with a `claude-session` on a host provisioned by this role. They are
tmux's own defaults - the role's `/etc/tmux.conf` adds clipboard settings only and rebinds no key -
checked against tmux 3.4 (Ubuntu 24.04).

**How to read the keys.** Every tmux command starts with the *prefix*, **Ctrl-b**: press Ctrl and b
together, release both, then press the next key. `Ctrl-b d` means Ctrl+b, release, d. `M-w` means
Alt+w (or Esc, then w).

## Sessions: leave, come back, switch

| Keys / command | What it does |
|---|---|
| `Ctrl-b d` | **Detach.** Leaves the session running - Claude keeps working - and returns you to the shell. Closing the terminal or losing the connection does the same |
| `claude-session <project>` | Reattach the project's session, or start it if there is none |
| `claude-session <project>` from a shell inside tmux | Switch to the project's session, or start Claude right here and rename this session after the project - no tmux inside tmux |
| `claude-session` | Pick a live session (Up/Down, Enter; `q` leaves). Lists projects when there is none |
| `Ctrl-b s` | Pick a session from a list (arrows, Enter). The quickest way between projects and worktree sessions |
| `Ctrl-b (` / `Ctrl-b )` | Previous / next session |
| `Ctrl-b L` | Back to the last session you were in |
| `tmux ls` | List sessions from a plain shell |
| `tmux attach -t <name>` | Attach a session by name, e.g. one Claude created for a worktree |
| `tmux kill-session -t <name>` | End a session, and the Claude in it, for good |

Ending Claude itself (`/exit`, or Ctrl+D twice) also ends a `claude-session` session - except one
started from a shell inside tmux, which returns you to that shell.

## Keys tmux takes away from Claude

tmux reads **Ctrl-b** before Claude sees it. Claude Code uses Ctrl+B to move a running command into
the background, and in the transcript view (Ctrl+O) to page up.

| You want | Press |
|---|---|
| Ctrl+B in Claude (background a running command) | `Ctrl-b Ctrl-b` - tmux sends one Ctrl+B through. Or Claude's alternative, `Ctrl+X Ctrl+B` |
| Page up in Claude's transcript view | `b` or PgUp instead |

Everything else - Ctrl+C, Ctrl+D, Ctrl+O, Ctrl+R, Shift+Tab, Esc - goes straight to Claude.

## Scrolling

Claude Code draws a fullscreen view (the alternate screen), and tmux keeps **no scrollback** of
that. Scroll inside Claude, not tmux:

| Keys | In Claude |
|---|---|
| PgUp / PgDn | Scroll the conversation |
| Ctrl+Home / Ctrl+End | Top / bottom |
| Mouse wheel | Scroll - if the wheel does nothing in your terminal, use PgUp / PgDn |
| Ctrl+O | Transcript view (then `g` / `G` top / bottom, Space / `b` page, `q` back) |

tmux's own scrollback (`Ctrl-b PgUp`, or `Ctrl-b [` then PgUp) is for a plain shell window, not for
Claude.

## Copy and paste

How this reaches your local clipboard - over OSC 52 or over X11 forwarding - is in the README's
[Copy and paste](README.md#copy-and-paste) section. The keys:

| You want | Do |
|---|---|
| Copy from Claude | **Drag with the mouse** - copied on release. Ctrl+C on a selection copies too |
| Copy with the terminal's own selection | **Shift+drag** (on screen only), then your terminal's copy key |
| Paste text | Your terminal's paste key (Ctrl+Shift+V, or Ctrl+V if you bound it) |
| Paste an image | Ctrl+V in Claude (X11 route only) |

### Copy mode - copying with the keyboard

For text you cannot drag over, tmux's copy mode selects from the visible screen with the keyboard.
A copy made here reaches your local clipboard the same way a mouse copy does.

| Keys | What it does |
|---|---|
| `Ctrl-b [` | Enter copy mode (a position counter appears top right) |
| Arrows, PgUp / PgDn, `C-a` / `C-e` | Move; start / end of line |
| `C-r` / `C-s` | Search up / down, incrementally |
| `C-Space` | Start the selection |
| `M-w` (or `C-w`) | Copy the selection and leave copy mode |
| `C-g` | Drop the selection, stay in copy mode |
| `q` or Esc | Leave copy mode without copying |
| `Ctrl-b ]` | Paste the last tmux copy into the current pane |
| `Ctrl-b =` | Choose an older tmux copy to paste |

These are the emacs style keys, tmux's default. If `EDITOR` or `VISUAL` names vi or vim when the
tmux server starts, copy mode uses vi keys instead: `Space` to start, `Enter` to copy, `v` for a
rectangle, `/` and `?` to search, `q` to leave.

## Windows - optional

One session can hold several windows, e.g. Claude in one and a shell in the next.

| Keys | What it does |
|---|---|
| `Ctrl-b c` | New window (a shell) |
| `Ctrl-b n` / `Ctrl-b p` | Next / previous window |
| `Ctrl-b l` | Last window |
| `Ctrl-b w` | Pick a window from a list |
| `Ctrl-b &` | Kill the window (asks first) - Claude too, if it runs there |

## When something looks wrong

| Situation | Way out |
|---|---|
| Stuck in copy mode (counter top right, keys do nothing) | `q` or Esc |
| Typed `Ctrl-b` by accident | Press Esc. tmux 3.4 waits for the next key indefinitely; an unbound one just cancels the prefix |
| A clock fills the screen | You pressed `Ctrl-b t`; any key ends it |
| Window too small or misdrawn after resizing | `Ctrl-b d`, then `claude-session <project>` again |
| Forgot a key | `Ctrl-b ?` lists them all (`q` to leave) |
