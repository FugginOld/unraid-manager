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
/* capacity_watch travels with the high-water mark: stored alone at 77 it is
   BELOW the inherited watch level of 80, which is the inverted pair the rule
   further down heals - and the healing would then be indistinguishable from
   the erasure this check exists to catch. */
file_put_contents($storedDir . '/manager.cfg',
    "db_path=/mnt/user/appdata/unraid-manager\npoll_fast=30\npoll_slow=600\n"
    . "capacity_watch=70\ncapacity_high_water=77\ntemp_warn=41\ntemp_crit=59\n"
    . "error_window_min=22\n");
check('a threshold omitted from the post preserves the stored value',
      um_settings_validate($good)['values']['capacity_high_water'] === 77);
/* P1 exit finding F-8 changed what blank MEANS. It used to write the constant;
   it now writes nothing, so the value keeps following Unraid's own Disk
   Settings instead of freezing at whatever they said the day it was saved.
   Storing the resolved number would look identical today and diverge the first
   time the operator changed Unraid's setting. */
check('a threshold present but empty is stored blank, so it keeps following Unraid',
      um_settings_validate(array_merge($good, ['capacity_high_water' => '']))['values']['capacity_high_water'] === '');

/* ── Unraid's own Disk Settings (F-8) ─────────────────────────────────────── */
$dynDir = sys_get_temp_dir() . '/um_dynamix_' . getmypid();
@mkdir($dynDir, 0700, true);
file_put_contents($dynDir . '/dynamix.cfg',
    "[display]
hot=\"45\"
max=\"55\"
warning=\"70\"
critical=\"90\"
hotssd=\"60\"
unit=\"C\"
");
$unraid = um_unraid_thresholds(UM_THRESHOLDS, $dynDir . '/dynamix.cfg');
check('the Unraid disk temperature thresholds map onto ours',
      $unraid['temp_warn'] === 45 && $unraid['temp_crit'] === 55);
check('the Unraid utilization thresholds map onto ours',
      $unraid['capacity_watch'] === 70 && $unraid['capacity_high_water'] === 90);
/* Telling an SSD from a spinner needs a rotational flag the physical
   enumeration does not carry, so these are deliberately not read - reading
   them would silently apply an SSD limit to a hard disk. */
check('the SSD-specific thresholds are not borrowed for every disk',
      !array_key_exists('temp_warn_ssd', $unraid) && count($unraid) === 4);
check('a missing dynamix.cfg is simply no opinion, not an error',
      um_unraid_thresholds(UM_THRESHOLDS, $dynDir . '/nope.cfg') === []);
file_put_contents($dynDir . '/junk.cfg', "[display]
hot=\"warm-ish\"
max=\"\"
");
check('a non-numeric value from another plugins file is ignored, not trusted',
      um_unraid_thresholds(UM_THRESHOLDS, $dynDir . '/junk.cfg') === []);

/* The two halves read the SAME file and must agree about it. daemon/config.py
   range-checks against THRESHOLD_BOUNDS; without the same check here the page
   reported "inherited: 4 C" while the daemon used 50 - one box, two answers,
   which is the defect F-8 set out to remove. */
file_put_contents($dynDir . '/wild.cfg', "[display]
hot=\"4\"
max=\"500\"
");
check('an out-of-range value from another plugins file is dropped, as python drops it',
      um_unraid_thresholds(UM_THRESHOLDS, $dynDir . '/wild.cfg') === []);

/* A Fahrenheit box inherits NO temperature, matching daemon/config.py: we
   cannot tell whether Unraid stores hot/max in F there or always in C, both
   guesses are unsafe in opposite directions, and declining is safe under
   either. Capacity is a percentage and is unaffected. */
file_put_contents($dynDir . '/f.cfg', "[display]
unit=\"F\"
hot=\"45\"
max=\"55\"
warning=\"70\"
critical=\"90\"
");
$f = um_unraid_thresholds(UM_THRESHOLDS, $dynDir . '/f.cfg');
check('a fahrenheit box inherits no temperature threshold at all',
      !array_key_exists('temp_warn', $f) && !array_key_exists('temp_crit', $f));
check('but it still inherits the capacity percentages',
      $f['capacity_watch'] === 70 && $f['capacity_high_water'] === 90);

/* Fails closed on a key with no bound, the way python raises KeyError for the
   same input - accepting it would diverge the two halves from silent-accept to
   daemon-crash on a file neither of them owns. */
check('a key with no bound is dropped, not accepted unchecked',
      um_unraid_thresholds(['temp_warn' => [20, 99]], $dynDir . '/dynamix.cfg')
      === ['temp_warn' => 45]);

/* The capacity pair now gets the refusal the temperature pair always had.
   Saved 95/90 cleanly before this, then config.py silently reset both. */
$inverted = um_settings_validate(array_merge($good,
    ['capacity_watch' => '95', 'capacity_high_water' => '90']));
check('an inverted capacity pair is refused, the way an inverted temperature pair is',
      $inverted['ok'] === false && str_contains((string) $inverted['error'], 'capacity'));

/* The clock preference: the pure predicate was pinned, the READ was not, so
   the whole body of um_display_clock_12h could be replaced with `return false`
   and the suite stayed green. */
file_put_contents($dynDir . '/12h.cfg', "[display]
time=\"%I:%M %p\"
");
file_put_contents($dynDir . '/24h.cfg', "[display]
time=\"%R\"
");
check('the clock preference is actually read out of the file',
      um_display_clock_12h($dynDir . '/12h.cfg') === true
      && um_display_clock_12h($dynDir . '/24h.cfg') === false);
@unlink($dynDir . '/wild.cfg'); @unlink($dynDir . '/f.cfg');
@unlink($dynDir . '/12h.cfg'); @unlink($dynDir . '/24h.cfg');
@unlink($dynDir . '/dynamix.cfg'); @unlink($dynDir . '/junk.cfg'); @rmdir($dynDir);

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
/* The both-seeded branch, which the explicit-post refusal above cannot reach.
   A legacy or hand-edited inverted pair already on flash, with NEITHER key in
   the post: the save must heal it rather than write it back. It did not - the
   guard was appended after `$stored = $thresholds`, so its reset wrote to a
   copy nobody read again, and the inversion survived every programmatic save
   while config.py silently reset both on each daemon start. The temperature
   twin works because its block sits before the copy. */
$invDir = sys_get_temp_dir() . '/um_invcfg_' . getmypid();
@mkdir($invDir, 0700, true);
um_set_cfg_dir($invDir);
file_put_contents($invDir . '/manager.cfg',
    "db_path=/mnt/user/appdata/unraid-manager\npoll_fast=30\npoll_slow=600\n"
    . "capacity_watch=95\ncapacity_high_water=90\n");
$healed = um_settings_validate([
    'db_path' => '/mnt/user/appdata/unraid-manager', 'poll_fast' => '30', 'poll_slow' => '600',
]);
check('an inverted capacity pair already on flash is healed, not written back',
      $healed['ok'] === true
      && (int) $healed['values']['capacity_watch'] < (int) $healed['values']['capacity_high_water']);
@unlink($invDir . '/manager.cfg'); @rmdir($invDir);
um_set_cfg_dir($storedDir);

check('capacity_high_water one below min is refused',
      um_settings_validate(array_merge($good, ['capacity_high_water' => '49']))['ok'] === false);
/* The watch level travels with it: 50 is a legal high-water mark, but not
   while the watch level sits at the inherited 80 - that is the inverted pair
   the rule above refuses, and submitting half a pair is what would trip it. */
check('capacity_high_water at min is accepted',
      um_settings_validate(array_merge($good, ['capacity_high_water' => '50',
                                               'capacity_watch' => '40']))['ok'] === true);
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

/* The page has to be able to REACH the new behaviour: an input that is never
   rendered, or a value the save never sends, is a setting that does not exist.
   Same class of hole as a green check on a screen nothing renders. */
$page = (string) file_get_contents($base . '/UnraidManagerSettings.page');
$js   = (string) file_get_contents($base . '/settings.js');
check('the capacity watch level has an input on the page',
      str_contains($page, 'um-capacity-watch'));
check('the save sends it', str_contains($js, 'capacity_watch:'));
check('the page says what a blank field falls back to',
      str_contains($page, 'Disk Settings'));
check('the page shows the inherited value as a placeholder',
      str_contains($js, 'placeholder'));
check('the page no longer hardcodes the old constants as its defaults',
      !str_contains($page, 'restore the default (90)')
      && !str_contains($page, 'its default (50 / 60)'));

echo $fails === 0 ? "settings: all pass\n" : "settings: $fails FAILED\n";
exit($fails === 0 ? 0 : 1);
