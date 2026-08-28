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

/* ── health thresholds ────────────────────────────────────────────────────── */
/* A P0-era manager.cfg has none of the four threshold keys. Reading it must
   yield the defaults, not nulls - this is what makes the plg's three-key seed
   harmless rather than a second place to keep in sync. */
$p0dir = sys_get_temp_dir() . '/um_p0cfg_' . getmypid();
@mkdir($p0dir, 0700, true);
um_set_cfg_dir($p0dir);
file_put_contents($p0dir . '/manager.cfg',
    "db_path=/mnt/user/appdata/unraid-manager\npoll_fast=30\npoll_slow=600\n");
$p0 = um_settings_get();
check('a P0-era manager.cfg defaults capacity_high_water', $p0['capacity_high_water'] === 90);
check('a P0-era manager.cfg defaults temp_warn', $p0['temp_warn'] === 50);
check('a P0-era manager.cfg defaults temp_crit', $p0['temp_crit'] === 60);
check('a P0-era manager.cfg defaults error_window_min', $p0['error_window_min'] === 15);
@unlink($p0dir . '/manager.cfg');
@rmdir($p0dir);
um_set_cfg_dir(UM_CFG_DIR_DEFAULT);

$withThresholds = array_merge($good, ['capacity_high_water' => '85', 'temp_warn' => '45',
                                      'temp_crit' => '55', 'error_window_min' => '30']);
$r = um_settings_validate($withThresholds);
check('thresholds are accepted', $r['ok'] === true);
check('thresholds are coerced to int', $r['values']['capacity_high_water'] === 85);

check('a missing threshold falls back to the default rather than failing',
      um_settings_validate($good)['values']['capacity_high_water'] === 90);
check('an out-of-range high water is refused',
      um_settings_validate(array_merge($good, ['capacity_high_water' => '5']))['ok'] === false);
check('a non-numeric threshold is refused',
      um_settings_validate(array_merge($good, ['temp_warn' => 'warm']))['ok'] === false);
/* '(int) "warm"' casts to 0, which the range check below would also reject -
   pin the actual error text so a dropped is_numeric guard cannot hide behind
   that overlap and still read as "refused". */
check('the non-numeric refusal names the reason, not a range violation',
      str_contains(um_settings_validate(array_merge($good, ['temp_warn' => 'warm']))['error'],
                   'must be a number'));
/* An inverted pair makes one thermal band unreachable. */
$r = um_settings_validate(array_merge($good, ['temp_warn' => '70', 'temp_crit' => '40']));
check('crit below warn is refused', $r['ok'] === false);
check('the refusal explains the inversion', str_contains(strtolower($r['error']), 'critical'));
check('an equal warn/crit pair is refused',
      um_settings_validate(array_merge($good, ['temp_warn' => '50', 'temp_crit' => '50']))['ok'] === false);

check('an empty threshold falls back to the default rather than failing',
      um_settings_validate(array_merge($good, ['capacity_high_water' => '']))['values']['capacity_high_water'] === 90);

/* Boundary-exact checks: a value one *below* the bounds table's min, or one
   *above* its max, and the min/max value itself. A test that only tries a
   wildly out-of-range value (see 'an out-of-range high water is refused'
   above) still passes if the bound itself drifts by one - these pin the
   table's actual numbers, per key, mirroring daemon/config.py's
   THRESHOLD_BOUNDS one entry at a time. temp_warn and temp_crit are paired
   because of the inversion guard: temp_warn's own max (99) and temp_crit's
   own min (20) can never be exercised in isolation since the pair also has
   to satisfy crit > warn - the same tautology exists in daemon/config.py. */
check('capacity_high_water one below min is refused',
      um_settings_validate(array_merge($good, ['capacity_high_water' => '49']))['ok'] === false);
check('capacity_high_water at min is accepted',
      um_settings_validate(array_merge($good, ['capacity_high_water' => '50']))['ok'] === true);
check('capacity_high_water at max is accepted',
      um_settings_validate(array_merge($good, ['capacity_high_water' => '99']))['ok'] === true);
check('capacity_high_water one above max is refused',
      um_settings_validate(array_merge($good, ['capacity_high_water' => '100']))['ok'] === false);

check('temp_warn one below min is refused',
      um_settings_validate(array_merge($good, ['temp_warn' => '19', 'temp_crit' => '60']))['ok'] === false);
check('temp_warn at min is accepted',
      um_settings_validate(array_merge($good, ['temp_warn' => '20', 'temp_crit' => '21']))['ok'] === true);

check('temp_crit at max is accepted',
      um_settings_validate(array_merge($good, ['temp_warn' => '98', 'temp_crit' => '99']))['ok'] === true);
check('temp_crit one above max is refused',
      um_settings_validate(array_merge($good, ['temp_warn' => '97', 'temp_crit' => '100']))['ok'] === false);

check('error_window_min one below min is refused',
      um_settings_validate(array_merge($good, ['error_window_min' => '0']))['ok'] === false);
check('error_window_min at min is accepted',
      um_settings_validate(array_merge($good, ['error_window_min' => '1']))['ok'] === true);
check('error_window_min at max is accepted',
      um_settings_validate(array_merge($good, ['error_window_min' => '1440']))['ok'] === true);
check('error_window_min one above max is refused',
      um_settings_validate(array_merge($good, ['error_window_min' => '1441']))['ok'] === false);

check('the rendered cfg carries capacity_high_water',
      str_contains(um_render_manager_cfg($withThresholds), 'capacity_high_water=85'));
check('the rendered cfg carries temp_warn',
      str_contains(um_render_manager_cfg($withThresholds), 'temp_warn=45'));
check('the rendered cfg carries temp_crit',
      str_contains(um_render_manager_cfg($withThresholds), 'temp_crit=55'));
check('the rendered cfg carries error_window_min',
      str_contains(um_render_manager_cfg($withThresholds), 'error_window_min=30'));

/* A stored (non-default) threshold must round-trip back out of um_settings_get
   unchanged - not just fall through to the default, which the P0-cfg test
   above cannot tell apart from a read path that ignores the file entirely. */
$fullDir = sys_get_temp_dir() . '/um_fullcfg_' . getmypid();
@mkdir($fullDir, 0700, true);
um_set_cfg_dir($fullDir);
file_put_contents($fullDir . '/manager.cfg',
    "db_path=/mnt/user/appdata/unraid-manager\npoll_fast=30\npoll_slow=600\n"
    . "capacity_high_water=77\ntemp_warn=41\ntemp_crit=59\nerror_window_min=22\n");
$stored = um_settings_get();
check('um_settings_get reads back a stored capacity_high_water', $stored['capacity_high_water'] === 77);
check('um_settings_get reads back a stored temp_warn', $stored['temp_warn'] === 41);
check('um_settings_get reads back a stored temp_crit', $stored['temp_crit'] === 59);
check('um_settings_get reads back a stored error_window_min', $stored['error_window_min'] === 22);
@unlink($fullDir . '/manager.cfg');
@rmdir($fullDir);
um_set_cfg_dir(UM_CFG_DIR_DEFAULT);

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
