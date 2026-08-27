<?PHP
/* GET events.php?since=<id> -> the journal, newest first, capped at 200. */

require_once __DIR__ . '/../include/common.php';

const UM_EVENTS_CAP = 200;

function um_events_query(SQLite3 $db, int $since, int $limit): array {
    $limit = max(0, min($limit, UM_EVENTS_CAP));
    if ($limit === 0) return [];
    return um_query($db,
        'SELECT id, ts, node_id, kind, message FROM events '
        . 'WHERE id > :since ORDER BY id DESC LIMIT :limit',
        [':since' => max(0, $since), ':limit' => $limit]);
}

if (PHP_SAPI !== 'cli') {
    um_require_session();
    $db = um_db();
    if ($db === null) {
        um_json(['events' => [], 'error' => 'no database yet — set a database path in settings'], 200);
    }
    um_json(['events' => um_events_query($db, (int) ($_GET['since'] ?? 0), UM_EVENTS_CAP)]);
}
