<?PHP
/* The Disks screen endpoint.
     php tests/php/disks_test.php  ->  "disks: all pass" (exit 0) */

$base = __DIR__ . '/../../source/usr/local/emhttp/plugins/unraid-manager';
require_once $base . '/include/common.php';
require_once $base . '/api/disks.php';

$fails = 0;
function check(string $name, bool $ok): void {
    global $fails;
    echo ($ok ? "PASS  " : "FAIL  ") . $name . "\n";
    if (!$ok) $fails++;
}

$db = new SQLite3(':memory:');
$db->enableExceptions(true);
$db->exec('CREATE TABLE nodes(id TEXT PRIMARY KEY, name TEXT, address TEXT, port INTEGER,
           tier INTEGER, enabled INTEGER, added_at TEXT, last_seen TEXT, api_key TEXT)');
$db->exec('CREATE TABLE node_state(node_id TEXT, domain TEXT, status TEXT, error TEXT,
           fetched_at TEXT, payload TEXT, PRIMARY KEY(node_id, domain))');
$db->exec("INSERT INTO nodes VALUES('a1b2','Golem','1.2.3.4',1,0,1,'x','y','SENTINEL-KEY-NOT-FOR-EXPORT')");
$db->exec("INSERT INTO nodes VALUES('b2c3','Raven','1.2.3.5',1,0,1,'x','y','SENTINEL-KEY-NOT-FOR-EXPORT')");

$disks = json_encode(['count' => 1, 'spare_count' => 1,
    'disks' => [['name' => 'sdc', 'device' => '/dev/sdc', 'vendor' => 'Seagate',
                 'size' => 18000207600128, 'temp' => 36.0, 'smart_status' => 'OK',
                 'interface' => 'SATA', 'serial' => 'SENTINEL-SERIAL-NOT-FOR-EXPORT']],
    'spares' => [['name' => 'sdz', 'device' => '/dev/sdz', 'vendor' => 'WDC',
                  'size' => 8001563222016, 'temp' => 31.0, 'smart_status' => 'OK',
                  'interface' => 'SATA', 'serial' => 'SENTINEL-SERIAL-NOT-FOR-EXPORT']]]);
$array = json_encode(['state' => 'STARTED',
    'disks' => [['idx' => 1, 'name' => 'disk1', 'device' => 'sdc', 'temp' => 36,
                 'numErrors' => 2, 'status' => 'DISK_OK']]]);

$db->exec("INSERT INTO node_state VALUES('a1b2','disks','ok',NULL,'2026-08-27T10:00:00Z','$disks')");
$db->exec("INSERT INTO node_state VALUES('a1b2','array','ok',NULL,'2026-08-27T10:00:00Z','$array')");
/* Raven's disks 504'd: status unknown, but the last-good payload and its
   fetched_at are retained by upsert_state. */
$db->exec("INSERT INTO node_state VALUES('b2c3','disks','unknown',
           'HTTP 504 Gateway Time-out','2026-08-27T04:00:00Z','$disks')");

$out = um_fleet_disks($db);

check('disks from every node are listed', count($out['disks']) === 2);
$golem = array_values(array_filter($out['disks'], fn($d) => $d['node'] === 'Golem'))[0];
check('the node name travels with the row', $golem['node'] === 'Golem');
check('vendor and size are carried', $golem['vendor'] === 'Seagate');
check('smart status is verbatim, not a verdict', $golem['smart_status'] === 'OK');
check('the array slot is merged in by device', ($golem['slot'] ?? '') === 'disk1');
check('error counters are merged in', ($golem['errors'] ?? null) === 2);
check('every row carries the age of its payload',
      $golem['fetched_at'] === '2026-08-27T10:00:00Z');

check('spares are listed separately', count($out['spares']) === 2);
check('a spare carries its node', $out['spares'][0]['node'] !== '');

check('a stale node is named with its domain status',
      count($out['stale']) === 1 && $out['stale'][0]['node'] === 'Raven');
check('the stale entry explains why', str_contains($out['stale'][0]['error'], '504'));

check('no serial anywhere in the payload',
      !preg_match('/serial/i', json_encode($out)));
check('the serial sentinel does not escape',
      !str_contains(json_encode($out), 'SENTINEL-SERIAL-NOT-FOR-EXPORT'));
check('no key anywhere in the payload', !str_contains(json_encode($out), '"key"'));
check('the api key sentinel does not escape',
      !str_contains(json_encode($out), 'SENTINEL-KEY-NOT-FOR-EXPORT'));

check('a null db answers empty rather than fataling',
      um_fleet_disks(null) === ['disks' => [], 'spares' => [], 'stale' => []]);

$src = (string) file_get_contents($base . '/api/disks.php');
check('session gated', str_contains($src, 'um_require_session()'));

echo $fails === 0 ? "disks: all pass\n" : "disks: $fails FAILED\n";
exit($fails === 0 ? 0 : 1);
