<?PHP
/* The node API.
 *
 *   GET  nodes.php                          all nodes, headlines only
 *   GET  nodes.php?id=<uuid>                one node, full payloads
 *   POST {action:probe,  address,port,key}  run a probe, persist nothing
 *   POST {action:enroll, name,address,port,key}
 *   POST {action:delete, id}
 *   POST {action:poll,   id}
 *   POST {action:test,   id}                re-probe an enrolled node, no key sent
 *
 * GETs read the database READ-ONLY. POSTs never write it: they write flash
 * config and send a socket command, because the daemon owns every DB write.
 * No response from this file ever contains an API key. */

require_once __DIR__ . '/../include/common.php';

/* Three-valued, and fail-closed: a node is never green while something about it
   is unreadable.
   CORRECTED 2026-08-26 on Raven. The rule as first written - ANY domain unknown
   makes the node unknown - contradicted this plan's own exit criterion, which
   says one failing domain taking a whole node grey is a P0 defect. It showed up
   immediately: Golem answered nine domains with a current last_seen and
   rendered as unreachable because one slow-lane disks query did not return.
   `unknown` at node level means WE CANNOT SEE THIS NODE, so it needs every
   domain to be unreadable. Anything in between is degraded - visibly not-green,
   without claiming the box is gone.
   Worst-of rollup with hysteresis is P1 (spec section 8). */
function um_rollup(array $domains): string {
    if (!$domains) return 'unknown';
    $counts = ['ok' => 0, 'error' => 0, 'unknown' => 0];
    foreach ($domains as $d) {
        $status = $d['status'] ?? 'unknown';
        if (!isset($counts[$status])) $status = 'unknown';
        $counts[$status]++;
    }
    if ($counts['unknown'] === count($domains)) return 'unknown';
    if ($counts['unknown'] || $counts['error']) return 'degraded';
    return 'ok';
}

function um_state_rows(SQLite3 $db, ?string $id = null): array {
    $sql = 'SELECT node_id, domain, status, error, fetched_at, payload FROM node_state';
    $rows = $id === null
        ? um_query($db, $sql)
        : um_query($db, $sql . ' WHERE node_id = :id', [':id' => $id]);
    $out = [];
    foreach ($rows as $row) {
        $out[$row['node_id']][$row['domain']] = $row;
    }
    return $out;
}

function um_shape_node(array $node, array $domains, bool $with_payloads): array {
    $payload = function (string $domain) use ($domains) {
        $raw = $domains[$domain]['payload'] ?? null;
        $decoded = $raw === null ? null : json_decode((string) $raw, true);
        return is_array($decoded) ? $decoded : null;
    };
    $info  = $payload('info') ?? [];
    $array = $payload('array') ?? [];
    $noti  = $payload('notifications') ?? [];
    $shares = $payload('shares') ?? [];

    $out = um_public_node($node);
    $out['state'] = um_rollup($domains);
    $out['array_state'] = $array['state'] ?? null;
    $out['array_empty'] = $array['empty'] ?? null;
    $out['capacity'] = $array['capacity'] ?? null;
    $out['unraid'] = $info['unraid'] ?? null;
    $out['api'] = $info['api'] ?? null;
    $out['hostname'] = $info['hostname'] ?? null;
    $out['booted_at'] = $info['booted_at'] ?? null;
    $out['shares'] = $shares['count'] ?? null;
    $out['unread'] = $noti['unread'] ?? null;

    $out['domains'] = [];
    foreach ($domains as $name => $row) {
        $entry = ['status' => $row['status'], 'error' => $row['error'],
                  'fetched_at' => $row['fetched_at']];
        if ($with_payloads) $entry['payload'] = $payload($name);
        $out['domains'][$name] = $entry;
    }
    return $out;
}

function um_nodes_list(?SQLite3 $db): array {
    if ($db === null) return [];
    $states = um_state_rows($db);
    $out = [];
    foreach (um_query($db, 'SELECT * FROM nodes ORDER BY name') as $node) {
        $out[] = um_shape_node($node, $states[$node['id']] ?? [], false);
    }
    return $out;
}

function um_node_detail(?SQLite3 $db, string $id): ?array {
    if ($db === null) return null;
    $rows = um_query($db, 'SELECT * FROM nodes WHERE id = :id', [':id' => $id]);
    if (!$rows) return null;
    return um_shape_node($rows[0], (um_state_rows($db, $id))[$id] ?? [], true);
}

function um_enroll_validate(array $post, array $existing): array {
    $bad = fn(string $why) => ['ok' => false, 'error' => $why, 'values' => []];

    $address = trim((string) ($post['address'] ?? ''));
    $name    = trim((string) ($post['name'] ?? ''));
    $key     = trim((string) ($post['key'] ?? ''));

    if ($address === '') return $bad('Enter the node address.');
    /* nodes.cfg is a sectioned ini and these values are written into it
       verbatim. A newline in either would inject a section — refuse rather
       than escape, because no legitimate hostname or label has one. */
    if (preg_match('/[\r\n\[\]=]/', $address)) return $bad('The address contains a character that is not allowed.');
    if ($name !== '' && preg_match('/[\r\n\[\]=]/', $name)) return $bad('The name contains a character that is not allowed.');
    if (!is_numeric($post['port'] ?? null)) return $bad('Enter the API port as a number.');
    $port = (int) $post['port'];
    if ($port < 1 || $port > 65535) return $bad('The port must be between 1 and 65535.');
    if ($key === '') return $bad('Paste the API key for this node. Create one on that box with: unraid-api apikey --create');
    if (um_duplicate_endpoint($existing, $address, $port)) {
        return $bad('A node is already enrolled at ' . $address . ':' . $port . '.');
    }

    return ['ok' => true, 'error' => null, 'values' => [
        'id' => um_new_uuid(),
        'name' => $name !== '' ? $name : $address,
        'address' => $address, 'port' => $port, 'tier' => 0, 'enabled' => 1,
    ]];
}

function um_enroll(array $values, string $key): array {
    /* Key file first: a node in the registry with no key on disk polls as
       unknown forever, which looks like a broken box rather than a failed save. */
    if (!um_write_key($values['id'], $key)) {
        return ['ok' => false, 'error' => 'could not write the key file under ' . um_keys_dir()];
    }
    $nodes = um_read_nodes();
    $nodes[] = $values;
    if (!um_write_nodes($nodes)) {
        um_delete_key($values['id']);
        return ['ok' => false, 'error' => 'could not write ' . um_nodes_cfg()];
    }
    return ['ok' => true, 'node' => um_public_node($values), 'reload' => um_ctl(['cmd' => 'reload'])];
}

function um_node_delete(string $id): array {
    $nodes = um_read_nodes();
    $remaining = array_values(array_filter($nodes, fn($n) => $n['id'] !== $id));
    if (count($remaining) === count($nodes)) return ['ok' => false, 'error' => 'no such node'];
    if (!um_write_nodes($remaining)) return ['ok' => false, 'error' => 'could not write ' . um_nodes_cfg()];
    um_delete_key($id);
    /* The daemon deletes the node's rows on the next registry sync — it owns
       every write to that database. */
    return ['ok' => true, 'reload' => um_ctl(['cmd' => 'reload'])];
}

if (PHP_SAPI !== 'cli') {
    um_require_session();

    if ($_SERVER['REQUEST_METHOD'] === 'POST') {
        um_require_csrf($_POST);
        $action = (string) ($_POST['action'] ?? '');

        if ($action === 'probe') {
            /* The key is passed straight through to the daemon for one probe
               and is never written anywhere. */
            um_json(um_ctl(['cmd' => 'test_node',
                            'address' => trim((string) ($_POST['address'] ?? '')),
                            'port' => (int) ($_POST['port'] ?? 0),
                            'key' => (string) ($_POST['key'] ?? '')], 30.0));
        }
        if ($action === 'enroll') {
            $checked = um_enroll_validate($_POST, um_read_nodes());
            if (!$checked['ok']) um_json(['error' => $checked['error']], 400);
            $result = um_enroll($checked['values'], trim((string) $_POST['key']));
            um_json($result, $result['ok'] ? 200 : 500);
        }
        if ($action === 'delete') {
            $result = um_node_delete((string) ($_POST['id'] ?? ''));
            um_json($result, $result['ok'] ? 200 : 400);
        }
        if ($action === 'poll') {
            um_json(um_ctl(['cmd' => 'poll_now', 'node_id' => (string) ($_POST['id'] ?? '')]));
        }
        if ($action === 'test') {
            /* Re-probe an already-enrolled node. No key crosses this boundary:
               the daemon reads it from flash for the node id we name. */
            um_json(um_ctl(['cmd' => 'test_node',
                            'node_id' => (string) ($_POST['id'] ?? '')], 30.0));
        }
        um_json(['error' => 'unknown action'], 400);
    }

    $db = um_db();
    if (isset($_GET['id'])) {
        $node = um_node_detail($db, (string) $_GET['id']);
        if ($node === null) um_json(['error' => 'no such node'], 404);
        um_json($node);
    }
    um_json(['nodes' => um_nodes_list($db), 'db' => um_db_readable($db)]);
}
