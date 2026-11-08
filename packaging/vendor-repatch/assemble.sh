#!/usr/bin/env bash
# assemble.sh — the release job of build.yml (v0.3.0-dev1), locally: unchanged guest/host assets from CI run
# 40747544254, the native bundle from the local build, our vendor image patched onto CI's, then a PKGBUILD
# with local sources.   usage: assemble.sh TAG NATIVE_RELEASE_DIR REPO_WORKTREE
set -euo pipefail
TAG=$1 NATIVE=$(realpath "$2") REPO=$(realpath "$3")
HERE=$(cd "$(dirname "$0")/.." && pwd)
CI=$HERE/ci-40747544254
OUT=$HERE/release-$TAG
rm -rf "$OUT" "$HERE/stage" && mkdir -p "$OUT" "$HERE/stage" && cd "$HERE/stage"

# host + guest + prebuilts: byte-for-byte the CI artifacts, packed as the release job does
mkdir -p host && cp "$CI"/host-venus-server/* host/
chmod 0755 host/virgl_test_server host/virgl_render_server host/libvirglrenderer.so.1
tar --zstd -C host -cf "$OUT/waydroid-nvidia-host-x86_64-$TAG.tar.zst" .
mkdir -p guest
for a in guest-vulkan-virtio-x86 guest-vulkan-virtio-x86_64 guest-gralloc; do cp -a "$CI/$a"/. guest/; done
tar --zstd -C guest -cf "$OUT/waydroid-nvidia-guest-android-x86_64-$TAG.tar.zst" .
mkdir -p prebuilts
for a in guest-hwcomposer guest-angle guest-surfaceflinger; do cp -a "$CI/$a"/. prebuilts/; done
chmod 0755 prebuilts/system/bin/surfaceflinger
(cd prebuilts && find vendor system -type f -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS.prebuilts)
printf 'CI-built guest prebuilts from Shiro836/waydroid-nvidia run 40747544254 (v0.3.0-dev1), unchanged.\n' > prebuilts/README.txt
tar --zstd -C prebuilts -cf "$OUT/waydroid-nvidia-guest-prebuilts-$TAG.tar.zst" .

# native: the local build's bundle, checked against its own BUILD-RECORD.json like the release job does
mkdir -p native
cp -a "$NATIVE/payload" "$NATIVE/gate" "$NATIVE/glibc-shim" "$NATIVE/driver" "$NATIVE/BUILD-RECORD.json" native/
chmod 0755 native/gate/bin/*/* native/payload/odm/bin/hw/*
python3 -I - native <<'PY'
import hashlib, json, pathlib, sys
b = pathlib.Path(sys.argv[1]); r = json.loads((b / 'BUILD-RECORD.json').read_text())
have = {str(p.relative_to(b)) for t in ('payload', 'gate') for p in (b / t).rglob('*') if p.is_file()}
assert r['format'] == 2 and have == set(r['files']), 'bundle differs from BUILD-RECORD.json'
assert all(hashlib.sha256((b / f).read_bytes()).hexdigest() == d for f, d in r['files'].items())
for key, tree in (('glibc_shim_files', 'glibc-shim'), ('driver_files', 'driver')):
    files = r[key]
    assert files and {str(p.relative_to(b)) for p in (b / tree).rglob('*') if p.is_file()} == set(files), tree
    assert all(hashlib.sha256((b / f).read_bytes()).hexdigest() == d for f, d in files.items())
print('native bundle matches its BUILD-RECORD.json:', len(r['files']), 'files, driver', r.get('driver'))
PY
(cd native && find payload gate glibc-shim driver -type f -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS.native)
tar --zstd -C native -cf "$OUT/waydroid-nvidia-native-x86_64-$TAG.tar.zst" .

# vendor image: CI's lineage-20 build with the new bundle's binaries and identity props
python3 -I "$HERE/tools/patch-vendor-img.py" "$CI/vendor-image-lineage-20-nvidia-prolapse/vendor.img" native \
    vendor.img "$TAG"
python3 -I "$REPO/packaging/vendor-ota.py" vendor.img native/BUILD-RECORD.json "$OUT" --tag "$TAG" \
    --repo YX14ng/waydroid-nvidia

# source tarball of the worktree (prefix = what the PKGBUILD's ${_tag#v} names)
git -C "$REPO" archive --format=tar.gz --prefix="waydroid-nvidia-${TAG#v}/" -o "$OUT/waydroid-nvidia-${TAG#v}.tar.gz" HEAD
(cd "$OUT" && sha256sum ./* > SHA256SUMS && cat SHA256SUMS)
rm -rf "$HERE/stage"
