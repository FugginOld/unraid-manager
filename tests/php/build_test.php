<?PHP
/* build.sh is what turns source/ into the package Unraid installs. It has no
   binaries to download (unlike HBAviewer's), so what it must get right is the
   tree shape and the root-owned tar. These are static checks on the script and
   the tree — they do not run the build, which needs a Linux tar.
     php tests/php/build_test.php  ->  "build: all pass" (exit 0) */

$fails = 0;
function check(string $name, bool $ok): void {
    global $fails;
    echo ($ok ? "PASS  " : "FAIL  ") . $name . "\n";
    if (!$ok) $fails++;
}

$root = __DIR__ . '/../..';
$sh   = (string) @file_get_contents($root . '/build.sh');

check('build.sh exists', $sh !== '');
check('packages from source/', str_contains($sh, 'cd source'));
check('tar is root-owned', str_contains($sh, '--owner=root') && str_contains($sh, '--group=root'));
check('output is releases/unraid-manager.txz', str_contains($sh, 'releases/unraid-manager.txz'));
check('prints md5 for the plg', str_contains($sh, 'md5sum'));
/* HBAviewer downloaded pinned binaries and checksummed them. This plugin
   downloads nothing at all, and a build step that reaches the network would be
   a new supply-chain surface — assert it stays absent. */
check('build fetches nothing from the network',
      !str_contains($sh, 'curl') && !str_contains($sh, 'wget'));
check('build refuses to package a key', str_contains($sh, 'Refusing to package a secret'));
/* The guard must catch both loose *.key files AND anything under a keys/
   directory (.gitignore's other reserved spot for secrets) — a find that only
   matches '*.key' misses a keys/ dir entirely. */
check('key guard also covers keys/ directories', str_contains($sh, "-path '*/keys/*'"));

$plugdir = $root . '/source/usr/local/emhttp/plugins/unraid-manager';
check('plugin dir at the emhttp path', is_dir($plugdir));
check('icon.png present', is_file($plugdir . '/icon.png'));

echo $fails === 0 ? "build: all pass\n" : "build: $fails FAILED\n";
exit($fails === 0 ? 0 : 1);
