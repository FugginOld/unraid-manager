<?PHP
/* Gate-chain and helper tests for include/common.php, in the HBAviewer pattern:
   pure predicates, no HTTP, no daemon, no live box.
     php tests/php/common_test.php  ->  "common: all pass" (exit 0) */

require_once __DIR__ . '/../../source/usr/local/emhttp/plugins/unraid-manager/include/common.php';

$fails = 0;
function check(string $name, bool $ok): void {
    global $fails;
    echo ($ok ? "PASS  " : "FAIL  ") . $name . "\n";
    if (!$ok) $fails++;
}

/* A few checks below assert on the source text — for things a unit test cannot
   observe on Windows (chmod is a no-op there) or that are about a call being
   present at all. */
$common_src = (string) file_get_contents(
    __DIR__ . '/../../source/usr/local/emhttp/plugins/unraid-manager/include/common.php');

/* ── the session gate ─────────────────────────────────────────────────────── */
check('a logged-in session passes',      um_session_ok(['unraid_login' => 1234567, 'unraid_user' => 'root']));
check('an empty session fails',          um_session_ok([]) === false);
check('a session with no user fails',    um_session_ok(['unraid_login' => 1234567]) === false);
check('an empty username fails',         um_session_ok(['unraid_login' => 1, 'unraid_user' => '']) === false);

/* ── the CSRF gate ────────────────────────────────────────────────────────── */
$var = ['csrf_token' => 'abc123'];
check('matching csrf passes',            um_csrf_ok(['csrf_token' => 'abc123'], $var));
check('missing csrf fails',              um_csrf_ok([], $var) === false);
check('wrong csrf fails',                um_csrf_ok(['csrf_token' => 'nope'], $var) === false);
check('empty csrf fails',                um_csrf_ok(['csrf_token' => ''], $var) === false);
/* Fail closed: no server token means we cannot verify, so we refuse. */
check('no server token fails closed',    um_csrf_ok(['csrf_token' => 'abc123'], []) === false);
check('empty server token fails closed', um_csrf_ok(['csrf_token' => ''], ['csrf_token' => '']) === false);

/* ── db_path validation, the same rule the daemon enforces ────────────────── */
check('a pool path is valid',            um_valid_db_path('/mnt/user/appdata/unraid-manager'));
check('a cache path is valid',           um_valid_db_path('/mnt/cache/unraid-manager'));
check('/boot is refused',                um_valid_db_path('/boot') === false);
check('/boot/config is refused',         um_valid_db_path('/boot/config/plugins/unraid-manager') === false);
check('a traversal onto /boot is refused', um_valid_db_path('/mnt/../boot/x') === false);
check('a doubled slash onto /boot is refused', um_valid_db_path('/boot//config') === false);
check('an empty path is refused',        um_valid_db_path('') === false);
check('a relative path is refused',      um_valid_db_path('appdata/unraid-manager') === false);
check('a path merely containing boot is fine', um_valid_db_path('/mnt/user/bootstrap'));

/* ── ini round-trip with the daemon's reader ──────────────────────────────── */
$nodes = [
    ['id' => 'a1b2', 'name' => 'Golem', 'address' => '192.168.2.248', 'port' => 15137, 'tier' => 0, 'enabled' => 1],
    ['id' => 'b2c3', 'name' => 'Raven', 'address' => '192.168.2.19',  'port' => 29220, 'tier' => 0, 'enabled' => 1],
];
$rendered = um_render_nodes_cfg($nodes);
$parsed   = um_parse_ini($rendered);
check('rendered nodes.cfg has a section per node', count($parsed) === 2);
check('section key is the node id',      isset($parsed['a1b2']));
check('name survives the round trip',    ($parsed['a1b2']['name'] ?? '') === 'Golem');
check('port survives the round trip',    ($parsed['b2c3']['port'] ?? '') === '29220');
check('no key material in nodes.cfg',    !preg_match('/[A-Za-z0-9_\-]{40,}/', $rendered));

$mgr = um_parse_ini(um_render_manager_cfg(['db_path' => '/mnt/user/x', 'poll_fast' => 30, 'poll_slow' => 600]));
check('manager.cfg keys are top level',  ($mgr['']['db_path'] ?? '') === '/mnt/user/x');
check('manager.cfg carries both intervals',
      ($mgr['']['poll_fast'] ?? '') === '30' && ($mgr['']['poll_slow'] ?? '') === '600');

check('ini parser ignores comments',     um_parse_ini("# c\nk=v\n")['']['k'] === 'v');
check('ini parser strips quotes',        um_parse_ini('k="v"')['']['k'] === 'v');

/* ── duplicate endpoint rejection ─────────────────────────────────────────── */
check('same address and port is a duplicate', um_duplicate_endpoint($nodes, '192.168.2.248', 15137));
check('same address, other port is not',      um_duplicate_endpoint($nodes, '192.168.2.248', 15138) === false);
check('an unknown endpoint is not',           um_duplicate_endpoint($nodes, '10.0.0.1', 80) === false);
check('a node does not duplicate itself',     um_duplicate_endpoint($nodes, '192.168.2.248', 15137, 'a1b2') === false);

/* ── uuid ─────────────────────────────────────────────────────────────────── */
$u1 = um_new_uuid(); $u2 = um_new_uuid();
check('uuid is hex and long enough', (bool) preg_match('/^[0-9a-f]{32}$/', $u1));
check('uuids differ',                $u1 !== $u2);
check('uuid is safe as a filename',  !str_contains($u1, '/') && !str_contains($u1, '.'));

/* ── no key ever leaves through a node payload ────────────────────────────── */
$row = ['id' => 'a1b2', 'name' => 'Golem', 'address' => '192.168.2.248', 'port' => 15137,
        'key' => 'super-secret-key-0123456789012345678901', 'api_key' => 'another-one'];
$pub = um_public_node($row);
check('public node reports has_key',     ($pub['has_key'] ?? null) === true);
check('public node drops key',           !array_key_exists('key', $pub));
check('public node drops api_key',       !array_key_exists('api_key', $pub));
check('no secret in the encoded payload', !str_contains(json_encode($pub), 'super-secret'));
$pub2 = um_public_node(['id' => 'x', 'name' => 'X', 'address' => 'h', 'port' => 1]);
check('has_key is false when there is none', ($pub2['has_key'] ?? null) === false);

/* ── the key file is written 0600 ─────────────────────────────────────────── */
/* A real node id is a 32-hex uuid from um_new_uuid(); um_safe_id() enforces
   that shape because the id becomes a filename. Use one here rather than
   loosening the guard to suit a short test fixture. */
$kid = str_repeat('a1b2', 8);
$tmp = sys_get_temp_dir() . '/um_keys_' . getmypid();
@mkdir($tmp, 0700, true);
um_set_cfg_dir($tmp);
@mkdir($tmp . '/keys', 0700, true);
check('write_key succeeds', um_write_key($kid, 'the-secret'));
$keyfile = $tmp . '/keys/' . $kid . '.key';
check('key file written', is_file($keyfile));
check('key file holds the key', trim((string) file_get_contents($keyfile)) === 'the-secret');
check('has_key sees it on disk', um_has_key($kid));
if (DIRECTORY_SEPARATOR === '/') {
    check('key file mode is 0600', substr(sprintf('%o', fileperms($keyfile)), -4) === '0600');
} else {
    /* Windows PHP makes chmod a no-op, so assert the call instead of the mode.
       CI runs on Linux and checks the real permission bits above. */
    check('write_key chmods 0600 (source check on Windows)', str_contains($common_src, 'chmod($path, 0600)'));
}
check('a traversing node id is refused',  um_write_key('../evil', 'x') === false);
check('a short non-uuid id is refused',   um_write_key('a1b2', 'x') === false);
check('an empty node id is refused',      um_write_key('', 'x') === false);
um_delete_key($kid);
check('delete_key removes the file', !is_file($keyfile));
@rmdir($tmp . '/keys'); @rmdir($tmp);

/* ── the database handle is read-only ─────────────────────────────────────── */
/* The daemon is the only writer. A page request must not be able to write, and
   the guard is a runtime PRAGMA, so prove it with an actual INSERT rather than
   trusting a DSN flag. */
$rodb = new PDO('sqlite::memory:');
$rodb->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
$rodb->exec('CREATE TABLE t(x)');
um_readonly($rodb);
$threw = false;
try { $rodb->exec("INSERT INTO t VALUES(1)"); } catch (PDOException $e) { $threw = true; }
check('a write through a read-only handle throws', $threw);
check('a read through a read-only handle still works',
      $rodb->query('SELECT COUNT(*) FROM t')->fetchColumn() === 0);
check('um_db applies the read-only pragma', str_contains($common_src, 'um_readonly('));

/* ── every endpoint passes through the gates ──────────────────────────────── */
/* Source-text assertions, in the style of the chmod check above: the gates
   themselves are proven by the predicate tests, and these prove each endpoint
   actually calls them. A new endpoint that forgets one fails here. */
foreach (['nodes', 'settings', 'events'] as $endpoint) {
    $src = (string) @file_get_contents(__DIR__ . '/../../source/usr/local/emhttp/plugins/unraid-manager/api/' . $endpoint . '.php');
    check("$endpoint.php requires a session", str_contains($src, 'um_require_session()'));
    if ($endpoint !== 'events') {   /* events.php is GET-only */
        check("$endpoint.php requires csrf on POST",
              str_contains($src, 'um_require_csrf($_POST)'));
    }
}

echo $fails === 0 ? "common: all pass\n" : "common: $fails FAILED\n";
exit($fails === 0 ? 0 : 1);
