#!/usr/bin/env python3
"""Check Ctrl+C / Ctrl+V between this workstation and a Claude session on an operator host.

Drives a REAL Claude Code on the host the way the operator uses it. Keys, mouse drags and
pastes are injected with "tmux send-keys" in the encoding a terminal sends them (SGR mouse
reports, bracketed paste). Both clipboard routes the claude_operator role sets up are
checked; see its README, section "Copy and paste".

The X11 route (terminals without OSC 52, GNOME Terminal among them): Claude is started in
tmux from a login shell of an "ssh -X" connection and used again after a NEW connection
has replaced it. Results are read from the REAL local clipboard, so a PASS
means the bytes reached this desktop, not a tmux buffer on the host. Cases, each before
and after the reconnect:

  * terminal paste   - the terminal's Paste action reaches Claude's prompt
  * Ctrl+V           - an image on the local clipboard is pasted into the prompt
  * select + Ctrl+C  - a mouse selection in Claude lands on the local clipboard

The OSC 52 route (kitty, WezTerm, foot, Alacritty, ...): Claude is started with NO X11
forwarding at all. For each terminal type a real tmux client is attached over "ssh -tt"
with that TERM, a selection is made, and the bytes tmux sends to the client must contain
the clipboard escape "ESC ] 52 ; c ; <base64 of the selection>" - exactly what such a
terminal turns into a clipboard write. The terminal's own acceptance of OSC 52 is its
documented behaviour and not tested here.

Standard library only, per the collection's tooling convention. Needs xclip locally (or
wl-clipboard on a desktop without XWayland) for the X11 route, and key based SSH to the
operator account.

Usage:
    ./check_clipboard.py <user>@<host> [--project <dir under ~/work>]
    ./check_clipboard.py <user>@<host> --route osc52 --term xterm-kitty --term foot
    ./check_clipboard.py <user>@<host> --ssh-option ForwardX11Trusted=yes

Nothing is ever submitted: the script never presses Enter, so no prompt reaches the model.
It runs on a private tmux socket (-L clipcheck), kills that server when done and puts the
previous local clipboard text back.
"""

import argparse
import base64
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import threading
import time

SOCKET = "clipcheck"
SESSION = "c"
WIDTH, HEIGHT = 120, 40
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "ControlMaster=no",
            "-o", "ControlPath=none"]
ESC = "\033"
SHIM = "/usr/local/bin/xclip"
# TERM values the OSC 52 route is checked with by default: what kitty, WezTerm, foot and
# Alacritty set, plus xterm-256color, which many others (Ghostty, Konsole) fall back to.
OSC52_TERMS = ["xterm-256color", "xterm-kitty", "wezterm", "foot", "alacritty"]
# 1x1 PNG, the smallest image Claude Code accepts as a paste.
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQ"
                       "DwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


class Host:
    def __init__(self, target, ssh_options=()):
        self.target = target
        self.ssh_options = [arg for option in ssh_options for arg in ("-o", option)]

    def run(self, command, check=False):
        """One shot command, WITHOUT X11 forwarding."""
        result = subprocess.run(["ssh", "-x", *SSH_OPTS, self.target, command],
                                capture_output=True, text=True, timeout=60)
        if check and result.returncode != 0:
            raise RuntimeError(f"remote command failed: {command}\n{result.stderr}")
        return result.stdout

    def tmux(self, *args):
        return self.run(shlex.join(["tmux", "-L", SOCKET, *args]))

    def attach(self, term):
        """A tmux client attached over "ssh -tt" with the given TERM, WITHOUT X11.

        Everything tmux writes to that terminal is collected in the returned buffer."""
        client = subprocess.Popen(
            ["ssh", "-x", "-tt", *SSH_OPTS, self.target,
             f"TERM={shlex.quote(term)} exec tmux -L {SOCKET} attach -t {SESSION}"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        received = bytearray()

        def pump():
            for chunk in iter(lambda: client.stdout.read1(4096), b""):
                received.extend(chunk)
        threading.Thread(target=pump, daemon=True).start()
        return client, received

    def connect(self, command):
        """A long lived "ssh -X" connection running a LOGIN shell, as an operator's would.

        The X11 display it gets lives exactly as long as this process."""
        script = f"echo DISPLAY=$DISPLAY; {command}; exec sleep 3600"
        return subprocess.Popen(["ssh", "-X", *SSH_OPTS, *self.ssh_options, self.target,
                                 "bash -lc " + shlex.quote(script)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def read_display(connection):
    line = connection.stdout.readline().strip()
    return line.removeprefix("DISPLAY=")


# The local clipboard is reached through XWayland with xclip when there is a DISPLAY:
# GNOME keeps the X11 and Wayland clipboards in sync, and wl-copy/wl-paste need window
# focus on GNOME (there is no data-control protocol), which Mutter does not always grant -
# they then block forever. wl-clipboard remains the fallback for a desktop without X.
LOCAL_TIMEOUT = 5


def _local_tool():
    if os.environ.get("DISPLAY") and shutil.which("xclip"):
        return "xclip"
    if shutil.which("wl-paste"):
        return "wl"
    sys.exit("check_clipboard: needs xclip (with DISPLAY) or wl-clipboard locally")


def local_clipboard():
    if _local_tool() == "xclip":
        command = ["xclip", "-selection", "clipboard", "-o"]
    else:
        command = ["wl-paste", "--no-newline", "--type", "text"]
    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                timeout=LOCAL_TIMEOUT)
    except subprocess.TimeoutExpired:
        sys.exit(f"check_clipboard: reading the local clipboard with {command[0]} hangs")
    return result.stdout if result.returncode == 0 else None


def set_local_clipboard(content, mime="text/plain"):
    data = content.encode() if isinstance(content, str) else content
    if _local_tool() == "xclip":
        target = "UTF8_STRING" if mime == "text/plain" else mime
        command = ["xclip", "-selection", "clipboard", "-t", target, "-i"]
    else:
        command = ["wl-copy", "--type", mime]
    # Both fork a process that serves the selection and keeps stdout open, so
    # do not wait for output - only for the command to take the data.
    subprocess.run(command, input=data, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, check=True, timeout=LOCAL_TIMEOUT)


def wait_for(predicate, timeout=15, interval=0.5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return None


class Checker:
    def __init__(self, host, project):
        self.host = host
        self.project = project
        self.results = []

    def report(self, name, ok, detail=""):
        self.results.append(ok)
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"\n      {detail}" if detail else ""))

    def screen(self):
        return self.host.tmux("capture-pane", "-p", "-t", SESSION)

    def prompt_line(self):
        lines = [l for l in self.screen().splitlines() if l.startswith("❯")]
        return lines[-1] if lines else ""

    def clear_prompt(self):
        self.host.tmux("send-keys", "-t", SESSION, "C-e", "C-u")
        time.sleep(0.5)

    def claude_env(self, name):
        pid = self.host.tmux("display", "-p", "-t", SESSION, "#{pane_pid}").strip()
        environ = self.host.run(f"tr '\\0' '\\n' < /proc/{pid}/environ")
        for line in environ.splitlines():
            if line.startswith(name + "="):
                return line.split("=", 1)[1]
        return ""

    # ---- cases ------------------------------------------------------------
    def check_environment(self):
        xclip = self.host.run("bash -lc 'command -v xclip' || true").strip()
        self.report("xclip resolves to the display following shim", xclip == SHIM,
                    f"login shell finds: {xclip or '(nothing)'}")
        hooks = self.host.tmux("show-hooks", "-g", "after-load-buffer")
        self.report("tmux passes loaded buffers to xclip", "xclip" in hooks,
                    hooks.strip() or "no after-load-buffer hook")
        clipboard = self.host.tmux("show", "-sv", "set-clipboard").strip()
        self.report("tmux set-clipboard on (OSC 52 terminals)", clipboard == "on",
                    f"set-clipboard={clipboard}")

    def check_text_paste(self, label):
        """The bytes GNOME Terminal sends for its Paste action: a bracketed paste.

        Claude Code has no text paste of its own - Ctrl+V is image paste only - so
        text always comes from the terminal; this proves tmux and Claude take it."""
        token = "paste-" + secrets.token_hex(6)
        self.clear_prompt()
        self.host.tmux("send-keys", "-t", SESSION, "-l", f"{ESC}[200~{token}{ESC}[201~")
        line = wait_for(lambda: token in self.prompt_line() and self.prompt_line(), timeout=8)
        self.report(f"terminal paste reaches Claude ({label})", bool(line),
                    "" if line else "prompt: " + self.prompt_line().strip())
        self.clear_prompt()

    def check_image_paste(self, label):
        set_local_clipboard(PNG, mime="image/png")
        self.clear_prompt()
        self.host.tmux("send-keys", "-t", SESSION, "C-v")
        line = wait_for(lambda: "[Image" in self.prompt_line() and self.prompt_line(),
                        timeout=10)
        self.report(f"Ctrl+V pastes a local image ({label})", bool(line),
                    "" if line else "prompt: " + self.prompt_line().strip()
                    + "\n      " + self.status_line())
        self.clear_prompt()

    def check_copy(self, label):
        token = "copy-" + secrets.token_hex(6)
        set_local_clipboard("sentinel-" + secrets.token_hex(4))
        self.clear_prompt()
        self.host.tmux("send-keys", "-t", SESSION, "-l", token)
        position = wait_for(lambda: self.find(token))
        if not position:
            self.report(f"select + Ctrl+C copies to the local clipboard ({label})", False,
                        "token never appeared in the prompt")
            return
        self.drag(position, len(token))
        self.host.tmux("send-keys", "-t", SESSION, "C-c")
        got = wait_for(lambda: local_clipboard() == token, timeout=5)
        self.report(f"select + Ctrl+C copies to the local clipboard ({label})", bool(got),
                    "" if got else f"local clipboard: {local_clipboard()!r}\n      "
                    + self.status_line())
        self.clear_prompt()

    def drag(self, position, length):
        """Select length cells from position with the mouse, as a terminal reports it."""
        row, first = position
        last = first + length - 1
        # press, motion, release - SGR encoded, 1 based
        for event in (f"<0;{first};{row}M", f"<32;{(first + last) // 2};{row}M",
                      f"<32;{last};{row}M", f"<0;{last};{row}m"):
            self.host.tmux("send-keys", "-t", SESSION, "-l", ESC + "[" + event)
            time.sleep(0.15)
        time.sleep(0.5)

    def check_osc52_copy(self, term):
        name = f"select copies as OSC 52 to a {term} client, no X11"
        client, received = self.host.attach(term)
        try:
            attached = wait_for(lambda: term in self.host.tmux("list-clients"), timeout=10)
            if not attached:
                self.report(name, False, "client did not attach: "
                            + received.decode(errors="replace").strip()[-200:])
                return
            token = "osc52-" + secrets.token_hex(6)
            self.clear_prompt()
            self.host.tmux("send-keys", "-t", SESSION, "-l", token)
            position = wait_for(lambda: self.find(token))
            if not position:
                self.report(name, False, "token never appeared in the prompt")
                return
            self.drag(position, len(token))
            self.host.tmux("send-keys", "-t", SESSION, "C-c")
            sequence = b"]52;c;" + base64.b64encode(token.encode())
            got = wait_for(lambda: sequence in received, timeout=5)
            seen = re.findall(rb"\]52;[^;]*;", bytes(received))
            self.report(name, bool(got), "" if got else
                        f"OSC 52 sequences seen: {sorted(set(seen)) or 'none'}")
            self.clear_prompt()
        finally:
            client.terminate()
            client.wait()

    def check_all(self, label):
        self.check_text_paste(label)
        self.check_image_paste(label)
        self.check_copy(label)

    def find(self, text):
        for index, line in enumerate(self.screen().splitlines()):
            column = line.find(text)
            if column >= 0:
                # capture-pane drops nothing before the text on that row, so the str
                # index is the screen column for ASCII-only prefixes like "❯ "
                return index + 1, len(line[:column]) + 1
        return None

    def status_line(self):
        """Claude's own notice about where a copy went, if it showed one."""
        for line in self.screen().splitlines():
            if re.search(r"copied|OSC 52|clipboard|image", line, re.I):
                return "claude: " + line.strip()
        return "claude: (no clipboard notice on screen)"

    # ---- driver -----------------------------------------------------------
    def start_claude(self):
        ready = wait_for(lambda: self.prompt_line(), timeout=30)
        if not ready:
            print(self.screen())
            raise RuntimeError("Claude did not reach its prompt")

    def run_osc52(self, terms):
        print("OSC 52 route - no X11 forwarding")
        self.host.tmux("kill-server")
        try:
            self.host.tmux("new-session", "-d", "-s", SESSION, "-x", str(WIDTH),
                           "-y", str(HEIGHT), "-c", f"/home/{self.user()}/work/{self.project}",
                           "claude")
            self.start_claude()
            clipboard = self.host.tmux("show", "-sv", "set-clipboard").strip()
            self.report("tmux set-clipboard on", clipboard == "on", f"set-clipboard={clipboard}")
            self.check_text_paste("no X11")
            for term in terms:
                self.check_osc52_copy(term)
        finally:
            self.host.tmux("kill-server")

    def user(self):
        return self.host.run("id -un").strip()

    def run_x11(self):
        print("X11 route - ssh -X, reconnect")
        self.host.tmux("kill-server")

        cwd = f"~/work/{self.project}"
        first = self.host.connect(
            f"tmux -L {SOCKET} new-session -d -s {SESSION} -x {WIDTH} -y {HEIGHT}"
            f" -c {cwd} claude")
        second = None
        try:
            display = read_display(first)
            print(f"      connection 1: DISPLAY={display or '(none - X11 forwarding refused)'}")
            self.start_claude()
            print(f"      claude sees DISPLAY={self.claude_env('DISPLAY') or '(unset)'}")
            self.check_environment()
            self.check_all("first connection")

            # The operator comes back later on a new connection - while the old one is
            # still open, so the display number really differs - and the old one ends.
            second = self.host.connect("true")
            print(f"      connection 2: DISPLAY={read_display(second) or '(none)'}")
            time.sleep(1)
            first.terminate()
            first.wait()
            self.check_all("after reconnect, same Claude process")
        finally:
            self.host.tmux("kill-server")
            for connection in (first, second):
                if connection and connection.poll() is None:
                    connection.terminate()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("target", help="<user>@<host> of the operator account")
    parser.add_argument("--project", default="platform",
                        help="trusted directory under ~/work to start Claude in")
    parser.add_argument("--ssh-option", action="append", default=[], metavar="OPTION",
                        help="extra ssh -o option for the X11 connections, e.g."
                             " ForwardX11Trusted=no; repeatable")
    parser.add_argument("--route", choices=["all", "x11", "osc52"], default="all",
                        help="which clipboard route to check (default: all)")
    parser.add_argument("--term", action="append", metavar="TERM",
                        help="TERM to check the OSC 52 route with; repeatable"
                             f" (default: {' '.join(OSC52_TERMS)})")
    args = parser.parse_args()

    checker = Checker(Host(args.target, args.ssh_option), args.project)
    saved = local_clipboard()
    try:
        if args.route in ("all", "x11"):
            checker.run_x11()
        if args.route in ("all", "osc52"):
            checker.run_osc52(args.term or OSC52_TERMS)
    finally:
        if saved is not None:
            set_local_clipboard(saved)
    sys.exit(0 if all(checker.results) else 1)


if __name__ == "__main__":
    main()
