"""Package exact validated files using the GitHub Pages GNU tar convention."""
import hashlib
import subprocess
import sys
import tarfile
from pathlib import Path

from verify_site import validate

manifest = validate()
archive_path = Path(sys.argv[1]).resolve()
subprocess.run([
    'tar', '--dereference', '--hard-dereference', '--directory', 'site',
    '-cf', str(archive_path), '.',
], check=True)
# Keep the standard ./ directory layout; preserve the required .well-known entry.
with tarfile.open(archive_path, 'r:') as archive:
    actual = {}
    for member in archive.getmembers():
        if member.isdir():
            continue
        if not member.isfile() or not member.name.startswith('./'):
            raise SystemExit('unexpected_pages_archive_member')
        name = member.name[2:]
        if name in actual or name not in manifest:
            raise SystemExit('unexpected_or_duplicate_pages_file')
        stream = archive.extractfile(member)
        if stream is None:
            raise SystemExit('missing_archive_member_bytes')
        actual[name] = hashlib.sha256(stream.read()).hexdigest()
    if actual != manifest:
        raise SystemExit('pages_archive_manifest_mismatch')
print('standard_pages_archive_verified files=24 security_txt=present')
