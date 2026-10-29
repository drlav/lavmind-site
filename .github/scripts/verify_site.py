"""Validate reviewed static output; optionally verify its live HTTPS bytes."""

import argparse
import hashlib
import json
import re
import stat
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

BASE = 'https://varnazov.com'
ROOT = Path('site')


def fail(reason):
    raise SystemExit(reason)


class Document(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.ids = set()
        self.canonical = None
        self.logos = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ('iframe', 'object', 'embed', 'form'):
            fail('unexpected_active_content')
        if tag == 'script' and attrs.get('type') != 'application/ld+json':
            fail('executable_script_not_allowed')
        if any(key.lower().startswith('on') for key in attrs):
            fail('inline_event_handler_not_allowed')
        if 'id' in attrs:
            self.ids.add(attrs['id'])
        if tag == 'link' and attrs.get('rel') == 'canonical':
            self.canonical = attrs.get('href')
        for key in ('href', 'src'):
            if key in attrs:
                self.links.append(attrs[key])
        if tag == 'img' and 'brand-mark' in attrs.get('class', '').split():
            self.logos.append(attrs.get('src'))


def validate():
    manifest = json.loads(Path('.github/site-manifest.json').read_text())
    if ROOT.is_symlink() or not ROOT.is_dir() or not 1 <= len(manifest) <= 1000:
        fail('invalid_site_or_manifest')
    files = {}
    for path in ROOT.rglob('*'):
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            fail('nonregular_site_file')
        files[path.relative_to(ROOT).as_posix()] = path.read_bytes()
    actual = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    if actual != manifest:
        fail('reviewed_site_manifest_mismatch')
    if sum(map(len, files.values())) > 2_000_000:
        fail('site_size_budget_exceeded')
    if files.get('CNAME') != b'varnazov.com\n':
        fail('wrong_domain')
    documents = {}
    for name, data in files.items():
        if not name.endswith('.html'):
            continue
        doc = Document()
        doc.feed(data.decode('utf-8'))
        expected_path = '/' + name.removesuffix('index.html')
        if name == 'mind/404.html':
            expected_path = '/404.html'
        if doc.canonical != BASE + expected_path:
            fail('incorrect_canonical_' + name)
        if not doc.logos or any('brand-mark.a3c49823f5a9727a.png' not in logo for logo in doc.logos):
            fail('original_brand_mark_required')
        if any(value in data for value in (b'#173d35', b'#f6f4ee', b'#daed72', b'/lavmind-site/')):
            fail('retired_design_or_project_path')
        documents[name] = doc
    for name, doc in documents.items():
        page_path = '/' + name.removesuffix('index.html')
        for href in doc.links:
            parsed = urllib.parse.urlsplit(urllib.parse.urljoin(BASE + page_path, href))
            if parsed.scheme == 'mailto':
                if parsed.path != 'hello@varnazov.com':
                    fail('unexpected_email')
                continue
            if parsed.scheme != 'https':
                fail('unexpected_link_scheme')
            if parsed.netloc != 'varnazov.com':
                if parsed.netloc not in ('t.me',):
                    fail('unexpected_external_origin')
                continue
            dest = parsed.path.lstrip('/')
            if parsed.path.endswith('/'):
                dest += 'index.html'
            if dest not in files:
                fail('broken_internal_link_' + dest)
            if parsed.fragment and (dest not in documents or parsed.fragment not in documents[dest].ids):
                fail('broken_fragment_' + dest)
    css = next(data.decode() for name, data in files.items() if name.endswith('.css'))
    for color in ('#2563eb', '#0f172a', '#64748b', '#cbd5e1', '#ffffff'):
        if color not in css.lower():
            fail('brand_palette_mismatch')
    for asset in re.findall(r'url\([\'"]?(/[^\s\)\'\"]+)', css):
        if asset.lstrip('/') not in files:
            fail('missing_css_asset')
    print(json.dumps({'files': len(files), 'manifest_exact': True, 'brand': 'original', 'internal_links': 'valid'}))
    return manifest


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def get(url):
    request = urllib.request.Request(url, headers={'Cache-Control': 'no-cache', 'User-Agent': 'LAV-brand-verifier'})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=15) as response:
            return response.status, response.headers, response.read(2_000_001)
    except urllib.error.HTTPError as error:
        return error.code, error.headers, error.read(2_000_001)


def verify_live(manifest):
    remaining = {name: digest for name, digest in manifest.items() if name != 'CNAME'}
    for attempt in range(10):
        for name, digest in list(remaining.items()):
            path = '/' + name.removesuffix('index.html')
            status, headers, data = get(BASE + path)
            if status == 200 and hashlib.sha256(data).hexdigest() == digest:
                print(json.dumps({'path': path, 'status': status, 'sha256_exact': True}))
                del remaining[name]
        if not remaining:
            break
        time.sleep(6)
    if remaining:
        fail('live_version_unconfirmed_no_automatic_redeploy: ' + ','.join(remaining))
    status, headers, _ = get('https://www.varnazov.com/')
    if status not in (301, 308) or headers.get('Location') != BASE + '/':
        fail('www_redirect_mismatch')
    status, _, body = get(BASE + '/mind/LAV-9999/')
    if status != 404 or hashlib.sha256(body).hexdigest() != manifest['404.html']:
        fail('custom_404_mismatch')
    print(json.dumps({'live_brand_verified': True, 'https': True, 'www_redirect': True, 'custom_404': True}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--archive', type=Path)
    args = parser.parse_args()
    result = validate()
    if args.archive:
        # Explicit verified manifest retains .well-known/security.txt, unlike wildcard exclusion.
        with tarfile.open(args.archive, 'w', format=tarfile.USTAR_FORMAT) as archive:
            for name in sorted(result):
                archive.add(ROOT / name, arcname=name, recursive=False)
        print('validated_pages_archive_created')
    if args.live:
        verify_live(result)
