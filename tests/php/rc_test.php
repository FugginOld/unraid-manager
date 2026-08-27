<?PHP
/* Static checks on rc.unraid-manager. It cannot be executed here — it signals
   pids and writes /var/run — so these assert the guards it must contain. The
   behaviour is verified on the box in the live-enrollment milestone; CI adds
   bash -n and shellcheck on top of this.
     php tests/php/rc_test.php  ->  "rc: all pass" (exit 0) */

$fails = 0;
function check(string $name, bool $ok): void {
    global $fails;
    echo ($ok ? "PASS  " : "FAIL  ") . $name . "\n";
    if (!$ok) $fails++;
}

$base = __DIR__ . '/../../source/usr/local/emhttp/plugins/unraid-manager';
$rc   = (string) @file_get_contents($base . '/scripts/rc.unraid-manager');

check('rc script exists', $rc !== '');
check('has a bash shebang', str_starts_with($rc, '#!/bin/bash'));
foreach (['start', 'stop', 'restart', 'status'] as $verb) {
    check("handles $verb", (bool) preg_match('/^\s*' . $verb . '\)/m', $rc));
}
/* The two retention verbs share one case label. */
check('handles prune and prune-vacuum', (bool) preg_match('/^\s*prune\|prune-vacuum\)/m', $rc));
check('prune-vacuum asks for a vacuum', str_contains($rc, "'vacuum'"));
check('usage lists every verb', str_contains($rc, 'prune-vacuum}') || str_contains($rc, 'prune-vacuum|'));
/* nc -U is not a safe bet on the Unraid image; python3 already is, because the
   daemon this script starts is written in it. */
check('the socket is spoken to with python3, not nc', str_contains($rc, 'AF_UNIX')
      && !str_contains($rc, 'nc -U'));

/* The guard the spec makes a startup condition: an unset db_path, or one on the
   USB stick, must stop the daemon from starting AT ALL — with a message that
   says which, because the operator's next action differs. */
check('refuses an unset db_path', str_contains($rc, 'db_path is not set')
      || str_contains($rc, 'not set'));
check('refuses a db_path under /boot', str_contains($rc, '/boot'));
check('start exits non-zero when the path is refused', str_contains($rc, 'exit 1'));

/* The script composes the pidfile from RUN_DIR, so assert the two halves
   rather than a literal the source never contains. */
check('run dir is under /var/run', str_contains($rc, 'RUN_DIR="/var/run/unraid-manager"'));
check('pidfile is composed from the run dir', str_contains($rc, 'PIDFILE="$RUN_DIR/managerd.pid"'));
/* The pid this script signals as root must not be writable by whoever feels
   like it. Unraid's umask is 000, so managerd chmods the file explicitly after
   writing - observed world-writable on Raven. Asserted here because the rc
   script is what turns that number into a signal. */
check('the daemon chmods its pidfile', str_contains(
      (string) @file_get_contents($base . '/daemon/managerd.py'), 'os.chmod(ctl.PID_PATH, 0o644)'));
check('stop sends SIGTERM, not SIGKILL', str_contains($rc, 'kill -TERM')
      && !str_contains($rc, 'kill -9'));
check('starts the daemon with python3', str_contains($rc, 'python3')
      && str_contains($rc, 'managerd.py'));
/* No secret is ever an argument or an environment variable here: keys are read
   from flash by the daemon itself, one file per node. */
check('no key handling in the rc script',
      !str_contains($rc, 'API_KEY') && !str_contains($rc, '.key'));

$started  = (string) @file_get_contents($base . '/event/started');
$stopping = (string) @file_get_contents($base . '/event/stopping_svcs');
check('event/started exists', $started !== '');
check('event/stopping_svcs exists', $stopping !== '');
check('event/started starts the daemon', str_contains($started, 'rc.unraid-manager start'));
check('event/stopping_svcs stops the daemon', str_contains($stopping, 'rc.unraid-manager stop'));

echo $fails === 0 ? "rc: all pass\n" : "rc: $fails FAILED\n";
exit($fails === 0 ? 0 : 1);
