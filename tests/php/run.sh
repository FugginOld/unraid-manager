#!/bin/bash
# Runs every PHP test in this directory. One non-zero exit fails the suite.
#   bash tests/php/run.sh
set -u
cd "$(dirname "$0")/../.." || exit 1
command -v php >/dev/null || { echo "php not on PATH"; exit 2; }
fails=0
for t in tests/php/*_test.php; do
    [ -e "$t" ] || continue
    echo "--- $t"
    out=$(php -f "$t" 2>&1)
    code=$?
    echo "$out"
    if [ "$code" -ne 0 ]; then
        fails=$((fails + 1))
    elif ! grep -q ': all pass$' <<< "$out"; then
        echo "!!! $t exited 0 without ever printing 'all pass' - treating as FAILED"
        fails=$((fails + 1))
    elif grep -qE '^(PHP )?(Warning|Notice|Deprecated|Fatal error|Parse error):' <<< "$out"; then
        # A guard whose only symptom is a diagnostic is otherwise unpinnable:
        # remove it, the output is identical, the file still prints 'all pass'
        # and exits 0. That is how an unguarded foreach shipped once already.
        echo "!!! $t printed a PHP diagnostic - treating as FAILED"
        fails=$((fails + 1))
    fi
done

# Proofs of behaviour no PHP source-text check can see:
#   live_singleton.mjs - live.js's singleton/refcount/mount-fetch behaviour
#   node_card.mjs      - NodeCard.vue's rendered output (null-vs-zero,
#                        empty-array-vs-0%, unknown-vs-failed)
#   views.mjs          - Disks.vue and Drift.vue's rendered output (an array
#                        slot with no disk behind it, 0-vs-unknown errors,
#                        never-polled vs failed-poll, absent vs unreported)
#   interact.mjs       - what those two views DO when clicked: the sort
#                        headers, both Disks filters, Drift's collapse toggle
# The first three cost no dependency beyond what `vue` already pulls in;
# interact.mjs needs happy-dom, because Vue's client runtime resolves
# `document` at load and there is no DOM-free way to dispatch a click at a
# mounted component. Dev-only - `vite build` never sees tests/.
# Guarded the way build.sh guards npm: skipped where node is absent. That guard
# is for a dev box without node, NEVER for CI - node being on PATH is not
# enough, these import out of frontend/node_modules, so the php job installs
# them (.github/workflows/tests.yml). It went red on 9a29279 and 95b67b0
# because it did not: the guard fired and every one of them aborted with
# ERR_MODULE_NOT_FOUND while every local run stayed green.
if command -v node >/dev/null; then
    for t in tests/js/live_singleton.mjs tests/js/node_card.mjs tests/js/views.mjs tests/js/interact.mjs; do
        echo "--- $t"
        out=$(node "$t" 2>&1)
        code=$?
        echo "$out"
        if [ "$code" -ne 0 ]; then
            fails=$((fails + 1))
        elif ! grep -q ': all pass$' <<< "$out"; then
            echo "!!! $t exited 0 without ever printing 'all pass' - treating as FAILED"
            fails=$((fails + 1))
        fi
    done
else
    echo "!!! node not on PATH - skipping tests/js/*.mjs (mandatory in CI)"
fi

if [ "$fails" -ne 0 ]; then
    echo "php suite: $fails file(s) FAILED"
    exit 1
fi
echo "php suite: all pass"
