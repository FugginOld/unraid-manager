<?PHP
/* GET health.php -> the Overview screen.
 *
 * Reads the daemon's stored verdict and computes nothing. The rollup rule,
 * the thresholds and the hysteresis all live in daemon/health.py; duplicating
 * any of them here would produce a second opinion that drifts. */

require_once __DIR__ . '/../include/common.php';

function um_health_rows(SQLite3 $db): array {
    $out = [];
    foreach (um_query($db, 'SELECT * FROM node_health') as $row) {
        $out[$row['node_id']][$row['indicator']] = $row;
    }
    return $out;
}

/* Seconds since one stored instant, measured on the SERVER. Both callers need
   it for the same reason: a viewer whose clock is skewed must not be able to
   grey a healthy fleet, nor hide a dead one behind a fresh-looking age. */
/* How old a node's own verdict may get before the pane stops believing it.
   ONE definition, shipped to the client as `stale_after` so the banner and the
   cards cannot drift apart - and so no browser has to hardcode a copy. */
const UM_STALE_AFTER = 180;

/* A verdict too old to assert, resolved to what the operator should see.
   Asymmetric, deliberately: a stale `ok` is a claim we can no longer make, so
   it becomes `unknown`; a stale `degraded` is still true and still the thing
   worth seeing, so it keeps its verdict and the card marks it instead.
   Greying a finding away to make a point about freshness loses the finding. */
function um_effective_state(string $state, ?int $age): string {
    if ($state === 'ok' && $age !== null && $age > UM_STALE_AFTER) return 'unknown';
    return $state;
}

function um_age_seconds($iso): ?int {
    if ($iso === null || $iso === '') return null;
    $ts = strtotime((string) $iso);
    if ($ts === false) return null;
    return max(0, time() - $ts);
}

/* How old the freshest thing in the fleet is, in seconds, against THIS box's
   clock. P1 exit finding F-1: the pane's stale banner fired on
   `Date.now() - lastGood`, and lastGood was stamped every time this endpoint
   answered - but this endpoint reads only the database, so it answers happily
   with managerd dead. Stopping the daemon for three minutes on Raven produced
   no banner at all. The banner claimed "the manager has not answered" and was
   measuring "the web server answered".

   Computed here rather than in the browser: the client would have to compare
   its own clock against a timestamp from this one, and a skewed browser clock
   would then either banner a healthy fleet or hide a dead daemon.

   null, never 0, when nothing has ever been collected - a fleet enrolled a
   minute ago has no age, and reporting one would banner it. An unparseable
   timestamp is null for the same reason: not knowing the age must not read as
   knowing it is fresh. */
function um_fleet_age(array $nodes): array {
    $newest = null;
    foreach ($nodes as $node) {
        $seen = $node['last_seen'] ?? null;
        if ($seen === null || $seen === '') continue;
        $ts = strtotime((string) $seen);
        if ($ts === false) continue;
        if ($newest === null || $ts > $newest[0]) $newest = [$ts, (string) $seen];
    }
    if ($newest === null) return ['newest' => null, 'age' => null];
    return ['newest' => $newest[1], 'age' => um_age_seconds($newest[1])];
}

function um_fleet_health(?SQLite3 $db): array {
    $empty = ['fleet' => ['nodes' => 0, 'ok' => 0, 'degraded' => 0, 'unknown' => 0],
              'nodes' => [], 'newest' => null, 'age' => null,
              'stale_after' => UM_STALE_AFTER,
              'tz' => um_local_timezone(), 'clock12' => um_display_clock_12h()];
    if ($db === null) return $empty;

    $health = um_health_rows($db);
    $states = [];
    foreach (um_query($db, 'SELECT node_id, domain, payload FROM node_state') as $row) {
        $states[$row['node_id']][$row['domain']] = $row['payload'];
    }

    $counts = ['ok' => 0, 'degraded' => 0, 'unknown' => 0];
    $nodes = [];
    foreach (um_query($db, 'SELECT * FROM nodes ORDER BY name') as $node) {
        $rows = $health[$node['id']] ?? [];
        $overall = $rows['overall'] ?? null;
        /* No verdict yet means the daemon has not evaluated this node. That is
           not health - a node enrolled a minute ago must never render green. */
        $state = $overall['state'] ?? 'unknown';
        if (!isset($counts[$state])) {
            /* A known severity outside ok|degraded|unknown (store.py's CHECK
               also admits watch/warn on this column) is still a node we CAN
               see - it must roll up to degraded, not read as unreachable.
               Only a genuinely unrecognised value fails closed to unknown. */
            $state = in_array($state, ['watch', 'warn'], true) ? 'degraded' : 'unknown';
        }

        $payload = function (string $domain) use ($states, $node) {
            $raw = $states[$node['id']][$domain] ?? null;
            $decoded = $raw === null ? null : json_decode((string) $raw, true);
            return is_array($decoded) ? $decoded : [];
        };
        $info = $payload('info');
        $array = $payload('array');
        $noti = $payload('notifications');

        $indicators = [];
        foreach ($rows as $name => $row) {
            if ($name === 'overall') continue;
            $indicators[$name] = ['state' => $row['state'], 'value' => $row['value'],
                                  'basis' => $row['basis'], 'since' => $row['since']];
        }

        $out = um_public_node($node);
        $out['since'] = $overall['since'] ?? null;
        $out['updated_at'] = $overall['updated_at'] ?? null;
        /* P1 triage P2-6. The fleet banner says the DATA is old; this says
           which node's verdict is. Measured from updated_at, not last_seen:
           last_seen is about reaching the box, and the question a grey chip
           answers is "how old is this verdict" - a node the daemon never
           managed to stamp has no age at all rather than an age of zero. */
        $out['age'] = um_age_seconds($out['updated_at']);
        /* The downgrade happens HERE, once, so the summary line below counts
           exactly what the cards show. It lived in NodeCard.vue for a day and
           the two disagreed on Raven: "0 unknown" beside a card reading
           "? Unknown". The stored verdict travels alongside as `stored_state`
           so the card can still say WHICH kind of staleness it is looking at
           without recomputing the rule. */
        $out['stored_state'] = $state;
        $effective = um_effective_state($state, $out['age']);
        if ($effective !== $state) {
            /* A downgraded verdict needs a downgraded clock. `since` says when
               the STORED state began, which is a different fact from when we
               stopped being able to assert it: a node ok for twelve minutes
               and stale for four rendered "unknown for 12m" (Raven, 12:37).
               Its new verdict dates from the last real one we had. */
            $out['since'] = $out['updated_at'];
        }
        $state = $effective;
        $out['state'] = $state;
        $counts[$state]++;
        $out['indicators'] = $indicators;
        $out['array_state'] = $array['state'] ?? null;
        $out['array_empty'] = $array['empty'] ?? null;
        $out['capacity'] = $array['capacity'] ?? null;
        $out['unraid'] = $info['unraid'] ?? null;
        $out['api'] = $info['api'] ?? null;
        $out['hostname'] = $info['hostname'] ?? null;
        $out['booted_at'] = $info['booted_at'] ?? null;
        /* The P0 Fleet tab carried an unread-notifications column and the
           Overview replaces that tab, so the counts have to travel with it or
           the replacement is a downgrade. The daemon has collected them since
           P0; only health.php was not passing them on. */
        $out['unread'] = $noti['unread'] ?? null;
        $nodes[] = $out;
    }

    /* The zone the BOX is set to, so the pane can render every instant it
       carries as a wall clock (frontend/src/time.js). One field, not a
       formatted twin of every timestamp. */
    return ['fleet' => ['nodes' => count($nodes)] + $counts, 'nodes' => $nodes,
            'stale_after' => UM_STALE_AFTER,
            'tz' => um_local_timezone(), 'clock12' => um_display_clock_12h()]
           + um_fleet_age($nodes);
}

if (PHP_SAPI !== 'cli') {
    um_require_session();
    $db = um_db();
    um_json(um_fleet_health($db) + ['db' => um_db_readable($db)]);
}
