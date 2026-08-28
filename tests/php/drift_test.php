<?PHP
/* The Drift screen endpoint.
     php tests/php/drift_test.php  ->  "drift: all pass" (exit 0) */

$base = __DIR__ . '/../../source/usr/local/emhttp/plugins/unraid-manager';
require_once $base . '/include/common.php';
require_once $base . '/api/drift.php';

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
$db->exec("INSERT INTO nodes VALUES('a1b2','Golem','1.2.3.4',1,0,1,'x','y')");
$db->exec("INSERT INTO nodes VALUES('b2c3','Raven','1.2.3.5',1,0,1,'x','y')");

$golemInfo = json_encode(['unraid' => '7.3.2', 'api' => '4.37.3', 'kernel' => '6.18.38-Unraid']);
$ravenInfo = json_encode(['unraid' => '7.3.1', 'api' => '4.37.3', 'kernel' => '6.18.38-Unraid']);
$db->exec("INSERT INTO node_state VALUES('a1b2','info','ok',NULL,'t','$golemInfo')");
$db->exec("INSERT INTO node_state VALUES('b2c3','info','ok',NULL,'t','$ravenInfo')");

$golemPlugins = json_encode(['count' => 2, 'plugins' => ['ca.backup2.plg', 'gpustat.plg']]);
$ravenPlugins = json_encode(['count' => 1, 'plugins' => ['ca.backup2.plg']]);
$db->exec("INSERT INTO node_state VALUES('a1b2','plugins','ok',NULL,'t','$golemPlugins')");
$db->exec("INSERT INTO node_state VALUES('b2c3','plugins','ok',NULL,'t','$ravenPlugins')");

$out = um_drift_matrix($db);

check('nodes are the columns, in name order',
      array_column($out['nodes'], 'name') === ['Golem', 'Raven']);

$rows = array_column($out['rows'], null, 'key');
check('the unraid version row exists', isset($rows['unraid']));
check('a divergent version row is flagged', $rows['unraid']['divergent'] === true);
check('an identical version row is not flagged', $rows['api']['divergent'] === false);
check('cells are keyed by node id',
      $rows['unraid']['cells']['a1b2'] === '7.3.2' && $rows['unraid']['cells']['b2c3'] === '7.3.1');

check('a plugin present everywhere is not divergent',
      $rows['plugin:ca.backup2.plg']['divergent'] === false);
check('a plugin on one node only is divergent',
      $rows['plugin:gpustat.plg']['divergent'] === true);
check('absence reads as absent, not blank',
      $rows['plugin:gpustat.plg']['cells']['b2c3'] === false);
check('presence reads as present',
      $rows['plugin:gpustat.plg']['cells']['a1b2'] === true);
check('plugin rows say they are plugin rows',
      $rows['plugin:gpustat.plg']['kind'] === 'plugin');

/* The Tier 0 limit is stated, not implied by an empty column. */
check('the plugin-version limit is declared', $out['plugin_versions_available'] === false);

/* A node that has not reported yet must not make every row look divergent by
   accident - its cell is null and null is excluded from the comparison. */
$db->exec("INSERT INTO nodes VALUES('c3d4','New','1.2.3.6',1,0,1,'x',NULL)");
$out = um_drift_matrix($db);
$rows = array_column($out['rows'], null, 'key');
check('an unreported node does not fake divergence', $rows['api']['divergent'] === false);
check('an unreported cell is null', $rows['api']['cells']['c3d4'] === null);

check('a null db answers empty rather than fataling',
      um_drift_matrix(null)['rows'] === []);

$src = (string) file_get_contents($base . '/api/drift.php');
check('session gated', str_contains($src, 'um_require_session()'));

echo $fails === 0 ? "drift: all pass\n" : "drift: $fails FAILED\n";
exit($fails === 0 ? 0 : 1);
