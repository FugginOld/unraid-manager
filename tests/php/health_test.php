<?PHP
/* The Overview endpoint. Reads the daemon's stored verdict; computes nothing.
     php tests/php/health_test.php  ->  "health: all pass" (exit 0) */

$base = __DIR__ . '/../../source/usr/local/emhttp/plugins/unraid-manager';
require_once $base . '/include/common.php';
require_once $base . '/api/health.php';

$fails = 0;
function check(string $name, bool $ok): void {
    global $fails;
    echo ($ok ? "PASS  " : "FAIL  ") . $name . "\n";
    if (!$ok) $fails++;
}

$db = new SQLite3(':memory:');
$db->enableExceptions(true);
$db->exec('CREATE TABLE nodes(id TEXT PRIMARY KEY, name TEXT, address TEXT, port INTEGER,
           tier INTEGER, enabled INTEGER, added_at TEXT, last_seen TEXT)');
$db->exec('CREATE TABLE node_state(node_id TEXT, domain TEXT, status TEXT, error TEXT,
           fetched_at TEXT, payload TEXT, PRIMARY KEY(node_id, domain))');
$db->exec('CREATE TABLE node_health(node_id TEXT, indicator TEXT, state TEXT, value REAL,
           basis TEXT, pending_state TEXT, pending_count INTEGER, since TEXT,
           updated_at TEXT, PRIMARY KEY(node_id, indicator))');

$db->exec("INSERT INTO nodes VALUES('a1b2','Golem','192.168.2.248',15137,0,1,
           '2026-08-27T09:00:00Z','2026-08-27T10:00:00Z')");
$db->exec("INSERT INTO nodes VALUES('b2c3','Raven','192.168.2.19',29220,0,1,
           '2026-08-27T09:00:00Z',NULL)");
$info = json_encode(['hostname' => 'Golem', 'unraid' => '7.3.2', 'api' => '4.37.3',
                     'booted_at' => '2026-08-11T04:12:07.000Z']);
$arr  = json_encode(['state' => 'STARTED', 'empty' => false,
                     'capacity' => ['free' => 100, 'used' => 900, 'total' => 1000]]);
$db->exec("INSERT INTO node_state VALUES('a1b2','info','ok',NULL,'2026-08-27T10:00:00Z','$info')");
$db->exec("INSERT INTO node_state VALUES('a1b2','array','ok',NULL,'2026-08-27T10:00:00Z','$arr')");

foreach ([['a1b2','overall','degraded','capacity'], ['a1b2','capacity','warn','90% used'],
          ['a1b2','thermal','ok','hottest disk 39 C'],
          ['b2c3','overall','unknown','info']] as $r) {
    [$node, $ind, $state, $basis] = $r;
    $db->exec("INSERT INTO node_health VALUES('$node','$ind','$state',NULL,'$basis',
               NULL,0,'2026-08-27T06:00:00Z','2026-08-27T10:00:00Z')");
}

$out = um_fleet_health($db);
check('both nodes present', count($out['nodes']) === 2);
$byId = array_column($out['nodes'], null, 'id');

check('the chip comes from the stored overall', $byId['a1b2']['state'] === 'degraded');
check('an unreachable node is unknown', $byId['b2c3']['state'] === 'unknown');
check('since is carried for "degraded for how long"',
      $byId['a1b2']['since'] === '2026-08-27T06:00:00Z');
check('indicators are exposed individually',
      ($byId['a1b2']['indicators']['capacity']['state'] ?? '') === 'warn');
check('the basis travels with the indicator',
      str_contains($byId['a1b2']['indicators']['capacity']['basis'] ?? '', '90%'));
check('the overall row is not repeated as an indicator',
      !isset($byId['a1b2']['indicators']['overall']));
check('headline fields come from the payloads', $byId['a1b2']['unraid'] === '7.3.2');
check('array state is carried', $byId['a1b2']['array_state'] === 'STARTED');
check('capacity numbers are carried', $byId['a1b2']['capacity']['total'] === 1000);

check('the fleet summary counts the chips',
      $out['fleet']['nodes'] === 2 && $out['fleet']['degraded'] === 1
      && $out['fleet']['unknown'] === 1 && $out['fleet']['ok'] === 0);

/* A node the daemon has never evaluated must not read as healthy. */
$db->exec("INSERT INTO nodes VALUES('c3d4','New','10.0.0.9',80,0,1,'2026-08-27T11:00:00Z',NULL)");
$out = um_fleet_health($db);
$byId = array_column($out['nodes'], null, 'id');
check('a never-evaluated node is unknown, not ok', $byId['c3d4']['state'] === 'unknown');

check('a null db answers empty rather than fataling',
      um_fleet_health(null) === ['fleet' => ['nodes' => 0, 'ok' => 0, 'degraded' => 0,
                                             'unknown' => 0], 'nodes' => []]);

$src = (string) file_get_contents($base . '/api/health.php');
check('session gated', str_contains($src, 'um_require_session()'));
check('no key can leave', !str_contains(json_encode($out), '"key"'));

echo $fails === 0 ? "health: all pass\n" : "health: $fails FAILED\n";
exit($fails === 0 ? 0 : 1);
