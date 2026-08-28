<?PHP
/* GET  -> the manager settings and the daemon's status line
 * POST -> write manager.cfg (flash, on user change only) and reload the daemon
 *
 * Requiring this file defines functions and does nothing else; the dispatch at
 * the bottom is skipped under CLI so the test suite can load it. */

require_once __DIR__ . '/../include/common.php';

const UM_POLL_FAST_MIN = 5;
const UM_POLL_MAX = 86400;

/* key => [min, max, default]. Mirrors daemon/config.py's THRESHOLD_BOUNDS;
   settings_test.php and test_config.py each assert their own side. */
const UM_THRESHOLDS = [
    'capacity_high_water' => [50, 99, 90],
    'capacity_watch'      => [10, 98, 80],
    'temp_warn'           => [20, 99, 50],
    'temp_crit'           => [20, 99, 60],
    'error_window_min'    => [1, 1440, 15],
];

/* What a blank field means: Unraid's own Disk Settings where it has an
   opinion, our constant where it does not (F-8). Blank is STORED blank rather
   than resolved to a number, so a later change in Unraid's settings is
   followed instead of frozen at whatever it said the day this was saved. */
function um_threshold_defaults(): array {
    $out = [];
    foreach (UM_THRESHOLDS as $key => [$min, $max, $default]) {
        $out[$key] = $default;
    }
    return array_merge($out, um_unraid_thresholds(UM_THRESHOLDS));
}

function um_settings_get(): array {
    $cfg = um_read_ini_file(um_manager_cfg())[''] ?? [];
    $daemon = um_ctl(['cmd' => 'status'], 5.0);
    $out = [
        'db_path' => (string) ($cfg['db_path'] ?? ''),
        'poll_fast' => (int) ($cfg['poll_fast'] ?? 30),
        'poll_slow' => (int) ($cfg['poll_slow'] ?? 600),
        'daemon' => $daemon,
        /* What each threshold would be if left blank: Unraid's Disk Settings
           where it has an opinion, our constant where it does not. The page
           shows these as placeholders so "blank" is never a mystery (F-8). */
        'inherited' => um_threshold_defaults(),
    ];
    /* Three different facts, kept apart: the EFFECTIVE value the daemon will
       use, what a blank field would inherit, and whether this key is currently
       an explicit override at all. Collapsing them is what would make a blank
       field indistinguishable from one that happens to equal the default. */
    $out['overrides'] = [];
    foreach (UM_THRESHOLDS as $key => [$min, $max, $unused]) {
        $stored = (string) ($cfg[$key] ?? '');
        $isOverride = $stored !== '' && is_numeric($stored);
        $out['overrides'][$key] = $isOverride ? (int) $stored : null;
        $out[$key] = $isOverride ? (int) $stored : $out['inherited'][$key];
    }
    return $out;
}

function um_settings_validate(array $post): array {
    $path = trim((string) ($post['db_path'] ?? ''));
    if ($path === '') {
        return ['ok' => false, 'error' => 'Set a database path on a pool, e.g. /mnt/user/appdata/unraid-manager', 'values' => []];
    }
    if (!um_valid_db_path($path)) {
        return ['ok' => false, 'error' =>
            'That path is on the USB flash device. Telemetry is written continuously and '
            . 'flash has finite write endurance — choose a directory on a pool, '
            . 'e.g. /mnt/user/appdata/unraid-manager', 'values' => []];
    }

    foreach (['poll_fast', 'poll_slow'] as $k) {
        if (!is_numeric($post[$k] ?? null)) {
            return ['ok' => false, 'error' => "$k must be a number of seconds", 'values' => []];
        }
    }
    $fast = (int) $post['poll_fast'];
    $slow = (int) $post['poll_slow'];
    if ($fast < UM_POLL_FAST_MIN || $fast > UM_POLL_MAX) {
        return ['ok' => false, 'error' => 'Fast poll interval must be between '
            . UM_POLL_FAST_MIN . ' and ' . UM_POLL_MAX . ' seconds', 'values' => []];
    }
    if ($slow < $fast || $slow > UM_POLL_MAX) {
        return ['ok' => false, 'error' => 'Slow poll interval must be at least the fast '
            . 'interval and at most ' . UM_POLL_MAX . ' seconds', 'values' => []];
    }
    /* A key absent from the post (a partial or programmatic save) must never
       erase a threshold - that reopens the erasure path Task 4's deferral
       about um_render_manager_cfg existed to close, just one field narrower.
       Seed those from what's already on flash. A key present but empty is the
       operator clearing the input box, which does restore the default - the
       two cases look identical only when nothing has been stored yet. */
    $onFlash = um_read_ini_file(um_manager_cfg())[''] ?? [];
    $thresholds = $fromFlash = $blank = [];
    $inherited = um_threshold_defaults();
    foreach (UM_THRESHOLDS as $key => [$min, $max, $unused]) {
        $default = $inherited[$key];
        $seeded = !array_key_exists($key, $post);
        $fromFlash[$key] = $seeded;
        $raw = $seeded ? (string) ($onFlash[$key] ?? '') : $post[$key];
        /* Blank means "follow Unraid": validated against the inherited value
           so the temp_crit > temp_warn rule below still sees real numbers, but
           written back blank so it keeps following. */
        if ($raw === '') { $thresholds[$key] = $default; $blank[$key] = true; continue; }
        if (!is_numeric($raw) || (int) $raw < $min || (int) $raw > $max) {
            /* A seeded value came off flash, not off the form, so refusing it
               would make the Settings page unsaveable until someone hand-fixed
               the file that broke it. Fall back the way config.py:82-84 already
               falls back, and let the operator's own submission be the only
               thing that can be refused. */
            if ($seeded) { $thresholds[$key] = $default; continue; }
            return ['ok' => false, 'error' => is_numeric($raw)
                ? "$key must be between $min and $max" : "$key must be a number",
                'values' => []];
        }
        $thresholds[$key] = (int) $raw;
    }
    if ($thresholds['temp_crit'] <= $thresholds['temp_warn']) {
        if ($fromFlash['temp_warn'] && $fromFlash['temp_crit']) {
            /* Neither half was submitted - the inverted pair is already on
               flash, so refusing here would make the Settings page unsaveable
               until someone hand-fixed the file that broke it. Reset both, the
               way config.py:85-88 resets them, and let the operator's own
               submission stay the only thing that can be refused. */
            $thresholds['temp_warn'] = $inherited['temp_warn'];
            $thresholds['temp_crit'] = $inherited['temp_crit'];
        } else {
            return ['ok' => false, 'error' =>
                'The critical temperature must be above the warning temperature, or one '
                . 'of the two bands can never be reached.', 'values' => []];
        }
    }

    if ($thresholds['capacity_watch'] >= $thresholds['capacity_high_water']) {
        /* Identical rule and identical reasoning to the temperature pair above,
           and it must sit BEFORE the $stored copy below: appended after it,
           the both-seeded reset wrote to a $thresholds nobody read again, so a
           legacy inverted pair survived every programmatic save forever:
           a watch level at or above the warning level can never be the one that
           fires. Without this the page saved 95/90 cleanly, redisplayed it, and
           config.py silently reset both - the daemon using 80/90 with nothing
           on screen saying so. */
        if ($fromFlash['capacity_watch'] && $fromFlash['capacity_high_water']) {
            $thresholds['capacity_watch'] = $inherited['capacity_watch'];
            $thresholds['capacity_high_water'] = $inherited['capacity_high_water'];
        } else {
            return ['ok' => false, 'error' =>
                'The capacity warning must be above the watch level, or one of the two '
                . 'bands can never be reached.', 'values' => []];
        }
    }

    /* Written back BLANK where the operator left it blank, so the setting keeps
       following Unraid's rather than freezing at whatever it said today. The
       checks above needed real numbers, which is why this is the last step. */
    $stored = $thresholds;
    foreach (array_keys($blank) as $key) {
        $stored[$key] = '';
    }
    return ['ok' => true, 'error' => null,
            'values' => ['db_path' => $path, 'poll_fast' => $fast,
                         'poll_slow' => $slow] + $stored];
}

const UM_RC = '/usr/local/emhttp/plugins/unraid-manager/scripts/rc.unraid-manager';

function um_daemon_action(string $action): array {
    /* An allow-list, not a sanitiser: the value reaches a shell, and the set of
       things a browser may ask for here is three words long. Anything else is
       refused outright rather than escaped. */
    if (!in_array($action, ['start', 'stop', 'restart'], true)) {
        return ['ok' => false, 'error' => 'unknown daemon action'];
    }
    $out = [];
    $code = 0;
    exec(escapeshellcmd(UM_RC) . ' ' . $action . ' 2>&1', $out, $code);
    /* rc `status` exits 3 when stopped, but start/stop/restart use 0 for
       success — so the exit code is the answer here, and its output is the
       explanation (the flash-path refusal arrives this way). */
    return ['ok' => $code === 0, 'action' => $action, 'exit' => $code,
            'output' => implode("\n", $out)];
}

function um_settings_save(array $values): array {
    if (!um_atomic_write(um_manager_cfg(), um_render_manager_cfg($values))) {
        return ['ok' => false, 'error' => 'could not write ' . um_manager_cfg()];
    }
    $reload = um_ctl(['cmd' => 'reload']);
    return ['ok' => true, 'settings' => um_settings_get(), 'reload' => $reload];
}

if (PHP_SAPI !== 'cli') {
    um_require_session();
    if ($_SERVER['REQUEST_METHOD'] === 'POST') {
        um_require_csrf($_POST);
        if (isset($_POST['daemon'])) {
            $result = um_daemon_action((string) $_POST['daemon']);
            $result['settings'] = um_settings_get();
            um_json($result, $result['ok'] ? 200 : 400);
        }
        $checked = um_settings_validate($_POST);
        if (!$checked['ok']) um_json(['error' => $checked['error']], 400);
        um_json(um_settings_save($checked['values']));
    }
    um_json(um_settings_get());
}
