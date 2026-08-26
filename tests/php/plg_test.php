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

/* 3b. Pull each Run="/bin/bash" block's own text, keyed by its Method
      attribute (install has none, uninstall has Method="remove"), so checks
      below can assert against the block that actually matters instead of the
      whole file — a string that merely appears *somewhere* in $raw (a <FILE
      Name=...> attribute, the other script, a comment) must not satisfy a
      check named for one specific block. */
function inlineTextFor(DOMDocument $doc, string $method): string {
    foreach ($doc->getElementsByTagName('FILE') as $file) {
        if ($file->getAttribute('Method') !== $method) continue;
        $inlines = $file->getElementsByTagName('INLINE');
        if ($inlines->length > 0) return $inlines->item(0)->textContent;
    }
    return '';
}
$installInline = $ok !== false ? inlineTextFor($doc, '') : '';
$removeInline  = $ok !== false ? inlineTextFor($doc, 'remove') : '';

/* 4. Install/uninstall contract from spec §1. Scoped to $installInline, not
      $raw: substring checks against the whole file pass even when the line
      that matters was deleted, as long as the same words appear anywhere
      else (cron lines also say "rc.unraid-manager", the remove block also
      calls update_cron, a <FILE Name=...> attribute also has the flash path). */
check('install seeds the flash config dir',
      str_contains($installInline, 'mkdir -p /boot/config/plugins/unraid-manager/keys'));
/* The exact invocation, not the bare word: the install block's own comment
   above the cron printf also says "update_cron" in prose, and a substring
   check against $installInline alone survives deleting the real call as long
   as that comment is still there. */
check('install registers the retention cron',
      str_contains($installInline, '/usr/local/sbin/update_cron'));
check('install unpacks the package',
      str_contains($installInline, 'tar -xJf /boot/config/plugins/unraid-manager/unraid-manager.txz'));
/* Spec section 4 asks for a daily prune AND a weekly VACUUM. Both must be
   scheduled, or the second one never happens. These two are fine matched
   against $raw: the strings they look for ("prune >", "prune-vacuum >") only
   ever occur in the cron lines, nowhere else in the file. */
check('the daily prune is scheduled', str_contains($raw, 'rc.unraid-manager prune >'));
check('the weekly vacuum is scheduled', str_contains($raw, 'rc.unraid-manager prune-vacuum >'));
/* Specifically the *start* invocation, not just any mention of the rc
   script's name — the cron lines inside this same block also say
   "rc.unraid-manager", so a bare substring match would survive deleting the
   line that actually starts the daemon. */
check('install starts the daemon via the rc script', str_contains($installInline, 'rc.unraid-manager start'));

/* An upgrade with the array up must not rm -rf a running daemon's directory
   out from under it. This has to be the first line of the install block —
   any earlier line already touched the directory. */
$installLines = array_values(array_filter(
    preg_split('/\r\n|\r|\n/', $installInline),
    fn($l) => trim($l) !== ''
));
check('install stops any running daemon before touching its directory (first line)',
      trim($installLines[0] ?? '') ===
      '[ -x /usr/local/emhttp/plugins/unraid-manager/scripts/rc.unraid-manager ] && '
      . '/usr/local/emhttp/plugins/unraid-manager/scripts/rc.unraid-manager stop');

check('remove block present', str_contains($raw, 'Method="remove"'));
check('remove drops the cron', str_contains($raw, 'rm -f /etc/cron.d/unraid-manager'));
/* Flash config and the pool DB survive an uninstall unless the operator says
   otherwise (spec §1). A blocklist of delete-command spellings (rm -rf vs.
   rm -fr vs. rm -r -f vs. find -delete, etc.) always misses one — so instead:
   walk every non-comment, non-blank line of the remove block's own text, and
   fail if ANY line other than the final informational echo mentions the
   flash-config path at all. A line that never names the path can't delete it
   under any spelling; a line that does name it and isn't the echo is a leak
   by construction. */
$leaks = 0;
foreach (preg_split('/\r\n|\r|\n/', $removeInline) as $line) {
    $trimmed = ltrim($line);
    if ($trimmed === '' || $trimmed[0] === '#') continue;
    if (str_starts_with($trimmed, 'echo ')) continue;
    if (str_contains($line, '/boot/config/plugins/unraid-manager')) $leaks++;
}
check('remove touches the flash-config path only in the final echo', $leaks === 0);

/* 5. No secret ever ships in the package definition. */
check('no api key in the plg', !preg_match('/[A-Za-z0-9_\-]{40,}/', $raw));

/* 6. Changelog block for the declared version — the forcing function
      release.yml uses to build the GitHub release notes. */
check('changelog block for this version', $ver !== '' && str_contains($raw, "###$ver###"));

echo $fails === 0 ? "plg: all pass\n" : "plg: $fails FAILED\n";
exit($fails === 0 ? 0 : 1);
