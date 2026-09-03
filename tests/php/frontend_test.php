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

/* Pins must look at CODE, not at the prose explaining the code - the same
   problem policy_test.php's php_code_only() solves for PHP. .vue/.js source
   has no PHP tokenizer to lean on, so this strips the three comment forms
   actually used in this tree: HTML/Vue-template comments,
   JS/CSS block comments, and JS line comments. Fix round 1 found two checks
   in this file pinned by a comment's prose rather than by the branch it was
   explaining. */
function vue_code_only(string $src): string {
    $src = preg_replace('/<!--.*?-->/s', '', $src);
    $src = preg_replace('#/\*.*?\*/#s', '', $src);
    $src = preg_replace('#(^|[^:])//[^\n]*#m', '$1', $src);
    return $src;
}

/* Cuts a .vue file's template out of it: from the first <template> to the LAST
   </template>. Not a regex ending at the first close - a view that wraps a
   block in <template v-if=...> closes one INSIDE its own template, so the
   first-close version silently truncated the slice at that point and every
   check reading the rest of the template passed vacuously. Two of task 14's
   did, against a view that satisfied them. */
function vue_template(string $code): string {
    $open = strpos($code, '<template>');
    $close = strrpos($code, '</template>');
    if ($open === false || $close === false || $close <= $open) return '';
    return substr($code, $open + 10, $close - $open - 10);
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
$chipTemplate = vue_template($chip);
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
/* tests/js/ssr.mjs stubs ../api.js for the view harnesses, so its stub is a
   second declaration of this contract. dbUnreadable/error/loading are pinned
   below; data and refresh were not, and renaming either would break every view
   while views.mjs stayed green against the stub. */
check('useEndpoint returns the data ref and the refresh function the views bind to',
      (bool) preg_match('/return\s*\{[^}]*data[^}]*refresh[^}]*\}/', $api));

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
/* glob components/, do not name them: NodeCard.vue and NodeDrawer.vue both
   declare `background:` and were exempt while this list named StatusChip
   explicitly, so `background: #1e1e1e` in a card passed the whole suite. */
$vueFiles = array_merge(
    [$src . '/App.vue'],
    glob($src . '/components/*.vue') ?: [],
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

/* ── overview ─────────────────────────────────────────────────────────────── */
$overview = (string) @file_get_contents($src . '/views/Overview.vue');
$card     = (string) @file_get_contents($src . '/components/NodeCard.vue');
$drawer   = (string) @file_get_contents($src . '/components/NodeDrawer.vue');

/* Comment-stripped from here on (fix round 1, item 2): every check below
   reads $overviewCode/$cardCode/$drawerCode or a template slice of one of
   them, never the raw file, so a check can never be satisfied by the prose
   explaining a branch instead of the branch itself. */
$overviewCode = vue_code_only($overview);
$cardCode     = vue_code_only($card);
$drawerCode   = vue_code_only($drawer);
$overviewTemplate = vue_template($overviewCode);
$cardTemplate     = vue_template($cardCode);

check('overview reads the health endpoint', str_contains($overviewCode, "'health'"));
check('overview shows a fleet summary line', str_contains($overviewCode, 'fleet'));
check('overview handles having no nodes at all', str_contains($overviewCode, 'No nodes'));
/* Task 12 moved the stale banner to App.vue so it covers every tab (Controller
   amendment C). Overview.vue must NOT grow a second one - frontend_test.php
   already pins this from the App.vue side ('the stale banner did not stay
   behind in Overview.vue'); this is the same invariant asserted here too. */
check('overview does not render its own stale banner (Task 12 amendment C owns it in App.vue)',
      !str_contains($overviewCode, 'stale'));

/* ── fix round 1, item 3: the checks must pin that a screen exists ───────── */
/* Reading NodeCard.vue/NodeDrawer.vue directly for their own checks proves
   those files contain the right words, never that Overview.vue actually
   composes them. Deleting <NodeCard>, <NodeDrawer> or useLive(refresh) from
   Overview.vue left all fifteen prior checks green. */
check('the overview actually mounts NodeCard and NodeDrawer, not just imports them',
      (bool) preg_match('/<NodeCard\b/', $overviewTemplate)
      && (bool) preg_match('/<NodeDrawer\b/', $overviewTemplate));
check('the overview subscribes to live updates via useLive(refresh)',
      (bool) preg_match('/\buseLive\s*\(\s*refresh\s*\)/', $overviewCode));

/* ── fix round 1 item 6, tightened in round 2: never a blank pane ────────
   Gated on !data ALONE. `loading` flips false the moment the first refresh
   rejects, so "!data && loading" rendered nothing at all from ~t+200ms until
   the 180s banner - which was the symptom item 6 named, still alive after the
   fix that claimed to close it. */
check('the placeholder is not gated on loading, which goes false on failure',
      (bool) preg_match('/v-if="\s*!\s*data\s*"/', $overviewTemplate)
      && !preg_match('/v-if="\s*!\s*data\s*&&\s*loading\s*"/', $overviewTemplate));
check('the overview surfaces the error rather than an eternal Loading',
      (bool) preg_match('/\berror\b/', $overviewTemplate)
      && (bool) preg_match('/const\s*\{[^}]*\berror\b[^}]*\}\s*=\s*useEndpoint/', $overviewCode));

/* ── fix round 1, item 7: an unreadable database must not also print wrong
   fleet-empty advice underneath App.vue's own "could not be read" banner ─── */
check('overview reads loading and dbUnreadable off the same memoised useEndpoint(\'health\') call',
      (bool) preg_match('/\{(?=[^}]*\bloading\b)(?=[^}]*\bdbUnreadable\b)[^}]*\}\s*=\s*useEndpoint\(/', $overviewCode));
check('the fleet summary line is suppressed when the database is unreadable',
      (bool) preg_match('/v-if="\s*fleet\s*&&\s*!\s*dbUnreadable\s*"/', $overviewTemplate));
check('the "no nodes enrolled" empty state is suppressed when the database is unreadable',
      (bool) preg_match('/v-if="\s*data\s*&&\s*!\s*dbUnreadable\s*&&\s*!\s*nodes\.length\s*"/', $overviewTemplate));

check('the card shows capacity as a bar', str_contains($cardCode, 'um-capbar'));
/* fix round 1, item 2: was str_contains($card, 'empty array'), which the
   comment above the branch satisfied on its own - deleting the whole
   v-if="node.array_empty" branch and making percent() return 0 for
   total===0 left this green, i.e. it pinned nothing on Raven's actual
   regression. Bind the label to its condition instead. */
check('an empty array is labelled, not shown as 0%',
      (bool) preg_match('/v-if="node\.array_empty"[\s\S]{0,120}empty array/', $cardTemplate));
check('the card lists the indicators', str_contains($cardCode, 'indicators'));
check('the card shows how long the state has held', str_contains($cardCode, 'since'));

check('the drawer closes on escape', str_contains($drawerCode, 'Escape'));
check('the drawer shows per-domain detail', str_contains($drawerCode, 'domains'));
check('the drawer never asks for a key',
      !preg_match('/\bkey\s*:/', $drawerCode) && !preg_match('/[?&]key=/', $drawerCode));
/* fix round 1, item 8: a hard-failed domain must read as an error, not a
   warning - fleet.js said "Error", the Vue drawer collapsed it into 'warn'. */
check('the drawer shows a hard-failed domain as distinct from a mere warning',
      (bool) preg_match('/domain\.status\s*===\s*[\'"]error[\'"]\s*\?\s*[\'"](?!warn[\'"])\w+[\'"]/', $drawerCode));

/* ── amendment A: unread notification counts, null distinct from zero ────────
   The P0 Fleet tab carried an alert/warning/info column. `null` means "we have
   not heard" and must not look like a confirmed zero. A single unguarded
   template expression (`node.unread?.alert || 0`) would print "0 alert · 0
   warn · 0 info" for both cases - collapsing exactly the distinction the
   amendment exists to preserve. Assert there are two genuinely different
   render paths, not one path with a fallback. */
check('the card renders the unread alert/warning/info breakdown when the payload is present',
      (bool) preg_match('/unread[\s\S]{0,40}\.alert/', $cardCode)
      && (bool) preg_match('/unread[\s\S]{0,40}\.warning/', $cardCode)
      && (bool) preg_match('/unread[\s\S]{0,40}\.info/', $cardCode));
check('the card has a distinct "we have not heard" branch for unread === null, styled unknown',
      (bool) preg_match('/v-if="node\.unread"[\s\S]{0,300}v-else[\s\S]{0,120}um-unknown/', $cardTemplate));
/* The two branches must actually be siblings gated on the same node.unread
   check, not two independent v-ifs that could both fire (which would let a
   null case fall through the "present" branch first and still print zeros). */
check('the present/unknown unread branches are if/else on node.unread, not two independent v-ifs',
      (bool) preg_match('/v-if="node\.unread"[\s\S]{0,300}v-else(?!-if)/', $cardTemplate));

/* ── amendment B: the Unraid API version travels alongside the OS version ──── */
/* fix round 1, item 4: was …node\.unraid…node\.api\b…, satisfied by the
   v-if="node.unraid || node.api" GATE alone - dropping the actual
   "/ API {{ node.api }}" interpolation still passed. Require the
   interpolation itself, not just a mention of node.api anywhere nearby. */
check('the card shows the Unraid API version, not only the OS version',
      (bool) preg_match('/\{\{\s*node\.api\b/', $cardTemplate));

/* ── fix round 1, item 8: the two other minor UI regressions worth pinning ── */
check('the card activates on Space as well as Enter (role="button" requires both)',
      str_contains($cardCode, "@keydown.space"));
check('"last seen never" gets the same um-unknown treatment fleet.js gave it',
      (bool) preg_match('/:class="\{[^}]*um-unknown[^}]*!\s*node\.last_seen[^}]*\}"[^>]*>\s*last seen/', $cardTemplate));

/* == disks (task 14) =====================================================
   Every RENDERED fact this screen carries - the orphan row, 0-vs-unknown
   errors, and the two stale wordings - is asserted against real output in
   tests/js/views.mjs. The checks here pin only what source text can honestly
   pin: that the wiring exists and that the fields named are the ones the
   endpoint actually emits. */
$disks = vue_code_only((string) @file_get_contents($src . '/views/Disks.vue'));
$disksTemplate = vue_template($disks);

check('the disks view reads its endpoint', str_contains($disks, "'disks'"));
check('the table is sortable', str_contains($disks, 'sortBy'));
check('rows filter by node', str_contains($disks, 'nodeFilter'));
check('rows filter by smart status', str_contains($disks, 'smartFilter'));
check('smart status is shown verbatim', str_contains($disks, 'smart_status'));
/* Tier 0 gives OK|UNKNOWN and nothing else, so its cell must still show
   smart_status verbatim rather than a value synthesised to look like one of
   the real (Tier 1) verdicts. Task 5 gave this screen a real `verdict` field
   off the endpoint, so the invariant worth pinning changed from "the word
   verdict never appears" to "a WATCH/FAIL reading is never manufactured from
   smart_status" - the one number a Tier 0 disk still cannot know. */
check('a verdict is never derived from smart_status - it comes from the endpoint field',
      str_contains($disks, 'disk.verdict')
      && !preg_match('/smart_status[\s\S]{0,40}(WATCH|FAIL)/', $disks));
check('a node whose disk payload is not current is labelled', str_contains($disks, 'stale'));
check('spares are listed', str_contains($disks, 'spares'));
check('the tier 0 smart limit is stated to the operator', str_contains($disks, 'Tier 1'));
check('the disks view subscribes to live updates via useLive(refresh)',
      str_contains($disks, 'useLive(refresh)'));

/* Controller amendment A: the endpoint emits `model`; `name` is what joined 0
   of 72 disks on Raven. A view reading disk.name renders an empty column
   against real hardware and a full one against any fixture that invents the
   field, which is exactly how the first cut passed. */
/* The rendered interpolation, not the mere substring: `disk.model === null`
   (the orphan test) satisfies a str_contains on its own, so the Model column
   could render nothing at all - amendment A's exact failure mode - with this
   green. views.mjs asserts the rendered value; this asserts the binding. */
check('disk rows render the model field the endpoint emits',
      (bool) preg_match('/\{\{\s*disk\.model\s*\}\}/', $disksTemplate));
check('no row reads a `name` field disks.php never emits',
      !preg_match('/\b(disk|spare)\.name\b/', $disks));
/* device is unique per node and present on every row including the orphans;
   model repeats across identical drives and is null on exactly those rows. */
/* Anchored on disk., not on node_id/device alone: the spares table's own
   :key satisfied the loose version, so keying the disk rows on the model -
   which repeats across identical drives and is null on every orphan - passed
   (mutation M7). Task 5 moved the key onto the wrapping <template v-for> (the
   row now has a sibling reasons row, and two <tr> cannot share one v-for) and
   through a named rowKey() - the check now follows the key to that function's
   own body rather than expecting the node_id/device pair written out inline. */
check('disk rows key on the node and the device, not on the repeating model',
      (bool) preg_match('/function rowKey[\s\S]{0,60}disk\.node_id[\s\S]{0,20}disk\.device/', $disks)
      && str_contains($disksTemplate, ':key="rowKey(disk)"'));

/* Controller amendment B: an array slot with no physical disk behind it is a
   drive that fell off the bus - the single most important row this screen can
   show. Detected by model === null; the WORD it renders is pinned in
   views.mjs, which is the only place that can see rendered output. */
check('a slot with no disk behind it is detected by model === null',
      (bool) preg_match('/model\s*===\s*null/', $disks));

/* Controller amendment C: a freshly enrolled fleet lists EVERY node as stale
   until its first slow poll lands, up to ten minutes. That wording must not
   read as an error, so the two cases cannot share one sentence - they are
   told apart by fetched_at, which is null only for the never-polled case. */
check('the never-polled and failed-poll stale cases are told apart by fetched_at',
      (bool) preg_match('/v-if="entry\.fetched_at"/', $disksTemplate)
      && str_contains($disksTemplate, 'v-else'));

/* Fix round 1, blocking 2: since Task 4 a single node can emit BOTH a disks
   and a smart stale entry with the same node_id, so keying on node_id alone
   collides. SSR cannot see the collision - it never diffs a patch, so a
   duplicate :key is invisible to it by construction - which is why this has
   to be a source pin rather than something views.mjs can catch on its own. */
check('the stale list keys on node_id AND domain, not node_id alone',
      (bool) preg_match('/:key="entry\.node_id[^"]*entry\.domain"/', $disksTemplate));

/* Task 13, items 6 and 7, applied to this screen: a pane that renders nothing
   while managerd is down, and an empty state that contradicts the shell's
   "database could not be read" banner, were both real defects there. */
check('the disks view never renders a blank pane', str_contains($disksTemplate, '!data'));
check('the disks empty state is suppressed when the database is unreadable',
      str_contains($disksTemplate, 'dbUnreadable'));
check('the disks view reads dbUnreadable off the same memoised useEndpoint call',
      (bool) preg_match('/const\s*\{[^}]*dbUnreadable[^}]*\}\s*=\s*useEndpoint\(\s*.disks.\s*\)/', $disks));
/* The page-wide "these numbers are old" banner belongs to App.vue (Task 12
   amendment C) - this view's per-node stale list is a different statement and
   must not grow a second copy of the shell's. */
check('the disks view does not render a second page-wide stale banner',
      !str_contains($disks, 'um-stale-banner'));

/* == drift (task 15) ===================================================== */
$drift = vue_code_only((string) @file_get_contents($src . '/views/Drift.vue'));
$driftTemplate = vue_template($drift);

check('the drift view reads its endpoint', str_contains($drift, "'drift'"));
check('identical rows collapse by default', str_contains($drift, 'divergent'));
check('the collapse is reversible', str_contains($drift, 'showAll'));
check('absence reads as a word, not a blank', str_contains($drift, 'absent'));
check('the tier 0 plugin-version limit is stated',
      str_contains($drift, 'plugin_versions_available'));
check('the drift view subscribes to live updates via useLive(refresh)',
      str_contains($drift, 'useLive(refresh)'));
/* drift.php distinguishes never-polled (null) from polled-and-absent (false)
   deliberately; a falsy test here would collapse the two back together and
   report a node we have never heard from as one that lacks the plugin. */
check('never-reported is tested against null, not merely falsy',
      (bool) preg_match('/===\s*null/', $drift));
check('the drift view never renders a blank pane', str_contains($driftTemplate, '!data'));
check('the "nothing differs" line is suppressed when the database is unreadable',
      str_contains($driftTemplate, 'dbUnreadable'));
check('the drift view reads dbUnreadable off the same memoised useEndpoint call',
      (bool) preg_match('/const\s*\{[^}]*dbUnreadable[^}]*\}\s*=\s*useEndpoint\(\s*.drift.\s*\)/', $drift));
check('the drift view does not render a second page-wide stale banner',
      !str_contains($drift, 'um-stale-banner'));

/* NodeDrawer fetches on mount, which SSR never runs, so views.mjs cannot
   render it with content - this is the one leg of the timezone plumbing a
   render test cannot reach. Reverting the cell to a raw `fetched_at` left the
   whole suite green (whole-branch review). Pinned here instead, and the reason
   is written down rather than left as a silent gap. */
$drawerCode2 = vue_code_only((string) @file_get_contents($src . '/components/NodeDrawer.vue'));
check('the drawer renders its per-domain timestamp through the shared formatter',
      str_contains($drawerCode2, 'localTime(domain.fetched_at'));
check('the drawer takes the zone and the clock from the shell, not from itself',
      str_contains($drawerCode2, "inject('um-tz'") && str_contains($drawerCode2, "inject('um-clock12'"));

/* P1 triage P2-7: the comparison lives in sort.js so it can be tested at all -
   SSR cannot click a column header. Pinned here so it cannot quietly move back
   inline, where the only possible coverage was a grep for the word sortBy. */
$sortSrc = (string) @file_get_contents($src . '/sort.js');
check('the sort comparison is a module, not an inline closure',
      str_contains($sortSrc, 'export function compareValues'));
check('the disks view uses it rather than its own comparator',
      str_contains($disks, 'sortRows(') && !preg_match('/\.sort\(\(a, b\)/', $disks));

echo $fails === 0 ? "frontend: all pass\n" : "frontend: $fails FAILED\n";
exit($fails === 0 ? 0 : 1);
