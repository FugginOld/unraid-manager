<?PHP
/* GET drift.php -> the Drift screen.
 *
 * Nodes are columns, one row per thing that can diverge. Version rows come
 * from the info payload; plugin rows are presence/absence only, because
 * installedUnraidPlugins returns names and no versions at Tier 0 - a fact the
 * response states outright rather than leaving the UI to render a blank
 * column and let the operator guess. */

require_once __DIR__ . '/../include/common.php';

/* The API exposes php/docker/nginx versions too (tier0-coverage, M1), but our
   `info` query never requests them and parse_info never parses them - so adding
   them here would ship two permanently empty rows. Widening the query means
   recapturing the committed fixtures on a live box; that is a follow-up, not
   part of P1. */
const UM_DRIFT_VERSIONS = ['unraid', 'api', 'kernel'];

function um_drift_payloads(SQLite3 $db, string $domain): array {
    $out = [];
    foreach (um_query($db, 'SELECT node_id, payload FROM node_state WHERE domain = :d',
                      [':d' => $domain]) as $row) {
        $decoded = json_decode((string) $row['payload'], true);
        if (is_array($decoded)) $out[$row['node_id']] = $decoded;
    }
    return $out;
}

function um_drift_row(string $key, string $kind, array $cells): array {
    /* A node that has not reported yet is null, and null is excluded from the
       comparison - otherwise enrolling a node would make every row look
       divergent for the first thirty seconds. */
    $known = array_values(array_filter($cells, fn($v) => $v !== null));
    return ['key' => $key, 'kind' => $kind, 'cells' => $cells,
            'divergent' => count($known) > 1 && count(array_unique($known, SORT_REGULAR)) > 1];
}

function um_drift_matrix(?SQLite3 $db): array {
    if ($db === null) {
        return ['nodes' => [], 'rows' => [], 'plugin_versions_available' => false];
    }

    $nodes = um_query($db, 'SELECT id, name FROM nodes ORDER BY name');
    $info = um_drift_payloads($db, 'info');
    $plugins = um_drift_payloads($db, 'plugins');

    $rows = [];
    foreach (UM_DRIFT_VERSIONS as $field) {
        $cells = [];
        foreach ($nodes as $node) {
            $cells[$node['id']] = $info[$node['id']][$field] ?? null;
        }
        if (array_filter($cells, fn($v) => $v !== null)) {
            $rows[] = um_drift_row($field, 'version', $cells);
        }
    }

    $everySeen = [];
    foreach ($plugins as $list) {
        foreach ($list['plugins'] ?? [] as $name) $everySeen[$name] = true;
    }
    ksort($everySeen);

    foreach (array_keys($everySeen) as $name) {
        $cells = [];
        foreach ($nodes as $node) {
            $list = $plugins[$node['id']]['plugins'] ?? null;
            /* Never polled is null; polled and absent is false. The two look
               identical in a table unless the API distinguishes them. */
            $cells[$node['id']] = $list === null ? null : in_array($name, $list, true);
        }
        $rows[] = um_drift_row('plugin:' . $name, 'plugin', $cells);
    }

    return ['nodes' => array_map(fn($n) => ['id' => $n['id'], 'name' => $n['name']], $nodes),
            'rows' => $rows,
            /* Tier 0 gives plugin names and no versions. Stated, not implied. */
            'plugin_versions_available' => false];
}

if (PHP_SAPI !== 'cli') {
    um_require_session();
    $db = um_db();
    um_json(um_drift_matrix($db) + ['db' => $db !== null]);
}
