# Executor operation

The default models are selected for the accounts checked during this recovery. Model
catalogues and billing can change. Codex probes the signed-in account's model list;
Codex is configured for `gpt-6-luna` with high effort and fails closed when the
account does not offer it. Copilot `auto` reports the selected model in one-shot attempt metadata. Cursor's
one-shot default requests Composer 2.5 standard speed. Cursor ACP currently exposes
Composer 2.5 fast as its default, so its cost can differ from one-shot runs.

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
