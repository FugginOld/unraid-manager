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
       every POST a 403. */
    return function_exists('csrf_terminate');
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
    foreach (['db_path', 'poll_fast', 'poll_slow'] as $k) {
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

function um_readonly(PDO $pdo): PDO {
    /* The daemon is the only writer; a page request must never be able to write
       to or corrupt its database.
       Deliberately a PRAGMA rather than a 'sqlite:file:...?mode=ro' DSN: URI
       filenames are a compile-time sqlite option that pdo_sqlite does not
       guarantee is on, and when it is off the whole DSN is taken as a literal
       path — which silently produces a WRITABLE handle to a file named
       "file:/mnt/...?mode=ro". query_only is a runtime setting that either
       applies or throws, and common_test.php proves it by attempting a write. */
    $pdo->exec('PRAGMA query_only = 1');
    return $pdo;
}

function um_db(): ?PDO {
    $cfg = um_read_ini_file(um_manager_cfg())[''] ?? [];
    $dir = (string) ($cfg['db_path'] ?? '');
    if ($dir === '' || !um_valid_db_path($dir)) return null;
    $file = rtrim($dir, '/') . '/manager.db';
    if (!is_file($file)) return null;
    try {
        $pdo = new PDO('sqlite:' . $file, null, null,
                       [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION]);
        $pdo->setAttribute(PDO::ATTR_DEFAULT_FETCH_MODE, PDO::FETCH_ASSOC);
        return um_readonly($pdo);
    } catch (PDOException $e) {
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
