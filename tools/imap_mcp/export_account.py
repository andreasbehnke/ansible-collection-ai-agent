#!/usr/bin/env python3
"""Export an imap-mcp-server account into the password store.

The imap_mcp role restores the mailbox account from two pass entries. This script
creates or refreshes them from a machine that already ran the setup wizard:

    <prefix>/<account>/accounts.json   the AES-encrypted account store
    <prefix>/<account>/key             its encryption key (~/.imap-mcp/.key)

imap-mcp-server keeps both files in the service user's ~/.imap-mcp. They are
static once set up (unlike signal-cli's rotating state), so the service does not
need to be stopped to read them. Secret material is piped straight into `pass`
and never printed. Existing entries are overwritten, that is the point.

Before the first export the account has to exist. Add it once as the imap-mcp
service user (writes ~/.imap-mcp/accounts.json and .key) - see the role README
"One-time account setup" section - then run this script to copy both files into
the password store:

    ./export_account.py --account agent@example.com --host agent.example.org

Examples:
    ./export_account.py --account agent@example.com --host agent.example.org
    ./export_account.py --account agent@example.com --host agent.example.org \\
        --ssh-user admin --sudo-pass-entry private/network/admin@agent
    ./export_account.py --account agent@example.com --dry-run
"""

import argparse
import shlex
import subprocess
import sys

DEFAULT_PASS_PREFIX = "private/network/imap"
DEFAULT_HOME = "/var/lib/imap-mcp"


def pass_show(entry):
    """Return the raw bytes of a pass entry."""
    return subprocess.run(["pass", "show", entry], check=True,
                          stdout=subprocess.PIPE).stdout


def pass_insert(entry, data, dry_run):
    """Store bytes verbatim under a pass entry (multiline, overwrites)."""
    if dry_run:
        print(f"  would store {len(data)} bytes -> {entry}")
        return
    subprocess.run(["pass", "insert", "--multiline", "--force", entry],
                   input=data, check=True)
    print(f"  stored {len(data)} bytes -> {entry}")


class Target:
    """Runs privileged commands on the machine holding the imap-mcp state."""

    def __init__(self, host, ssh_user, sudo_pass_entry, host_key_checking):
        self.host = host
        self.ssh_user = ssh_user
        self.host_key_checking = host_key_checking
        self.sudo_password = None
        if sudo_pass_entry:
            self.sudo_password = pass_show(sudo_pass_entry) + b"\n"

    def read_file(self, path):
        """cat one root-only file, return its bytes."""
        command = f"cat {shlex.quote(path)}"
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

        proc = subprocess.run(argv, input=self.sudo_password,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if proc.returncode != 0:
            sys.exit(f"failed to read {path}: {proc.stderr.decode(errors='replace').strip()}")
        return proc.stdout


def main():
    parser = argparse.ArgumentParser(description="Export an imap-mcp account into pass.")
    parser.add_argument("--account", required=True,
                        help="account id / mailbox, the pass subdirectory name")
    parser.add_argument("--host", default="",
                        help="ssh host holding the state; empty runs locally")
    parser.add_argument("--ssh-user", default="")
    parser.add_argument("--sudo-pass-entry", default="",
                        help="pass entry with the sudo password of the ssh user")
    parser.add_argument("--host-key-checking", default="accept-new")
    parser.add_argument("--home", default=DEFAULT_HOME,
                        help=f"imap-mcp home holding .imap-mcp (default {DEFAULT_HOME})")
    parser.add_argument("--pass-prefix", default=DEFAULT_PASS_PREFIX)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    target = Target(args.host, args.ssh_user, args.sudo_pass_entry,
                    args.host_key_checking)
    base = f"{args.home}/.imap-mcp"
    prefix = f"{args.pass_prefix}/{args.account}"

    for src_name, pass_name in (("accounts.json", "accounts.json"), (".key", "key")):
        data = target.read_file(f"{base}/{src_name}")
        if not data:
            sys.exit(f"{base}/{src_name} is empty - set the account up first")
        pass_insert(f"{prefix}/{pass_name}", data, args.dry_run)

    print("done" if not args.dry_run else "dry-run complete")


if __name__ == "__main__":
    main()
