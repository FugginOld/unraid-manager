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
/* 'values' is built as an explicit literal, so checking array_key_exists alone
   is unfalsifiable short of 'values' => $post. Carry a value through to the
   rendered cfg as well, so a future refactor that dumps $post (or merges it)
   into the written file is what this actually guards against. */
$keyed = um_settings_validate(array_merge($good, ['key' => 'should-never-persist']));
check('no key field is ever accepted into validated values', !array_key_exists('key', $keyed['values']));
check('no key field leaks into the rendered cfg',
      !str_contains(um_render_manager_cfg($keyed['values']), 'should-never-persist'));

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

/* Two different intentions look identical if the fixture has no stored value:
   an absent key is a partial or programmatic save and must preserve whatever
   is already on flash (never erase a threshold by not mentioning it); a
   present-but-empty key is the operator clearing the input box, and that
   still restores the default. Give the stored value a distinct number from
   the default so the two paths can't be confused by coincidence. */
$storedDir = sys_get_temp_dir() . '/um_storedcfg_' . getmypid();
@mkdir($storedDir, 0700, true);
um_set_cfg_dir($storedDir);
file_put_contents($storedDir . '/manager.cfg',
    "db_path=/mnt/user/appdata/unraid-manager\npoll_fast=30\npoll_slow=600\n"
    . "capacity_high_water=77\ntemp_warn=41\ntemp_crit=59\nerror_window_min=22\n");
check('a threshold omitted from the post preserves the stored value',
      um_settings_validate($good)['values']['capacity_high_water'] === 77);
check('a threshold present but empty restores the default even with a stored value on flash',
      um_settings_validate(array_merge($good, ['capacity_high_water' => '']))['values']['capacity_high_water'] === 90);

/* A hand-edited manager.cfg must not be able to make the Settings page
   unsaveable. The seeded values never passed through the form, so refusing them
   would punish the operator for a file they may not have broken - config.py
   already falls back rather than refusing, and this mirrors it. What the
   operator actually submits is still refused on its own merits. */
$brokenDir = sys_get_temp_dir() . '/um_settings_broken_' . getmypid();
@mkdir($brokenDir, 0700, true);
file_put_contents($brokenDir . '/manager.cfg',
    "capacity_high_water=200
temp_warn=80
temp_crit=30
error_window_min=abc
");
um_set_cfg_dir($brokenDir);
$b = um_settings_validate($good);
check('an out-of-range value on flash falls back rather than refusing the save',
      $b['ok'] === true && $b['values']['capacity_high_water'] === 90);
check('a non-numeric value on flash falls back rather than being cast to zero',
      $b['values']['error_window_min'] === 15);
check('an inverted pair on flash resets both, it does not block the save',
      $b['values']['temp_warn'] === 50 && $b['values']['temp_crit'] === 60);
check('an inverted pair the operator actually submits is still refused',
      um_settings_validate(array_merge($good, ['temp_warn' => '70', 'temp_crit' => '40']))['ok'] === false);
@unlink($brokenDir . '/manager.cfg');
@rmdir($brokenDir);
@unlink($storedDir . '/manager.cfg');
@rmdir($storedDir);
/* Deliberately NOT um_set_cfg_dir(UM_CFG_DIR_DEFAULT). um_settings_validate
   reads flash for any threshold the post omits, and $good omits all four - so
   resetting to the default would make every check below read the operator's
   real /boot/config/plugins/unraid-manager/manager.cfg. That passes here and in
   CI only because /boot does not exist; run on the box, a hand-edited or a
   pre-P1 cfg would red this suite for reasons unrelated to the change. Point at
   an empty scratch dir instead, so the seed path resolves to the defaults. */
$emptyDir = sys_get_temp_dir() . '/um_settings_empty_' . getmypid();
@mkdir($emptyDir, 0700, true);
um_set_cfg_dir($emptyDir);

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

/* Boundary-exact checks: a value one *below* the bounds table's min, or one
   *above* its max, and the min/max value itself. A test that only tries a
   wildly out-of-range value (see 'an out-of-range high water is refused'
   above) still passes if the bound itself drifts by one - these pin the
   table's actual numbers, per key, mirroring daemon/config.py's
   THRESHOLD_BOUNDS one entry at a time. temp_warn's own max (99) and
   temp_crit's own min (20) can never be *accepted* in isolation, because the
   pair also has to satisfy crit > warn - the same tautology exists in
   daemon/config.py. That does NOT make a change to those two bounds
   unobservable, though: the range loop at the top of um_settings_validate
   runs before the inversion guard, so at the degenerate equal pairs (99,99)
   and (20,20) the two guards compete for the same rejection, and whichever
   runs first names the reason. 'ok' is false in both cases either way - the
   refusal TEXT is what a shifted bound would change, from an inversion
   sentence to "must be between X and Y". Those two checks are below,
   immediately after the equal-pair check above; together with the ordinary
   boundary checks that follow, that closes the pair to 18/18. */
check('the degenerate pair at the shared max names the inversion, not a bound',
      str_contains(strtolower(um_settings_validate(
          array_merge($good, ['temp_warn' => '99', 'temp_crit' => '99']))['error']), 'critical'));
check('the degenerate pair at the shared min names the inversion, not a bound',
      str_contains(strtolower(um_settings_validate(
          array_merge($good, ['temp_warn' => '20', 'temp_crit' => '20']))['error']), 'critical'));
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

@rmdir($emptyDir);

echo $fails === 0 ? "settings: all pass\n" : "settings: $fails FAILED\n";
exit($fails === 0 ? 0 : 1);
