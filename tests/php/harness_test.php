<?PHP
/* Proves the PHP suite runs at all: php version floor and the check() helper
   every other gate test in this directory uses.
     php tests/php/harness_test.php  ->  "harness: all pass" (exit 0) */

$fails = 0;
function check(string $name, bool $ok): void {
    global $fails;
    echo ($ok ? "PASS  " : "FAIL  ") . $name . "\n";
    if (!$ok) $fails++;
}

check('php 8.0 or newer', PHP_VERSION_ID >= 80000);
check('json extension present', function_exists('json_encode'));
check('pdo_sqlite present', in_array('sqlite', PDO::getAvailableDrivers(), true));

echo $fails === 0 ? "harness: all pass\n" : "harness: $fails FAILED\n";
exit($fails === 0 ? 0 : 1);
