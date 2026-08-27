<?PHP
/* nodes.php logic: the rollup, enrollment validation, and the shape of what
   goes out over the wire. No session, no daemon, no live box.
     php tests/php/nodes_test.php  ->  "nodes: all pass" (exit 0) */

$base = __DIR__ . '/../../source/usr/local/emhttp/plugins/unraid-manager';
require_once $base . '/include/common.php';
require_once $base . '/api/nodes.php';

$fails = 0;
function check(string $name, bool $ok): void {
    global $fails;
    echo ($ok ? "PASS  " : "FAIL  ") . $name . "\n";
    if (!$ok) $fails++;
}

/* ── the three-value rollup ───────────────────────────────────────────────── */
check('all ok is ok', um_rollup(['info' => ['status' => 'ok'], 'array' => ['status' => 'ok']]) === 'ok');
check('one error is degraded', um_rollup(['info' => ['status' => 'ok'], 'array' => ['status' => 'error']]) === 'degraded');
check('one unknown outranks an error', um_rollup(['info' => ['status' => 'unknown'], 'array' => ['status' => 'error']]) === 'unknown');
check('all unknown is unknown', um_rollup(['info' => ['status' => 'unknown']]) === 'unknown');
/* Constraint 5: nothing known yet is not health. A node enrolled ten seconds
   ago and never polled must not render green. */
check('no domains at all is unknown', um_rollup([]) === 'unknown');
check('an unrecognised status is unknown', um_rollup(['info' => ['status' => 'weird']]) === 'unknown');
check('rollup is never green on a missing status key', um_rollup(['info' => []]) === 'unknown');

/* ── enrollment validation ────────────────────────────────────────────────── */
$existing = [['id' => 'a1b2', 'name' => 'Golem', 'address' => '192.168.2.248', 'port' => 15137]];
$good = ['name' => 'Raven', 'address' => '192.168.2.19', 'port' => '29220',
         'key' => 'a-read-scoped-key-supplied-by-the-operator'];

$r = um_enroll_validate($good, $existing);
check('a good enrollment is accepted', $r['ok'] === true);
check('port is coerced to int', $r['values']['port'] === 29220);
check('a generated id is present and filename-safe', (bool) preg_match('/^[0-9a-f]{32}$/', $r['values']['id']));

check('a duplicate address:port is refused',
      um_enroll_validate(array_merge($good, ['address' => '192.168.2.248', 'port' => '15137']), $existing)['ok'] === false);
check('the same address on a different port is allowed',
      um_enroll_validate(array_merge($good, ['address' => '192.168.2.248', 'port' => '15138']), $existing)['ok'] === true);
check('a missing address is refused', um_enroll_validate(array_merge($good, ['address' => '']), $existing)['ok'] === false);
check('a missing key is refused', um_enroll_validate(array_merge($good, ['key' => '']), $existing)['ok'] === false);
check('a whitespace-only key is refused', um_enroll_validate(array_merge($good, ['key' => '   ']), $existing)['ok'] === false);
check('port 0 is refused', um_enroll_validate(array_merge($good, ['port' => '0']), $existing)['ok'] === false);
check('port 70000 is refused', um_enroll_validate(array_merge($good, ['port' => '70000']), $existing)['ok'] === false);
check('a non-numeric port is refused', um_enroll_validate(array_merge($good, ['port' => 'https']), $existing)['ok'] === false);
check('an address with a newline is refused',
      um_enroll_validate(array_merge($good, ['address' => "1.2.3.4\n[evil]"]), $existing)['ok'] === false);
check('a name with a newline is refused',
      um_enroll_validate(array_merge($good, ['name' => "X\nport=1"]), $existing)['ok'] === false);
$r = um_enroll_validate(array_merge($good, ['name' => '']), $existing);
check('an empty name falls back to the address', $r['values']['name'] === '192.168.2.19');

/* The validated values are what gets rendered into nodes.cfg — the key must
   not be among them. It goes to its own 0600 file, never into the registry. */
check('the key is not in the registry values', !array_key_exists('key', $r['values']));

/* ── list and detail shapes ───────────────────────────────────────────────── */
$db = new SQLite3(':memory:');
$db->enableExceptions(true);
$db->exec('CREATE TABLE nodes(id TEXT PRIMARY KEY, name TEXT, address TEXT, port INTEGER,
           tier INTEGER, enabled INTEGER, added_at TEXT, last_seen TEXT)');
$db->exec('CREATE TABLE node_state(node_id TEXT, domain TEXT, status TEXT, error TEXT,
           fetched_at TEXT, payload TEXT, PRIMARY KEY(node_id, domain))');
$db->exec("INSERT INTO nodes VALUES('a1b2','Golem','192.168.2.248',15137,0,1,'2026-08-25T09:00:00Z','2026-08-25T10:00:00Z')");
$db->exec("INSERT INTO nodes VALUES('b2c3','Raven','192.168.2.19',29220,0,1,'2026-08-25T09:00:00Z',NULL)");
$info = json_encode(['hostname' => 'Golem', 'unraid' => '7.3.2', 'api' => '4.37.3', 'booted_at' => '2026-08-11T04:12:07.000Z']);
$arr  = json_encode(['state' => 'STARTED', 'empty' => false, 'capacity' => ['free' => 100, 'used' => 900, 'total' => 1000]]);
$noti = json_encode(['unread' => ['info' => 3, 'warning' => 1, 'alert' => 0, 'total' => 4]]);
$db->exec("INSERT INTO node_state VALUES('a1b2','info','ok',NULL,'2026-08-25T10:00:00Z','$info')");
$db->exec("INSERT INTO node_state VALUES('a1b2','array','ok',NULL,'2026-08-25T10:00:00Z','$arr')");
$db->exec("INSERT INTO node_state VALUES('a1b2','notifications','ok',NULL,'2026-08-25T10:00:00Z','$noti')");
$db->exec("INSERT INTO node_state VALUES('b2c3','info','unknown','connection refused',NULL,NULL)");

$list = um_nodes_list($db);
check('both nodes listed', count($list) === 2);
$byId = array_column($list, null, 'id');
check('a polled node rolls up ok', $byId['a1b2']['state'] === 'ok');
check('an unreachable node rolls up unknown', $byId['b2c3']['state'] === 'unknown');
check('headline array state present', $byId['a1b2']['array_state'] === 'STARTED');
check('headline capacity present', $byId['a1b2']['capacity']['total'] === 1000);
check('headline versions present', $byId['a1b2']['unraid'] === '7.3.2');
check('headline unread counts present', $byId['a1b2']['unread']['total'] === 4);
check('last_seen present', $byId['a1b2']['last_seen'] === '2026-08-25T10:00:00Z');
check('an unpolled node has a null last_seen', $byId['b2c3']['last_seen'] === null);
check('the list carries no payload bodies', !isset($byId['a1b2']['domains']['info']['payload']));
check('per-domain status is in the list', $byId['a1b2']['domains']['info']['status'] === 'ok');
check('the domain error is carried', $byId['b2c3']['domains']['info']['error'] === 'connection refused');
check('no key anywhere in the list', !str_contains(json_encode($list), '"key"'));
check('has_key is reported', array_key_exists('has_key', $byId['a1b2']));

$detail = um_node_detail($db, 'a1b2');
check('detail carries full payloads', ($detail['domains']['array']['payload']['state'] ?? '') === 'STARTED');
check('detail for an unknown id is null', um_node_detail($db, 'nosuch') === null);
check('no key in the detail payload', !str_contains(json_encode($detail), '"key"'));

/* Constraint 3, all the way to the wire. */
$db->exec("UPDATE node_state SET payload='" . json_encode(['state' => 'STARTED', 'empty' => true,
          'capacity' => ['free' => 0, 'used' => 0, 'total' => 0]]) . "' WHERE node_id='a1b2' AND domain='array'");
$list = array_column(um_nodes_list($db), null, 'id');
check('an empty array still rolls up ok', $list['a1b2']['state'] === 'ok');
check('an empty array is flagged for the UI', $list['a1b2']['array_empty'] === true);

/* A null database (settings not filled in yet) must answer, not fatal. */
check('a null db lists nothing without fataling', um_nodes_list(null) === []);
check('a null db detail is null', um_node_detail(null, 'a1b2') === null);

echo $fails === 0 ? "nodes: all pass\n" : "nodes: $fails FAILED\n";
exit($fails === 0 ? 0 : 1);
