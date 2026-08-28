<?PHP
/* The bundle mount. Asserts the manifest reader and the build wiring without
   requiring a build to have happened.
     php tests/php/frontend_test.php  ->  "frontend: all pass" (exit 0) */

$root = __DIR__ . '/../..';
$base = $root . '/source/usr/local/emhttp/plugins/unraid-manager';
require_once $base . '/include/common.php';

$fails = 0;
function check(string $name, bool $ok): void {
    global $fails;
    echo ($ok ? "PASS  " : "FAIL  ") . $name . "\n";
    if (!$ok) $fails++;
}

/* A manifest in a temp tree, so this test never depends on a build having run. */
$tmp = sys_get_temp_dir() . '/um_ui_' . getmypid();
@mkdir($tmp . '/.vite', 0700, true);
file_put_contents($tmp . '/.vite/manifest.json', json_encode([
    'src/main.js' => ['file' => 'assets/main-abc123.js', 'isEntry' => true,
                      'css' => ['assets/main-def456.css']],
]));

$tags = um_asset_tags('src/main.js', $tmp);
check('the script tag is a module', str_contains($tags, 'type="module"'));
check('the hashed js filename is resolved', str_contains($tags, 'assets/main-abc123.js'));
check('the hashed css filename is resolved', str_contains($tags, 'assets/main-def456.css'));
check('the css is a stylesheet link', str_contains($tags, 'rel="stylesheet"'));
check('paths are absolute under the plugin',
      str_contains($tags, '/plugins/unraid-manager/ui/assets/main-abc123.js'));

/* Failure modes: a missing build must say so, not render a blank page. */
check('a missing manifest yields a visible message, not silence',
      str_contains(um_asset_tags('src/main.js', $tmp . '/nope'), 'not built'));
check('an unknown entry yields a visible message',
      str_contains(um_asset_tags('src/nosuch.js', $tmp), 'not built'));
@unlink($tmp . '/.vite/manifest.json'); @rmdir($tmp . '/.vite'); @rmdir($tmp);

/* Build wiring. */
$sh = (string) file_get_contents($root . '/build.sh');
check('build.sh builds the frontend', str_contains($sh, 'npm run build'));
check('build.sh refuses without npm rather than shipping a stale bundle',
      str_contains($sh, 'command -v npm'));
check('build.sh installs from the lockfile', str_contains($sh, 'npm ci'));

$page = (string) file_get_contents($base . '/UnraidManager.page');
check('the page mounts the app', str_contains($page, 'um-app'));
check('the page emits the asset tags', str_contains($page, 'um_asset_tags'));
check('no hash is hardcoded in the page', !str_contains($page, 'assets/'));
check('the page still declares an icon-font Code',
      (bool) preg_match('/^Code="[0-9a-f]{4}"/m', $page));

$ci = (string) file_get_contents($root . '/.github/workflows/tests.yml');
check('CI builds the bundle', str_contains($ci, 'npm ci') && str_contains($ci, 'npm run build'));
check('CI enforces the size budget at the specced 250 KB',
      str_contains($ci, 'BUDGET') && str_contains($ci, '256000'));

$ignore = (string) file_get_contents($root . '/.gitignore');
check('node_modules is ignored', str_contains($ignore, 'node_modules'));
check('the built bundle is not committed', str_contains($ignore, 'unraid-manager/ui/'));

/* Every check above passes $tmp, so the no-argument call - the one the live
   page actually makes - was never executed. Both defaults could be wrong and
   the whole suite stayed green while the box rendered "not built" forever:
   mutating um_ui_dir() to ../wrong-ui, or the entry to src/nope.js, left
   frontend_test AND pages_test at exit 0. Guarded on is_file so it is a no-op
   without a local build, and mandatory in the frontend CI job, which builds. */
/* The presence test must NOT go through um_ui_dir(): gating the check on the
   very function it is meant to pin makes it skip itself the moment that
   function is wrong, which is fail-open and is how the first version of this
   check passed a deliberately broken um_ui_dir(). Use the build's real
   location, spelled out. */
$builtManifest = __DIR__ . '/../../source/usr/local/emhttp/plugins/unraid-manager'
               . '/ui/.vite/manifest.json';
if (is_file($builtManifest)) {
    $real = um_asset_tags();
    check('the default ui dir and entry name resolve the real build',
          !str_contains($real, 'not built'));
    if (preg_match('#src="' . preg_quote(UM_UI_URL, '#') . '/([^"]+)"#', $real, $m)) {
        check('the resolved script exists on disk', is_file(um_ui_dir() . '/' . $m[1]));
    } else {
        check('the resolved script exists on disk', false);
    }
    $mf = json_decode((string) file_get_contents($builtManifest), true);
    check("the manifest really has the entry the default names",
          is_array($mf) && array_key_exists('src/main.js', $mf));
}

/* ── the app shell ────────────────────────────────────────────────────────── */
$src  = $root . '/frontend/src';
$api  = (string) @file_get_contents($src . '/api.js');
$live = (string) @file_get_contents($src . '/live.js');
$chip = (string) @file_get_contents($src . '/components/StatusChip.vue');
$app  = (string) @file_get_contents($src . '/App.vue');

check('the api client exists', $api !== '');
check('requests carry the session cookie', str_contains($api, 'same-origin'));
check('the api client never sends a key',
      !preg_match('/[?&]key=/', $api) && !preg_match('/\bkey\s*:/', $api));

check('live updates subscribe to the nchan channel', str_contains($live, '/sub/unraid-manager'));
check('live updates use EventSource', str_contains($live, 'EventSource'));
check('there is a polling fallback', str_contains($live, '30000'));
check('there is a three-minute stale threshold', str_contains($live, '180000'));

/* Whole-file substring checks pass on a word surviving in <script> (the
   LABELS object literal) even after it is deleted from <template> - the
   thing that actually renders. Isolate the template before asserting on
   what it renders. */
$chipTemplate = (string) preg_replace('/^[\s\S]*?<template>|<\/template>[\s\S]*$/', '', $chip);
check('the chip template renders both the glyph and the word, not colour alone',
      str_contains($chipTemplate, ')[0]') && str_contains($chipTemplate, ')[1]'));
check('the chip pairs colour with a glyph', (bool) preg_match('/[\x{2713}\x{26A0}?]/u', $chip));
check('unknown has its own treatment', str_contains($chip, 'um-unknown'));
check('the chip class binding is driven by the state prop, not a static string',
      (bool) preg_match('/:class\s*=\s*"[^"]*\bstate\b[^"]*"/', $chipTemplate));

check('the shell has all three tabs',
      str_contains($app, 'Overview') && str_contains($app, 'Disks')
      && str_contains($app, 'Drift'));
check('no router library was added',
      !str_contains((string) file_get_contents($root . '/frontend/package.json'), 'vue-router'));

/* ── amendment 1: the db flag ─────────────────────────────────────────────── */
/* An unreadable database is byte-identical to a healthy empty fleet unless the
   shell distinguishes them. Handled once in useEndpoint/App.vue, not per view. */
check('the api client reads the db property off a response',
      (bool) preg_match('/\bdb\s*===?\s*false\b|\.db\b/', $api));
check('the api client exposes error and loading alongside dbUnreadable',
      str_contains($api, 'dbUnreadable') && str_contains($api, 'error')
      && str_contains($api, 'loading'));
/* Deleting dbUnreadable from useEndpoint's RETURNED object leaves the banner
   permanently hidden, yet the identifier still survives in its `ref()`
   declaration and the doc comment above it - a whole-file substring check
   cannot tell the two apart. Anchor on the return statement itself. */
check('the api client actually returns dbUnreadable from useEndpoint, not just declares it',
      (bool) preg_match('/return\s*\{[^}]*\bdbUnreadable\b[^}]*\}/', $api));
check('App.vue destructures dbUnreadable out of useEndpoint(), not just mentions it',
      (bool) preg_match('/\{[^}]*\bdbUnreadable\b[^}]*\}\s*=\s*useEndpoint/', $app));
check('App.vue renders a persistent database-unreadable banner',
      str_contains($app, 'could not be read'));
check('the db-unreadable banner points at the settings page',
      str_contains($app, 'UnraidManagerSettings'));

/* A keyword blacklist for "dismissible" survives `v-if="dbUnreadable && !gone"`
   plus `@click="gone = true"` - neither word appears. Assert the actual
   construct: the v-if condition is the bare flag, and nothing inside the
   banner is clickable. */
$appTemplate = (string) preg_replace('#<style>.*#s', '', $app);
preg_match('/<p\s+v-if="dbUnreadable"[^>]*>.*?<\/p>/s', $appTemplate, $dbBannerMatch);
$dbBannerBlock = $dbBannerMatch[0] ?? '';
check('the db-unreadable banner condition is the bare flag, not a dismissible compound',
      $dbBannerBlock !== '' && !str_contains($dbBannerBlock, '@click'));
check('the db-unreadable banner is rendered by the shell, above the tabs, not by a view',
      preg_match('#um-db-banner.*?<nav\b#s', $appTemplate) === 1);

/* ── amendment 2A: useLive must tear down / be a singleton ───────────────── */
/* `str_contains($live, 'started')` survives deleting `if (started) return` -
   the exact defect - because `started = true` is still on the next line. A
   substring of an identifier is not an assertion about a construct. This is
   also why the presence check below is deliberately weak: whether
   onUnmounted() actually deregisters the right thing - and only the right
   thing, when the identity is shared across callers (fix round 2's Critical)
   - is a runtime question a grep cannot answer. That proof lives in
   tests/js/live_singleton.mjs, via a real component mount and unmount. */
check('useLive registers per-caller teardown via onUnmounted',
      (bool) preg_match('/onUnmounted\s*\(/', $live));
check('start() guards against re-creating the singleton stream and timers',
      (bool) preg_match('/if\s*\(\s*started\s*\)\s*return/', $live));
preg_match('/export function useLive[\s\S]*?\n\}/', $live, $useLiveMatch);
$useLiveBody = $useLiveMatch[0] ?? '';
/* Anchored, not strpos: 'register(refresh)' is a substring of
   'unregister(refresh)', so the loose form matched the teardown call and
   deleting the real registration left the whole PHP suite green. */
$addPos = preg_match('/(?<![A-Za-z])register\(refresh\)/', $useLiveBody, $m, PREG_OFFSET_CAPTURE)
    ? $m[0][1] : false;
$kickPos = strpos($useLiveBody, 'kick(refresh)');
check('a newly registered caller is kicked immediately, not left for the fallback timer',
      $addPos !== false && $kickPos !== false && $addPos < $kickPos);
/* The Map alone is not the invariant - a Map that never counts fails exactly
   the same way the Set did. Assert the counting itself; the behavioural proof
   lives in tests/js/live_singleton.mjs, which is what actually catches this. */
check('callbacks are refcounted (a Map), not a Set keyed by the memoised identity',
      (bool) preg_match('/callbacks\s*=\s*new Map\s*\(\s*\)/', $live)
      && (bool) preg_match('/callbacks\.set\(.*\+\s*1\)/', $live)
      && (bool) preg_match('/callbacks\.delete\(/', $live));

/* ── amendment 2B: the stale banner is page-wide ──────────────────────────── */
check('App.vue imports the live-updates module', str_contains($app, "live.js"));
/* Match the <nav> tag itself, not the ".um-tabs" CSS selector that also lives
   in this file's <style> block — matching the selector would let the banner
   move anywhere in the template (even after <component :is>) and still pass,
   since the CSS always comes last. */
check('the stale banner is rendered by the shell, above the tabs, not by a view',
      preg_match('#um-stale-banner.*?<nav\b#s', $appTemplate) === 1);
/* Whole-file str_contains('lastGood') passes on the script's own destructure
   (`const { stale, lastGood } = useLive(refresh)`) even after the banner
   text stops using it - the same vacuous-substring shape as the dbUnreadable
   checks above. Anchor on the stale-banner block itself. */
preg_match('/<p\s+v-if="stale"[^>]*>.*?<\/p>/s', $appTemplate, $staleBannerMatch);
$staleBannerBlock = $staleBannerMatch[0] ?? '';
check('the stale banner names when the manager last answered',
      str_contains($staleBannerBlock, 'lastGood'));
/* A negative literal (`!preg_match('/v-else-if\s*=\s*"stale"/')`) is evaded
   by quote style or whitespace; assert the positive construct instead. Both
   $dbBannerBlock and $staleBannerBlock above are captured by a regex that
   requires the literal attribute `v-if=` right after the tag name - `v-else-if`
   does not match it, so either banner being demoted to v-else-if empties the
   corresponding block and this fails. */
check('both banners are independent sibling v-if elements, neither is v-else-if',
      $dbBannerBlock !== '' && $staleBannerBlock !== '');
$overviewStub = (string) @file_get_contents($src . '/views/Overview.vue');
check('the stale banner did not stay behind in Overview.vue',
      !str_contains($overviewStub, 'stale'));

/* ── amendment 2C: a malformed 200 is not a fresh refresh ─────────────────── */
check('the api client imports its own get() rather than duplicating fetch',
      substr_count($api, 'fetch(') === 1);
check('the expected-key map covers health -> nodes',
      (bool) preg_match('/health\s*:\s*[\'"]nodes[\'"]/', $api));
check('the expected-key map covers disks -> disks',
      (bool) preg_match('/disks\s*:\s*[\'"]disks[\'"]/', $api));
check('the expected-key map covers drift -> rows',
      (bool) preg_match('/drift\s*:\s*[\'"]rows[\'"]/', $api));
check('useEndpoint throws on a name with no expected key registered (fails closed)',
      (bool) preg_match('/if\s*\(\s*!\s*expectKey\s*\)\s*throw/', $api));
check('useEndpoint throws when the response lacks its expected top-level key',
      (bool) preg_match('/!\(\s*expectKey\s+in\s+json\s*\)[\s\S]{0,60}throw/', $api));
/* A memoisation check belongs on the returned VALUE (are two calls the same
   object?), not on the presence of a CACHE-shaped identifier - `return
   buildEndpoint(name)` with the CACHE lines left sitting unused nearby still
   satisfies both substrings. That is a runtime question; it is asserted for
   real in tests/js/live_singleton.mjs via reference equality. */

/* ── binding constraints that were unpinned ───────────────────────────────── */
/* tokens.css's own header: "A hardcoded background anywhere in this bundle is
   a bug" - it is meant to read Unraid's own custom property, always. Check
   every .vue file, not just App.vue, so a future component cannot slip one in
   either. */
$vueFiles = array_merge(
    [$src . '/App.vue', $src . '/components/StatusChip.vue'],
    glob($src . '/views/*.vue') ?: []
);
$hardcodedBg = false;
foreach ($vueFiles as $f) {
    if (preg_match('/background(-color)?\s*:\s*#[0-9a-fA-F]{3,8}\b/', (string) file_get_contents($f))) {
        $hardcodedBg = true;
        break;
    }
}
check('no .vue file hardcodes a background colour (tokens.css owns those)', !$hardcodedBg);

echo $fails === 0 ? "frontend: all pass\n" : "frontend: $fails FAILED\n";
exit($fails === 0 ? 0 : 1);
