# unraid-manager

One pane over every Unraid box on the network. Installs on one host — the
**manager** — and polls the rest over Unraid's official GraphQL API. Peers need
no plugin installed; a read-scoped API key is the whole integration.

Status: **P2a complete** — the pane ships fleet health, the disk table and the
drift matrix, and a peer can now be enrolled as **Tier 1**: one script on its
flash reached over SSH through a forced command, for the things GraphQL does not
expose. Still **read-only against every peer** — no verb in the agent writes
anything. Verified on two live boxes. Not yet released — install from source, see
[HOWTO.md](HOWTO.md).

## Docs

| | |
| --- | --- |
| [HOWTO.md](HOWTO.md) | Install it, enroll a node, and what to do when something looks wrong. |
| [ARCHITECTURE.md](ARCHITECTURE.md) | What it is made of and why it is shaped this way. |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Test commands, version floors, and the rules that are not negotiable. |
| [docs/review-policy.md](docs/review-policy.md) | Evidenced protected paths. **Read before reviewing anything.** |
| [docs/verification/tier0-coverage.md](docs/verification/tier0-coverage.md) | What the platform actually does. Binding over any design doc. |
| [docs/verification/p0-exit.md](docs/verification/p0-exit.md) | The live two-node trial, and the fourteen defects it found. |
| [docs/verification/p2-checks.md](docs/verification/p2-checks.md) | What the hardware said about each change since P1, and how each was told apart from the build before it. |
| [unraid-manager-plan.md](unraid-manager-plan.md) | Product scope, module map, build phases. |

## Development prerequisites

- **Python 3.11**, stdlib only. No pip, no vendored modules, in the daemon or
  its tests. The dev machine may run newer; CI pins 3.11 and is the authority.
- **PHP 8.2.** Note that the CLI and Unraid's php-fpm are different platforms
  with different extension sets — the target has `sqlite3` and **no
  `pdo_sqlite`**, so the read layer uses the `SQLite3` class. Do not assume your
  CLI's extension list matches the box.

```bash
python -m unittest discover -s tests/python
bash tests/php/run.sh
```

No test touches a network or a live Unraid box. Fixtures only.
