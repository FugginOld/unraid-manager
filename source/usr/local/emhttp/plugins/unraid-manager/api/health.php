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

function um_fleet_health(?SQLite3 $db): array {
    $empty = ['fleet' => ['nodes' => 0, 'ok' => 0, 'degraded' => 0, 'unknown' => 0],
              'nodes' => []];
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
        if (!isset($counts[$state])) $state = 'unknown';
        $counts[$state]++;

        $payload = function (string $domain) use ($states, $node) {
            $raw = $states[$node['id']][$domain] ?? null;
            $decoded = $raw === null ? null : json_decode((string) $raw, true);
            return is_array($decoded) ? $decoded : [];
        };
        $info = $payload('info');
        $array = $payload('array');

        $indicators = [];
        foreach ($rows as $name => $row) {
            if ($name === 'overall') continue;
            $indicators[$name] = ['state' => $row['state'], 'value' => $row['value'],
                                  'basis' => $row['basis'], 'since' => $row['since']];
        }

        $out = um_public_node($node);
        $out['state'] = $state;
        $out['since'] = $overall['since'] ?? null;
        $out['updated_at'] = $overall['updated_at'] ?? null;
        $out['indicators'] = $indicators;
        $out['array_state'] = $array['state'] ?? null;
        $out['array_empty'] = $array['empty'] ?? null;
        $out['capacity'] = $array['capacity'] ?? null;
        $out['unraid'] = $info['unraid'] ?? null;
        $out['api'] = $info['api'] ?? null;
        $out['hostname'] = $info['hostname'] ?? null;
        $out['booted_at'] = $info['booted_at'] ?? null;
        $nodes[] = $out;
    }

    return ['fleet' => ['nodes' => count($nodes)] + $counts, 'nodes' => $nodes];
}

if (PHP_SAPI !== 'cli') {
    um_require_session();
    um_json(um_fleet_health(um_db()));
}
