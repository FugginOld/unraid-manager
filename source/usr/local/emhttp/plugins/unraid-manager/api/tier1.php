<?PHP
/* Tier 1 enrollment: generate a keypair, render an installer the operator
 * pastes on the peer, test the connection, and persist tier=1 to flash only
 * when a real reply comes back.
 *
 *   POST {action:prepare, node_id}   generate (or reuse) a keypair, return
 *                                    the installer command for that node
 *   POST {action:test,    node_id}   ask the daemon to run agent.hello, and
 *                                    on success write tier=1 to nodes.cfg
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
   half back - that file is written and left alone. */
function um_tier1_keygen(string $node_id): ?string {
    if (!um_safe_id($node_id)) return null;
    $priv = um_keys_dir() . '/' . $node_id . '.ssh';
    $pub = $priv . '.pub';
    if (!is_file($pub)) {
        if (!is_dir(um_keys_dir()) && !@mkdir(um_keys_dir(), 0700, true)) return null;
        @unlink($priv);
        $result = um_run_argv(['ssh-keygen', '-t', 'ed25519', '-N', '', '-f', $priv,
                                '-C', 'unraid-manager']);
        if (!$result['ok']) return null;
        @chmod($priv, 0600);
    }
    $key = @file_get_contents($pub);
    return $key === false ? null : trim($key);
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
        /* Order is load-bearing: test first, write flash only after a real
           reply, reload only after that write. A failed test must leave
           nodes.cfg untouched - there must be no state meaning "probably
           Tier 1" (spec section 4). */
        $result = um_ctl(['cmd' => 'agent_hello', 'node_id' => $id], 30.0);
        if (!empty($result['ok'])) {
            if (!um_tier1_persist($id)) {
                um_json(['ok' => false, 'error' => 'agent verified but could not write '
                         . um_nodes_cfg()], 500);
            }
            um_ctl(['cmd' => 'reload']);
        }
        um_json($result);
    }
    um_json(['error' => 'unknown action'], 400);
}
