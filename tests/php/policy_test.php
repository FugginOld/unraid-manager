<?PHP
/* Pins docs/review-policy.md to the tree.

   A policy file drifts from the code silently, and then defends functions that
   no longer exist. Two directions are checked for every citable claim:

     - the GUARD is still in the source (a rename or a "cleanup" removes it)
     - the CITED TEST still names the thing (deleting one line from another
       suite leaves it entirely green while the guard it represented stops
       being asserted, and this file would go on citing it)

   Only mechanically checkable claims are pinned. The prose is not.
     php tests/php/policy_test.php  ->  "policy: all pass" (exit 0) */

$fails = 0;
function check(string $name, bool $ok): void {
    global $fails;
    echo ($ok ? "PASS  " : "FAIL  ") . $name . "\n";
    if (!$ok) $fails++;
}

$root   = __DIR__ . '/../..';
$plugin = $root . '/source/usr/local/emhttp/plugins/unraid-manager';
function src(string $path): string { return (string) @file_get_contents($path); }

/* Pins must look at CODE, not at the prose explaining the code. Three of these
   originally matched the very comments that describe the guard, and stayed
   green after the guard itself was deleted - caught by mutation-testing them. */
function php_code_only(string $src): string {
    $out = '';
    foreach (token_get_all($src) as $tok) {
        if (is_array($tok)) {
            if ($tok[0] === T_COMMENT || $tok[0] === T_DOC_COMMENT) continue;
            $out .= $tok[1];
        } else {
            $out .= $tok;
        }
    }
    return $out;
}

$policy = src($root . '/docs/review-policy.md');
check('the policy file exists', $policy !== '');

/* ── guards named in "Rejected on sight" are still in the source ──────────── */
$common    = src($plugin . '/include/common.php');
$managerd  = src($plugin . '/daemon/managerd.py');
$store     = src($plugin . '/daemon/store.py');
$collector = src($plugin . '/daemon/collector.py');
$nodes     = src($plugin . '/api/nodes.php');
$rc        = src($plugin . '/scripts/rc.unraid-manager');
$fleetpage = src($plugin . '/UnraidManager.page');
$settingsjs = src($plugin . '/settings.js');
$plg       = src($root . '/unraid-manager.plg');

check('guard: the session is started, not assumed',
      str_contains($common, 'function um_session()') && str_contains($common, 'session_write_close()'));
check('guard: the csrf token is read from var.ini',
      str_contains($common, 'parse_ini_file(UM_VAR_INI)'));
check('guard: the platform gate is credited only on POST',
      str_contains($common, "REQUEST_METHOD'] ?? '') === 'POST'"));
check('guard: the read layer is SQLite3',
      str_contains($common, 'function um_db(): ?SQLite3') && str_contains($common, 'new SQLite3('));
check('guard: read-only is a runtime pragma',
      str_contains($common, 'PRAGMA query_only = 1'));
check('guard: the fleet page declares an icon-font Code',
      (bool) preg_match('/^Code="[0-9a-f]{4}"/m', $fleetpage));
check('guard: the nchan listen pattern tolerates trailing directives',
      str_contains($managerd, 'unix:([^\s;]+)'));
check('guard: the plg pre-stop block exits 0',
      (bool) preg_match('/rc\.unraid-manager stop\s*\nexit 0/', $plg));
/* Anchored on the connect CALL, not on the word: the comment above it explains
   check_same_thread at length and would keep this green on its own. [^)]* will
   not do - the argument list contains os.path.join(...). */
check('guard: the sqlite connection crosses threads',
      (bool) preg_match('/sqlite3\.connect\(.{0,200}?check_same_thread=False/s', $store));
check('guard: shutdown is bounded',
      str_contains($managerd, 'cancel_futures=True') && str_contains($managerd, 'exit_fn'));
check('guard: the pidfile is chmodded',
      str_contains($managerd, 'os.chmod(ctl.PID_PATH, 0o644)'));
check('guard: the rc script signals TERM and never KILL',
      str_contains($rc, 'kill -TERM') && !str_contains($rc, 'kill -9'));
check('guard: the rc script speaks the socket with python3',
      str_contains($rc, 'AF_UNIX') && !str_contains($rc, 'nc -U'));
check('guard: unknown means every domain is unreadable',
      str_contains($nodes, "=== count(\$domains)) return 'unknown'"));
check('guard: the probed hostname is the blank-name fallback',
      str_contains($settingsjs, 'probedHostname'));
/* ── "Do not re-add" — confirmed absent, not merely intended to be ────────── */
$parityQuery = '';
if (preg_match("/_domain\('parity'.*?parse_parity\)/s", $collector, $m)) $parityQuery = $m[0];
check('absent: the parity query does not ask for errors',
      $parityQuery !== '' && !str_contains($parityQuery, 'status errors'));
/* Globbed, not named: a read path added after this pin (health.php was
   exactly the kind of code that broke on php-fpm) must be covered without
   anyone remembering to list it here. */
/* `?: []` degraded to "nothing to check": moving api/ would have silently
   narrowed this pin to common.php alone and left it green. A pin that passes
   by finding no files is not a pin (P1 triage F-d). */
$pdoFiles = glob($plugin . '/api/*.php') ?: [];
check('the PDO pin actually sees the endpoints it claims to cover',
      count($pdoFiles) >= 4);
$pdoLeak = false;
foreach (array_merge([$plugin . '/include/common.php'], $pdoFiles) as $pdoFile) {
    if (str_contains(php_code_only(src($pdoFile)), 'new PDO(')) $pdoLeak = true;
}
check('absent: no PDO instantiation survives in the php layer', !$pdoLeak);
check('absent: no repo-level CLAUDE.md',
      !is_file($root . '/CLAUDE.md'));

/* ── cited tests still assert what the policy says they assert ────────────── */
/* The failure this catches: another suite keeps passing after its assertion is
   deleted, and the policy goes on naming a guard that no longer runs. */
$cited = [
    'harness_test.php'        => "class_exists('SQLite3')",
    'common_test.php'         => 'a write through a read-only handle throws',
    'rc_test.php'             => 'the socket is spoken to with python3, not nc',
    'plg_test.php'            => 'the pre-stop block cannot fail a fresh install',
    'pages_test.php'          => 'fleet page declares an icon-font Code',
    'nodes_test.php'          => 'one unknown alongside ok is degraded',
];
foreach ($cited as $file => $needle) {
    check("cited test $file still asserts its guard",
          str_contains(src(__DIR__ . '/' . $file), $needle));
}
/* [file, needle] pairs, not a file => needle map: test_source_policy.py
   carries four of these guards alone (the four moved out of this file's own
   py_code_only()/py_function() when they were replaced with an ast walk),
   and a map can hold only one needle per key - a second entry for the same
   file would silently overwrite the first instead of adding a second check. */
$pyCited = [
    ['test_store_writes.py',      'test_the_connection_is_usable_from_another_thread'],
    ['test_managerd.py',          'test_shutdown_does_not_wait_on_in_flight_polls'],
    ['test_collector_fast.py',    'test_the_parity_query_does_not_ask_for_errors'],
    ['test_collector_slow.py',    'test_serial_is_dropped_from_the_payload'],
    ['test_source_policy.py',     'test_parse_smart_strips_serial_number_and_logical_unit_id'],
    ['test_source_policy.py',     'test_disk_row_does_not_leak_serial_num'],
    ['test_source_policy.py',     'test_no_domain_query_contains_a_mutation'],
    ['test_source_policy.py',     'test_no_domain_query_contains_an_introspection_query'],
];
foreach ($pyCited as [$file, $needle]) {
    check("cited test $file still asserts $needle",
          str_contains(src($root . '/tests/python/' . $file), $needle));
}

/* ── the scope section still names the paths it protects ──────────────────── */
foreach (['store.py', 'common.php', 'rc.unraid-manager', 'unraid-manager.plg', 'build.sh'] as $path) {
    check("policy scope still names $path", str_contains($policy, $path));
}
check('policy still forbids ponytail-audit on this repo',
      str_contains($policy, 'Never `/ponytail-audit` this repo'));

echo $fails === 0 ? "policy: all pass\n" : "policy: $fails FAILED\n";
exit($fails === 0 ? 0 : 1);
