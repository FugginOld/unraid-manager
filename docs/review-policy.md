# Review policy

Findings that contradict this file are defects in the review, not in the code.
Every entry in **Rejected on sight** is tied to a commit where the simpler
version shipped and broke something on real hardware. Arbitration order is in
global CLAUDE.md and is not restated here.

Pinned by `tests/php/policy_test.php`, which fails if a cited guard leaves the
tree or a cited test stops asserting it.

## Scope

| Tool | Governs | In this repo |
| --- | --- | --- |
| Superpowers | Process — spec, plan, TDD, verify | All code |
| ui-ux-pro-max | UX checklist only (`--domain ux`, `--domain icons`) | **Never** `--design-system`, never `--persist`. The UI renders inside Unraid's webGUI shell and inherits its CSS; it does not own a design system. The `--um-*` token set is P1 and deliberately absent. |
| Ponytail | YAGNI bias, **diff only**, presentation and packaging | Excludes every path below |

**Never `/ponytail-audit` this repo.** It has layered validation by design — the
flash-path refusal exists three times over, in the daemon, the rc script and the
settings endpoint — and it has mutating filesystem paths that run as root:
flash config writes, key files, and a package that unpacks over `/`.

Paths reviewed for correctness only, never for style or concision:

- `source/.../daemon/store.py` — the only writer to the database
- `source/.../include/common.php` — session, CSRF, read-only DB, key files
- `source/.../scripts/rc.unraid-manager` and `event/*` — lifecycle, signals
- `unraid-manager.plg` — installs and removes as root on someone else's server
- `build.sh` — decides what ships
- `tests/python/fixtures/**` — evidence, not editable data

## Rejected on sight

### Platform integration — the webGUI is not a normal PHP host

Nine of these were invisible to a green test suite. They are facts about
emhttp, php-fpm and nginx, and a reviewer without a box in front of them will
read every one as redundant.

- **`um_session()` starting a session before reading `$_SESSION`** — `0828786`.
  A `.page` is rendered by emhttp's dispatcher, which has already started one; a
  standalone endpoint under `/plugins/` has not. Removing this 401s every
  request regardless of who is logged in.
- **`um_var()` reading the CSRF token from `var.ini`** — `8be1bf2`. `global $var`
  is emhttp's, and an endpoint never has it. Without the file read the *server*
  token is empty and every POST is refused while the browser sends a valid one.
- **Accepting the platform's CSRF gate rather than re-checking it** — `420dc1a`.
  Unraid's `local_prepend.php` validates `csrf_token` on every POST and then
  `unset($_POST['csrf_token'])`. A gate that insists on seeing a token is
  checking for evidence the platform deliberately destroyed, and refuses every
  write. The `REQUEST_METHOD === 'POST'` clause on it (`0e37935`) is
  preventative, not field-evidenced — see Documented boundaries.
- **`SQLite3`, not PDO, in the read layer** — `cd76aa1`. Unraid's php-fpm ships
  the `sqlite3` extension and **no `pdo_sqlite` driver**;
  `PDO::getAvailableDrivers()` is empty there. Every read failed, was caught, and
  returned nothing while the daemon polled happily. The php CLI *does* have
  pdo_sqlite, so the suite stayed green — `tests/php/harness_test.php` now checks
  for the extension the target actually has. Proposing PDO back is proposing the
  outage.
- **`PRAGMA query_only = 1` instead of an open-readonly flag** — a WAL database
  must map its `-shm` to be read at all, and a reader without write access to it
  fails in a way that looks like corruption. Proven by an attempted INSERT in
  `common_test.php`, not asserted.
- **`Code="…"` in `UnraidManager.page`** — `1694238`. A top-level tab renders an
  icon-font glyph from `Code=`; `Menu=` + `Type="xmenu"` + `Title` + `Icon` is
  the Utilities-submenu shape and the top bar skips it silently. Without `Code`
  the Fleet tab does not exist.
- **The tolerant nchan `listen` pattern `unix:([^\s;]+)`** — `b988c8a`. The real
  line is `listen unix:/var/run/nginx.socket default_server;`; the path ends at
  whitespace. Anchoring on `;` matched nothing and cost live updates on a box
  whose `servers.conf` declares `nchan_publisher` two lines below.
- **`exit 0` at the end of the `.plg`'s pre-stop block** — `d991abe`. `[ -x f ] &&
  f stop` exits 1 when the file is absent, which is every fresh install. Unraid
  reads that as `run failed` and aborts before unpacking a single file. A guarded
  no-op must still succeed.

### Concurrency and lifecycle

- **`check_same_thread=False` plus `Manager._lock`** — `faea89f`. These are one
  guard, not two. sqlite3 refuses cross-thread use by default, so the control
  socket and every worker poll raised `ProgrammingError`; the flag removes the
  interpreter's guard and the lock is then the *only* serialisation left.
  Removing either is a data race or an outage. The lock also covers `_node()`,
  `status()` and `prune` — reads, deliberately, because they run on worker and
  listener threads.
- **Bounded shutdown: `cancel_futures=True`, lock, `exit_fn`** — `b0e6155`.
  Waiting on in-flight polls means waiting up to the 90s slow-lane timeout, so
  `rc stop` failed at 10s and array stop would have stalled behind a peer. Taking
  the lock before closing is what keeps this safe; dropping an in-flight HTTP
  request costs one poll.
- **`os.chmod(ctl.PID_PATH, 0o644)`** — `9f063ce`. Unraid runs with umask 000, so
  the pidfile lands world-writable, and the rc script feeds that number to
  `kill -TERM` as root.
- **`kill -TERM`, never `kill -9`, in the rc script** — SIGKILL mid-write on a WAL
  database earns a recovery on next open for no reason.
- **`python3`, not `nc -U`, for the socket in the rc script** — python3 is already
  a hard dependency; which netcat the image ships, and whether it speaks unix
  sockets, is not something a cron job should bet on. `rc_test.php` asserts the
  netcat form stays absent.

### Correctness of what we show

- **Per-domain queries, one HTTP request each** — a batched GraphQL query
  containing one unsatisfiable resolver returns `data: null` for *everything*.
  Merging domains to save requests breaks the entire fleet view on the first box
  with an odd hardware configuration. Observed live with `upsDevices`.
- **The rollup treating `unknown` as "nothing is readable"** — `2800c93`. The
  earlier rule (any domain unknown → node unknown) let one slow `disks` query
  declare a nine-domain-healthy node unreachable. Still fail-closed: never `ok`
  while a domain is blind.
- **Not requesting `ParityCheck.errors`** — `055fe02`. Golem holds a history row
  reading `2441379360`, past the `Int` the API types the field as, and asking
  for it makes the API answer the whole query with `INTERNAL_SERVER_ERROR`.
  `parityHistory` takes no arguments, so the newest row cannot be asked for
  alone. The parser still reads the field where a box returns it and reports
  `None` — unknown, never zero.
- **`serialNum` dropped in `_disk_row()`** — no raw serial in an API-bound
  payload, ever. Nothing in P0 needs one.
- **The `probedHostname` fallback in `settings.js`** — `f0b3e7b`. The field says
  "taken from the node if left blank" and the server's fallback is the address,
  so Golem enrolled as `192.168.2.248`.
- **Three separate flash-path refusals** — `e777fce`, `abb367b`, and
  `um_settings_validate()`. Not redundant: they fail at different times and tell
  the operator different things. `6298933` and `243ee5b` closed echo, `cd` and
  variable-indirection bypasses in the gate test that proves them.

## Do not re-add

Confirmed absent from the tree, and each was removed for a reason:

- **`errors` in the parity query.** See above; it breaks the domain.
- **`nc -U` anywhere.** Replaced by python3 deliberately.
- **`new PDO(` in the PHP layer.** The driver does not exist on the target. The
  string survives only inside the comment explaining this.
- **Any mutation query in the domain table.** P0 is strictly read-only against
  peers and a test asserts no `mutation` string appears, even unused.
- **Introspection queries.** Off on production boxes; capability probing uses
  targeted per-domain queries.
- **A repo-level `CLAUDE.md`.** Repo material lives in these docs by standard.

## Documented boundaries

A comment explains the intent, but nothing shows the simpler version shipped and
broke. Read the comment before proposing a change; these are not prohibitions.

- **Unverified TLS in `gqlclient._tls_context()`** — deliberate and recorded
  (spec §1, the plan's Risks table, and a `ponytail:` comment). Unraid serves a
  self-signed certificate on its LAN address; turning verification on fails every
  enrollment. Certificate pinning at enrollment is the P1 upgrade. Raised twice
  by automated security review and rejected both times on this basis.
- **The `REQUEST_METHOD === 'POST'` clause on `um_platform_csrf_enforced()`** —
  scanner-raised, not field-observed. `csrf_terminate()` is defined on every
  request but only validates on POST, so the clause keeps a future mutation route
  from being credited a check that never ran.
- **Hand-rolled ini parsing in `config.py` and `common.php`** — these files are
  hand-editable by operators and `configparser`'s dialect is not theirs.
- **`SLOW_TIMEOUT = 90` though nginx cuts at 60** — harmless, and correct if the
  endpoint is ever reached without nginx in front.
- **Backoff never lengthening the slow lane** — it is already ten minutes;
  stretching it further leaves a recovered node showing an hour-old disk list.
- **The key prompt in `capture_fixtures.py` not being a CLI argument** —
  arguments land in shell history.
- **`_NodeState` storing last-dispatch rather than a deadline** — a deadline
  freezes the interval at dispatch time, so a failure recorded afterwards cannot
  lengthen the slot it is meant to lengthen.

## Not evidence

Deliberately excluded, so the next pass does not re-harvest them:

- Scanner-raised hardening with no field failure — counted as a boundary, not a
  scar. Two so far, both from automated security review.
- The stale-expectation corrections during plan execution (`670e79d`, `87d78ac`,
  `eb1bf9e`, `4fb6df2`). Those fixed a *document's invented JSON* against live
  captures. The lesson — fixtures win — is in CONTRIBUTING.md where it belongs.
- Style, naming and comment commits.
- The CRLF renormalisation (`.gitattributes`). A tooling fact, not a design one.

## Cadence and disposal

Re-harvest on triggers, not a calendar: after any live-hardware trial, after a
platform upgrade on the target, and before a release. Merge into this file —
never regenerate. History does not change, so correct a superseded entry and say
what superseded it rather than deleting it.

Findings become issues or are rejected in the same session. **Never commit a
review report** — a retained one is re-read as context by the next pass and its
contents re-reported as fresh.

**Known gap:** no automated guard covers platform integration. Everything in the
first section above was found by a human running the plugin on a real box, and
the next one will be too. CI proves the code is self-consistent; it cannot prove
the platform is what we think it is. `docs/verification/tier0-coverage.md` is the
running record of what the platform actually does and is binding over any design
document that disagrees.
