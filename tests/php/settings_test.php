<?PHP
/* Validation and query logic for settings.php and events.php. The dispatch
   blocks are skipped under CLI, so requiring these files runs no gate and
   touches no daemon.
     php tests/php/settings_test.php  ->  "settings: all pass" (exit 0) */

$base = __DIR__ . '/../../source/usr/local/emhttp/plugins/unraid-manager';
require_once $base . '/include/common.php';
require_once $base . '/api/settings.php';
require_once $base . '/api/events.php';

$fails = 0;
function check(string $name, bool $ok): void {
    global $fails;
    echo ($ok ? "PASS  " : "FAIL  ") . $name . "\n";
    if (!$ok) $fails++;
}

/* ── settings validation ──────────────────────────────────────────────────── */
$good = ['db_path' => '/mnt/user/appdata/unraid-manager', 'poll_fast' => '30', 'poll_slow' => '600'];
$r = um_settings_validate($good);
check('a valid settings post is accepted', $r['ok'] === true);
check('values are coerced to int', $r['values']['poll_fast'] === 30);

$r = um_settings_validate(array_merge($good, ['db_path' => '/boot/config/plugins/unraid-manager']));
check('a /boot db_path is refused', $r['ok'] === false);
check('the refusal explains flash wear', str_contains(strtolower($r['error']), 'flash'));

check('an empty db_path is refused', um_settings_validate(array_merge($good, ['db_path' => '']))['ok'] === false);
check('a relative db_path is refused', um_settings_validate(array_merge($good, ['db_path' => 'appdata']))['ok'] === false);
check('poll_fast below 5s is refused', um_settings_validate(array_merge($good, ['poll_fast' => '1']))['ok'] === false);
check('a non-numeric poll_fast is refused', um_settings_validate(array_merge($good, ['poll_fast' => 'soon']))['ok'] === false);
check('poll_slow below poll_fast is refused', um_settings_validate(array_merge($good, ['poll_slow' => '10']))['ok'] === false);
check('poll_slow equal to poll_fast is accepted', um_settings_validate(array_merge($good, ['poll_slow' => '30']))['ok'] === true);
check('an absurd poll_fast is refused', um_settings_validate(array_merge($good, ['poll_fast' => '999999']))['ok'] === false);
check('no key field is ever accepted here', !array_key_exists('key', um_settings_validate(array_merge($good, ['key' => 'x']))['values']));

/* ── daemon controls ──────────────────────────────────────────────────────── */
/* The action reaches a shell, so the allow-list is the security boundary.
   start/stop/restart are exercised for real in the live-enrollment task; here
   we prove nothing else gets through. */
foreach (['status', 'prune', '', 'start; rm -rf /', 'start x', 'START'] as $bad) {
    check("daemon action '" . $bad . "' is refused", um_daemon_action($bad)['ok'] === false);
}
$src = (string) file_get_contents(__DIR__ . '/../../source/usr/local/emhttp/plugins/unraid-manager/api/settings.php');
check('the allow-list is exactly start/stop/restart',
      str_contains($src, "['start', 'stop', 'restart']"));
check('the daemon action is csrf-gated like every other post',
      str_contains($src, 'um_require_csrf($_POST)'));

/* ── events query ─────────────────────────────────────────────────────────── */
$db = new SQLite3(':memory:');
$db->enableExceptions(true);
$db->exec('CREATE TABLE events(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
           node_id TEXT, kind TEXT NOT NULL, message TEXT NOT NULL)');
for ($i = 1; $i <= 250; $i++) {
    $db->exec("INSERT INTO events(ts,node_id,kind,message) VALUES('2026-08-25T10:00:00Z','a1b2','poll_fail','row $i')");
}

$rows = um_events_query($db, 0, 200);
check('capped at 200 rows', count($rows) === 200);
check('newest first', $rows[0]['message'] === 'row 250');
check('since filters by id', count(um_events_query($db, 240, 200)) === 10);
check('since past the end is empty', um_events_query($db, 9999, 200) === []);
check('a negative since is treated as zero', count(um_events_query($db, -5, 200)) === 200);
check('a limit above the cap is clamped', count(um_events_query($db, 0, 5000)) === 200);
check('a zero limit returns nothing', um_events_query($db, 0, 0) === []);

echo $fails === 0 ? "settings: all pass\n" : "settings: $fails FAILED\n";
exit($fails === 0 ? 0 : 1);
