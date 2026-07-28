#!/usr/bin/env python3
"""Initialize an imap-mcp-server mailbox account for the first time.

The imap_mcp role restores an account from the password store, but the account
has to exist once before it can be exported. imap-mcp-server has no add-account
CLI - accounts are created through its `imap_add_account` MCP tool. This script
drives that tool over a one-shot stdio MCP session, run as the imap-mcp service
user on the agent host, so the encrypted account store lands in the right place:

    {home}/.imap-mcp/accounts.json   the AES-encrypted account store
    {home}/.imap-mcp/.key            its encryption key

It then (unless --no-test) calls imap_test_account to confirm the mailbox is
reachable. Afterwards run export_account.py to copy both files into `pass`.

The IMAP password is read from a pass entry (--imap-password-entry) or prompted
interactively; it is never taken on the command line and never printed.

Examples:
    ./init_account.py --account agent@example.com \\
        --imap-host imap.example.com --imap-user agent@example.com \\
        --imap-password-entry private/network/imap/agent@example.com/password \\
        --host agent.example.org --sudo-pass-entry private/network/admin@agent
    ./init_account.py --account agent --imap-host imap.example.com \\
        --imap-user agent --host agent.example.org        # prompts for password
"""

import argparse
import getpass
import json
import subprocess
import sys

DEFAULT_HOME = "/var/lib/imap-mcp"
DEFAULT_CODE_DIR = "/usr/local/lib/imap-mcp-server"
DEFAULT_SERVICE_USER = "imap-mcp"
DEFAULT_PASS_PREFIX = "private/network/imap"


def pass_show(entry):
    out = subprocess.run(["pass", "show", entry], check=True,
                         stdout=subprocess.PIPE).stdout
    return out.decode().splitlines()[0] if out else ""


def handshake():
    return [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                    "clientInfo": {"name": "init_account", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
    ]


def add_call(args, password):
    add_args = {
        "name": args.account,
        "host": args.imap_host,
        "port": args.imap_port,
        "user": args.imap_user,
        "password": password,
        "tls": not args.no_tls,
    }
    if args.email:
        add_args["email"] = args.email
    return {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "imap_add_account", "arguments": add_args}}


def test_call(account_id):
    # imap_test_account takes only the accountId returned by imap_add_account.
    return {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "imap_test_account",
                       "arguments": {"accountId": account_id}}}


def run_server(args, messages, sudo_password):
    """Feed the JSON-RPC session to a one-shot imap-mcp-server, return stdout."""
    # Run the server as the service user so ~/.imap-mcp lands in its home; do not
    # restrict IMAP_MCP_ENABLED_TOOLS here - this admin bootstrap legitimately
    # uses imap_add_account, which the agent-facing service never exposes.
    inner = (f"env HOME={args.home} node {args.code_dir}/dist/index.js")
    if sudo_password:
        remote = f"sudo -S -p '' -u {args.service_user} {inner}"
    else:
        remote = f"sudo -n -u {args.service_user} {inner}"
    remote = f"timeout 60 {remote}"

    if args.host:
        argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                "-o", f"StrictHostKeyChecking={args.host_key_checking}",
                f"{args.ssh_user}@{args.host}" if args.ssh_user else args.host,
                remote]
    else:
        argv = ["bash", "-c", remote]

    stdin = ""
    if sudo_password:
        stdin += sudo_password + "\n"
    stdin += "".join(json.dumps(m) + "\n" for m in messages)

    proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    try:
        out, _ = proc.communicate(input=stdin, timeout=70)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate()
    return out


def find_result(stdout, msg_id):
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if m.get("id") == msg_id:
            return m
    return None


def result_text(msg):
    """Flatten a tools/call result's content array into text."""
    if not msg:
        return ""
    result = msg.get("result") or {}
    parts = [c.get("text", "") for c in result.get("content", [])
             if isinstance(c, dict)]
    return "\n".join(p for p in parts if p).strip()


def main():
    parser = argparse.ArgumentParser(
        description="Initialize an imap-mcp mailbox account on the agent host.")
    parser.add_argument("--account", required=True,
                        help="account name (also the pass subdir for export)")
    parser.add_argument("--imap-host", required=True)
    parser.add_argument("--imap-user", required=True)
    parser.add_argument("--imap-port", type=int, default=993)
    parser.add_argument("--no-tls", action="store_true", help="disable TLS (default on)")
    parser.add_argument("--email", default="", help="From: address, defaults to the user")
    parser.add_argument("--imap-password-entry", default="",
                        help="pass entry holding the mailbox password (else prompt)")
    parser.add_argument("--no-test", action="store_true",
                        help="skip the imap_test_account connection check")
    parser.add_argument("--host", default="", help="ssh host; empty runs locally")
    parser.add_argument("--ssh-user", default="")
    parser.add_argument("--sudo-pass-entry", default="",
                        help="pass entry with the sudo password of the ssh user")
    parser.add_argument("--host-key-checking", default="accept-new")
    parser.add_argument("--home", default=DEFAULT_HOME)
    parser.add_argument("--code-dir", default=DEFAULT_CODE_DIR)
    parser.add_argument("--service-user", default=DEFAULT_SERVICE_USER)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.dry_run:
        print("would run imap_add_account on "
              f"{args.host or 'localhost'} as {args.service_user}, then "
              "imap_test_account with the returned accountId:")
        print(json.dumps(handshake() + [add_call(args, "***")], indent=2))
        return

    if args.imap_password_entry:
        password = pass_show(args.imap_password_entry)
    else:
        password = getpass.getpass(f"IMAP password for {args.imap_user}: ")
    if not password:
        sys.exit("no IMAP password given")

    sudo_password = pass_show(args.sudo_pass_entry) if args.sudo_pass_entry else ""

    # 1) add the account
    stdout = run_server(args, handshake() + [add_call(args, password)], sudo_password)
    add = find_result(stdout, 2)
    if add is None:
        sys.exit("no response from imap_add_account - is imap-mcp-server "
                 f"installed at {args.code_dir} and node on PATH?\n{stdout[:500]}")
    if "error" in add or (add.get("result") or {}).get("isError"):
        sys.exit(f"imap_add_account failed: {result_text(add) or add.get('error')}")

    try:
        account_id = json.loads(result_text(add)).get("accountId")
    except ValueError:
        account_id = None
    print(f"account added: {result_text(add) or args.account}")

    # 2) test the connection with the returned accountId
    if not args.no_test and account_id:
        stdout2 = run_server(args, handshake() + [test_call(account_id)], sudo_password)
        test = find_result(stdout2, 2)
        text = result_text(test)
        if test is None:
            print("warning: no imap_test_account response (account was still added)")
        elif "error" in test or (test.get("result") or {}).get("isError"):
            print(f"warning: connection test failed - check the credentials:\n  {text}")
        else:
            print(f"connection test ok: {text or 'reachable'}")
    elif not args.no_test:
        print("warning: could not read accountId from the add response, skipping test")

    print(f"\naccount store written under {args.home}/.imap-mcp/ (accounts.json + .key).")
    print("next: export it into the password store, e.g.")
    export_host = f" --host {args.host}" if args.host else ""
    export_sudo = f" --sudo-pass-entry {args.sudo_pass_entry}" if args.sudo_pass_entry else ""
    print(f"  ./export_account.py --account {args.account}{export_host}{export_sudo}")


if __name__ == "__main__":
    main()
