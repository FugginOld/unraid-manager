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

check('the chip pairs colour with a word',
      str_contains($chip, 'OK') && str_contains($chip, 'Degraded')
      && str_contains($chip, 'Unknown'));
check('the chip pairs colour with a glyph', (bool) preg_match('/[\x{2713}\x{26A0}?]/u', $chip));
check('unknown has its own treatment', str_contains($chip, 'um-unknown'));

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
check('the api client exposes dbUnreadable distinct from error/loading',
      str_contains($api, 'dbUnreadable') && str_contains($api, 'error')
      && str_contains($api, 'loading'));
check('App.vue references the dbUnreadable flag', str_contains($app, 'dbUnreadable'));
check('App.vue renders a persistent database-unreadable banner',
      str_contains($app, 'could not be read'));
check('the db-unreadable banner is not wrapped in anything dismissible',
      !preg_match('/dismiss|@click="[^"]*(close|hide|dismiss)/i', $app));
check('the db-unreadable banner points at the settings page',
      str_contains($app, 'UnraidManagerSettings'));

/* ── amendment 2A: useLive must tear down / be a singleton ───────────────── */
check('useLive registers per-caller teardown or is a module singleton',
      str_contains($live, 'onUnmounted') || str_contains($live, 'started'));

/* ── amendment 2B: the stale banner is page-wide ──────────────────────────── */
check('App.vue imports the live-updates module', str_contains($app, "live.js"));
check('the stale banner is rendered by the shell, above the tabs, not by a view',
      preg_match('#um-stale-banner.*?um-tabs#s', $app) === 1);
$overviewStub = (string) @file_get_contents($src . '/views/Overview.vue');
check('the stale banner did not stay behind in Overview.vue',
      !str_contains($overviewStub, 'stale'));

/* ── amendment 2C: a malformed 200 is not a fresh refresh ─────────────────── */
check('the api client imports its own get() rather than duplicating fetch',
      substr_count($api, 'fetch(') === 1);
check('useEndpoint guards on an expected top-level key before accepting a refresh',
      str_contains($api, 'health') && str_contains($api, 'disks') && str_contains($api, 'rows'));

echo $fails === 0 ? "frontend: all pass\n" : "frontend: $fails FAILED\n";
exit($fails === 0 ? 0 : 1);
