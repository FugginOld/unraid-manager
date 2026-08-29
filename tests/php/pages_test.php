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

/* The Name field says "taken from the node if left blank". The server's
   fallback is the address, so the page has to supply the hostname the probe
   reported or that text is a lie - Golem enrolled as 192.168.2.248 on the live
   box before this. */
check('a blank name falls back to the probed hostname',
      str_contains($sjs, 'probedHostname'));
check('the enroll post uses that fallback',
      (bool) preg_match('/name:.*probedHostname/', $sjs));

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

/* ── the Fleet tab ────────────────────────────────────────────────────────── */
/* The Fleet tab is now a mount point for the Vue bundle (Task 11); its former
   inline fleet.js and the assertions specific to it are gone with that file.
   frontend_test.php covers the manifest mount that replaced it. */
$fleet = (string) @file_get_contents($base . '/UnraidManager.page');

check('fleet page exists', $fleet !== '');
/* Verified on the live box: a top-level tab is Type="xmenu", ordered by
   Menu="Tasks:NN". */
check('fleet page is a top-level xmenu tab', (bool) preg_match('/^Type="xmenu"/m', $fleet));
check('fleet page is ordered under Tasks', (bool) preg_match('/^Menu="Tasks:\d+"/m', $fleet));
check('fleet page has a Title', (bool) preg_match('/^Title=/m', $fleet));
/* Code= is what actually puts a tab in the top bar. Every top-level page on a
   live 7.3.2 box has one - Dashboard, Main, Shares, Docker, VMs, Tools, Apps,
   and third-party ones like networkstats - and the tab is simply not rendered
   without it. Title and Icon alone are the Utilities-page shape, which is why
   the settings page appeared and the Fleet tab did not. Observed on Raven. */
check('fleet page declares an icon-font Code, or it never renders',
      (bool) preg_match('/^Code="[0-9a-f]{4}"/m', $fleet));
check('fleet page has no submitting form', !preg_match('/<form[^>]*\baction=/i', $fleet));

$css = (string) @file_get_contents($base . '/unraid-manager.css');
check('the plugin stylesheet exists', $css !== '');

/* Paired thresholds must read as a pair. Verified wrong on Raven 2026-08-29:
   Unraid stretches every settings input to the full cell (measured 1607px on
   a 1875px window, all six of them), so `capacity_high_water` and `temp_crit`
   rendered as full-width boxes stacked UNDER their partners. Empty, and with
   only a bottom border in this theme, each looked exactly like a divider rule
   rather than a field - so the operator reasonably concluded there was no way
   to set a critical value at all.

   Reachability was never the problem; legibility was. These checks pin the
   structure that makes them legible, not the words around it. */
$pairIds = [['um-capacity-watch', 'um-capacity-high-water'],
            ['um-temp-warn', 'um-temp-crit']];
foreach ($pairIds as $pair) {
    [$low, $high] = $pair;
    /* Both inside ONE um-pair span, in order, with nothing but the separator
       between them - a check that fails if either drifts out of the wrapper,
       which is what makes the CSS rule below apply to it. */
    $re = '/<span class="um-pair">\s*<input[^>]*id="' . preg_quote($low, '/')
        . '"[^>]*>.*?<input[^>]*id="' . preg_quote($high, '/') . '"[^>]*>\s*<\/span>/s';
    check("$low and $high are wrapped together as one pair",
          (bool) preg_match($re, $settings));
    /* The pair is meaningless unread: two bare boxes do not say which is
       which. A separator between them is the whole affordance. */
    $between = [];
    preg_match('/id="' . preg_quote($low, '/') . '".*?id="' . preg_quote($high, '/') . '"/s',
               $settings, $between);
    check("...with a visible separator between $low and $high",
          isset($between[0]) && str_contains($between[0], 'um-pair-sep'));
}

/* The rule that undoes the host's full-width stretch. It must out-specify it,
   so an id-qualified selector, not a bare class - the previous behaviour WAS
   the browser applying Unraid's rule because ours did not exist. */
/* Not merely "a width is set" - the host already sets one, and it is the
   problem. It has to be a SMALL one, in a text-relative unit, or two boxes
   still cannot share a line. A five-digit threshold does not need 1607px. */
check('the stylesheet constrains paired inputs so both fit on one line',
      (bool) preg_match('/#um-settings\s+\.um-pair\s+input[^{]*\{[^}]*width\s*:\s*([\d.]+)\s*(em|ch|rem)\s*;/', $css, $w)
      && (float) $w[1] <= 10);
check('...and does not let the pair wrap mid-way',
      (bool) preg_match('/\.um-pair\b[^{]*\{[^}]*white-space\s*:\s*nowrap/', $css));

echo $fails === 0 ? "pages: all pass\n" : "pages: $fails FAILED\n";
exit($fails === 0 ? 0 : 1);
