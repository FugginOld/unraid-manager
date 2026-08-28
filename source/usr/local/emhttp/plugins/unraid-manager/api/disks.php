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

/* Both payloads identify a disk by device, but not in the same form: the
   physical enumeration reports "/dev/sda" and array.disks reports "sdj". Reduce
   both to the bare kernel name so the join has a single key. */
function um_device_key(?string $device): string {
    $device = trim((string) $device);
    if ($device === '') return '';
    $slash = strrpos($device, '/');
    return $slash === false ? $device : substr($device, $slash + 1);
}

function um_fleet_disks(?SQLite3 $db): array {
    if ($db === null) {
        return ['disks' => [], 'spares' => [], 'stale' => [],
                'tz' => um_local_timezone(), 'clock12' => um_display_clock_12h()];
    }

    $byNode = um_disk_payloads($db);
    $disks = $spares = $stale = [];

    foreach (um_query($db, 'SELECT id, name FROM nodes ORDER BY name') as $node) {
        $rows = $byNode[$node['id']] ?? [];
        $diskRow = $rows['disks'] ?? null;

        if ($diskRow === null) {
            /* The slow lane has never run for this node at all - enrolled but
               not yet polled. Fail closed: an uncollected node is visibly
               uncollected, not silently absent from every list. */
            $stale[] = ['node' => $node['name'], 'node_id' => $node['id'],
                        'status' => 'unknown', 'error' => 'no disks poll recorded yet',
                        'fetched_at' => null];
            continue;
        }

        if (($diskRow['status'] ?? '') !== 'ok') {
            $stale[] = ['node' => $node['name'], 'node_id' => $node['id'],
                        'status' => $diskRow['status'],
                        'error' => (string) $diskRow['error'],
                        'fetched_at' => $diskRow['fetched_at']];
        }

        $payload = json_decode((string) $diskRow['payload'], true);
        /* A first-ever failure leaves payload NULL (store.py has no prior row
           to retain). The stale entry above already covers it - there is
           nothing left to merge. */
        if (!is_array($payload)) continue;

        /* array.disks carries slot and numErrors; the physical enumeration does
           not. The two payloads name the same disk differently, verified live on
           Raven 2026-08-27: the physical row's `name` is a MODEL string
           ("ST10000NM0226") and its `device` is a full path ("/dev/sda"), while
           array.disks reports a bare kernel name ("sdj"). Joining on anything but
           the device basename matches nothing at all - the first cut joined the
           physical name and produced 0 hits out of 72 disks on real hardware,
           green the whole time against a fixture that had invented "name":"sdc". */
        $slots = [];
        $arrayPayload = json_decode((string) ($rows['array']['payload'] ?? ''), true);
        foreach ((is_array($arrayPayload) ? $arrayPayload['disks'] ?? [] : []) as $slot) {
            $k = um_device_key($slot['device'] ?? null);
            if ($k !== '') $slots[$k] = $slot;
        }

        $usedSlots = [];
        foreach ($payload['disks'] ?? [] as $disk) {
            $key = um_device_key($disk['device'] ?? null);
            $slot = $slots[$key] ?? [];
            if ($key !== '' && isset($slots[$key])) $usedSlots[$key] = true;
            $disks[] = [
                'node' => $node['name'], 'node_id' => $node['id'],
                'model' => $disk['name'] ?? null, 'device' => $disk['device'] ?? null,
                'vendor' => $disk['vendor'] ?? null, 'size' => $disk['size'] ?? null,
                'temp' => $disk['temp'] ?? null,
                'smart_status' => $disk['smart_status'] ?? null,
                'interface' => $disk['interface'] ?? null,
                'slot' => $slot['slot'] ?? null,
                'errors' => $slot['numErrors'] ?? null,
                'array_status' => $slot['status'] ?? null,
                'fetched_at' => $diskRow['fetched_at'],
            ];
        }
        /* array.disks can name a slot with nothing behind it in the physical
           enumeration - a drive that fell off the bus. That is exactly what
           this screen exists to show, so it gets a row too: everything the
           array reported survives, every physical-only reading is null
           rather than invented. */
        foreach ($slots as $key => $slot) {
            if (isset($usedSlots[$key])) continue;
            $disks[] = [
                'node' => $node['name'], 'node_id' => $node['id'],
                'model' => null, 'device' => $slot['device'] ?? null,
                'vendor' => null, 'size' => null, 'temp' => null,
                'smart_status' => null, 'interface' => null,
                'slot' => $slot['slot'] ?? null,
                'errors' => $slot['numErrors'] ?? null,
                'array_status' => $slot['status'] ?? null,
                /* Every field on this row came from the array payload, and array
                   is a FAST domain while disks is SLOW. Stamping it with the
                   disks timestamp would misreport the age of what is shown -
                   pessimistically most of the time, and in the wrong direction
                   when array is failing on a retained payload while disks is
                   current. */
                'fetched_at' => $rows['array']['fetched_at'] ?? null,
            ];
        }
        foreach ($payload['spares'] ?? [] as $spare) {
            $spares[] = ['node' => $node['name'], 'node_id' => $node['id'],
                         'model' => $spare['name'] ?? null,
                         'device' => $spare['device'] ?? null,
                         'vendor' => $spare['vendor'] ?? null,
                         'size' => $spare['size'] ?? null,
                         'smart_status' => $spare['smart_status'] ?? null,
                         'fetched_at' => $diskRow['fetched_at']];
        }
    }

    /* The box's zone, so the stale labels can render their fetched_at as a
       wall clock rather than a UTC instant (common.php's um_local_timezone,
       frontend/src/time.js). */
    return ['disks' => $disks, 'spares' => $spares, 'stale' => $stale,
            'tz' => um_local_timezone(), 'clock12' => um_display_clock_12h()];
}

if (PHP_SAPI !== 'cli') {
    um_require_session();
    $db = um_db();
    um_json(um_fleet_disks($db) + ['db' => $db !== null]);
}
