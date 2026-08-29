<?PHP
/* Shared plumbing for the Unraid-Manager API endpoints.
 *
 * Requiring this file must have no side effects: the test suite loads it
 * directly. Everything that acts is a function, and the gates are split into a
 * pure predicate (um_*_ok) and a thin wrapper that refuses the request — the
 * predicate is what the gate test actually exercises.
 *
 * Two rules this file exists to keep:
 *   - The daemon owns every database write. Here the database is opened
 *     READ-ONLY, always. Mutations go to flash config or over the socket.
 *   - No API key is ever returned, logged or rendered. um_public_node() is the
 *     only way a node row reaches a response.
 */

const UM_CFG_DIR_DEFAULT = '/boot/config/plugins/unraid-manager';
const UM_SOCKET = '/var/run/unraid-manager/managerd.sock';

$um_cfg_dir = UM_CFG_DIR_DEFAULT;

function um_set_cfg_dir(string $dir): void { global $um_cfg_dir; $um_cfg_dir = rtrim($dir, '/'); }
function um_cfg_dir(): string { global $um_cfg_dir; return $um_cfg_dir; }
function um_keys_dir(): string { return um_cfg_dir() . '/keys'; }
function um_manager_cfg(): string { return um_cfg_dir() . '/manager.cfg'; }
function um_nodes_cfg(): string { return um_cfg_dir() . '/nodes.cfg'; }

/* ── gates ────────────────────────────────────────────────────────────────── */

function um_session_ok(array $session): bool {
    return !empty($session['unraid_login']) && !empty($session['unraid_user']);
}

function um_session(): array {
    /* A .page is rendered by emhttp's dispatcher, which has already started the
       session. A standalone endpoint under /plugins/ has NOT — $_SESSION is
       simply empty there, so every request 401s no matter who is logged in.
       Found on Raven during the P0 live trial.

       Copied from Unraid's own auth-request.php: only touch the session when a
       session cookie exists (no cookie, no session to read), and close it
       immediately — a held session lock serialises every concurrent API call
       behind whichever one got there first, and this page fires several at
       once. */
    if (session_status() === PHP_SESSION_ACTIVE) return $_SESSION ?? [];
    if (!isset($_COOKIE[session_name()])) return [];
    session_start();
    $session = $_SESSION ?? [];
    session_write_close();
    return $session;
}

function um_csrf_ok(array $post, array $var): bool {
    $server = (string) ($var['csrf_token'] ?? '');
    $given  = (string) ($post['csrf_token'] ?? '');
    /* No server token means we cannot verify, which is a refusal, not a pass.
       hash_equals so a wrong token costs the same time as a right one. */
    if ($server === '' || $given === '') return false;
    return hash_equals($server, $given);
}

function um_json($data, int $code = 200): void {
    http_response_code($code);
    header('Content-Type: application/json');
    header('Cache-Control: no-store');
    echo json_encode($data);
    exit;
}

function um_require_session(): void {
    if (!um_session_ok(um_session())) um_json(['error' => 'not authenticated'], 401);
}

const UM_VAR_INI = '/var/local/emhttp/var.ini';

function um_var(): array {
    /* Same shape of problem as um_session(): emhttp's dispatcher hands a .page
       its $var, and a standalone endpoint under /plugins/ gets nothing — so the
       SERVER's token reads as empty and every POST is refused as invalid CSRF
       even though the browser sent the right one. Read it from the file emhttp
       writes it to. Found on Raven during the P0 live trial.

       Still fails closed: an unreadable var.ini yields no token, and
       um_csrf_ok() refuses when the server side is empty. */
    global $var;
    if (is_array($var ?? null) && !empty($var['csrf_token'])) return $var;
    $ini = @parse_ini_file(UM_VAR_INI);
    return is_array($ini) ? $ini : [];
}

function um_platform_csrf_enforced(): bool {
    /* Unraid runs webGui/include/local_prepend.php via auto_prepend_file on
       EVERY request. On a POST it validates csrf_token against var.ini itself,
       terminates the request outright if it is missing or wrong, and then
       `unset($_POST['csrf_token'])`. So an endpoint never sees the token: by
       the time our code runs the check has already happened and the evidence
       has been deliberately removed.

       csrf_terminate() is defined by that prepend and by nothing else, so its
       presence is positive proof the prepend executed in this process — which
       means any POST that reached us passed the platform's check. Found on
       Raven during the P0 live trial, where re-checking a consumed token made
       every POST a 403.

       The method test is not redundant. The prepend DEFINES csrf_terminate on
       every request but only VALIDATES on POST, so proof it ran is not proof
       it checked anything — without this clause the gate would pass anything
       that reached it over GET. No mutation route is reachable that way today;
       the clause is what keeps that true after the next refactor. */
    return function_exists('csrf_terminate') && ($_SERVER['REQUEST_METHOD'] ?? '') === 'POST';
}

function um_require_csrf(array $post): void {
    /* Our own check first: it still applies wherever the token survives to us.
       Then the platform's. Neither holding is a refusal — this fails closed. */
    if (um_csrf_ok($post, um_var())) return;
    if (um_platform_csrf_enforced()) return;
    um_json(['error' => 'invalid csrf token'], 403);
}

/* ── paths and identity ───────────────────────────────────────────────────── */

function um_valid_db_path(string $path): bool {
    $path = trim($path);
    if ($path === '' || $path[0] !== '/') return false;

    /* Normalize without touching the filesystem: the path may not exist yet,
       and '/mnt/../boot/x' must be refused for the same reason '/boot/x' is. */
    $parts = [];
    foreach (explode('/', $path) as $part) {
        if ($part === '' || $part === '.') continue;
        if ($part === '..') { array_pop($parts); continue; }
        $parts[] = $part;
    }
    $normal = '/' . implode('/', $parts);
    return !($normal === '/boot' || str_starts_with($normal, '/boot/'));
}

function um_new_uuid(): string { return bin2hex(random_bytes(16)); }

function um_safe_id(string $id): bool {
    return $id !== '' && (bool) preg_match('/^[0-9a-f]{8,64}$/', $id);
}

/* ── ini ──────────────────────────────────────────────────────────────────── */

function um_parse_ini(string $text): array {
    $out = ['' => []];
    $section = '';
    foreach (preg_split('/\R/', $text) as $line) {
        $line = trim($line);
        if ($line === '' || $line[0] === '#' || $line[0] === ';') continue;
        if ($line[0] === '[' && str_ends_with($line, ']')) {
            $section = trim(substr($line, 1, -1));
            $out[$section] = $out[$section] ?? [];
            continue;
        }
        if (!str_contains($line, '=')) continue;
        [$k, $v] = explode('=', $line, 2);
        $out[$section][trim($k)] = trim(trim($v), "\"'");
    }
    /* A sectioned file (nodes.cfg) has no pre-section keys, and leaving an
       empty '' behind makes "one section per node" count one too many. Callers
       that want the top level use [''] ?? [] and are unaffected. */
    if ($out[''] === []) unset($out['']);
    return $out;
}

function um_read_ini_file(string $path): array {
    $text = @file_get_contents($path);
    return um_parse_ini($text === false ? '' : $text);
}

function um_render_manager_cfg(array $kv): string {
    $out = '';
    foreach (['db_path', 'poll_fast', 'poll_slow', 'capacity_high_water',
              'capacity_watch', 'temp_warn', 'temp_crit', 'error_window_min'] as $k) {
        $out .= $k . '=' . str_replace(["\r", "\n"], '', (string) ($kv[$k] ?? '')) . "\n";
    }
    return $out;
}

function um_render_nodes_cfg(array $nodes): string {
    $out = "# Unraid-Manager node registry. Authoritative: the daemon syncs FROM this.\n";
    foreach ($nodes as $n) {
        $out .= "\n[" . $n['id'] . "]\n";
        foreach (['name', 'address', 'port', 'tier', 'enabled'] as $k) {
            $value = $n[$k] ?? '';
            if ($k === 'enabled') $value = ((int) $value) ? 1 : 0;
            $out .= $k . '=' . str_replace(["\r", "\n"], '', (string) $value) . "\n";
        }
    }
    return $out;
}

function um_read_nodes(): array {
    $nodes = [];
    foreach (um_read_ini_file(um_nodes_cfg()) as $id => $fields) {
        if ($id === '' || empty($fields['address'])) continue;
        $nodes[] = ['id' => $id, 'name' => $fields['name'] ?? $fields['address'],
                    'address' => $fields['address'], 'port' => (int) ($fields['port'] ?? 0),
                    'tier' => (int) ($fields['tier'] ?? 0),
                    'enabled' => (int) ($fields['enabled'] ?? 1)];
    }
    return $nodes;
}

function um_write_nodes(array $nodes): bool {
    return um_atomic_write(um_nodes_cfg(), um_render_nodes_cfg($nodes));
}

function um_atomic_write(string $path, string $content): bool {
    /* Flash is FAT32 and a torn registry write costs the operator every
       enrollment. Write beside it, then rename. */
    $tmp = $path . '.tmp';
    if (@file_put_contents($tmp, $content) === false) return false;
    return @rename($tmp, $path);
}

function um_duplicate_endpoint(array $nodes, string $address, int $port, ?string $exceptId = null): bool {
    foreach ($nodes as $n) {
        if ($exceptId !== null && ($n['id'] ?? '') === $exceptId) continue;
        if (($n['address'] ?? '') === $address && (int) ($n['port'] ?? 0) === $port) return true;
    }
    return false;
}

/* ── keys ─────────────────────────────────────────────────────────────────── */

function um_key_path(string $id): ?string {
    return um_safe_id($id) ? um_keys_dir() . '/' . $id . '.key' : null;
}

function um_write_key(string $id, string $key): bool {
    $path = um_key_path($id);
    if ($path === null || trim($key) === '') return false;
    if (!is_dir(um_keys_dir()) && !@mkdir(um_keys_dir(), 0700, true)) return false;
    if (@file_put_contents($path, trim($key) . "\n") === false) return false;
    chmod($path, 0600);
    return true;
}

function um_has_key(string $id): bool {
    $path = um_key_path($id);
    return $path !== null && is_file($path);
}

function um_delete_key(string $id): void {
    $path = um_key_path($id);
    if ($path !== null) @unlink($path);
}

/* Unraid's own Settings -> Disk Settings, mapped onto our threshold keys.
   P1 exit finding F-8: the operator has already said how hot is too hot, and
   shipping unrelated constants gave one box two answers - Raven's Unraid said
   45/55 while this plugin said 50/60, so a disk at 47 C was warm to one and
   fine to the other.

   Mirrors daemon/config.py's UNRAID_THRESHOLD_KEYS; test_config.py and
   settings_test.php each assert their own side. hotssd/maxssd are deliberately
   not read - telling an SSD from a spinner needs a rotational flag the
   physical enumeration does not carry. */
const UM_UNRAID_THRESHOLD_KEYS = [
    'hot'      => 'temp_warn',
    'max'      => 'temp_crit',
    'warning'  => 'capacity_watch',
    'critical' => 'capacity_high_water',
];

/* $bounds is REQUIRED, not optional: this reads another plugin's file, and a
   caller that forgets to range-check would report a fleet warning at 4 C on
   the settings page while the daemon quietly used 50 - the two halves
   disagreeing about the same file. daemon/config.py range-checks the same way;
   settings.php passes UM_THRESHOLDS. */
function um_unraid_thresholds(array $bounds,
                              string $path = '/boot/config/plugins/dynamix/dynamix.cfg'): array {
    $display = um_read_ini_file($path)['display'] ?? [];
    /* `unit` is NOT read, deliberately - see daemon/config.py for the whole
       story. dynamix.cfg stores hot/max in Celsius whatever the display unit
       says, verified on hardware 2026-08-29 on a box switched to Fahrenheit
       for the purpose. There is nothing to convert, and the guard that used to
       decline to inherit on an F box is gone with it. */
    $out = [];
    foreach (UM_UNRAID_THRESHOLD_KEYS as $theirs => $ours) {
        $raw = $display[$theirs] ?? null;
        /* A value that is not a plain integer is dropped, leaving our own
           default in place, rather than trusted. */
        if ($raw === null || $raw === '' || !ctype_digit((string) $raw)) continue;
        $value = (int) $raw;
        /* Fails CLOSED on a key with no bound: a required $bounds argument stops
           a caller forgetting the parameter, not a maintainer forgetting an
           entry, and python raises KeyError for the same input - so accepting
           it here would diverge the two halves from silent-accept to
           daemon-crash. */
        if (!isset($bounds[$ours])) continue;
        [$min, $max] = $bounds[$ours];
        if ($value >= $min && $value <= $max) {
            $out[$ours] = $value;
        }
    }
    return $out;
}

/* The timezone the BOX is set to, for endpoints to report so the pane can
   render stored UTC instants as a wall clock (frontend/src/time.js).

   Unraid runs PHP with date.timezone unset, so date_default_timezone_get()
   answers UTC on a box whose own clock says EDT - verified on Raven, where php
   said 21:36 UTC and `date` said 17:36 EDT. Formatting against that would put
   every timestamp in the pane four hours out and look entirely plausible. The
   system zone lives in the /etc/localtime symlink, which points into the
   zoneinfo tree and may be relative.

   $link is injectable so the PRECEDENCE is testable off the box: on a dev
   machine both branches answer UTC, and deleting the symlink lookup entirely
   passed the whole suite until a test pinned it. */
function um_zone_from_link(?string $link): ?string {
    if (!is_string($link)) return null;
    $at = strpos($link, 'zoneinfo/');
    if ($at === false) return null;
    $zone = substr($link, $at + strlen('zoneinfo/'));
    return $zone === '' ? null : $zone;
}

function um_local_timezone(?string $link = null): string {
    $link = $link ?? @readlink('/etc/localtime');
    return um_zone_from_link($link) ?? date_default_timezone_get();
}

/* Unraid's Settings -> Date & Time writes strftime formats into dynamix.cfg's
   [display] section. Raven, verified 2026-08-28: time="%I:%M %p" (a 12-hour
   clock) and date="%c" (the "System Setting" placeholder).

   Only the clock is read. Translating %c and the rest of the date formats into
   something Intl speaks is a lookup table with a long tail, and the
   alternative - formatting every timestamp server-side - is the field
   proliferation this deliberately replaced. The pane renders YYYY-MM-DD, which
   is unambiguous whatever the operator's locale, and honours the 12/24-hour
   setting exactly.

   Unset means 24-hour: an operator who has never chosen gets the unambiguous
   one, not a guess. */
function um_clock_is_12h(?string $format): bool {
    $f = (string) $format;
    /* %p is the AM/PM marker; %I and %l are the 12-hour hour. %H, %k and %R
       are the 24-hour ones and match none of these. */
    return str_contains($f, '%p') || str_contains($f, '%P')
        || str_contains($f, '%I') || str_contains($f, '%l');
}

function um_display_clock_12h(string $path = '/boot/config/plugins/dynamix/dynamix.cfg'): bool {
    return um_clock_is_12h(um_read_ini_file($path)['display']['time'] ?? null);
}

function um_public_node(array $row): array {
    /* An allow-list, not a blocklist: a column added to the nodes table later
       cannot leak by default. */
    $out = [];
    foreach (['id', 'name', 'address', 'port', 'tier', 'enabled', 'added_at', 'last_seen'] as $k) {
        if (array_key_exists($k, $row)) $out[$k] = $row[$k];
    }
    /* has_key answers "is there a key for this node", from whichever source the
       caller has: um_enroll() passes the row it just validated, before the key
       file has been re-read, while um_nodes_list() passes a database row that
       never carries key material at all. Checking only the filesystem makes the
       enrollment response say has_key:false about a node it just wrote a key
       for. */
    $out['has_key'] = !empty($row['key']) || !empty($row['api_key'])
        || (isset($row['id']) && um_has_key((string) $row['id']));
    return $out;
}

/* ── database, read-only ──────────────────────────────────────────────────── */

function um_readonly(SQLite3 $db): SQLite3 {
    /* The daemon is the only writer; a page request must never be able to write
       to or corrupt its database.

       SQLite3, not PDO. Unraid's php-fpm ships the sqlite3 extension but NOT
       pdo_sqlite - PDO::getAvailableDrivers() is an empty array there, so every
       `new PDO('sqlite:...')` fails with "could not find driver" and every read
       silently returned nothing. The CLI does have pdo_sqlite, which is why the
       test suite never noticed. Found on Raven during the P0 live trial;
       recorded as verified platform fact 15.

       query_only is deliberately a PRAGMA rather than an OPEN_READONLY flag: a
       WAL database needs to map its -shm to be read at all, and a reader
       without write access to it fails in a way that looks like corruption.
       Opening read-write and then forbidding writes at runtime is both safer
       and provable - common_test.php proves it by attempting an INSERT. */
    $db->enableExceptions(true);
    $db->exec('PRAGMA query_only = 1');
    return $db;
}

/* Whether any um_query in THIS request gave up and answered nothing.
 *
 * The silent [] is the right degradation for a dropped derived table - a node
 * with no node_health rows rolls up to "unknown", which is true. It LIES for a
 * missing `nodes` table: the fleet then reads "0 node(s)" with db:true and no
 * banner, which is indistinguishable from a fleet nobody has enrolled. That is
 * the exact invariant Task 13 item 7 and the whole dbUnreadable path exist to
 * protect, so the flag folds into the db flag the shell already renders rather
 * than growing a second banner. Narrow - migrate() never drops `nodes`, so it
 * needs a corrupt or foreign database - but it fails in the one direction that
 * tells the operator everything is fine. */
function um_query_failed(?bool $set = null): bool {
    static $failed = false;
    if ($set !== null) $failed = $set;
    return $failed;
}

/* The db flag every endpoint reports: openable AND actually answering. */
function um_db_readable(?SQLite3 $db): bool {
    return $db !== null && !um_query_failed();
}

function um_query(SQLite3 $db, string $sql, array $params = []): array {
    /* SQLite3 has no fetchAll. One helper beats four hand-rolled while loops.
     *
     * The handle has enableExceptions(true), so prepare() THROWS on a missing
     * table rather than returning false - the `=== false` guards below were
     * dead, and an endpoint answered a PHP fatal (an HTML 500 where the pane
     * expects JSON) instead of an empty list. Reachable in the window where
     * migrate() has dropped a derived table and not yet rebuilt it, and after
     * any future schema change that lands before the daemon restarts.
     *
     * Caught, not prevented: the caller's own "no rows" path is the honest
     * answer, and it is the one every caller already handles. The guards stay
     * because a future caller may hand this a handle without exceptions. */
    try {
        $stmt = $db->prepare($sql);
    } catch (Throwable $e) {
        um_query_failed(true);
        return [];
    }
    if ($stmt === false) { um_query_failed(true); return []; }
    foreach ($params as $name => $value) {
        $stmt->bindValue($name, $value, is_int($value) ? SQLITE3_INTEGER : SQLITE3_TEXT);
    }
    try {
        $result = $stmt->execute();
    } catch (Throwable $e) {
        um_query_failed(true);
        return [];
    }
    if ($result === false) { um_query_failed(true); return []; }
    $rows = [];
    while ($row = $result->fetchArray(SQLITE3_ASSOC)) $rows[] = $row;
    $result->finalize();
    return $rows;
}

function um_db(): ?SQLite3 {
    $cfg = um_read_ini_file(um_manager_cfg())[''] ?? [];
    $dir = (string) ($cfg['db_path'] ?? '');
    if ($dir === '' || !um_valid_db_path($dir)) return null;
    $file = rtrim($dir, '/') . '/manager.db';
    if (!is_file($file)) return null;
    try {
        $db = new SQLite3($file, SQLITE3_OPEN_READWRITE);
        $db->busyTimeout(5000);
        return um_readonly($db);
    } catch (Throwable $e) {
        return null;
    }
}

/* ── control socket ───────────────────────────────────────────────────────── */

function um_ctl(array $cmd, float $timeout = 10.0): array {
    $sock = @stream_socket_client('unix://' . UM_SOCKET, $errno, $errstr, $timeout);
    if ($sock === false) {
        return ['ok' => false, 'error' => 'managerd is not running (' . $errstr . ')'];
    }
    stream_set_timeout($sock, (int) $timeout);
    fwrite($sock, json_encode($cmd) . "\n");
    $line = fgets($sock, 1024 * 1024);
    fclose($sock);
    if ($line === false) return ['ok' => false, 'error' => 'no reply from managerd'];
    $decoded = json_decode($line, true);
    return is_array($decoded) ? $decoded : ['ok' => false, 'error' => 'malformed reply from managerd'];
}

/* ── the built pane ───────────────────────────────────────────────────────── */

const UM_UI_URL = '/plugins/unraid-manager/ui';

function um_ui_dir(): string { return __DIR__ . '/../ui'; }

function um_asset_tags(string $entry = 'src/main.js', ?string $dir = null): string {
    /* Vite emits hashed filenames, and nothing may hardcode a hash into a page.
       Read the manifest at run time and resolve them - the same approach Unraid
       uses for its own bundle in
       dynamix.my.servers/include/web-components-extractor.php.

       Vite 5+ writes .vite/manifest.json; older versions wrote manifest.json at
       the root. Accept both, so a toolchain bump cannot blank the page. */
    $dir = $dir ?? um_ui_dir();
    $manifest = [];
    foreach (['/.vite/manifest.json', '/manifest.json'] as $candidate) {
        $raw = @file_get_contents($dir . $candidate);
        if ($raw === false) continue;
        $decoded = json_decode($raw, true);
        if (is_array($decoded)) { $manifest = $decoded; break; }
    }

    $item = $manifest[$entry] ?? null;
    if (!$item || empty($item['file'])) {
        /* Say so loudly. A blank page with nothing in the console is the worst
           possible outcome of a build that did not run. */
        return '<p class="um-stale">The Unraid-Manager interface is not built. '
             . 'Reinstall the plugin, or run <code>bash build.sh</code> on a machine '
             . 'that has node installed.</p>';
    }

    $tags = '';
    /* Cast, not a bare ??: a manifest whose css is a scalar would raise
       'foreach() argument must be of type array|object' - and run.sh exists to
       fail on exactly that, because an unguarded foreach shipped here once. */
    foreach ((array) ($item['css'] ?? []) as $css) {
        $tags .= '<link rel="stylesheet" href="' . UM_UI_URL . '/'
               . htmlspecialchars($css) . '">' . "\n";
    }
    return $tags . '<script type="module" src="' . UM_UI_URL . '/'
         . htmlspecialchars($item['file']) . '"></script>';
}
