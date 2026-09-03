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
                         . "FROM node_state WHERE domain IN ('disks','array','smart')") as $row) {
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

    foreach (um_query($db, 'SELECT id, name, tier FROM nodes ORDER BY name') as $node) {
        $rows = $byNode[$node['id']] ?? [];
        $diskRow = $rows['disks'] ?? null;

        /* The tier is READ from the registry, never inferred from whether a
           payload happens to exist. A tier 1 node that has not been polled yet
           has no smart row, and calling that tier 0 would tell the operator the
           node cannot be assessed when it merely has not been.
           This sits above the disks-payload early return below because it
           depends on nothing but $node and $rows: a node whose agent has never
           reported smart AND whose Unraid API has never reported disks (a
           first-ever failure on both) still needs its own 'smart' stale entry,
           not just the 'disks' one - hiding one behind the other is exactly the
           silent loss this file's header doctrine rules out. */
        $tier = (int) ($node['tier'] ?? 0);
        $smartRow = $rows['smart'] ?? null;
        $verdicts = [];
        $smartPayload = json_decode((string) ($smartRow['payload'] ?? ''), true);
        /* The smart payload adds a third naming convention to this join: like
           the physical enumeration, the agent reports its own full path
           ("/dev/sda"), not array.disks' bare kernel name. Running it through
           the same um_device_key() as both other payloads keeps every payload
           on one join key rather than assuming the paths always agree - the
           same discipline the array join above exists to enforce. */
        foreach ((is_array($smartPayload) ? $smartPayload['disks'] ?? [] : []) as $dev => $v) {
            $k = um_device_key($dev);
            // Legacy pre-Task-3 raw payloads store a bare null per device that
            // could not be read. The `?? null` chains below would absorb that
            // silently on their own; this guard just makes the intent explicit
            // at the point the legacy value actually arrives.
            if ($k !== '' && is_array($v)) $verdicts[$k] = $v;
        }
        if ($tier >= 1 && $smartRow === null) {
            $stale[] = ['node' => $node['name'], 'node_id' => $node['id'],
                        'domain' => 'smart', 'status' => 'unknown',
                        'error' => 'no SMART poll recorded yet', 'fetched_at' => null];
        } elseif ($smartRow !== null && ($smartRow['status'] ?? '') !== 'ok') {
            $stale[] = ['node' => $node['name'], 'node_id' => $node['id'],
                        'domain' => 'smart', 'status' => $smartRow['status'],
                        'error' => (string) $smartRow['error'],
                        'fetched_at' => $smartRow['fetched_at']];
        }

        if ($diskRow === null) {
            /* The slow lane has never run for this node at all - enrolled but
               not yet polled. Fail closed: an uncollected node is visibly
               uncollected, not silently absent from every list. */
            $stale[] = ['node' => $node['name'], 'node_id' => $node['id'],
                        'domain' => 'disks',
                        'status' => 'unknown', 'error' => 'no disks poll recorded yet',
                        'fetched_at' => null];
            continue;
        }

        if (($diskRow['status'] ?? '') !== 'ok') {
            $stale[] = ['node' => $node['name'], 'node_id' => $node['id'],
                        'domain' => 'disks',
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
                'verdict' => $verdicts[$key]['verdict'] ?? null,
                'reasons' => $verdicts[$key]['reasons'] ?? [],
                'smart_tier' => $tier,
                'smart_fetched_at' => $smartRow['fetched_at'] ?? null,
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
                'verdict' => $verdicts[$key]['verdict'] ?? null,
                'reasons' => $verdicts[$key]['reasons'] ?? [],
                'smart_tier' => $tier,
                'smart_fetched_at' => $smartRow['fetched_at'] ?? null,
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
            $spareKey = um_device_key($spare['device'] ?? null);
            /* verdict/reasons/smart_tier/smart_fetched_at are populated here
               for parity with the disk row shape above - Disks.vue's Spares
               table does not render any of them. That is deliberate, not an
               oversight: the table's own hint says every spare also appears
               in the main disks table, and that is where the operator reads
               its verdict. */
            $spares[] = ['node' => $node['name'], 'node_id' => $node['id'],
                         'model' => $spare['name'] ?? null,
                         'device' => $spare['device'] ?? null,
                         'vendor' => $spare['vendor'] ?? null,
                         'size' => $spare['size'] ?? null,
                         'smart_status' => $spare['smart_status'] ?? null,
                         'verdict' => $verdicts[$spareKey]['verdict'] ?? null,
                         'reasons' => $verdicts[$spareKey]['reasons'] ?? [],
                         'smart_tier' => $tier,
                         'smart_fetched_at' => $smartRow['fetched_at'] ?? null,
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
    um_json(um_fleet_disks($db) + ['db' => um_db_readable($db)]);
}
