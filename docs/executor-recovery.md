# Executor operation

The default models are selected for the accounts checked during this recovery. Model
catalogues and billing can change. Codex probes the signed-in account's model list;
Codex is configured for `gpt-6-luna` with high effort and fails closed when the
account does not offer it. The gocc lane uses the newer ChatGPT-bundled CLI.
Copilot routes difficulty 1–3 to Haiku 5.5 and difficulty 4–5 to Sonnet 5.5,
both at medium effort; resident lanes GH1 and GH2 select those models explicitly.
Cursor's K1 lane uses print mode with `composer-2.5[fast=false]`: ACP currently
resolves the bare Composer 2.5 model to the Fast variant. A print-mode lane starts
a fresh process per turn and preserves the sandbox and explicit standard model.

Pi uses a Linux bubblewrap launcher and a single-model MiniMax broker. The broker runs
on the host that already has the Pi credential; the worker never receives that
credential. The worker needs bubblewrap, socat, and a Pi 0.73-compatible installation.
The launcher disables extension, skill and context discovery, starts with an empty
environment, and lets the sandbox reach only the broker socket. The broker accepts
only the configured model and the MiniMax messages endpoint.
Each broker process accepts at most 64 inference requests and exits so its login
agent can restart it.

The persistent deployment uses a restricted SSH key through the existing relay,
the `app.openswarm.pi-broker` macOS login agent, and the
`openswarm-pi-proxy.service` Linux service. The worker socket is
`/run/openswarm-pi/broker.sock`; the Pi lane service sets
`OPENSWARM_PI_BROKER_SOCKET` explicitly so startup does not race socket creation.
The macOS broker starts when the graphical user session is available. The worker
service reconnects through SSH for each request. The key on the relay permits only
the designated forwarding destination, and the key on the broker host forces the
broker stdio command.

As a temporary alternative, from a machine with the existing SSH identities, start
the bounded bridge:

```sh
bin/pi-fleet-bridge --broker-host gocc --worker gores1 --minutes 120
```

The command prints the worker socket path. On the worker, set
`OPENSWARM_PI_BROKER_SOCKET` to that path and `OPENSWARM_PI_RUNTIME` to the isolated
Pi installation prefix, then run the candidate's `bin/openswarm-fleet`. This uses a
separate OpenSwarm state directory, preserving the existing fleet configuration. Keep the bridge
alive for the run. Interrupting it or reaching its time limit closes its SSH sessions
and removes its private sockets. It is a transport process, not an executor.

Pi read-only review and consult use read-only workspace mounts and the read-only
tool set. Read-only assignments to a resident writable Pi lane are rejected.
One-shot isolated Pi cannot resume a rate-limited session;
resident RPC Pi keeps its session inside the sandbox. A missing bridge, unsupported
host, unapproved model or unisolated binary fails closed. Do not turn off
`pi.require_isolation` on a host containing credentials or other valuable data.

Copilot on macOS uses the system keychain in the graphical login session. The
`copilot-gui-run` bridge starts one-shot jobs in that session and terminates their
process group when the caller disconnects. The Copilot sandbox restricts shell
commands; built-in file edits use the CLI's best-effort policy rather than an OS
sandbox.

## Fleet dispatch and admission

On gores1, `openswarm auto --type impl --difficulty 3 --prompt '...' --verify '...'`
chooses a currently idle lane on gores1 or gocc. Use `--dry-run` to inspect the
choice. Difficulty 1–3 selects Copilot Haiku when Copilot is chosen; difficulty
4–5 selects Sonnet. Review and research profiles use isolated, read-only Pi
one-shot execution. The dispatcher does not replace the legacy `openswarm run`
default or integrate the OpenBot Hub's own dispatcher.

Both candidate installations use `/etc/openswarm-fleet-capacity.json` regardless
of `OPENSWARM_HOME`. Admission is recorded in a SQLite coordinator on gocc, with
host limits of two per host and a fleet total of eight. The provider/backend caps
are enforced as well. gores1 connects with a separate forced-command SSH key;
the worker cannot obtain a shell through that key. A missing or unreachable
coordinator rejects new executor admissions. A crash or uncertain response can
leave a persistent reservation; abnormal executor exits retain their reservation
for inspection. Reservations are never released just because a timer expired.

Inspect reservations on gocc with:

```sh
printf '{"op":"status"}' | ~/tools/openswarm-executor-candidate/bin/fleet-capacity gocc
```

For an orphan, first independently verify that the named run and every executor
child and descendant are stopped on its host. Only then may a local gocc
operator submit `{"op":"recover","host":"gores1","run_id":"...",`
`"confirm":"all-executor-processes-stopped"}` to the same command. This
manual recovery is not available over the worker's restricted key.

The guard covers the candidate's run, lane, review and consult execution paths;
unmodified old OpenSwarm binaries and separately started tools can bypass it.
The legacy govpn2 K2 Fast service is disabled; do not re-enable it as part of
this fleet. These limits are not evidence that unconfigured hosts are controlled.
