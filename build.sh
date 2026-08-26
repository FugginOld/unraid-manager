#!/bin/bash
# Builds unraid-manager.txz from source/.
# Run on Linux (or on the Unraid server) before cutting a GitHub release.
#
# Output: releases/unraid-manager.txz
#
# Usage:
#   bash build.sh [version]
#   bash build.sh 2026.08.25
#
# Unlike the HBAviewer build this one downloads nothing: every byte shipped is
# in this repo. Keep it that way — a fetch here is a supply-chain surface on a
# package that unpacks as root on someone else's server.

set -e

VERSION="${1:-$(date +%Y.%m.%d)}"
OUTPUT="releases/unraid-manager.txz"
PLUGDIR="source/usr/local/emhttp/plugins/unraid-manager"

echo "==> Unraid-Manager build  (version: $VERSION)"

# Refuse to package a key. Enrollment keys live on flash at 0600 and are
# supplied by the operator at run time; one committed by accident would ship to
# every install. Covers loose *.key/*.pem files and anything under a keys/
# directory (.gitignore's other reserved spot for secrets).
if find source \( -name '*.key' -o -name '*.pem' -o -path '*/keys/*' \) -print -quit | grep -q .; then
    echo "ERROR: a .key file is inside source/. Refusing to package a secret."
    exit 1
fi

test -d "$PLUGDIR" || { echo "ERROR: $PLUGDIR missing"; exit 1; }
test -f "$PLUGDIR/scripts/rc.unraid-manager" || echo "WARN: rc script not present yet"

mkdir -p releases
echo "--> Building $OUTPUT..."
cd source
if command -v makepkg &>/dev/null; then
    makepkg -l y -c n "../$OUTPUT"
else
    # makepkg is Slackware-only; CI and dev machines get the plain tar the
    # HBAviewer releases have always actually shipped.
    tar --owner=root --group=root -cJf "../$OUTPUT" .
fi
cd ..

MD5=$(md5sum "$OUTPUT" | awk '{print $1}')
echo "--> MD5: $MD5"
echo ""
echo "Done: $OUTPUT"
echo ""
echo "Next steps (release.yml does all of this for a pushed tag):"
echo "  1. <!ENTITY md5     \"$MD5\"> in unraid-manager.plg"
echo "  2. <!ENTITY version \"$VERSION\"> in unraid-manager.plg"
echo "  3. git tag $VERSION && git push --tags"
