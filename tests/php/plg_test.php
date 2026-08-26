<?PHP
/* Runnable checks for unraid-manager.plg — the file Unraid's plugin manager
   parses to decide whether this plugin installs at all.
     php tests/php/plg_test.php  ->  "plg: all pass" (exit 0)

   Ported from Unraid-HBAviewer, where a bare '<' inside a shell comment in an
   INLINE block made a release uninstallable for everyone. INLINE blocks are XML
   text: a raw '<' or '&' anywhere in them, comments included, breaks install. */

$fails = 0;
function check(string $name, bool $ok): void {
    global $fails;
    echo ($ok ? "PASS  " : "FAIL  ") . $name . "\n";
    if (!$ok) $fails++;
}

$plg = __DIR__ . '/../../unraid-manager.plg';
check('unraid-manager.plg exists', is_file($plg));
$raw = (string) @file_get_contents($plg);

/* 1. It must parse. */
$prev = libxml_use_internal_errors(true);
libxml_clear_errors();
$doc = new DOMDocument();
$ok  = $doc->loadXML($raw, LIBXML_NOENT);
$errs = array_map(fn($e) => trim($e->message) . ' (line ' . $e->line . ')', libxml_get_errors());
libxml_clear_errors();
libxml_use_internal_errors($prev);
check('parses as XML' . ($ok ? '' : ' — ' . implode('; ', array_slice($errs, 0, 3))), $ok !== false);

/* 2. The entities release.yml patches. All three must exist in the shape it
      sed-matches, or a release silently ships a stale md5. */
foreach (['name', 'author', 'version', 'pkgURL', 'md5'] as $ent) {
    check("entity $ent declared", (bool) preg_match('/<!ENTITY\s+' . $ent . '\s+"[^"]*">/', $raw));
}
preg_match('/<!ENTITY\s+version\s+"([^"]*)">/', $raw, $mv);
$ver = $mv[1] ?? '';
check('version is a date tag', (bool) preg_match('/^20\d\d\.\d\d\.\d\d(\.\d+)?$/', $ver));
check('pkgURL path carries that same version',
      $ver !== '' && str_contains($raw, "/download/$ver/unraid-manager.txz"));

/* 3. INLINE blocks are XML text. */
preg_match_all('/<INLINE>(.*?)<\/INLINE>/s', $raw, $inl);
$bad = 0;
foreach ($inl[1] as $block) {
    /* The parser already resolved entities, so look at the raw source text:
       any '<' or '&' that is not a well-formed entity reference is a landmine. */
    if (preg_match('/&(?!(amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)/', $block)) $bad++;
}
check('no bare ampersand in any INLINE block', $bad === 0);
check('at least one INLINE block present', count($inl[1]) > 0);

/* 4. Install/uninstall contract from spec §1. */
check('install seeds the flash config dir', str_contains($raw, '/boot/config/plugins/unraid-manager'));
check('install registers the retention cron', str_contains($raw, 'update_cron'));
/* Spec section 4 asks for a daily prune AND a weekly VACUUM. Both must be
   scheduled, or the second one never happens. */
check('the daily prune is scheduled', str_contains($raw, 'rc.unraid-manager prune >'));
check('the weekly vacuum is scheduled', str_contains($raw, 'rc.unraid-manager prune-vacuum >'));
check('install starts the daemon via the rc script', str_contains($raw, 'rc.unraid-manager'));
check('remove block present', str_contains($raw, 'Method="remove"'));
check('remove drops the cron', str_contains($raw, 'rm -f /etc/cron.d/unraid-manager'));
/* Flash config and the pool DB survive an uninstall unless the operator says
   otherwise (spec §1) — the remove block must not delete either. Anchored to
   the start of a line so the documentation comment that TELLS the operator how
   to remove it by hand does not read as the command itself. */
check('remove does not delete the flash config',
      !preg_match('/^\s*rm\s+-rf?\s+\/boot\/config\/plugins\/unraid-manager\b/m', $raw));

/* 5. No secret ever ships in the package definition. */
check('no api key in the plg', !preg_match('/[A-Za-z0-9_\-]{40,}/', $raw));

/* 6. Changelog block for the declared version — the forcing function
      release.yml uses to build the GitHub release notes. */
check('changelog block for this version', $ver !== '' && str_contains($raw, "###$ver###"));

echo $fails === 0 ? "plg: all pass\n" : "plg: $fails FAILED\n";
exit($fails === 0 ? 0 : 1);
