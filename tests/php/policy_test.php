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

function py_function(string $src, string $name): string {
    $pattern = '/^def ' . preg_quote($name, '/') . '\(.*?(?=^def |^class |\z)/ms';
    if (!preg_match($pattern, $src, $m)) return '';
    return $m[0];
}

function py_code_only(string $src): string {
    /* # strips FIRST, docstrings SECOND - the opposite order desyncs on a
       lone """ inside a # comment or an ordinary string literal (e.g.
       "# a docstring is delimited by """ in python" or SEP = '"""'):
       the docstring regex opens there and closes on the next real triple
       quote anywhere downstream, deleting real code between them. Stripping
       # first removes that lone triple-quote before the docstring regex
       ever sees it.

       The docstring regex is ANCHORED to a whole line - ^[ \t]*"""..."""[ \t]*$
       under /m - not a bare "any triple-quoted span". A GraphQL query
       written as a triple-quoted VALUE, e.g.
       _domain('shares', FAST, '''mutation { x }''', parse_shares), sits
       mid-line next to other code and is not anchored, so it survives; only
       a standalone docstring line is prose here.

       Demonstrated live (round 2's find): delete both doc.pop(...) calls
       from parse_smart and add one docstring sentence that quotes
       doc.pop('serial_number', None) - the unanchored, #-second version of
       this function left that quoted call sitting in $out, so the pin read
       the description and reported the guard present while the code
       implementing it was gone.

       Not a full Python parser. Two known limits, in the two directions a
       limit can fail:
         - FALSE-ALARM direction (loud, safe): a docstring containing a
           nested/escaped triple-quote of the same kind is not handled and
           may under- or over-strip, which reads as a spurious pin failure -
           caught immediately by whoever's pin turns red, never silent.
         - FALSE-PASS direction (the dangerous one, and why the two self-
           checks below exist): a triple-quoted VALUE assigned with nothing
           else on its line - `x = '''real code'''` written as the entire
           line - is indistinguishable from a docstring by this anchor and
           WOULD be stripped as if it were prose. None of this file's own
           domain queries are written that way today (they are call
           arguments, not their own line), so this is a known, written-down
           ceiling, not a fixed one. */
    $src = preg_replace('/#[^\n]*/', '', $src);
    return preg_replace('/^[ \t]*("""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\')[ \t]*$/m', '', $src);
}

/* The helper above is load-bearing for four pins below; nothing exercised it
   directly and a regression here (round 3's find) landed green through a
   781-check suite. Two directions of the same rule, pinned here so the next
   change to this function has to keep both true. */
check('guard: py_code_only keeps a triple-quoted VALUE (it is code)',
      str_contains(py_code_only("q = '''mutation { x }'''"), 'mutation'));
check('guard: py_code_only drops a triple-quoted DOCSTRING (it is prose)',
      !str_contains(py_code_only("def f():\n    '''calls doc.pop(x)'''\n    return 1\n"), 'doc.pop'));

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
/* The whole function, not the file: the disks QUERY legitimately selects
   serialNum, and what must never happen is the row builder carrying it out. */
$diskRow = py_code_only(py_function($collector, '_disk_row'));
check('guard: serialNum never reaches a payload',
      $diskRow !== '' && str_contains($diskRow, "'smart_status'")
      && !preg_match('/serial/i', $diskRow));

/* Source pin, not behavioural: a stripped key and a key smart.verdict() never
   read produce the identical stored envelope, so no assertion on the OUTPUT
   of parse_smart can tell "stripped" from "never there" apart. The only place
   this guard is observable at all is the source line that does the popping. */
$parseSmart = py_code_only(py_function($collector, 'parse_smart'));
check('guard: parse_smart strips serial_number and logical_unit_id',
      $parseSmart !== ''
      && str_contains($parseSmart, "doc.pop('serial_number', None)")
      && str_contains($parseSmart, "doc.pop('logical_unit_id', None)"));

/* ── "Do not re-add" — confirmed absent, not merely intended to be ────────── */
$parityQuery = '';
if (preg_match("/_domain\('parity'.*?parse_parity\)/s", $collector, $m)) $parityQuery = $m[0];
check('absent: the parity query does not ask for errors',
      $parityQuery !== '' && !str_contains($parityQuery, 'status errors'));
/* py_code_only, not the raw file - these two pins are about what we SEND, and
   scanning the prose that explains them is the exact failure this file's own
   header records ("three of these originally matched the very comments that
   describe the guard"). Both scanned raw text until 2026-08-31, when a comment
   reading "the no-mutation assertion included" turned the first one red on a
   commit that added no query at all. The helper was already here, one check
   above; it just was not being used. */
$collectorCode = py_code_only($collector);
check('absent: no mutation appears in any domain query',
      !preg_match('/\bmutation\b/i', $collectorCode));
check('absent: no introspection query',
      !str_contains($collectorCode, '__schema') && !str_contains($collectorCode, '__type'));
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
$pyCited = [
    'test_store_writes.py'    => 'test_the_connection_is_usable_from_another_thread',
    'test_managerd.py'        => 'test_shutdown_does_not_wait_on_in_flight_polls',
    'test_collector_fast.py'  => 'test_the_parity_query_does_not_ask_for_errors',
    'test_collector_slow.py'  => 'test_serial_is_dropped_from_the_payload',
];
foreach ($pyCited as $file => $needle) {
    check("cited test $file still asserts its guard",
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
