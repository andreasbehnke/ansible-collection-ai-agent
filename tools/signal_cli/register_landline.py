#!/usr/bin/env python3
"""Register a Signal account by VOICE verification on a signal_cli host.

This automates the fiddly, interactive registration flow needed for a *landline*
number (which cannot receive the SMS code, so Signal must place a voice call).

It drives `signal-cli` over SSH on the target machine. The operator only has to:

  1. solve one captcha in a browser and paste the token, and
  2. read back the 6-digit code from the automated phone call.

See roles/signal_cli/README.md for the full process and troubleshooting.

The remote signal-cli commands run as:
    ssh <ssh-user>@<host> "sudo -u <signal-user> -H signal-cli --config <data-dir> -a <number> ..."
so <ssh-user> needs sudo rights on the target: either passwordless, or with the
sudo password taken from the password store via --sudo-pass-entry.
"""

import argparse
import json
import shlex
import subprocess
import sys
import time

CAPTCHA_URL = "https://signalcaptchas.org/registration/generate.html"
DEFAULT_DATA_DIR = "/var/lib/signal-cli"
DEFAULT_API_URL = "http://127.0.0.1:8080"


class Remote:
    """Runs signal-cli / systemctl commands on the target host over SSH."""

    def __init__(self, host, ssh_user, signal_user, service, data_dir, api_url,
                 number, host_key_checking, sudo_password):
        self.host = host
        self.ssh_user = ssh_user
        self.signal_user = signal_user
        self.service = service
        self.data_dir = data_dir
        self.api_url = api_url
        self.number = number
        self.host_key_checking = host_key_checking
        self.sudo_password = sudo_password

    @property
    def sudo(self):
        """sudo invocation, reading the password from stdin when we have one."""
        return "sudo -S -p ''" if self.sudo_password else "sudo"

    @property
    def target(self):
        return f"{self.ssh_user}@{self.host}" if self.ssh_user else self.host

    def _ssh(self, remote_cmd):
        ssh_cmd = [
            "ssh",
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=10",
            "-o", f"StrictHostKeyChecking={self.host_key_checking}",
            self.target,
            remote_cmd,
        ]
        proc = subprocess.run(ssh_cmd, input=self.sudo_password, text=True,
                              capture_output=True)
        # signal-cli splits messages between stdout and stderr; callers want both.
        proc.combined = (proc.stdout or "") + (proc.stderr or "")
        return proc

    def signal(self, *args):
        # --config is passed explicitly, even though /etc/signal-cli/config.json
        # already points at the same data dir.
        inner = ["-u", self.signal_user, "-H",
                 "signal-cli", "--config", self.data_dir, "-a", self.number, *args]
        return self._ssh(f"{self.sudo} " + " ".join(shlex.quote(a) for a in inner))

    def systemctl(self, action):
        return self._ssh(f"{self.sudo} systemctl {action} {shlex.quote(self.service)}")

    def raw(self, remote_cmd):
        return self._ssh(remote_cmd)


# ---- small console helpers -------------------------------------------------

def step(msg):
    print(f"\n\033[1;36m==> {msg}\033[0m")


def info(msg):
    print(f"    {msg}")


def warn(msg):
    print(f"\033[1;33m    ! {msg}\033[0m")


def fail(msg):
    print(f"\033[1;31m    ERROR: {msg}\033[0m")
    sys.exit(1)


def prompt(msg):
    try:
        return input(f"\033[1;35m{msg}\033[0m ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        fail("aborted by operator")


def pass_show(entry):
    proc = subprocess.run(["pass", "show", entry], text=True, capture_output=True)
    if proc.returncode != 0:
        fail(f"pass show {entry} failed: {proc.stderr.strip()}")
    return proc.stdout.strip() + "\n"


# ---- flow ------------------------------------------------------------------

def check_reachable(r):
    step(f"Checking SSH access to {r.target}")
    if r.raw("true").returncode != 0:
        fail(f"cannot reach {r.target} over SSH")
    # A cheap check that sudo to the signal user works.
    probe = r.raw(f"{r.sudo} -u {shlex.quote(r.signal_user)} -H signal-cli --version")
    if probe.returncode != 0 and "sudo" in probe.combined.lower():
        fail("sudo for signal-cli failed; check the ssh user's sudo rights or pass "
             "the sudo password with --sudo-pass-entry")
    info(f"reachable, {probe.stdout.strip() or 'signal-cli present'}")


def already_registered(r):
    """True if this number already has a registered (verified) account.

    signal-cli records accounts in accounts.json; a registered account has a
    non-null uuid (an unverified/half-registered one has "uuid": null).
    """
    out = r.raw(
        f"{r.sudo} cat "
        f"{shlex.quote(r.data_dir)}/data/accounts.json "
        "2>/dev/null"
    ).stdout
    try:
        accounts = json.loads(out).get("accounts", [])
    except (ValueError, AttributeError):
        return False
    return any(a.get("number") == r.number and a.get("uuid") for a in accounts)


def stop_service(r):
    step(f"Stopping {r.service} (release the account lock)")
    r.systemctl("stop")
    info("stopped")


def wipe_local_session(r):
    step(f"Wiping any stale local session for {r.number}")
    r.signal("deleteLocalAccountData", "--ignore-registered")
    # A missing account is fine here.
    info("clean slate")


def request_voice(r, token):
    # For a landline the SMS transport is rejected (InvalidTransportModeException);
    # that call still creates/primes the registration session, which is required
    # before a voice code can be requested.
    step("Priming the registration session (SMS attempt; invalid for a landline)")
    res = r.signal("register", "--captcha", token)
    combined = res.combined
    if "InvalidTransportModeException" in combined:
        info("SMS not available for this number (expected) - session primed")
    elif res.returncode == 0:
        info("session created")
    elif "Captcha" in combined or "Authorization" in combined:
        fail(f"captcha rejected - get a FRESH token from {CAPTCHA_URL}\n{combined.strip()}")
    else:
        warn(combined.strip() or "unexpected response; continuing to voice request")

    step("Requesting the VOICE call")
    res = r.signal("register", "--voice", "--captcha", token)
    if res.returncode == 0 and "Failed" not in res.combined:
        info(f"voice call requested - Signal is now calling {r.number}")
        return
    if "Authorization" in res.combined or "Captcha" in res.combined:
        fail(f"captcha consumed/invalid - re-run and paste a FRESH token from {CAPTCHA_URL}\n{res.combined.strip()}")
    if "Before requesting voice" in res.combined:
        fail("server wants the SMS step first; wait ~60s and re-run (with a fresh captcha)")
    fail(res.combined.strip() or "voice request failed")


def verify(r, code):
    step(f"Verifying code for {r.number}")
    res = r.signal("verify", code)
    if res.returncode == 0 and "error" not in res.combined.lower():
        info("verified")
        return
    if "404" in res.combined:
        fail("verification session expired (404) - re-run the whole flow cleanly "
             "and enter the code immediately after the call")
    fail(res.combined.strip() or "verification failed")


def start_and_check(r):
    step(f"Starting {r.service}")
    r.systemctl("start")
    time.sleep(6)
    active = r.systemctl("is-active").stdout.strip()
    info(f"service is: {active}")
    check_url = f"{r.api_url.rstrip('/')}/api/v1/check"
    code = r.raw(
        f'curl -s -o /dev/null -w "%{{http_code}}" {shlex.quote(check_url)}'
    ).stdout.strip()
    info(f"daemon {check_url} -> HTTP {code or '???'}")
    if active == "active" and code == "200":
        print("\n\033[1;32m*** Registration complete: signal-cli daemon is running. ***\033[0m")
    else:
        warn("service/daemon not fully healthy yet - check: "
             f"ssh {r.target} 'sudo journalctl -u {r.service} -n 30'")


def main():
    ap = argparse.ArgumentParser(
        description="Register a Signal landline (voice) on a signal_cli host.")
    ap.add_argument("--number", required=True,
                    help="E.164 phone number to register, e.g. +49123456789")
    ap.add_argument("--host", required=True,
                    help="host running signal-cli")
    ap.add_argument("--ssh-user", default=None,
                    help="SSH user with sudo rights (default: current user)")
    ap.add_argument("--sudo-pass-entry", default=None,
                    help="pass entry holding the sudo password of the SSH user; "
                         "omit when sudo works without a password")
    ap.add_argument("--signal-user", default="signal",
                    help="local user that owns signal-cli data (default: %(default)s)")
    ap.add_argument("--service", default="signal-cli",
                    help="systemd unit for the daemon (default: %(default)s)")
    ap.add_argument("--data-dir", default=DEFAULT_DATA_DIR,
                    help="signal-cli data directory on the target (default: %(default)s)")
    ap.add_argument("--api-url", default=DEFAULT_API_URL,
                    help="REST api base url on the target (default: %(default)s)")
    ap.add_argument("--host-key-checking", default="accept-new",
                    choices=["accept-new", "yes", "no"],
                    help="ssh StrictHostKeyChecking (default: %(default)s)")
    ap.add_argument("--force", action="store_true",
                    help="re-register even if the number already looks registered "
                         "(this wipes the existing account!)")
    args = ap.parse_args()

    sudo_password = pass_show(args.sudo_pass_entry) if args.sudo_pass_entry else None
    r = Remote(args.host, args.ssh_user, args.signal_user, args.service,
               args.data_dir, args.api_url, args.number, args.host_key_checking,
               sudo_password)

    print(f"Signal landline registration for {args.number} on {args.host}")
    check_reachable(r)

    if already_registered(r) and not args.force:
        fail(f"{args.number} already appears registered on {args.host}. "
             "Re-run with --force to wipe and re-register.")

    stop_service(r)
    wipe_local_session(r)

    step("Solve the captcha")
    info(f"1) open {CAPTCHA_URL}")
    info("2) solve it, then RIGHT-CLICK the 'Open Signal' link and Copy link")
    info("   (it starts with signalcaptcha://). Do NOT click the link.")
    token = prompt("Paste the signalcaptcha:// token:")
    if not token.startswith("signalcaptcha://"):
        warn("token does not start with signalcaptcha:// - continuing anyway")

    request_voice(r, token)

    info(f"\n    Signal is placing an automated voice call to {args.number}.")
    info("    Answer it and note the 6-digit code (it is usually repeated).")
    code = prompt("Enter the 6-digit code from the call:").replace("-", "").replace(" ", "")

    verify(r, code)
    start_and_check(r)


if __name__ == "__main__":
    main()
