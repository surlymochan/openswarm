"""Shared Windows spawn fix for the bin/ scripts (single implementation).

Windows cannot CreateProcess a script directly (WinError 193): when the
target is a file with a python shebang, re-invoke it with this interpreter.
POSIX executes shebangs natively and never takes the fallback branch.

Loaded by bin/openswarm, bin/openswarm_consult.py, bin/hub-fleet-gateway
and bin/fleet-capacity via a script-directory importlib load (the same
pattern bin/openswarm uses for openswarm_consult.py), so every script in
bin/ shares this one implementation instead of carrying its own copy.
Deployments ship the whole bin/ tree, so the sibling module is always
present next to its consumers.
"""
import os
import sys


def spawnable(cmd):
    if os.name != "nt" or not cmd:
        return cmd
    head = str(cmd[0])
    if head.lower().endswith((".exe", ".com", ".bat", ".cmd")):
        return cmd
    try:
        with open(head, "rb") as fh:
            first = fh.readline(256).decode("utf-8", "replace")
    except OSError:
        return cmd
    if first.startswith("#!") and "python" in first:
        return [sys.executable, *cmd]
    return cmd
