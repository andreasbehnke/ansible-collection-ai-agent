#!/usr/bin/env python3
"""Check Ctrl+C / Ctrl+V between this workstation and a Claude session on an operator host.

Drives a REAL Claude Code on the host the way the operator uses it: started in tmux from a
login shell of an "ssh -X" connection, reattached later from a NEW connection. Keys and the
mouse drag are injected with "tmux send-keys", in the same encoding the local terminal sends
them (SGR mouse reports), and the result is read from the REAL local clipboard with
wl-paste - so a PASS means the bytes reached this desktop, not a tmux buffer on the host.

Standard library only, per the collection's tooling convention. Needs wl-copy/wl-paste
locally (a Wayland session) and key based SSH to the operator account.

Usage:
    ./check_clipboard.py <user>@<host> [--project <dir under ~/work>]

Nothing is ever submitted: the script never presses Enter, so no prompt reaches the model.
It runs on a private tmux socket (-L clipcheck), kills that server when done and puts the
previous local clipboard text back.
"""

import argparse
import re
import secrets
import shlex
import subprocess
import sys
import time

SOCKET = "clipcheck"
SESSION = "c"
WIDTH, HEIGHT = 120, 40
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "ControlMaster=no",
            "-o", "ControlPath=none"]
ESC = "\033"


class Host:
    def __init__(self, target):
        self.target = target

    def run(self, command, check=False):
        """One shot command, WITHOUT X11 forwarding."""
        result = subprocess.run(["ssh", "-x", *SSH_OPTS, self.target, command],
                                capture_output=True, text=True, timeout=60)
        if check and result.returncode != 0:
            raise RuntimeError(f"remote command failed: {command}\n{result.stderr}")
        return result.stdout

    def tmux(self, *args):
        return self.run(shlex.join(["tmux", "-L", SOCKET, *args]))

    def connect(self, command):
        """A long lived "ssh -X" connection running a LOGIN shell, as an operator's would.

        The X11 display it gets lives exactly as long as this process."""
        script = f"echo DISPLAY=$DISPLAY; {command}; exec sleep 3600"
        return subprocess.Popen(["ssh", "-X", *SSH_OPTS, self.target,
                                 "bash -lc " + shlex.quote(script)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def read_display(connection):
    line = connection.stdout.readline().strip()
    return line.removeprefix("DISPLAY=")


def local_clipboard():
    result = subprocess.run(["wl-paste", "--no-newline"], capture_output=True, text=True)
    return result.stdout if result.returncode == 0 else None


def set_local_clipboard(text):
    subprocess.run(["wl-copy"], input=text, text=True, check=True)


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
        xclip = self.host.run("command -v xclip || true").strip()
        self.report("xclip on the host", bool(xclip), xclip or "not installed")
        clipboard = self.host.tmux("show", "-sv", "set-clipboard").strip()
        self.report("tmux set-clipboard on (OSC 52 terminals)", clipboard == "on",
                    f"set-clipboard={clipboard}")

    def check_paste(self, label):
        token = "paste-" + secrets.token_hex(6)
        set_local_clipboard(token)
        self.clear_prompt()
        self.host.tmux("send-keys", "-t", SESSION, "C-v")
        line = wait_for(lambda: token in self.prompt_line() and self.prompt_line(), timeout=8)
        self.report(f"Ctrl+V pastes the local clipboard ({label})", bool(line),
                    line or "prompt: " + self.prompt_line().strip()
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
        row, col = position
        first, last = col, col + len(token) - 1
        # press, motion, release - SGR encoded, 1 based, as the terminal reports them
        for event in (f"<0;{first};{row}M", f"<32;{(first + last) // 2};{row}M",
                      f"<32;{last};{row}M", f"<0;{last};{row}m"):
            self.host.tmux("send-keys", "-t", SESSION, "-l", ESC + "[" + event)
            time.sleep(0.15)
        time.sleep(0.5)
        self.host.tmux("send-keys", "-t", SESSION, "C-c")
        got = wait_for(lambda: local_clipboard() == token, timeout=5)
        self.report(f"select + Ctrl+C copies to the local clipboard ({label})", bool(got),
                    "" if got else f"local clipboard: {local_clipboard()!r}\n      "
                    + self.status_line())
        self.clear_prompt()

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
    def run(self):
        self.host.tmux("kill-server")

        cwd = f"~/work/{self.project}"
        first = self.host.connect(
            f"tmux -L {SOCKET} new-session -d -s {SESSION} -x {WIDTH} -y {HEIGHT}"
            f" -c {cwd} claude")
        second = None
        try:
            display = read_display(first)
            print(f"      connection 1: DISPLAY={display or '(none - X11 forwarding refused)'}")
            ready = wait_for(lambda: self.prompt_line(), timeout=30)
            if not ready:
                print(self.screen())
                raise RuntimeError("Claude did not reach its prompt")
            print(f"      claude sees DISPLAY={self.claude_env('DISPLAY') or '(unset)'}")
            self.check_environment()
            self.check_paste("first connection")
            self.check_copy("first connection")

            # The operator comes back later on a new connection - while the old one is
            # still open, so the display number really differs - and the old one ends.
            second = self.host.connect("true")
            print(f"      connection 2: DISPLAY={read_display(second) or '(none)'}")
            time.sleep(1)
            first.terminate()
            first.wait()
            self.check_paste("after reconnect, same Claude process")
            self.check_copy("after reconnect, same Claude process")
        finally:
            self.host.tmux("kill-server")
            for connection in (first, second):
                if connection and connection.poll() is None:
                    connection.terminate()
        return all(self.results)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("target", help="<user>@<host> of the operator account")
    parser.add_argument("--project", default="platform",
                        help="trusted directory under ~/work to start Claude in")
    args = parser.parse_args()

    saved = local_clipboard()
    try:
        ok = Checker(Host(args.target), args.project).run()
    finally:
        if saved is not None:
            set_local_clipboard(saved)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
