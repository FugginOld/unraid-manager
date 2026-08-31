<?PHP
/* Enrollment: keypair generation, installer rendering, node id validation.
   The dispatch block is skipped under CLI, so requiring this file runs no
   gate and touches no daemon or real /boot.
     php tests/php/tier1_test.php  ->  "tier1: all pass" (exit 0) */

$base = __DIR__ . '/../../source/usr/local/emhttp/plugins/unraid-manager';
require_once $base . '/include/common.php';
require_once $base . '/api/tier1.php';

$fails = 0;
function check(string $name, bool $ok): void {
    global $fails;
    echo ($ok ? "PASS  " : "FAIL  ") . $name . "\n";
    if (!$ok) $fails++;
}

/* ── installer rendering (pure) ────────────────────────────────────────────── */
$installer = um_tier1_installer('ssh-ed25519 AAAAC3Nz TESTKEY', base64_encode("print('hi')\n"));

check('the installer pins the forced command',
      str_contains($installer, 'command="/boot/config/plugins/unraid-manager/agent-exec"'));
check('the installer grants the key nothing else',
      str_contains($installer, 'no-pty') && str_contains($installer, 'no-port-forwarding')
      && str_contains($installer, 'no-agent-forwarding'));
check('the installer prints the peer fingerprint for comparison',
      str_contains($installer, 'ssh-keygen -lf'));
/* The agent travels IN the paste: no unauthenticated endpoint on the manager,
   no internet on the peer, and the installed agent is exactly the version the
   manager printed. */
check('the agent is embedded, not fetched',
      str_contains($installer, 'base64 -d') && !str_contains($installer, 'curl'));
/* authorized_keys is not empty on a real box - the operator's own keys are
   already in it. A naive substring search for '> /root/.ssh/authorized_keys'
   would match the second '>' of '>>' too, so this can't just check presence
   of the append form: it has to also fail if a lone '>' targeted that path. */
check('the installer appends to authorized_keys, never truncates it',
      str_contains($installer, '>> /root/.ssh/authorized_keys')
      && !preg_match('/(?<!>)>\s*\/root\/\.ssh\/authorized_keys/', $installer));

/* A call with only a public key and an agent blob in scope can prove the
   string 'PRIVATE' is absent, but it never had private key material to leak
   in the first place - it would pass just as well if the leak existed one
   parameter over. Prove it against a REAL keypair: generate one, read the
   actual private file back directly (never through the function under
   test), and confirm that exact content is not a substring of what the
   installer renders. */
$tmp = sys_get_temp_dir() . '/um_tier1_' . getmypid();
@mkdir($tmp, 0700, true);
um_set_cfg_dir($tmp);
$nodeId = str_repeat('a', 32);
$pubkey = um_tier1_keygen($nodeId);
$privatePath = um_keys_dir() . '/' . $nodeId . '.ssh';
$privateMaterial = (string) @file_get_contents($privatePath);

check('a real keypair was generated', $pubkey !== null && str_starts_with($pubkey, 'ssh-ed25519'));
check('the private key file actually holds a private key',
      str_contains($privateMaterial, 'PRIVATE KEY'));
if (DIRECTORY_SEPARATOR === '/') {
    check('the private key file is 0600',
          substr(sprintf('%o', fileperms($privatePath)), -4) === '0600');
} else {
    /* Windows PHP makes chmod a no-op; CI runs on Linux and checks the real
       bits above. Mirrors common_test.php's key-file-mode check. */
    $tier1_src = (string) file_get_contents($base . '/api/tier1.php');
    check('keygen chmods the private key 0600 (source check on Windows)',
          str_contains($tier1_src, 'chmod($priv, 0600)'));
}

$realInstaller = um_tier1_installer($pubkey, base64_encode('x'));
check('the installer never contains a private key',
      !str_contains($realInstaller, 'PRIVATE') && !str_contains($realInstaller, $privateMaterial));

/* Re-opening the installer (the operator navigating back to it) must not
   rotate the key out from under a peer that already trusts the old one. */
check('keygen is idempotent', um_tier1_keygen($nodeId) === $pubkey);

/* um_tier1_validate()'s format guard and its registry-existence check both
   land on ok:false for a traversal id, since no real node ever has a
   non-hex id - so a check on um_tier1_validate() alone cannot tell "the
   format guard fired" apart from "the id just wasn't found", and would
   still pass with the format guard deleted. The guard that actually matters
   sits in um_tier1_keygen(), which is the function that turns the id into a
   filesystem path passed to ssh-keygen. Prove THAT guard fires before any
   path is touched: with keys_dir under $tmp, '../evil' would resolve one
   directory up, to $tmp/evil.ssh - still inside our own sandbox, so this is
   safe to actually attempt rather than only asserting the return value. */
check('keygen refuses a traversal id',
      um_tier1_keygen('../evil') === null);
check('...and never wrote a file outside the keys directory for it',
      !is_file($tmp . '/evil.ssh') && !is_file($tmp . '/evil.ssh.pub'));

@unlink($privatePath);
@unlink($privatePath . '.pub');
@rmdir(um_keys_dir());
@rmdir($tmp);
um_set_cfg_dir('/boot/config/plugins/unraid-manager');

/* ── um_tier1_validate() ───────────────────────────────────────────────────── */

check('a node id that is not ours is refused',
      um_tier1_validate(['node_id' => '../../etc/shadow'])['ok'] === false);
check('an unknown node id is refused',
      um_tier1_validate(['node_id' => 'no-such-node'])['ok'] === false);
/* 'no-such-node' is refused by um_safe_id's hex-only shape before
   um_read_nodes() is ever consulted - the check above can't tell that guard
   apart from the registry lookup. Pin the lookup on its own with an id that
   IS a well-formed 32-hex id but names no node. */
check('a well-formed id naming no node is refused, not just a malformed one',
      um_tier1_validate(['node_id' => str_repeat('f', 32)])['ok'] === false);

/* ── um_tier1_persist() ────────────────────────────────────────────────────── */
/* This is the only place tier=1 is written, and it goes through nodes.cfg -
   flash, not sqlite - because sync_registry() treats flash as authoritative
   for this column. Two things matter: it flips tier and NOTHING else on the
   node it targets, and it changes NOTHING when the id does not resolve. */

$ptmp = sys_get_temp_dir() . '/um_tier1p_' . getmypid();
@mkdir($ptmp, 0700, true);
um_set_cfg_dir($ptmp);
$pid = str_repeat('c', 32);
um_write_nodes([[
    'id' => $pid, 'name' => 'Golem', 'address' => '192.168.2.248',
    'port' => 15137, 'tier' => 0, 'enabled' => 1,
]]);

check('persist succeeds for a real node', um_tier1_persist($pid) === true);
$after = um_read_nodes();
check('tier is now 1', $after[0]['tier'] === 1);
/* The whole registry is read, mutated, and rewritten - rewriting to change
   one column is exactly the shape of operation that quietly drops a field
   nobody was looking at. Prove the other four survive untouched rather than
   just checking tier flipped. */
check('every other field round-trips untouched',
      $after[0]['name'] === 'Golem' && $after[0]['address'] === '192.168.2.248'
      && $after[0]['port'] === 15137 && $after[0]['enabled'] === 1);

$beforeRaw = (string) file_get_contents(um_nodes_cfg());
check('persist refuses an id that resolves to no node', um_tier1_persist(str_repeat('d', 32)) === false);
check('...and a failed persist leaves nodes.cfg byte-for-byte unchanged',
      (string) file_get_contents(um_nodes_cfg()) === $beforeRaw);

@unlink(um_nodes_cfg());
@rmdir($ptmp);
um_set_cfg_dir('/boot/config/plugins/unraid-manager');

echo $fails === 0 ? "tier1: all pass\n" : "tier1: $fails FAILED\n";
exit($fails === 0 ? 0 : 1);
