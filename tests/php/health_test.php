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
           tier INTEGER, enabled INTEGER, added_at TEXT, last_seen TEXT, api_key TEXT)');
$db->exec('CREATE TABLE node_state(node_id TEXT, domain TEXT, status TEXT, error TEXT,
           fetched_at TEXT, payload TEXT, PRIMARY KEY(node_id, domain))');
$db->exec('CREATE TABLE node_health(node_id TEXT, indicator TEXT, state TEXT, value REAL,
           basis TEXT, pending_state TEXT, pending_count INTEGER, since TEXT,
           updated_at TEXT, PRIMARY KEY(node_id, indicator))');

$db->exec("INSERT INTO nodes VALUES('a1b2','Golem','192.168.2.248',15137,0,1,
           '2026-08-27T09:00:00Z','2026-08-27T10:00:00Z','SENTINEL-NOT-FOR-EXPORT')");
$db->exec("INSERT INTO nodes VALUES('b2c3','Raven','192.168.2.19',29220,0,1,
           '2026-08-27T09:00:00Z',NULL,'SENTINEL-NOT-FOR-EXPORT')");
$info = json_encode(['hostname' => 'Golem', 'unraid' => '7.3.2', 'api' => '4.37.3',
                     'booted_at' => '2026-08-11T04:12:07.000Z']);
$arr  = json_encode(['state' => 'STARTED', 'empty' => false,
                     'capacity' => ['free' => 100, 'used' => 900, 'total' => 1000]]);
$db->exec("INSERT INTO node_state VALUES('a1b2','info','ok',NULL,'2026-08-27T10:00:00Z','$info')");
/* parse_notifications' real shape - the P0 Fleet tab showed these counts and
   the Overview replaces that tab, so losing them would be a downgrade. */
$noti = json_encode(['unread' => ['alert' => 2, 'warning' => 1, 'info' => 5], 'total' => 8]);
$db->exec("INSERT INTO node_state VALUES('a1b2','notifications','ok',NULL,'2026-08-27T10:00:00Z','$noti')");
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
$db->exec("INSERT INTO nodes VALUES('c3d4','New','10.0.0.9',80,0,1,'2026-08-27T11:00:00Z',NULL,'SENTINEL-NOT-FOR-EXPORT')");
$out = um_fleet_health($db);
$byId = array_column($out['nodes'], null, 'id');
check('a never-evaluated node is unknown, not ok', $byId['c3d4']['state'] === 'unknown');

/* A known severity outside ok|degraded|unknown must roll up to degraded, not
   read as "we cannot see this box". A genuinely unrecognised value still
   fails closed to unknown. */
$db->exec("INSERT INTO nodes VALUES('d4e5','Warn','10.0.0.10',80,0,1,
           '2026-08-27T11:00:00Z',NULL,'SENTINEL-NOT-FOR-EXPORT')");
$db->exec("INSERT INTO nodes VALUES('e5f6','Banana','10.0.0.11',80,0,1,
           '2026-08-27T11:00:00Z',NULL,'SENTINEL-NOT-FOR-EXPORT')");
$db->exec("INSERT INTO node_health VALUES('d4e5','overall','warn',NULL,'thermal',
           NULL,0,'2026-08-27T06:00:00Z','2026-08-27T10:00:00Z')");
$db->exec("INSERT INTO nodes VALUES('f6a7','Watch','10.0.0.12',80,0,1,
           '2026-08-27T11:00:00Z',NULL,'SENTINEL-NOT-FOR-EXPORT')");
$db->exec("INSERT INTO node_health VALUES('f6a7','overall','watch',NULL,'capacity',
           NULL,0,'2026-08-27T06:00:00Z','2026-08-27T10:00:00Z')");
$db->exec("INSERT INTO node_health VALUES('e5f6','overall','banana',NULL,'?',
           NULL,0,'2026-08-27T06:00:00Z','2026-08-27T10:00:00Z')");
$out = um_fleet_health($db);
$byId = array_column($out['nodes'], null, 'id');
check('a warn overall rolls up to degraded', $byId['d4e5']['state'] === 'degraded');
check('a watch overall rolls up to degraded too', $byId['f6a7']['state'] === 'degraded');
check('an unrecognised overall value fails closed to unknown', $byId['e5f6']['state'] === 'unknown');

check('unread notification counts travel with the node',
      ($byId['a1b2']['unread']['alert'] ?? null) === 2
      && ($byId['a1b2']['unread']['warning'] ?? null) === 1);
check('a node with no notifications payload reports null, not zero',
      array_key_exists('unread', $byId['b2c3']) && $byId['b2c3']['unread'] === null);

check('a null db answers empty rather than fataling',
      um_fleet_health(null) === ['fleet' => ['nodes' => 0, 'ok' => 0, 'degraded' => 0,
                                             'unknown' => 0], 'nodes' => [],
                                 'newest' => null, 'age' => null,
                                 'stale_after' => UM_STALE_AFTER,
                                 'tz' => um_local_timezone(),
                                 'clock12' => um_display_clock_12h()]);

/* ── the age of the DATA, not of the request (P1 exit finding F-1) ──────────
   The pane's stale banner used to fire on `Date.now() - lastGood`, where
   lastGood was stamped whenever this endpoint answered - and this endpoint
   reads only the database, so it answers happily with managerd dead. Stopping
   the daemon for three minutes on Raven produced no banner at all: the banner
   said "the manager has not answered" and measured "the web server answered".
   The age is computed HERE, against the server's own clock, so a browser with
   a skewed clock cannot mis-report it either way. */
$fresh = um_fleet_health($db);
check('the payload carries the newest reading in the fleet',
      ($fresh['newest'] ?? null) === '2026-08-27T10:00:00Z');
check('the payload carries how old that reading is, in seconds',
      is_int($fresh['age'] ?? null)
      && abs($fresh['age'] - (time() - strtotime('2026-08-27T10:00:00Z'))) <= 2);

/* ── the operator's wall clock, not UTC ─────────────────────────────────────
   Unraid runs PHP with date.timezone unset, so date_default_timezone_get()
   answers UTC on a box whose own clock says EDT (Raven: php 21:36 UTC, `date`
   17:36 EDT). Both halves are pure functions so the conversion is pinned
   against a named zone rather than against whatever zone this test machine
   happens to be in. */
check('the zone is read out of the /etc/localtime symlink',
      um_zone_from_link('/usr/share/zoneinfo/America/New_York') === 'America/New_York');
check('a relative symlink resolves the same way',
      um_zone_from_link('../usr/share/zoneinfo/Europe/London') === 'Europe/London');
check('a symlink that is not into the zoneinfo tree yields nothing to guess from',
      um_zone_from_link('/etc/something-else') === null
      && um_zone_from_link(null) === null);

/* The symlink WINS over date_default_timezone_get(). On a dev machine both
   answer UTC, so without this the whole zone lookup could be deleted and every
   test still passed - it was the one mutation that survived the first round. */
check('the system symlink beats PHP\'s own (UTC) default',
      um_local_timezone('/usr/share/zoneinfo/Asia/Tokyo') === 'Asia/Tokyo');
check('with nothing usable in the link, PHP\'s default is the fallback',
      um_local_timezone('/etc/not-a-zone') === date_default_timezone_get());

/* The zone travels with the payload; the PANE formats every instant with it
   (frontend/src/time.js), so there is one mechanism rather than a formatted
   twin of every timestamp. */
check('the payload names the zone the box is set to',
      is_string($fresh['tz'] ?? null) && $fresh['tz'] !== '');
/* Unraid's own Settings -> Date & Time clock preference (dynamix.cfg
   [display] time="%I:%M %p" on Raven). An operator who chose a 12-hour clock
   should not be shown a 24-hour one by this pane. */
check('the payload reports the clock preference', is_bool($fresh['clock12'] ?? null));
check('a %I/%p time format is a 12-hour clock',
      um_clock_is_12h('%I:%M %p') && um_clock_is_12h('%l:%M %P'));
check('a %H/%R time format is not', !um_clock_is_12h('%H:%M') && !um_clock_is_12h('%R'));
check('an unset format is 24-hour, not a guess',
      !um_clock_is_12h(null) && !um_clock_is_12h(''));

/* A node that has never been seen must not be read as "seen at the epoch",
   which would report an age of half a century and banner a fresh enrolment. */
$onlyNew = new SQLite3(':memory:');
$onlyNew->enableExceptions(true);
$onlyNew->exec('CREATE TABLE nodes(id TEXT PRIMARY KEY, name TEXT, address TEXT, port INTEGER,
                tier INTEGER, enabled INTEGER, added_at TEXT, last_seen TEXT, api_key TEXT)');
$onlyNew->exec('CREATE TABLE node_state(node_id TEXT, domain TEXT, status TEXT, error TEXT,
                fetched_at TEXT, payload TEXT, PRIMARY KEY(node_id, domain))');
$onlyNew->exec('CREATE TABLE node_health(node_id TEXT, indicator TEXT, state TEXT, value REAL,
                basis TEXT, pending_state TEXT, pending_count INTEGER, since TEXT,
                updated_at TEXT, PRIMARY KEY(node_id, indicator))');
$onlyNew->exec("INSERT INTO nodes VALUES('n1','New','10.0.0.5',80,0,1,'2026-08-27T09:00:00Z',NULL,NULL)");
$never = um_fleet_health($onlyNew);
check('a fleet nothing has ever been collected from reports no age at all',
      $never['newest'] === null && $never['age'] === null);
/* An unparseable timestamp is not an age of "now" - that would read as fresh
   data and suppress the banner, which is the failure mode this whole finding
   is about. */
$bad = new SQLite3(':memory:');
$bad->enableExceptions(true);
$bad->exec('CREATE TABLE nodes(id TEXT PRIMARY KEY, name TEXT, address TEXT, port INTEGER,
            tier INTEGER, enabled INTEGER, added_at TEXT, last_seen TEXT, api_key TEXT)');
$bad->exec('CREATE TABLE node_state(node_id TEXT, domain TEXT, status TEXT, error TEXT,
            fetched_at TEXT, payload TEXT, PRIMARY KEY(node_id, domain))');
$bad->exec('CREATE TABLE node_health(node_id TEXT, indicator TEXT, state TEXT, value REAL,
            basis TEXT, pending_state TEXT, pending_count INTEGER, since TEXT,
            updated_at TEXT, PRIMARY KEY(node_id, indicator))');
$bad->exec("INSERT INTO nodes VALUES('n1','Bad','10.0.0.6',80,0,1,'2026-08-27T09:00:00Z','not a date',NULL)");
$badAge = um_fleet_health($bad);
check('an unreadable last_seen reports no age rather than a fresh one',
      $badAge['age'] === null);

$src = (string) file_get_contents($base . '/api/health.php');
check('session gated', str_contains($src, 'um_require_session()'));
/* The dispatch block cannot run under CLI, so the only way to keep the
   unreadable-database flag from being silently reverted is to pin its text. */
check('the dispatch reports whether the database was readable',
      str_contains($src, "'db' => um_db_readable(\$db)"));
check('no key can leave', !str_contains(json_encode($out), 'SENTINEL-NOT-FOR-EXPORT'));

/* P1 triage F-e: the sentinel above only ever lived in the `nodes` row, so
   redaction was proven for exactly one source. This endpoint also decodes and
   re-emits `info`, `array` and `notifications` payloads out of node_state, and
   it selects named fields from each - an allow-list, the same shape as
   um_public_node's. Nothing proved that. A payload carrying a field we do NOT
   select must not ride along, whatever a future peer decides to put in one:
   these come off another box over the network, and neither their shape nor
   their contents are ours to assume. */
$leaky = new SQLite3(':memory:');
$leaky->enableExceptions(true);
$leaky->exec('CREATE TABLE nodes(id TEXT PRIMARY KEY, name TEXT, address TEXT, port INTEGER,
              tier INTEGER, enabled INTEGER, added_at TEXT, last_seen TEXT, api_key TEXT)');
$leaky->exec('CREATE TABLE node_state(node_id TEXT, domain TEXT, status TEXT, error TEXT,
              fetched_at TEXT, payload TEXT, PRIMARY KEY(node_id, domain))');
$leaky->exec('CREATE TABLE node_health(node_id TEXT, indicator TEXT, state TEXT, value REAL,
              basis TEXT, pending_state TEXT, pending_count INTEGER, since TEXT,
              updated_at TEXT, PRIMARY KEY(node_id, indicator))');
$leaky->exec("INSERT INTO nodes VALUES('n1','Leaky','10.0.0.20',80,0,1,'x','y',NULL)");
foreach (['info', 'array', 'notifications'] as $domain) {
    $payload = json_encode(['hostname' => 'Leaky', 'unraid' => '7.3.2',
                            'state' => 'STARTED', 'capacity' => ['used' => 1, 'total' => 2],
                            'unread' => ['alert' => 0],
                            /* Not selected by health.php, and must not travel. */
                            'api_key' => 'SENTINEL-PAYLOAD-NOT-FOR-EXPORT',
                            'serial' => 'SENTINEL-PAYLOAD-NOT-FOR-EXPORT']);
    $leaky->exec("INSERT INTO node_state VALUES('n1','$domain','ok',NULL,'2026-08-27T10:00:00Z','$payload')");
}
check('a field the endpoint does not select cannot ride along in a payload',
      !str_contains(json_encode(um_fleet_health($leaky)), 'SENTINEL-PAYLOAD-NOT-FOR-EXPORT'));
check('...while the fields it DOES select still arrive',
      str_contains(json_encode(um_fleet_health($leaky)), '7.3.2'));


/* P1 triage P2-6: a card must be able to age out on its own, not only via the
   fleet-wide banner. The age is computed HERE, off the server clock, for the
   same reason um_fleet_age is - a browser whose clock is skewed must not be
   able to grey a healthy fleet or hide a dead one. */
$aged = um_fleet_health($db);
$agedById = array_column($aged['nodes'], null, 'id');
check('every node carries its own age in seconds',
      is_int($agedById['a1b2']['age'] ?? null));
check('the per-node age is measured from the server clock',
      abs($agedById['a1b2']['age'] - (time() - strtotime('2026-08-27T10:00:00Z'))) <= 2);

$now = new SQLite3(':memory:');
$now->enableExceptions(true);
$now->exec('CREATE TABLE nodes(id TEXT PRIMARY KEY, name TEXT, address TEXT, port INTEGER,
            tier INTEGER, enabled INTEGER, added_at TEXT, last_seen TEXT, api_key TEXT)');
$now->exec('CREATE TABLE node_state(node_id TEXT, domain TEXT, status TEXT, error TEXT,
            fetched_at TEXT, payload TEXT, PRIMARY KEY(node_id, domain))');
$now->exec('CREATE TABLE node_health(node_id TEXT, indicator TEXT, state TEXT, value REAL,
            basis TEXT, pending_state TEXT, pending_count INTEGER, since TEXT,
            updated_at TEXT, PRIMARY KEY(node_id, indicator))');
$stamp = gmdate('Y-m-d\TH:i:s\Z');
$now->exec("INSERT INTO nodes VALUES('f1','Fresh','10.0.0.9',80,0,1,'$stamp',NULL,NULL)");
$now->exec("INSERT INTO nodes VALUES('n0','Never','10.0.0.8',80,0,1,'$stamp',NULL,NULL)");
$now->exec("INSERT INTO node_health VALUES('f1','overall','ok',NULL,'all clear',
            NULL,0,'$stamp','$stamp')");
$freshById = array_column(um_fleet_health($now)['nodes'], null, 'id');
check('a verdict written this second is not stale',
      ($freshById['f1']['age'] ?? null) !== null && $freshById['f1']['age'] <= 2);
check('a node with no verdict at all has no age, not an age of zero',
      array_key_exists('age', $freshById['n0']) && $freshById['n0']['age'] === null);
/* Raven's last_seen is NULL while its overall row is stamped, so a version
   that measured last_seen instead would report null here and never grey. */
check('the age follows the verdict (updated_at), not last_seen',
      $agedById['b2c3']['age'] !== null && $agedById['b2c3']['updated_at'] !== null);


/* The fleet summary must be a TALLY OF THE CARDS, always. Verified wrong on
   Raven 2026-08-29: the line read "0 unknown" while a card beside it showed
   "? Unknown", because the staleness downgrade lived in NodeCard.vue and the
   count was taken here from the stored state. Two implementations of one rule
   in two languages - the exact defect this round of work was fixing, and I
   reintroduced it one layer up. The downgrade now happens once, here, and the
   card renders what it is given. */
function um_tally(array $out): array {
    $t = ['ok' => 0, 'degraded' => 0, 'unknown' => 0];
    foreach ($out['nodes'] as $n) { $t[$n['state']]++; }
    return $t;
}
$tallied = um_fleet_health($db);
check('the fleet summary is a tally of the node states it ships',
      um_tally($tallied) === ['ok' => $tallied['fleet']['ok'],
                              'degraded' => $tallied['fleet']['degraded'],
                              'unknown' => $tallied['fleet']['unknown']]);
check('the payload names the threshold it used, so no client re-invents one',
      is_int($tallied['stale_after'] ?? null) && $tallied['stale_after'] > 0);

$mix = new SQLite3(':memory:');
$mix->enableExceptions(true);
$mix->exec('CREATE TABLE nodes(id TEXT PRIMARY KEY, name TEXT, address TEXT, port INTEGER,
            tier INTEGER, enabled INTEGER, added_at TEXT, last_seen TEXT, api_key TEXT)');
$mix->exec('CREATE TABLE node_state(node_id TEXT, domain TEXT, status TEXT, error TEXT,
            fetched_at TEXT, payload TEXT, PRIMARY KEY(node_id, domain))');
$mix->exec('CREATE TABLE node_health(node_id TEXT, indicator TEXT, state TEXT, value REAL,
            basis TEXT, pending_state TEXT, pending_count INTEGER, since TEXT,
            updated_at TEXT, PRIMARY KEY(node_id, indicator))');
$fresh = gmdate('Y-m-d\TH:i:s\Z');
$old   = gmdate('Y-m-d\TH:i:s\Z', time() - 7200);
foreach ([['g1', 'FreshOk', 'ok', $fresh], ['g2', 'StaleOk', 'ok', $old],
          ['g3', 'StaleBad', 'degraded', $old]] as $r) {
    [$id, $name, $state, $stamp] = $r;
    $mix->exec("INSERT INTO nodes VALUES('$id','$name','10.0.0.1',80,0,1,'$fresh',NULL,NULL)");
    $mix->exec("INSERT INTO node_health VALUES('$id','overall','$state',NULL,'b',
                NULL,0,'$fresh','$stamp')");
}
$mixed = um_fleet_health($mix);
$mixById = array_column($mixed['nodes'], null, 'id');
check('a stale ok node reports unknown - old good news is a claim we cannot make',
      $mixById['g2']['state'] === 'unknown');
check('...while a fresh ok node is untouched', $mixById['g1']['state'] === 'ok');
/* The asymmetry, server side now: a stale finding is still true and still the
   thing worth seeing, so it keeps its verdict rather than being greyed away. */
check('a stale degraded node keeps its verdict', $mixById['g3']['state'] === 'degraded');
check('the counts follow the downgrade, not the stored row',
      $mixed['fleet']['ok'] === 1 && $mixed['fleet']['unknown'] === 1
      && $mixed['fleet']['degraded'] === 1);
check('the stored verdict still travels, so the card can say what went stale',
      $mixById['g2']['stored_state'] === 'ok');
check('a node with no age at all is never downgraded by staleness',
      um_tally($mixed)['unknown'] === 1);

echo $fails === 0 ? "health: all pass\n" : "health: $fails FAILED\n";
exit($fails === 0 ? 0 : 1);
