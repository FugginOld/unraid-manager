<?PHP
/* GET disks.php -> the Disks screen.
 *
 * Merges the slow-lane `disks` payload with `array.disks`, which is the only
 * place slot and error counters exist. The two are joined on device name.
 *
 * Query.disks 504s at nginx's 60s timeout on a loaded peer, so a payload here
 * may be the last good one from hours ago. Every row therefore carries the
 * fetched_at it came from, and a node whose domain is not currently ok is
 * listed in `stale` with the reason - hiding it would be worse than showing
 * old numbers, and showing old numbers unlabelled would be worse still. */

require_once __DIR__ . '/../include/common.php';

function um_disk_payloads(SQLite3 $db): array {
    $out = [];
    foreach (um_query($db, "SELECT node_id, domain, status, error, fetched_at, payload "
                         . "FROM node_state WHERE domain IN ('disks','array')") as $row) {
        $out[$row['node_id']][$row['domain']] = $row;
    }
    return $out;
}

function um_fleet_disks(?SQLite3 $db): array {
    if ($db === null) return ['disks' => [], 'spares' => [], 'stale' => []];

    $byNode = um_disk_payloads($db);
    $disks = $spares = $stale = [];

    foreach (um_query($db, 'SELECT id, name FROM nodes ORDER BY name') as $node) {
        $rows = $byNode[$node['id']] ?? [];
        $diskRow = $rows['disks'] ?? null;
        if ($diskRow === null) continue;

        $payload = json_decode((string) $diskRow['payload'], true);
        if (!is_array($payload)) continue;

        if (($diskRow['status'] ?? '') !== 'ok') {
            $stale[] = ['node' => $node['name'], 'status' => $diskRow['status'],
                        'error' => (string) $diskRow['error'],
                        'fetched_at' => $diskRow['fetched_at']];
        }

        /* array.disks carries slot and numErrors; the physical enumeration does
           not. Join on device name, which both report. */
        $slots = [];
        $arrayPayload = json_decode((string) ($rows['array']['payload'] ?? ''), true);
        foreach ((is_array($arrayPayload) ? $arrayPayload['disks'] ?? [] : []) as $slot) {
            if (!empty($slot['device'])) $slots[$slot['device']] = $slot;
        }

        foreach ($payload['disks'] ?? [] as $disk) {
            $slot = $slots[$disk['name'] ?? ''] ?? [];
            $disks[] = [
                'node' => $node['name'], 'node_id' => $node['id'],
                'name' => $disk['name'] ?? null, 'device' => $disk['device'] ?? null,
                'vendor' => $disk['vendor'] ?? null, 'size' => $disk['size'] ?? null,
                'temp' => $disk['temp'] ?? null,
                'smart_status' => $disk['smart_status'] ?? null,
                'interface' => $disk['interface'] ?? null,
                'slot' => $slot['name'] ?? null,
                'errors' => $slot['numErrors'] ?? null,
                'array_status' => $slot['status'] ?? null,
                'fetched_at' => $diskRow['fetched_at'],
            ];
        }
        foreach ($payload['spares'] ?? [] as $spare) {
            $spares[] = ['node' => $node['name'], 'node_id' => $node['id'],
                         'name' => $spare['name'] ?? null,
                         'device' => $spare['device'] ?? null,
                         'vendor' => $spare['vendor'] ?? null,
                         'size' => $spare['size'] ?? null,
                         'smart_status' => $spare['smart_status'] ?? null,
                         'fetched_at' => $diskRow['fetched_at']];
        }
    }

    return ['disks' => $disks, 'spares' => $spares, 'stale' => $stale];
}

if (PHP_SAPI !== 'cli') {
    um_require_session();
    $db = um_db();
    um_json(um_fleet_disks($db) + ['db' => $db !== null]);
}
