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
      str_contains($installer, 'command="/usr/bin/python3 /boot/config/plugins/unraid-manager/agent-exec"'));
/* /boot is vfat mounted fmask=0177 (verified live on Golem, 2026-08-31): every
   file on flash is forced to mode 0600 and the execute bit is UNSETTABLE, so
   execve() on the script itself can never work - confirmed live, exit 126.
   The forced command must therefore name an interpreter by absolute path
   (never a bare 'python3', which depends on a login shell's PATH a forced
   command does not get) with the script as an ARGUMENT to it, not the
   executable. A substring check on 'python3' alone would pass just as well
   for 'command="/boot/.../agent-exec python3"', which execve()s the flash
   script first and never runs - so this parses out the exact command="..."
   value and checks its shape, not just what tokens appear somewhere in it. */
preg_match('/command="([^"]+)"/', $installer, $cmdMatch);
check('the forced command names the interpreter by absolute path, script as its argument',
      ($cmdMatch[1] ?? '') === '/usr/bin/python3 /boot/config/plugins/unraid-manager/agent-exec');
/* chmod on a flash file is a silent no-op (fmask=0177 already fixed the mode
   at 0600) - leaving one in would look like it does something and change
   nothing, which is worse than no line at all. */
check('the installer never chmods the agent script',
      !preg_match('/\bchmod\b[^\n]*agent-exec/', $installer));
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

/* ── keygen, against a FAKE runner ────────────────────────────────────────────
   $runner is injectable on um_tier1_keygen precisely so this suite never
   needs the real ssh-keygen binary - it is absent from the php:8.2-cli image
   both CI and the documented verification command run in. Same idiom as
   post_fn/exec_fn/run_fn/PROC_MOUNTS elsewhere in this codebase.

   The fake still has to write REALISTIC private key material to the file a
   real ssh-keygen would have written to, or "the installer never contains a
   private key" below would pass for having nothing to leak rather than for
   actually being clean. */
function um_tier1_fake_keygen(array $argv, ?string $input = null): array {
    $i = array_search('-f', $argv, true);
    $priv = $argv[$i + 1];
    file_put_contents($priv, "-----BEGIN OPENSSH PRIVATE KEY-----\n"
        . "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZWQy\n"
        . "NTUxOQAAACBmYWtla2V5Zm9ydGVzdGluZ29ubHlub3RyZWFsMQAAAKB0ZXN0AA==\n"
        . "-----END OPENSSH PRIVATE KEY-----\n");
    file_put_contents($priv . '.pub',
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGZha2VwdWJrZXlmb3J0ZXN0aW5nb25seW5vdHJlYWwx unraid-manager\n");
    return ['ok' => true, 'code' => 0, 'out' => '', 'err' => ''];
}

$tmp = sys_get_temp_dir() . '/um_tier1_' . getmypid();
@mkdir($tmp, 0700, true);
um_set_cfg_dir($tmp);
$nodeId = str_repeat('a', 32);
$pubkey = um_tier1_keygen($nodeId, 'um_tier1_fake_keygen');
$privatePath = um_keys_dir() . '/' . $nodeId . '.ssh';
$privateMaterial = (string) @file_get_contents($privatePath);

check('a keypair was generated', $pubkey !== null && str_starts_with($pubkey, 'ssh-ed25519'));
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

/* A call with only a public key and an agent blob in scope can prove the
   string 'PRIVATE' is absent, but it never had private key material to leak
   in the first place - it would pass just as well if the leak existed one
   parameter over. Prove it against the private file just generated: read it
   back directly (never through the function under test) and confirm that
   exact content is not a substring of what the installer renders. */
$realInstaller = um_tier1_installer($pubkey, base64_encode('x'));
check('the installer never contains a private key',
      !str_contains($realInstaller, 'PRIVATE') && !str_contains($realInstaller, $privateMaterial));

/* Re-opening the installer (the operator navigating back to it) must not
   rotate the key out from under a peer that already trusts the old one. */
check('keygen is idempotent', um_tier1_keygen($nodeId, 'um_tier1_fake_keygen') === $pubkey);

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
      um_tier1_keygen('../evil', 'um_tier1_fake_keygen') === null);
check('...and never wrote a file outside the keys directory for it',
      !is_file($tmp . '/evil.ssh') && !is_file($tmp . '/evil.ssh.pub'));

@unlink($privatePath);
@unlink($privatePath . '.pub');
@rmdir(um_keys_dir());
@rmdir($tmp);
um_set_cfg_dir('/boot/config/plugins/unraid-manager');

/* ── keygen, against the REAL ssh-keygen - exactly one test ───────────────────
   Everything above proves um_tier1_keygen's own logic (guards, permissions,
   idempotence, no private-key leak) without the real binary. This is the
   seam's other half: that the default $runner really does invoke a real
   ssh-keygen and get back a real, usable key pair. Skips LOUDLY rather than
   silently when the binary is absent, matching run.sh's own "node not on
   PATH" idiom for the JS suites - a quiet skip here is exactly how a broken
   default runner would go unnoticed. proc_open's array form (no shell)
   reports a missing binary as exit code 127, the same convention a shell
   uses for "command not found"; confirmed against php:8.2-cli, which has no
   ssh-keygen, and against a real ssh-keygen locally (neither ever returns
   127 for anything else). */
$hasSshKeygen = um_run_argv(['ssh-keygen'])['code'] !== 127;
if ($hasSshKeygen) {
    $rtmp = sys_get_temp_dir() . '/um_tier1_real_' . getmypid();
    @mkdir($rtmp, 0700, true);
    um_set_cfg_dir($rtmp);
    $rid = str_repeat('b', 32);
    $rpub = um_tier1_keygen($rid);   // no $runner given - exercises the real default
    check('the real ssh-keygen produces a usable ed25519 public key',
          $rpub !== null && str_starts_with($rpub, 'ssh-ed25519'));
    @unlink(um_keys_dir() . '/' . $rid . '.ssh');
    @unlink(um_keys_dir() . '/' . $rid . '.ssh.pub');
    @rmdir(um_keys_dir());
    @rmdir($rtmp);
    um_set_cfg_dir('/boot/config/plugins/unraid-manager');
} else {
    echo "!!! ssh-keygen not on PATH - skipping the real-keygen test "
       . "(the fake-runner tests above still cover um_tier1_keygen's own logic)\n";
}

/* ── um_tier1_scan_host(), against a FAKE runner ──────────────────────────────
   Same reasoning as the keygen fake: ssh-keyscan is as absent from the test
   environment as ssh-keygen. $um_scan_version lets the fake return a
   DIFFERENT key on a second call for the same address, which is what makes
   "a second scan replaces rather than appends" provable rather than
   assumed - a fake that always returned the same key could not tell a
   replace from a no-op append of an identical line. */
$um_scan_version = 1;
function um_tier1_fake_scan(array $argv, ?string $input = null): array {
    global $um_scan_version;
    if ($argv[0] === 'ssh-keyscan') {
        $address = end($argv);
        return ['ok' => true, 'code' => 0,
                'out' => "# comment line ssh-keyscan would also print\n"
                       . "$address ssh-ed25519 AAAAFAKEHOSTKEYv{$um_scan_version}\n",
                'err' => ''];
    }
    // ssh-keygen -lf - : $input is the known_hosts line just scanned.
    return ['ok' => true, 'code' => 0,
            'out' => "256 SHA256:fakefingerprintV{$um_scan_version} no comment (ED25519)\n",
            'err' => ''];
}
function um_tier1_fake_scan_fail(array $argv, ?string $input = null): array {
    return ['ok' => false, 'code' => 1, 'out' => '', 'err' => 'ssh-keyscan: connection timed out'];
}

$stmp = sys_get_temp_dir() . '/um_tier1s_' . getmypid();
@mkdir($stmp, 0700, true);
um_set_cfg_dir($stmp);
$addr = '192.168.2.248';
$khPath = um_keys_dir() . '/known_hosts';

$scan1 = um_tier1_scan_host($addr, 'um_tier1_fake_scan');
check('the fingerprint is returned, not just recorded',
      $scan1 !== null && $scan1['fingerprint'] === 'SHA256:fakefingerprintV1');
$lines = array_values(array_filter(explode("\n", (string) file_get_contents($khPath)), fn($l) => $l !== ''));
check('a scan writes exactly one line for the address',
      count(array_filter($lines, fn($l) => str_starts_with($l, $addr . ' '))) === 1);
if (DIRECTORY_SEPARATOR === '/') {
    check('known_hosts is written 0600',
          substr(sprintf('%o', fileperms($khPath)), -4) === '0600');
} else {
    $tier1_src = $tier1_src ?? (string) file_get_contents($base . '/api/tier1.php');
    check('scan_host chmods known_hosts 0600 (source check on Windows)',
          str_contains($tier1_src, "chmod(\$path, 0600)"));
}

/* An unrelated host's own entry must survive a scan of a DIFFERENT address -
   proves the strip is scoped to the address being scanned, not the whole
   file. */
file_put_contents($khPath, (string) file_get_contents($khPath) . "10.0.0.9 ssh-ed25519 AAAAUNRELATEDHOST\n");

$um_scan_version = 2;
$scan2 = um_tier1_scan_host($addr, 'um_tier1_fake_scan');
$lines2 = array_values(array_filter(explode("\n", (string) file_get_contents($khPath)), fn($l) => $l !== ''));
$matching = array_values(array_filter($lines2, fn($l) => str_starts_with($l, $addr . ' ')));
check('a second scan of the same address replaces rather than appends',
      count($matching) === 1 && str_contains($matching[0], 'v2'));
check('...and the fingerprint reflects the new key, not the old one',
      $scan2['fingerprint'] === 'SHA256:fakefingerprintV2' && $scan2['fingerprint'] !== $scan1['fingerprint']);
check("an unrelated host's existing entry is preserved by the strip",
      (bool) array_filter($lines2, fn($l) => str_starts_with($l, '10.0.0.9 ')));

$beforeKH = (string) @file_get_contents($khPath);
check('a failed scan returns null', um_tier1_scan_host($addr, 'um_tier1_fake_scan_fail') === null);
check('...and leaves known_hosts untouched',
      (string) @file_get_contents($khPath) === $beforeKH);

@unlink($khPath);
check('a failed scan with no existing known_hosts creates nothing',
      um_tier1_scan_host($addr, 'um_tier1_fake_scan_fail') === null && !is_file($khPath));

@unlink($khPath);
@rmdir(um_keys_dir());
@rmdir($stmp);
um_set_cfg_dir('/boot/config/plugins/unraid-manager');

/* No real-ssh-keyscan test: unlike ssh-keygen (which needs no network to
   generate a pair), ssh-keyscan needs a live sshd to scan against, so
   "binary present" would not be enough to make a real-binary test hermetic
   the way the keygen one is - it would need a real reachable host, which
   this suite has no business depending on. The fake-runner tests above
   cover every branch of um_tier1_scan_host's own logic. */

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
