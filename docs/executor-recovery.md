# Executor operation

The default models are selected for the accounts checked during this recovery. Model
catalogues and billing can change. Codex probes the signed-in account's model list;
Copilot `auto` reports the selected model in one-shot attempt metadata. Cursor's
one-shot default requests Composer 2.5 standard speed. Cursor ACP currently exposes
Composer 2.5 fast as its default, so its cost can differ from one-shot runs.

Pi uses a Linux bubblewrap launcher and a single-model MiniMax broker. The broker runs
on the host that already has the Pi credential; the worker never receives that
credential. The worker needs bubblewrap, socat, and a Pi 0.73-compatible installation.
The launcher disables extension, skill and context discovery, starts with an empty
environment, and lets the sandbox reach only the broker socket. The broker accepts
only the configured model and the MiniMax messages endpoint.
Each broker process accepts at most 64 inference requests; restart the bridge when
that limit is reached.

From a machine with the existing SSH identities, start the bounded bridge:

```sh
bin/pi-fleet-bridge --broker-host gocc --worker gores1 --minutes 120
```

The command prints the worker socket path. On the worker, set
`OPENSWARM_PI_BROKER_SOCKET` to that path and `OPENSWARM_PI_RUNTIME` to the isolated
Pi installation prefix, then run the candidate's `bin/openswarm-fleet`. This uses a
separate OpenSwarm state directory, preserving the existing fleet configuration. Keep the bridge
alive for the run. Interrupting it or reaching its time limit closes its SSH sessions
and removes its private sockets. It is a transport process, not an executor.

Pi read-only review and consult are deliberately rejected until their own sandbox
contract is verified. One-shot isolated Pi cannot resume a rate-limited session;
resident RPC Pi keeps its session inside the sandbox. A missing bridge, unsupported
host, unapproved model or unisolated binary fails closed. Do not turn off
`pi.require_isolation` on a host containing credentials or other valuable data.

Copilot on macOS uses the normal GitHub device login. If the system keychain is
unavailable, its CLI may store the credential in plaintext under `COPILOT_HOME`;
restrict the directory and files to the owning account and treat that location as a
credential store. The Copilot sandbox restricts shell commands; built-in file edits
use the CLI's best-effort policy rather than an OS sandbox.
