<?PHP
/* Proves the PHP suite runs at all: php version floor and this file's local
   check() helper. Every later gate test in this directory defines its own
   identical copy of check() rather than sharing one.
     php tests/php/harness_test.php  ->  "harness: all pass" (exit 0) */

$fails = 0;
function check(string $name, bool $ok): void {
    global $fails;
    echo ($ok ? "PASS  " : "FAIL  ") . $name . "\n";
    if (!$ok) $fails++;
}

check('php 8.0 or newer', PHP_VERSION_ID >= 80000);
check('json extension present', function_exists('json_encode'));
/* sqlite3, NOT pdo_sqlite. Unraid's php-fpm ships the sqlite3 extension and no
   pdo_sqlite driver, so a suite that checks for PDO checks something the target
   does not have - which is how every PHP read came back empty on Raven while
   this test passed locally. */
check('the sqlite3 extension is present', class_exists('SQLite3'));

echo $fails === 0 ? "harness: all pass\n" : "harness: $fails FAILED\n";
exit($fails === 0 ? 0 : 1);
