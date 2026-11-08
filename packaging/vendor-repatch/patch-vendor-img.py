#!/usr/bin/env python3
"""Rebuild the nvidia-prolapse vendor.img for a new native bundle without a Lineage tree.

usage: patch-vendor-img.py <base vendor.img> <native bundle dir> <out vendor.img> <version tag>

The Lineage build copies the bundle binaries verbatim (vendor_image.py IMAGE_PREBUILTS: the installer checks
them byte for byte). This swaps exactly those files for the new bundle's, keeping each inode's mode, owner and
SELinux label, and rewrites the two identity props in /build.prop (build_record = sha256 of the new
BUILD-RECORD.json, version). Grows the ext4 if the new files do not fit. Rootless: debugfs -w on a copy.
"""
import hashlib, os, re, shutil, subprocess, sys, tempfile
from pathlib import Path

base, bundle, out, tag = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4]
sys.path.insert(0, str(Path(__file__).resolve().parent))
REPO = Path(os.environ.get('WNV_REPO', Path.home() / 'Projects/waydroid-nvidia-620'))

# IMAGE_PREBUILTS, read from the installer itself (single source of truth)
src = (REPO / 'packaging/native/vendor_image.py').read_text()
block = src[src.index('IMAGE_PREBUILTS = {}'):src.index("    '/odm/bin/hw/android.hardware.media.c2@1.2-service-nvprolapse'") + 70]
ns = {}
exec(block.split('\n\n')[0], ns)
PREBUILTS = ns['IMAGE_PREBUILTS']
assert len(PREBUILTS) == 19, PREBUILTS


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def dbg(img, cmd, write=False):
    args = ['debugfs'] + (['-w'] if write else []) + ['-R', cmd, str(img)]
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout


def stat(img, path):
    s = dbg(img, f'stat {path}')
    mode = int(re.search(r'Mode:\s+(\d+)', s).group(1), 8)
    uid = int(re.search(r'User:\s+(\d+)', s).group(1))
    gid = int(re.search(r'Group:\s+(\d+)', s).group(1))
    size = int(re.search(r'Size:\s+(\d+)', s).group(1))
    return mode, uid, gid, size


shutil.copyfile(base, out)
work = Path(tempfile.mkdtemp(prefix='vimg-'))
new_record = bundle / 'BUILD-RECORD.json'
record_sha = sha(new_record)

# space check: grow by the size delta + 16 MiB headroom if needed
delta = 0
for rel, inside in PREBUILTS.items():
    delta += (bundle / rel).stat().st_size - stat(out, inside)[3]
free = int(re.search(r'Free blocks:\s+(\d+)', subprocess.run(['dumpe2fs', '-h', str(out)], capture_output=True,
                                                                  text=True).stdout).group(1)) * 4096
if delta + (4 << 20) > free:
    grow = delta + (16 << 20)
    blocks = int(re.search(r'Block count:\s+(\d+)', subprocess.run(['dumpe2fs', '-h', str(out)], capture_output=True,
                                                                    text=True).stdout).group(1))
    newblocks = blocks + (grow + 4095) // 4096
    os.truncate(out, newblocks * 4096)
    subprocess.run(['e2fsck', '-fy', str(out)], capture_output=True)
    subprocess.run(['resize2fs', str(out), str(newblocks)], check=True, capture_output=True)
    print(f'grew the image by {grow >> 20} MiB (size delta {delta} bytes, free was {free})')

files = dict((inside, bundle / rel) for rel, inside in PREBUILTS.items())

# build.prop: identity props
bp = work / 'build.prop'
dbg(out, f'dump /build.prop {bp}')
text = bp.read_text()
for key, value in (('ro.vendor.nvidia_prolapse.build_record', record_sha), ('ro.vendor.nvidia_prolapse.version', tag)):
    text, n = re.subn(rf'^{re.escape(key)}=.*$', f'{key}={value}', text, flags=re.M)
    assert n == 1, key
bp.write_text(text)
files['/build.prop'] = bp

for inside, new in sorted(files.items()):
    mode, uid, gid, _ = stat(out, inside)
    label = work / 'label'
    dbg(out, f'ea_get -f {label} {inside} security.selinux')
    assert label.read_bytes().startswith(b'u:object_r:'), (inside, label.read_bytes())
    d, name = os.path.split(inside)
    cmds = work / 'cmds'
    cmds.write_text('\n'.join([
        f'cd {d or "/"}', f'rm {name}', f'write {new} {name}',
        f'sif {name} mode 0{0o100000 | mode:o}', f'sif {name} uid {uid}', f'sif {name} gid {gid}',
        f'ea_set -f {label} {name} security.selinux', '']))
    subprocess.run(['debugfs', '-w', '-f', str(cmds), str(out)], capture_output=True, text=True, check=True)
    got = work / 'check'
    dbg(out, f'dump {inside} {got}')
    assert sha(got) == sha(new), inside
    assert stat(out, inside)[:3] == (mode, uid, gid), inside
    dbg(out, f'ea_get -f {got} {inside} security.selinux')
    assert got.read_bytes() == label.read_bytes(), inside
    print(f'{inside}: replaced ({mode:o} {uid}:{gid}, {label.read_bytes().rstrip(bytes(1)).decode()})')

fsck = subprocess.run(['e2fsck', '-fn', str(out)], capture_output=True, text=True)
assert fsck.returncode == 0, fsck.stdout + fsck.stderr
shutil.rmtree(work)
print(f'{out}: e2fsck clean; build_record={record_sha}; version={tag}')
