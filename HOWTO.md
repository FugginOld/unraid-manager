# HOWTO

Operator-facing recipes. For how the thing is built, see
[ARCHITECTURE.md](ARCHITECTURE.md); for working on it,
[CONTRIBUTING.md](CONTRIBUTING.md).

## Install a development build on a box

No release is cut yet, so the `.plg`'s `pkgURL` points at a GitHub release that
does not exist. Stage the package locally instead — the installer skips the
download when the file is already there and its md5 matches the entity.

Run all of this **on the Unraid box**, as root. Do not build on Windows: exec
bits on `rc.unraid-manager` and the event hooks do not survive, and `makepkg`
only exists here.

```bash
set +H                                    # stop bash expanding ! in the sed below

mkdir -p /tmp/um && cd /tmp/um
curl -fsSL https://codeload.github.com/FugginOld/unraid-manager/tar.gz/refs/heads/dev \
  | tar xz --strip-components=1
bash build.sh 2026.08.25

mkdir -p /boot/config/plugins/unraid-manager
cp releases/unraid-manager.txz /boot/config/plugins/unraid-manager/
MD5=$(md5sum releases/unraid-manager.txz | awk '{print $1}')
sed 's|"00000000000000000000000000000000"|"'"$MD5"'"|' unraid-manager.plg \
  > /boot/config/plugins/unraid-manager.plg

plugin install /boot/config/plugins/unraid-manager.plg
```

**Order matters when reinstalling.** `plugin remove` deletes
`/boot/config/plugins/unraid-manager.plg` along with the package, so write the
patched plg *after* the remove, never before — otherwise the install has no file
to read and fails with "XML file doesn't exist or xml parse error". Observed
2026-08-27.

**Reinstalling.** `plugin install` refuses a plugin that is already registered,
and `plugin update` resolves `pluginURL` — which points at `main` and will not
have your changes. Use `plugin remove unraid-manager.plg`, re-stage the `.plg`
(remove deletes it from flash), then install again. Your config, registry and
keys survive a remove by design.

**Patching one file while iterating.** `/usr/local/emhttp` is tmpfs, so a single
file can be dropped in and picked up on the next request or daemon restart. Pin
the **commit SHA**, not the branch — `raw.githubusercontent.com` caches branch
URLs for minutes and will hand you a stale copy:

```bash
S=<full-commit-sha>
curl -fsSL https://raw.githubusercontent.com/FugginOld/unraid-manager/$S/source/usr/local/emhttp/plugins/unraid-manager/daemon/managerd.py \
  -o /usr/local/emhttp/plugins/unraid-manager/daemon/managerd.py
/usr/local/emhttp/plugins/unraid-manager/scripts/rc.unraid-manager restart
```

## First run

1. **Settings → Utilities → Unraid-Manager.** Set the database path to a
   directory on a pool — `/mnt/cache/unraid-manager`, or `/mnt/user/appdata/...`
   on a box with array disks. It will refuse anything under `/boot`. Save.
2. Press **Start** in the Daemon section, or run
   `rc.unraid-manager start`.
3. **Enroll a node.** On the peer, create a key — read scope is enough:
   ```bash
   unraid-api apikey --create
   ```
   Back on the manager, enter the peer's address and API port, paste the key, and
   press **Probe**. Enroll unlocks on verdict `ok` or `partial`. Leave Name blank
   to take the peer's own hostname.
4. Open the **Fleet** tab. State appears within one fast cycle (30s).

The peer's API port is the one `unraid-api` listens on, not the webGUI port —
it differs per box.

## The rc script

```
rc.unraid-manager start | stop | restart | status | prune | prune-vacuum
```

`status` exits 0 running, 3 stopped. `prune` runs retention through the daemon
so there is exactly one writer, and is skipped entirely when it is down. Cron
runs `prune` daily and `prune-vacuum` weekly; both are installed by the `.plg`.

## When something looks wrong

**A node shows grey `? Unknown`.** Nothing about it is readable — unreachable,
wrong port, or a rejected key. `last seen` stops advancing. Press **Test** beside
it on the settings page for a per-domain report; the key is not re-sent, the
daemon reads it from flash.

**A node shows amber `⚠ Degraded`.** It is answering, but at least one domain is
not. Click the row on the Fleet tab for the per-domain breakdown and the reason.
The most common cause is the `disks` 504 below.

**`disks` is unknown with a 504.** Expected on a loaded box. `Query.disks` can
exceed nginx's 60-second gateway timeout on the peer, and that ceiling is not
ours to raise — our own timeout is already 90s. Observed on both a 37-disk box
and an empty one, so it is load- and state-dependent rather than a property of
one machine. The other eight domains stay readable and the last successful
`fetched_at` is preserved.

**"Live updates unavailable".** The daemon could not find nginx's nchan
publisher socket. Harmless: the page falls back to a 30-second poll and stays
correct. `grep nchan /var/log/unraid-manager/managerd.log` says which path was
taken.

**The daemon will not start.** Almost always the database path. Run
`rc.unraid-manager start` from a shell — the refusal names the reason, which the
UI also surfaces:

```
unraid-manager: db_path '/boot/...' is on the USB flash device.
  Telemetry is written continuously and flash wears out. Use a pool path.
```

**Nothing appears on the settings page at all.** Check the daemon status line
first. If it says running while the node list is empty, the PHP layer cannot read
the database — check that `db_path` in `manager.cfg` matches where `manager.db`
actually is.

## Looking at the data directly

```bash
python3 -c "
import sqlite3
c = sqlite3.connect('/mnt/cache/unraid-manager/manager.db')
print(c.execute('SELECT name,address,port,last_seen FROM nodes').fetchall())
for r in c.execute('SELECT n.name, s.domain, s.status, substr(coalesce(s.error,\"\"),1,80) '
                   'FROM node_state s JOIN nodes n ON n.id=s.node_id ORDER BY n.name, s.domain'):
    print(' ', r)
"
```

Talking to the daemon directly (`status`, `reload`, `poll_now`, `prune`):

```bash
python3 -c "
import socket, json
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.settimeout(30)
s.connect('/var/run/unraid-manager/managerd.sock')
s.sendall(json.dumps({'cmd': 'status'}).encode() + b'\n')
print(s.recv(65536).decode())
"
```

Use `python3`, not `nc -U` — python3 is already a hard dependency here, and
which netcat the image ships (and whether it speaks unix sockets) is not
something to bet a cron job on.

## Uninstalling

```bash
plugin remove unraid-manager.plg
```

Stops the daemon, removes the package and the cron entries, and **keeps** your
flash config, node registry and keys at
`/boot/config/plugins/unraid-manager/`, along with the database on the pool.
Delete those by hand if you want them gone.
