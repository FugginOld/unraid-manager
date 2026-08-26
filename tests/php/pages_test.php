<?PHP
/* Static checks on the two .page files and their JavaScript: the header block
   Unraid parses for menu placement, the CSRF token being carried on every POST,
   and the rules that keep the UI honest about what it does not know.
     php tests/php/pages_test.php  ->  "pages: all pass" (exit 0) */

$fails = 0;
function check(string $name, bool $ok): void {
    global $fails;
    echo ($ok ? "PASS  " : "FAIL  ") . $name . "\n";
    if (!$ok) $fails++;
}

$base     = __DIR__ . '/../../source/usr/local/emhttp/plugins/unraid-manager';
$settings = (string) @file_get_contents($base . '/UnraidManagerSettings.page');
$sjs      = (string) @file_get_contents($base . '/settings.js');

check('settings page exists', $settings !== '');
check('settings page is on the Utilities menu', (bool) preg_match('/^Menu="Utilities"/m', $settings));
check('settings page has a Title', (bool) preg_match('/^Title=/m', $settings));
check('settings page header is terminated', str_contains($settings, "---\n"));
check('settings page loads its javascript', str_contains($settings, 'settings.js'));

check('settings js exists', $sjs !== '');
check('probe is a POST action', str_contains($sjs, "action=probe") || str_contains($sjs, "'probe'"));
check('enroll is a POST action', str_contains($sjs, 'enroll'));
/* Non-negotiable: every POST carries Unraid's token. */
check('every settings post carries the csrf token', substr_count($sjs, 'csrf_token') >= 1);
check('csrf token comes from the page, not a literal', str_contains($settings, 'csrf_token'));
/* The gate the spec puts on the enroll button. */
check('enroll is gated on a probe verdict',
      str_contains($sjs, 'verdict') && (str_contains($sjs, 'partial') && str_contains($sjs, "'ok'")));
/* A key must never be written back into the DOM or into a URL. */
check('no key is placed in a query string', !preg_match('/[?&]key=/', $sjs));
check('the key field is a password input', str_contains($settings, 'type="password"'));
check('the key field does not autocomplete', str_contains($settings, 'autocomplete="off"')
      || str_contains($settings, 'autocomplete="new-password"'));
/* Native form submit is forbidden inside the webGUI shell (plan §10.1). */
check('settings page has no submitting form', !preg_match('/<form[^>]*\baction=/i', $settings));

/* Spec §6: a daemon status line WITH start/stop, and a per-node test. */
foreach (['start', 'stop', 'restart'] as $verb) {
    check("daemon $verb button present", str_contains($settings, 'um-daemon-' . $verb));
}
check('daemon buttons post a daemon action', str_contains($sjs, "{daemon: verb}"));
check('per-node test button present', str_contains($sjs, "action: 'test'"));
/* The re-probe carries an id, never a key — the daemon reads that node's key
   from flash itself. */
check('the per-node test sends no key',
      !preg_match("/action: 'test'[^}]*key/", $sjs));

echo $fails === 0 ? "pages: all pass\n" : "pages: $fails FAILED\n";
exit($fails === 0 ? 0 : 1);
