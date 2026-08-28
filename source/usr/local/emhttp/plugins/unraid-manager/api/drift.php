<?PHP
/* GET drift.php -> the Drift screen.
 *
 * Nodes are columns, one row per thing that can diverge. Version rows come
 * from the info payload; plugin rows are presence/absence only, because
 * installedUnraidPlugins returns names and no versions at Tier 0 - a fact the
 * response states outright rather than leaving the UI to render a blank
 * column and let the operator guess. */

require_once __DIR__ . '/../include/common.php';

/* docs/verification/tier0-coverage.md confirms `info` also exposes php and
   docker versions, so this list is not a platform limit - it is bounded by
   what daemon/collector.py's `info` query actually requests:
   `versions { core { unraid api kernel } }` (collector.py:209-210). Widening
   it is not done here: tier0-coverage.md records only the GraphQL type names
   (CoreVersions/PackageVersions), not the field path, and guessing a field
   name is exactly what made the API answer the whole query with
   INTERNAL_SERVER_ERROR in P0 - a wrong guess nulls `info`, the fast lane's
   most important payload, on every node at once. Introspecting `versions`
   against a live box first is tracked as a Task 10 follow-up. */
const UM_DRIFT_VERSIONS = ['unraid', 'api', 'kernel', 'php', 'docker'];

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
    /* array_filter is what makes this null-safe: a node that has not reported
       yet is null, and dropping nulls before the comparison is what stops
       enrolling a node from making every row look divergent for the first
       thirty seconds. No separate count guard is needed - a $known of 0 or 1
       elements can never have more than 1 unique value.
       Default string comparison, not SORT_REGULAR: SORT_REGULAR compares
       loosely, so "7.3" and "7.30" collapse into one value and a row that
       actually disagrees reports divergent:false - the worst failure mode
       on a screen whose entire purpose is showing disagreement. */
    $known = array_values(array_filter($cells, fn($v) => $v !== null));
    return ['key' => $key, 'kind' => $kind, 'cells' => $cells,
            'divergent' => count(array_unique($known)) > 1];
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
        /* Same malformed-payload guard as the cell lookup below: a `plugins`
           field that decoded but is not a list must not even reach foreach,
           which merely warns and continues on a string but would still be the
           same unguarded assumption this file is otherwise consistent about. */
        $seen = is_array($list['plugins'] ?? null) ? $list['plugins'] : [];
        foreach ($seen as $name) $everySeen[$name] = true;
    }
    ksort($everySeen);

    foreach (array_keys($everySeen) as $name) {
        $cells = [];
        foreach ($nodes as $node) {
            $list = $plugins[$node['id']]['plugins'] ?? null;
            /* Never polled is null; polled and absent is false. The two look
               identical in a table unless the API distinguishes them.
               A payload whose `plugins` is present but not a list (malformed -
               not something the current daemon writes, but every other decoded
               payload in this file is guarded the same way) falls into the
               same null bucket as never-polled: we do not actually know, and
               reporting it as absent would claim a certainty the payload does
               not support. */
            $cells[$node['id']] = is_array($list) ? in_array($name, $list, true) : null;
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
