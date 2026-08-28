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

/* `kernel` is deliberately never reported by either node below - it exercises
   the all-null row skip (no node has ever reported this field, so it must not
   ship an empty row at all; see the "no row" check further down). */
$golemInfo = json_encode(['unraid' => '7.3.2', 'api' => '4.37.3']);
$ravenInfo = json_encode(['unraid' => '7.3.1', 'api' => '4.37.3']);
$db->exec("INSERT INTO node_state VALUES('a1b2','info','ok',NULL,'t','$golemInfo')");
$db->exec("INSERT INTO node_state VALUES('b2c3','info','ok',NULL,'t','$ravenInfo')");

/* Raven's plugin list is built so that first-seen order (a1b2 processed
   before b2c3) and sorted order genuinely differ: 'aaa.example.plg' is only
   ever seen on the second node processed, but sorts first. This is what lets
   the row-order check below catch a dropped ksort() - with this fixture's
   original two plugins alone, first-seen order and sorted order coincide and
   a missing sort would go unnoticed. */
$golemPlugins = json_encode(['count' => 2, 'plugins' => ['ca.backup2.plg', 'gpustat.plg']]);
$ravenPlugins = json_encode(['count' => 2, 'plugins' => ['aaa.example.plg', 'ca.backup2.plg']]);
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

/* Item 5: a version key no node has ever reported must produce no row at all
   - not a row full of nulls that would render as an empty line on the Drift
   screen. */
check('a version key no node reports produces no row at all', !isset($rows['kernel']));

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

/* Item 6: assert order directly off $out['rows'] - array_column(..., 'key')
   re-keys into an associative array and destroys row order, so it cannot
   catch a dropped ksort(). */
$pluginKeys = array_values(array_map(fn($r) => $r['key'],
    array_filter($out['rows'], fn($r) => $r['kind'] === 'plugin')));
check('plugin rows are kept in sorted-name order, not first-seen order',
      $pluginKeys === ['plugin:aaa.example.plg', 'plugin:ca.backup2.plg', 'plugin:gpustat.plg']);

/* The Tier 0 limit is stated, not implied by an empty column. */
check('the plugin-version limit is declared', $out['plugin_versions_available'] === false);

/* A node that has not reported yet must not make every row look divergent by
   accident - its cell is null and null is excluded from the comparison. Its
   name ('New') sorts between Golem and Raven, so this also exercises node
   order: insertion order is a1b2, b2c3, c3d4 while name order is
   a1b2, c3d4, b2c3 - the two genuinely differ. */
$db->exec("INSERT INTO nodes VALUES('c3d4','New','1.2.3.6',1,0,1,'x',NULL)");
$out = um_drift_matrix($db);
$rows = array_column($out['rows'], null, 'key');
check('an unreported node does not fake divergence', $rows['api']['divergent'] === false);
check('an unreported cell is null', $rows['api']['cells']['c3d4'] === null);

/* Item 1: nodes[].id is the join key Drift.vue uses to look up cells (Task
   15). A null or misordered id makes every lookup miss while divergent stays
   true, rendering a Drift screen full of em-dashes that still claims to have
   found differences. Name order alone (checked above) does not pin this down
   - it is the id array, in this same order, that must be right. */
check('node ids are the cells join key, in name order',
      array_column($out['nodes'], 'id') === ['a1b2', 'c3d4', 'b2c3']);

/* Item 2: a node that has never polled the slow lane is null in a plugin row,
   not absent. Conflating the two would make a freshly enrolled node look like
   it lacks every plugin for up to ten minutes, lighting up every plugin row
   as divergent for no real reason. */
check('a never-polled node is null in a plugin row, not absent',
      $rows['plugin:ca.backup2.plg']['cells']['c3d4'] === null
      && $rows['plugin:ca.backup2.plg']['divergent'] === false);

/* Item 7: a plugins payload that decodes but whose `plugins` field is not a
   list (malformed - not something the current daemon writes) must read as
   unknown, the same as never-polled, rather than crashing the endpoint or
   being misreported as "no plugins". */
$db->exec("INSERT INTO nodes VALUES('d4e5','Malformed','1.2.3.7',1,0,1,'x','y')");
$malformedPlugins = json_encode(['count' => 0, 'plugins' => 'oops']);
$db->exec("INSERT INTO node_state VALUES('d4e5','plugins','ok',NULL,'t','$malformedPlugins')");
$out = um_drift_matrix($db);
$rows = array_column($out['rows'], null, 'key');
check('a malformed plugins payload reads as unknown, not absent',
      $rows['plugin:ca.backup2.plg']['cells']['d4e5'] === null);

/* Item 3: SORT_REGULAR compares two version strings loosely, so "7.3" and
   "7.30" collapse into one value under PHP 8's numeric-string comparison and
   a genuinely divergent row reports divergent:false - invisible on a screen
   whose entire purpose is showing disagreement. Isolated fixture: a clean
   two-node database exercising only this. */
$db2 = new SQLite3(':memory:');
$db2->enableExceptions(true);
$db2->exec('CREATE TABLE nodes(id TEXT PRIMARY KEY, name TEXT, address TEXT, port INTEGER,
            tier INTEGER, enabled INTEGER, added_at TEXT, last_seen TEXT)');
$db2->exec('CREATE TABLE node_state(node_id TEXT, domain TEXT, status TEXT, error TEXT,
            fetched_at TEXT, payload TEXT, PRIMARY KEY(node_id, domain))');
$db2->exec("INSERT INTO nodes VALUES('n1','N1','h',1,0,1,'x','y')");
$db2->exec("INSERT INTO nodes VALUES('n2','N2','h',1,0,1,'x','y')");
$db2->exec("INSERT INTO node_state VALUES('n1','info','ok',NULL,'t','"
           . json_encode(['unraid' => '7.3']) . "')");
$db2->exec("INSERT INTO node_state VALUES('n2','info','ok',NULL,'t','"
           . json_encode(['unraid' => '7.30']) . "')");
$trailingZero = array_column(um_drift_matrix($db2)['rows'], null, 'key');
check('versions differing only in trailing zeros are still divergent',
      $trailingZero['unraid']['divergent'] === true);

check('a null db answers empty rather than fataling',
      um_drift_matrix(null)['rows'] === []);

$src = (string) file_get_contents($base . '/api/drift.php');
check('session gated', str_contains($src, 'um_require_session()'));

echo $fails === 0 ? "drift: all pass\n" : "drift: $fails FAILED\n";
exit($fails === 0 ? 0 : 1);
