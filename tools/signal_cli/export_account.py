#!/usr/bin/env python3
"""Export a registered signal-cli account into the password store.

The signal_cli role reproduces an account from three pass entries. This script
creates or refreshes all of them from a machine that already runs the daemon:

    <prefix>/<number>/accounts.json      the account registry
    <prefix>/<number>/account.json       the account identity (data/<path>)
    <prefix>/<number>/state.tar.gz.b64   the runtime state (data/<path>.d/)

The daemon is stopped while the files are read, so the sqlite database inside the
state snapshot is consistent, and started again afterwards - also when the export
fails. Secret material is piped straight into `pass` and never printed.

Existing entries are overwritten, that is the point of a refresh.

Examples:
    ./export_account.py --number +49123456789 --host agent.example.org
    ./export_account.py --number +49123456789 --host agent.example.org \\
        --ssh-user admin --sudo-pass-entry private/network/admin@agent
    ./export_account.py --number +49123456789 --skip-state --dry-run
"""

import argparse
import json
import shlex
import subprocess
import sys

DEFAULT_PASS_PREFIX = "private/network/signal"
DEFAULT_DATA_DIR = "/var/lib/signal-cli"
DEFAULT_SERVICE = "signal-cli"


class Target:
    """Runs privileged commands on the machine holding the signal-cli state."""

    def __init__(self, host, ssh_user, sudo_pass_entry, host_key_checking):
        self.host = host
        self.ssh_user = ssh_user
        self.host_key_checking = host_key_checking
        self.sudo_password = None
        if sudo_pass_entry:
            self.sudo_password = pass_show(sudo_pass_entry) + b"\n"

    def run(self, command):
        """Run one shell command as root, return its stdout as bytes."""
        if self.sudo_password:
            command = f"sudo -S -p '' {command}"
        else:
            command = f"sudo -n {command}"

        if self.host:
            argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                    "-o", f"StrictHostKeyChecking={self.host_key_checking}",
                    f"{self.ssh_user}@{self.host}" if self.ssh_user else self.host,
                    command]
        else:
            argv = ["bash", "-c", command]

        proc = subprocess.run(argv, input=self.sudo_password, capture_output=True)
        if proc.returncode != 0:
            err = proc.stderr.decode(errors="replace").strip()
            sys.exit(f"failed on target: {command}\n{err}")
        return proc.stdout

    def systemctl(self, action, service):
        self.run(f"systemctl {action} {shlex.quote(service)}")


def pass_show(entry):
    proc = subprocess.run(["pass", "show", entry], capture_output=True)
    if proc.returncode != 0:
        sys.exit(f"pass show {entry} failed: {proc.stderr.decode().strip()}")
    return proc.stdout.strip()


def pass_insert(entry, content):
    proc = subprocess.run(["pass", "insert", "-m", "-f", entry],
                          input=content, capture_output=True)
    if proc.returncode != 0:
        sys.exit(f"pass insert {entry} failed: {proc.stderr.decode().strip()}")
    print(f"  stored {entry} ({len(content)} bytes)")


def main():
    ap = argparse.ArgumentParser(
        description="Export a signal-cli account into the password store.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    ap.add_argument("--number", required=True,
                    help="E.164 number of the account, e.g. +49123456789")
    ap.add_argument("--host", default=None,
                    help="host running signal-cli (default: local machine)")
    ap.add_argument("--ssh-user", default=None,
                    help="SSH user on that host (default: current user)")
    ap.add_argument("--sudo-pass-entry", default=None,
                    help="pass entry holding the sudo password of the SSH user; "
                         "omit when sudo works without a password")
    ap.add_argument("--data-dir", default=DEFAULT_DATA_DIR,
                    help="signal-cli data directory (default: %(default)s)")
    ap.add_argument("--service", default=DEFAULT_SERVICE,
                    help="systemd unit of the daemon (default: %(default)s)")
    ap.add_argument("--pass-prefix", default=DEFAULT_PASS_PREFIX,
                    help="password store prefix (default: %(default)s)")
    ap.add_argument("--skip-state", action="store_true",
                    help="do not export the runtime state snapshot (groups, "
                         "contacts and sessions stay unrestorable)")
    ap.add_argument("--keep-running", action="store_true",
                    help="do not stop the daemon; the state snapshot may be "
                         "inconsistent")
    ap.add_argument("--host-key-checking", default="accept-new",
                    choices=["accept-new", "yes", "no"],
                    help="ssh StrictHostKeyChecking (default: %(default)s)")
    ap.add_argument("--dry-run", action="store_true",
                    help="read everything, but write nothing to the password store")
    args = ap.parse_args()

    target = Target(args.host, args.ssh_user, args.sudo_pass_entry,
                    args.host_key_checking)
    data = args.data_dir.rstrip("/") + "/data"
    prefix = f"{args.pass_prefix.rstrip('/')}/{args.number}"
    where = args.host or "local machine"
    stopped = False

    print(f"exporting {args.number} from {where}")
    try:
        if not (args.keep_running or args.dry_run):
            print(f"stopping {args.service}")
            target.systemctl("stop", args.service)
            stopped = True

        accounts_raw = target.run(f"cat {shlex.quote(data + '/accounts.json')}")
        accounts = json.loads(accounts_raw)
        try:
            entry = next(a for a in accounts["accounts"]
                         if a.get("number") == args.number)
        except StopIteration:
            sys.exit(f"{args.number} is not in {data}/accounts.json")
        path = entry["path"]
        print(f"  registry version {accounts.get('version')}, "
              f"data file data/{path}, registered={bool(entry.get('uuid'))}")

        account_raw = target.run(f"cat {shlex.quote(data + '/' + path)}")

        state_raw = None
        if not args.skip_state:
            state_raw = target.run(
                f"tar czf - -C {shlex.quote(data)} {shlex.quote(path + '.d')} "
                f"| base64 -w0")
    finally:
        if stopped:
            print(f"starting {args.service}")
            target.systemctl("start", args.service)

    if args.dry_run:
        print(f"dry run, nothing written; would store below {prefix}: "
              f"accounts.json ({len(accounts_raw)} bytes), "
              f"account.json ({len(account_raw)} bytes)"
              + (f", state.tar.gz.b64 ({len(state_raw)} bytes)" if state_raw else ""))
        return

    print("writing to the password store")
    pass_insert(f"{prefix}/accounts.json", accounts_raw)
    pass_insert(f"{prefix}/account.json", account_raw)
    if state_raw is not None:
        pass_insert(f"{prefix}/state.tar.gz.b64", state_raw)
    else:
        print("  skipped state.tar.gz.b64")
    print("done")


if __name__ == "__main__":
    main()
