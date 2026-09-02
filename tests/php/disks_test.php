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
/* Inserted out of name order on purpose: Raven before Golem. If disks.php ever
   drops ORDER BY name, SQLite falls back to rowid order and the check on
   $out['disks'][0] below flips. */
$db->exec("INSERT INTO nodes VALUES('b2c3','Raven','1.2.3.5',1,0,1,'x','y','SENTINEL-KEY-NOT-FOR-EXPORT')");
/* Golem is the Tier 1 node: it runs the agent, so it has a smart payload.
   Raven, Ash and Bramble stay at tier 0 and correctly have none. */
$db->exec("INSERT INTO nodes VALUES('a1b2','Golem','1.2.3.4',1,1,1,'x','y','SENTINEL-KEY-NOT-FOR-EXPORT')");
/* Ash: disks 504'd on the very first poll - no prior row, so store.py leaves
   payload and fetched_at NULL. Bramble: enrolled, never polled at all - no
   disks row in node_state whatsoever. */
$db->exec("INSERT INTO nodes VALUES('c3d4','Ash','1.2.3.6',1,0,1,'x','y','SENTINEL-KEY-NOT-FOR-EXPORT')");
$db->exec("INSERT INTO nodes VALUES('d4e5','Bramble','1.2.3.7',1,0,1,'x','y','SENTINEL-KEY-NOT-FOR-EXPORT')");

/* Captured from Golem via Raven on 2026-08-27, not invented. The physical
   enumeration's `name` is a MODEL string and its `device` is a full path; the
   previous fixture wrote 'name' => 'sdc', which the system never produces, and
   that fiction hid a join that matched 0 of 72 disks on real hardware. The
   serial key is a sentinel we add - the daemon does not store one. */
$disks = json_encode(['count' => 1, 'spare_count' => 1,
    'disks' => [['name' => 'ST10000NM0226', 'device' => '/dev/sdc',
                 'vendor' => 'Seagate', 'size' => 18000207600128, 'temp' => 36.0,
                 'smart_status' => 'OK', 'interface' => 'SAS',
                 'serial' => 'SENTINEL-SERIAL-NOT-FOR-EXPORT']],
    'spares' => [['name' => 'MG07SCA14TE', 'device' => '/dev/sdz', 'vendor' => 'WDC',
                  'size' => 8001563222016, 'temp' => 31.0, 'smart_status' => 'OK',
                  'interface' => 'SATA', 'serial' => 'SENTINEL-SERIAL-NOT-FOR-EXPORT']]]);
/* Golem's array payload has a second slot, 'sdd', with no matching physical
   disk - a drive that fell off the bus. This is parse_array's real shape:
   'slot' (not 'name'), plus temp/numErrors/status/size on every entry. */
$array = json_encode(['state' => 'STARTED',
    'disks' => [['slot' => 'disk1', 'device' => 'sdc', 'temp' => 36,
                 'numErrors' => 2, 'status' => 'DISK_OK', 'size' => 18000207600128],
                ['slot' => 'disk2', 'device' => 'sdd', 'temp' => 40,
                 'numErrors' => 0, 'status' => 'DISK_DSBL', 'size' => 4000787030016]]]);

/* parse_smart's shape after the verdict chain: the verdict, its reasons and a
   small summary per device. Keyed by the full device path, exactly as the
   agent reports it - um_device_key() reduces it to the same basename the
   other two payloads join on. */
$smart = json_encode(['count' => 2, 'disks' => [
    '/dev/sdc' => ['verdict' => 'WATCH',
                   'reasons' => ['grown defects: 4', 'last self-test 21316 h ago'],
                   'summary' => ['model' => 'ST10000NM0226', 'power_on_hours' => 55161]],
    '/dev/sdz' => ['verdict' => 'OK', 'reasons' => [],
                   'summary' => ['model' => 'MG07SCA14TE', 'power_on_hours' => 100]]]]);
$db->exec("INSERT INTO node_state VALUES('a1b2','smart','ok',NULL,'2026-09-01T02:00:00Z','"
          . SQLite3::escapeString($smart) . "')");

$db->exec("INSERT INTO node_state VALUES('a1b2','disks','ok',NULL,'2026-08-27T10:00:00Z','$disks')");
$db->exec("INSERT INTO node_state VALUES('a1b2','array','ok',NULL,'2026-08-27T09:00:00Z','$array')");
/* Raven's disks 504'd: status unknown, but the last-good payload and its
   fetched_at are retained by upsert_state. */
$db->exec("INSERT INTO node_state VALUES('b2c3','disks','unknown',
           'HTTP 504 Gateway Time-out','2026-08-27T04:00:00Z','$disks')");
/* Ash: first-ever failure - no prior row to retain, so payload and fetched_at
   are genuinely NULL in the table, not merely absent from this query. */
$db->exec("INSERT INTO node_state VALUES('c3d4','disks','unknown',
           'HTTP 504 Gateway Time-out',NULL,NULL)");
/* Bramble has no node_state row for 'disks' at all. */

$out = um_fleet_disks($db);

check('disks from every node are listed', count($out['disks']) === 3);
$golem = array_values(array_filter($out['disks'],
    fn($d) => $d['node'] === 'Golem' && $d['device'] === '/dev/sdc'))[0];
$raven = array_values(array_filter($out['disks'], fn($d) => $d['node'] === 'Raven'))[0];
$orphan = array_values(array_filter($out['disks'], fn($d) => $d['device'] === 'sdd'))[0];

check('the node name travels with the row', $raven['node'] === 'Raven');
check('vendor and size are carried', $golem['vendor'] === 'Seagate' && $golem['size'] === 18000207600128);
check('device is carried', $golem['device'] === '/dev/sdc');
/* The physical payload's `name` is a model string, so it is exposed as `model`.
   Calling it `name` invited exactly the join this fixture used to hide. */
check('the model is carried under a name that says what it is',
      $golem['model'] === 'ST10000NM0226' && !array_key_exists('name', $golem));
check('the join survives the two payloads spelling device differently',
      $golem['slot'] === 'disk1');
check('temp is carried', $golem['temp'] === 36);
check('interface is carried', $golem['interface'] === 'SAS');
check('smart status is verbatim, not a verdict', $golem['smart_status'] === 'OK');
check('the array slot is merged in by device', ($golem['slot'] ?? '') === 'disk1');
check('error counters are merged in', ($golem['errors'] ?? null) === 2);
check('array_status is carried', $golem['array_status'] === 'DISK_OK');
check('every row carries the age of its payload',
      $golem['fetched_at'] === '2026-08-27T10:00:00Z');
check('disks are ordered by node name', $out['disks'][0]['node'] === 'Golem');

check('an orphaned array slot still appears', $orphan['slot'] === 'disk2');
check('an orphan has no invented physical reading', $orphan['smart_status'] === null);
check('an orphan carries its array_status', $orphan['array_status'] === 'DISK_DSBL');
check('an orphan invents neither a temperature nor a capacity',
      $orphan['temp'] === null && $orphan['size'] === null);
/* The orphan row is built entirely from the array payload, so it must carry the
   array domain's timestamp - not the disks domain's, which is a slower lane. */
check('an orphan is aged by the array payload it came from',
      $orphan['fetched_at'] === '2026-08-27T09:00:00Z');

check('spares are listed separately', count($out['spares']) === 2);
check('a spare carries its node', $out['spares'][0]['node'] === 'Golem');
check('spare smart_status is carried', $out['spares'][0]['smart_status'] === 'OK');
check('spare size is carried', $out['spares'][0]['size'] === 8001563222016);
check('spare fetched_at is carried', $out['spares'][0]['fetched_at'] === '2026-08-27T10:00:00Z');

check('a stale node is named with its domain status', count($out['stale']) === 3);
$ravenStale = array_values(array_filter($out['stale'], fn($s) => $s['node'] === 'Raven'))[0];
$ashStale = array_values(array_filter($out['stale'], fn($s) => $s['node'] === 'Ash'))[0];
$brambleStale = array_values(array_filter($out['stale'], fn($s) => $s['node'] === 'Bramble'))[0];

check('stale status is carried', $ravenStale['status'] === 'unknown');
check('the stale entry explains why', str_contains($ravenStale['error'], '504'));
check('stale fetched_at is carried', $ravenStale['fetched_at'] === '2026-08-27T04:00:00Z');
check('stale rows carry their node_id', $ravenStale['node_id'] === 'b2c3');

check('a first-ever failure with no retained payload is still stale',
      $ashStale['status'] === 'unknown' && str_contains($ashStale['error'], '504'));
check('a first-ever failure has no fetched_at to show', $ashStale['fetched_at'] === null);

check('a never-polled node is stale, not silently absent',
      $brambleStale['status'] === 'unknown' && $brambleStale['error'] !== '');
check('a never-polled node names the reason, not a blank', $brambleStale['error'] !== '');

check('no serial anywhere in the payload',
      !preg_match('/serial/i', json_encode($out)));
check('the serial sentinel does not escape',
      !str_contains(json_encode($out), 'SENTINEL-SERIAL-NOT-FOR-EXPORT'));
check('the api key sentinel does not escape',
      !str_contains(json_encode($out), 'SENTINEL-KEY-NOT-FOR-EXPORT'));

check('a null db answers empty rather than fataling',
      um_fleet_disks(null) === ['disks' => [], 'spares' => [], 'stale' => [],
                                'tz' => um_local_timezone(),
                                'clock12' => um_display_clock_12h()]);
/* The stale labels quote a fetched_at, and an operator reads a wall clock -
   the pane renders it with this zone (frontend/src/time.js). This screen is
   reachable without Overview ever having loaded, so it carries its own. */
check('the payload names the zone the box is set to',
      is_string(um_fleet_disks($db)['tz'] ?? null) && um_fleet_disks($db)['tz'] !== '');

$src = (string) file_get_contents($base . '/api/disks.php');
check('session gated', str_contains($src, 'um_require_session()'));
check('the dispatch reports whether the database was readable',
      str_contains($src, "'db' => um_db_readable(\$db)"));

$out = um_fleet_disks($db);
$byDevice = [];
foreach ($out['disks'] as $row) $byDevice[$row['node'] . ':' . $row['device']] = $row;

$golem = $byDevice['Golem:/dev/sdc'];
check('a tier 1 disk carries its verdict', $golem['verdict'] === 'WATCH');
check('a tier 1 disk carries its reasons', $golem['reasons'][0] === 'grown defects: 4');
check('a tier 1 disk is marked tier 1', $golem['smart_tier'] === 1);
/* The smart domain has its own clock. Stamping a smart reading with the disks
   timestamp misreports its age, the same way stamping an orphan row with it
   already would. */
check('the smart reading keeps its own timestamp',
      $golem['smart_fetched_at'] === '2026-09-01T02:00:00Z'
      && $golem['fetched_at'] !== $golem['smart_fetched_at']);

$raven = $byDevice['Raven:/dev/sdc'] ?? null;
check('a tier 0 disk is marked tier 0', $raven !== null && $raven['smart_tier'] === 0);
check('a tier 0 disk has no verdict', $raven !== null && $raven['verdict'] === null);
$ravenStale = array_filter($out['stale'], fn($s) => $s['node'] === 'Raven'
                                                && $s['domain'] === 'smart');
check('a tier 0 node is not stale for a domain it never runs', $ravenStale === []);

/* The case that proves the tier is READ, not inferred. Cedar is tier 1 with a
   disks payload and no smart payload at all - enrolled and not yet polled.
   Inferred from payload presence it would read tier 0 and be labelled
   "(limited)", telling the operator it CANNOT be assessed when it merely has
   not been. */
$db->exec("INSERT INTO nodes VALUES('e5f6','Cedar','1.2.3.8',1,1,1,'x','y','SENTINEL-KEY-NOT-FOR-EXPORT')");
$db->exec("INSERT INTO node_state VALUES('e5f6','disks','ok',NULL,'2026-09-01T01:00:00Z','"
          . SQLite3::escapeString($disks) . "')");
$out = um_fleet_disks($db);
$cedar = null;
foreach ($out['disks'] as $row) if ($row['node'] === 'Cedar') { $cedar = $row; break; }
check('an unpolled tier 1 node is still tier 1', $cedar !== null && $cedar['smart_tier'] === 1);
check('an unpolled tier 1 node has no verdict yet', $cedar !== null && $cedar['verdict'] === null);
$cedarStale = array_values(array_filter($out['stale'],
    fn($s) => $s['node'] === 'Cedar' && $s['domain'] === 'smart'));
check('an unpolled tier 1 node is listed as stale for smart',
      count($cedarStale) === 1
      && $cedarStale[0]['error'] === 'no SMART poll recorded yet');
check('every stale entry names its domain',
      count(array_filter($out['stale'], fn($s) => !isset($s['domain']))) === 0);

/* store.py's v4 migration copies node_state rows rather than clearing them, so
   a 'smart' row written by the PREVIOUS build can still hold the raw smartctl
   document shape until the slow lane repolls - including a bare null for a
   device that could not be read. That old shape has no 'verdict' key at all;
   it must read as "no verdict for this disk", not warn or fatal. */
$db->exec("INSERT INTO nodes VALUES('f6a7','Ember','1.2.3.9',1,1,1,'x','y','SENTINEL-KEY-NOT-FOR-EXPORT')");
$rawSmart = json_encode(['/dev/sde' => null, '/dev/sdf' => ['temperature' => 30]]);
$db->exec("INSERT INTO node_state VALUES('f6a7','disks','ok',NULL,'2026-09-01T01:00:00Z','"
          . SQLite3::escapeString($disks) . "')");
$db->exec("INSERT INTO node_state VALUES('f6a7','smart','ok',NULL,'2026-09-01T02:00:00Z','"
          . SQLite3::escapeString($rawSmart) . "')");
$out = um_fleet_disks($db);
$ember = null;
foreach ($out['disks'] as $row) if ($row['node'] === 'Ember') { $ember = $row; break; }
check('an old raw smart payload does not crash the join', $ember !== null);
check('an old raw smart payload yields no verdict', $ember !== null && $ember['verdict'] === null);

echo $fails === 0 ? "disks: all pass\n" : "disks: $fails FAILED\n";
exit($fails === 0 ? 0 : 1);
