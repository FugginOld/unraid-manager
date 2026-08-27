<?PHP
/* The release workflow patches the plg on main and pushes it — the branch
   Unraid clients poll for updates. A mistake there ships a broken plugin to
   every install, so its shape is asserted rather than trusted.
     php tests/php/workflow_test.php  ->  "workflow: all pass" (exit 0) */

$fails = 0;
function check(string $name, bool $ok): void {
    global $fails;
    echo ($ok ? "PASS  " : "FAIL  ") . $name . "\n";
    if (!$ok) $fails++;
}

$root = __DIR__ . '/../..';
$tests   = (string) @file_get_contents($root . '/.github/workflows/tests.yml');
$release = (string) @file_get_contents($root . '/.github/workflows/release.yml');

check('tests workflow exists', $tests !== '');
check('pins python 3.11 to match the unraid base image', str_contains($tests, "'3.11'"));
check('pins php 8.2 to match unraid 7.x', str_contains($tests, "'8.2'"));
check('runs the python suite', str_contains($tests, 'unittest discover'));
check('runs the php suite', str_contains($tests, 'tests/php/run.sh'));
check('lints every php file', str_contains($tests, 'php -l'));
check('lints every shell script', str_contains($tests, 'bash -n'));
check('is callable from the release workflow', str_contains($tests, 'workflow_call'));
/* No test may reach a live box, so CI must never hold a key. */
check('the test workflow needs no secrets', !str_contains($tests, 'secrets.'));

check('release workflow exists', $release !== '');
check('release is gated on the test suite', str_contains($release, 'needs: test'));
check('release triggers on a date tag', str_contains($release, "tags: ['20*']"));
check('release builds the txz', str_contains($release, 'bash build.sh'));
foreach (['version', 'md5', 'pkgURL'] as $ent) {
    check("release patches the $ent entity", str_contains($release, $ent));
}
check('release verifies every entity landed', str_contains($release, '::error::'));
check('the tag must be the tip of main', str_contains($release, 'rev-parse'));
check('the changelog block is required', str_contains($release, '###'));
check('no secret is echoed', !preg_match('/echo .*secrets\./', $release));

echo $fails === 0 ? "workflow: all pass\n" : "workflow: $fails FAILED\n";
exit($fails === 0 ? 0 : 1);
