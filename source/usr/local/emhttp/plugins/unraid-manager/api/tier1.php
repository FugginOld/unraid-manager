<?PHP
/* Tier 1 enrollment: generate a keypair, render an installer the operator
 * pastes on the peer, test the connection, and persist tier=1 to flash only
 * when a real reply comes back.
 *
 *   POST {action:prepare, node_id}   generate (or reuse) a keypair, return
 *                                    the installer command for that node
 *   POST {action:test,    node_id}   scan+record the peer's host key, ask the
 *                                    daemon to run agent.hello, and on
 *                                    success write tier=1 to nodes.cfg
 *
 * This file contains NO transport logic: 'test' asks the daemon over the
 * existing unix socket, the way every other live action already works. The
 * daemon itself writes no state for this - flash is sync_registry()'s one
 * source of truth for `tier`, so persisting it here (the same read-modify-
 * write path nodes.php's enroll/delete already use) is what survives the
 * next reload() rather than being reverted by it.
 * The generated PRIVATE key is written to disk at 0600 and is never read
 * back into any response - only the public half ever leaves this process. */

require_once __DIR__ . '/../include/common.php';

const UM_TIER1_AGENT_SCRIPT = __DIR__ . '/../scripts/agent-exec';
const UM_TIER1_FORCED_COMMAND = '/boot/config/plugins/unraid-manager/agent-exec';

/* Refuses BEFORE anything touches a filesystem path built from the id: a
   traversal id ('../../etc/shadow') fails um_safe_id's hex-only shape and
   never reaches um_read_nodes(), let alone a path built from it. A
   syntactically fine id that names no node is refused separately, by the
   registry lookup below. */
function um_tier1_validate(array $post): array {
    $id = (string) ($post['node_id'] ?? '');
    if (!um_safe_id($id)) {
        return ['ok' => false, 'error' => 'invalid node id'];
    }
    foreach (um_read_nodes() as $node) {
        if ($node['id'] === $id) {
            return ['ok' => true, 'error' => null, 'node' => $node];
        }
    }
    return ['ok' => false, 'error' => 'no such node'];
}

/* One ed25519 pair per node, beside the GraphQL key file, so ssh access and
   API access revoke independently (spec 8). Idempotent: re-opening the
   installer after it was already generated must not rotate the key out from
   under a peer that already has the old public half in its authorized_keys.
   Returns the public key line, or null on failure. NEVER reads the private
   half back - that file is written and left alone.

   $runner is injectable the way post_fn/exec_fn/run_fn already are elsewhere
   in this codebase, defaulting to the real um_run_argv: the test suite must
   not require ssh-keygen on the box that runs it (it is absent from the
   php:8.2-cli image CI and the documented verification command both use). */
function um_tier1_keygen(string $node_id, ?callable $runner = null): ?string {
    $runner = $runner ?? 'um_run_argv';
    if (!um_safe_id($node_id)) return null;
    $priv = um_keys_dir() . '/' . $node_id . '.ssh';
    $pub = $priv . '.pub';
    if (!is_file($pub)) {
        if (!is_dir(um_keys_dir()) && !@mkdir(um_keys_dir(), 0700, true)) return null;
        @unlink($priv);
        $result = $runner(['ssh-keygen', '-t', 'ed25519', '-N', '', '-f', $priv,
                            '-C', 'unraid-manager']);
        if (!$result['ok']) return null;
        @chmod($priv, 0600);
    }
    $key = @file_get_contents($pub);
    return $key === false ? null : trim($key);
}

/* Trust-on-first-use, made a check rather than a shrug. agentclient.ssh_argv
   passes StrictHostKeyChecking=yes and UserKnownHostsFile=<keys_dir>/known_hosts,
   but nothing else ever writes that file - confirmed live against Golem:
   "ssh exited 255: ... Host key verification failed" on every attempt until
   this exists. um_tier1_installer's last line prints the peer's OWN
   fingerprint (computed on the peer); this scans independently over the
   network and returns what it found, so the operator has two strings to
   compare rather than a scan that just records silently.

   $runner is injectable exactly like um_tier1_keygen's: ssh-keyscan is as
   absent from the test environment as ssh-keygen is.

   Replaces, never appends: a re-imaged peer gets a new host key, and a stale
   entry beside a fresh one makes ssh refuse the host outright with a key
   conflict - which would look like "the agent broke" long after the actual
   cause. Nothing is written if the scan itself fails - there must be no
   known_hosts entry for a host that was never actually verified, the same
   principle as um_tier1_persist's "nothing on a failed test". */
function um_tier1_scan_host(string $address, ?callable $runner = null): ?array {
    $runner = $runner ?? 'um_run_argv';
    $scan = $runner(['ssh-keyscan', '-t', 'ed25519', '-T', '5', $address]);
    if (!$scan['ok']) return null;
    $line = null;
    foreach (explode("\n", $scan['out']) as $candidate) {
        $candidate = rtrim($candidate, "\r");
        if ($candidate !== '' && $candidate[0] !== '#') { $line = $candidate; break; }
    }
    if ($line === null) return null;

    /* The fingerprint is ssh-keygen's own computation, not ours - '-f -'
       reads the public key line from stdin rather than a file, so nothing
       here parses or hashes the key material itself. */
    $fp = $runner(['ssh-keygen', '-lf', '-'], $line);
    if (!$fp['ok'] || !preg_match('/(SHA256:\S+)/', $fp['out'], $m)) return null;

    $path = um_keys_dir() . '/known_hosts';
    $kept = [];
    foreach (preg_split('/\R/', (string) @file_get_contents($path)) as $existing) {
        if ($existing === '') continue;
        $host = explode(',', explode(' ', $existing, 2)[0])[0];
        if ($host !== $address) $kept[] = $existing;
    }
    $kept[] = $line;
    if (!is_dir(um_keys_dir()) && !@mkdir(um_keys_dir(), 0700, true)) return null;
    if (!um_atomic_write($path, implode("\n", $kept) . "\n")) return null;
    @chmod($path, 0600);

    return ['line' => $line, 'fingerprint' => $m[1]];
}

/* Pure rendering: everything it needs is passed in as a parameter, so the
   private key material um_tier1_keygen() writes to disk has no path INTO
   this function at all - there is no argument it could be threaded through,
   by construction, not merely by the string this happens not to contain.
 *
 * The authorized_keys append is the single most dangerous line here: that
 * file is not empty (the operator's own keys are already in it), so this
 * uses `>>` only, never `>`. A leading newline is written unconditionally
 * before the new line, since the existing file's last byte may not already
 * be one. */
function um_tier1_installer(string $pubkey, string $agentB64): string {
    $pubkey = trim(str_replace(["\r", "\n"], '', $pubkey));
    $line = 'command="' . UM_TIER1_FORCED_COMMAND . '",no-pty,no-port-forwarding,'
          . 'no-agent-forwarding ' . $pubkey;
    return <<<BASH
#!/bin/bash
set -euo pipefail
mkdir -p /boot/config/plugins/unraid-manager
echo '{$agentB64}' | base64 -d > /boot/config/plugins/unraid-manager/agent-exec
chmod 700 /boot/config/plugins/unraid-manager/agent-exec
mkdir -p /root/.ssh
printf '\\n%s\\n' '{$line}' >> /root/.ssh/authorized_keys
echo 'Compare this fingerprint with what the manager records for this node:'
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub 2>/dev/null || ssh-keygen -lf /etc/ssh/ssh_host_rsa_key.pub
BASH;
}

/* The only place tier=1 is written, and only ever called after a real
   agent_hello reply. Flash's nodes.cfg is sync_registry()'s single source of
   truth for this column - writing it here, through the existing
   read-modify-write-the-whole-registry path nodes.php's own enroll/delete
   already use, is what makes the change survive the next reload() instead
   of being silently folded back to 0 by it. Every other field on the node
   is round-tripped untouched: um_read_nodes()/um_write_nodes() agree on
   exactly the same five fields nodes.cfg has (name, address, port, tier,
   enabled), so nothing this function does not touch can be dropped. */
function um_tier1_persist(string $node_id): bool {
    $nodes = um_read_nodes();
    $found = false;
    foreach ($nodes as &$node) {
        if ($node['id'] === $node_id) {
            $node['tier'] = 1;
            $found = true;
            break;
        }
    }
    unset($node);
    return $found && um_write_nodes($nodes);
}

if (PHP_SAPI !== 'cli') {
    um_require_session();
    if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
        um_json(['error' => 'POST only'], 405);
    }
    um_require_csrf($_POST);

    $checked = um_tier1_validate($_POST);
    if (!$checked['ok']) um_json(['error' => $checked['error']], 400);
    $id = $checked['node']['id'];
    $action = (string) ($_POST['action'] ?? '');

    if ($action === 'prepare') {
        $pubkey = um_tier1_keygen($id);
        if ($pubkey === null) um_json(['error' => 'could not generate a key pair'], 500);
        $agent = @file_get_contents(UM_TIER1_AGENT_SCRIPT);
        if ($agent === false) um_json(['error' => 'agent script missing on the manager'], 500);
        um_json(['installer' => um_tier1_installer($pubkey, base64_encode($agent))]);
    }
    if ($action === 'test') {
        /* Order is load-bearing: scan the peer's host key first - agent_hello
           is exactly what needs known_hosts populated, since agentclient's
           StrictHostKeyChecking=yes has nothing to check against otherwise -
           then test, write flash only after a real reply, reload only after
           that write. A failure at any step must leave nodes.cfg untouched -
           there must be no state meaning "probably Tier 1" (spec section 4). */
        $scan = um_tier1_scan_host($checked['node']['address']);
        if ($scan === null) {
            um_json(['ok' => false, 'error' => "could not verify the peer's host key "
                     . '(ssh-keyscan failed)'], 502);
        }
        $result = um_ctl(['cmd' => 'agent_hello', 'node_id' => $id], 30.0);
        if (!empty($result['ok'])) {
            if (!um_tier1_persist($id)) {
                um_json(['ok' => false, 'error' => 'agent verified but could not write '
                         . um_nodes_cfg()], 500);
            }
            um_ctl(['cmd' => 'reload']);
        }
        /* The whole point of trust-on-first-use: the installer already
           printed the peer's own fingerprint, computed on the peer. This is
           the manager's independent half - the operator compares the two
           strings, which is what makes it a check rather than a shrug. */
        $result['fingerprint'] = $scan['fingerprint'];
        um_json($result);
    }
    um_json(['error' => 'unknown action'], 400);
}
