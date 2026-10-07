#!/usr/bin/env python3
"""Real ffmpeg/API acceptance 3, 4, 7, 8. Only creates its own test project.

REELS_ROOT and REELS_EDITOR_PORT must match the running local server.
Optional REELS_EDITOR_HOST, REELS_EDITOR_TOKEN, REELS_EDITOR_PID (RSS sampling).
The 600 MiB source and exported package remain recoverable in .trash afterwards.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
import zipfile

ROOT = Path(os.environ['REELS_ROOT']).expanduser().resolve()
HOST = os.environ.get('REELS_EDITOR_HOST', '127.0.0.1')
BASE = 'http://%s:%s' % (HOST, os.environ.get('REELS_EDITOR_PORT', '8091'))
TOKEN = os.environ.get('REELS_EDITOR_TOKEN', '')
slug = None
failures = []


def request(path, method='GET', data=None, content_type=None, expected=200, raw=False):
    headers = {'X-Editor-Token': TOKEN}
    if data is not None and not isinstance(data, bytes):
        data = json.dumps(data).encode()
        content_type = 'application/json'
    if content_type:
        headers['Content-Type'] = content_type
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        response = urllib.request.urlopen(req, timeout=600)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        body = response.read()
        assert response.status == expected, (method, path, response.status, body[:1000])
        return body if raw else json.loads(body)


def upload(path, file, field='file'):
    boundary = uuid.uuid4().hex
    body = ('--%s\r\nContent-Disposition: form-data; name="%s"; filename="%s"\r\n'
            'Content-Type: application/octet-stream\r\n\r\n' % (boundary, field, file.name)).encode()
    body += file.read_bytes() + ('\r\n--%s--\r\n' % boundary).encode()
    return request(path, 'POST', body, 'multipart/form-data; boundary=' + boundary, expected=201 if field == 'files' else 200)


def api(tail=''):
    return '/api/projects/' + slug + tail


def save(p):
    return request(api(), 'PUT', p)['project']


def report(number, fn):
    try:
        detail = fn()
        print('PASS %s: %s' % (number, detail), flush=True)
    except Exception as exc:
        failures.append(number)
        print('FAIL %s: %s' % (number, exc), flush=True)


def fixture(path, second=False):
    colors = ['red', 'blue', 'white', 'black'] if not second else ['green', 'magenta', 'black', 'white']
    cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y']
    for color, duration in zip(colors, [2, 5, 9, 3]):
        cmd += ['-f', 'lavfi', '-i', 'color=c=%s:s=270x480:r=25:d=%s' % (color, duration)]
    cmd += ['-filter_complex', '[0:v][1:v][2:v][3:v]concat=n=4:v=1:a=0[v]', '-map', '[v]', '-c:v', 'mpeg4', '-q:v', '3', str(path)]
    subprocess.run(cmd, check=True)


def autocut_checks():
    p = request(api())
    pattern = {'id': 'check-2b', 'name': 'check-2b', 'shots': [
        {'seconds': n, 'role': role} for n, role in [(1, 'hook'), (1.5, ''), (2, ''), (.8, ''), (1, ''), (1, 'cta')]]}
    p['pattern'] = pattern
    p = save(p)
    rev = p['rev']
    started = time.monotonic()
    first = request(api('/autocut'), 'POST', {})
    elapsed = time.monotonic() - started
    cached_files = list((ROOT / 'projects' / slug / 'analysis').glob('*/cuts-0.3.json'))
    assert len(cached_files) == 2, cached_files
    mtimes = {str(f): f.stat().st_mtime_ns for f in cached_files}
    started = time.monotonic()
    second = request(api('/autocut'), 'POST', {})
    cached_elapsed = time.monotonic() - started
    assert mtimes == {str(f): f.stat().st_mtime_ns for f in cached_files}
    assert request(api())['rev'] == rev
    assert not first['skipped_sources'], first['skipped_sources']
    cursors = {'s1': 0., 's2': 0.}
    for i, shot in enumerate(first['scenes']):
        sid = 's%d' % (i % 2 + 1)
        assert shot['source'] == sid
        # Known hard cuts in the generated sources: 2, 7 and 16 seconds.
        candidate = next((x for x in [2., 7., 16.] if x >= cursors[sid]), cursors[sid])
        start = candidate + .1
        if start + .5 > 19:
            start = 2.1
        assert abs(shot['in'] - start) <= .11, (shot, start)
        expected_length = min(pattern['shots'][i]['seconds'], 19 - shot['in'])
        assert abs(shot['out'] - shot['in'] - expected_length) < .001
        assert shot['reason'] == pattern['shots'][i]['role']
        cursors[sid] = shot['out'] + 1
    assert len(second['scenes']) == 6
    p['scenes'] = first['scenes']
    save(p)
    return 's1/s2 alternate; cuts +0.1, lengths, roles verified; cold %.3fs / cache %.3fs; cache mtime and rev unchanged' % (elapsed, cached_elapsed)


def locked_checks():
    p = request(api())
    p['scenes'][1]['locked'] = True
    p = save(p)
    result = request(api('/autocut'), 'POST', {})
    assert result['scenes'][1] == p['scenes'][1]
    for i in [0, 2, 3, 4, 5]:
        assert result['scenes'][i]['id'] != p['scenes'][i]['id']
    assert request(api())['rev'] == p['rev']
    return 'locked shot 2 exact object/index retained; other IDs renewed; rev unchanged'


def frame_checks():
    first = request(api('/sources/s1/frame?t=1'), raw=True)
    later = request(api('/sources/s1/frame?t=3'), raw=True)
    assert first[:2] == later[:2] == b'\xff\xd8'
    assert hashlib.sha256(first).digest() != hashlib.sha256(later).digest()
    folder = ROOT / 'projects' / slug / 'analysis' / 's1' / 'frames'
    mtimes = {f.name: f.stat().st_mtime_ns for f in folder.glob('*.jpg')}
    assert len(mtimes) >= 2
    assert request(api('/sources/s1/frame?t=1'), raw=True) == first
    assert mtimes == {f.name: f.stat().st_mtime_ns for f in folder.glob('*.jpg')}
    return '1s / 3s JPEG hashes differ; repeated frame bytes and cache mtimes unchanged'


def export_checks(work):
    # Upload an unused third source to check package filtering.
    upload(api('/sources'), work / 'first.mp4', 'files')
    png = work / 'brand.png'
    png.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII='))
    upload(api('/logo'), png)
    p = request(api())
    request(api('/export'), 'POST', {'rev': p['rev'] - 1}, expected=409)
    # Extend our own valid MP4 with an ISO BMFF free box. Sparse padding avoids
    # generating 600 MiB of video; zip must still stream every uncompressed byte.
    source = ROOT / 'projects' / slug / p['sources'][0]['file']
    padding = 600 * 1024 * 1024 - source.stat().st_size
    with source.open('ab') as f:
        f.write(struct.pack('>I4s', padding, b'free'))
        f.truncate(600 * 1024 * 1024)
    pid = os.environ.get('REELS_EDITOR_PID')
    samples = []
    stop = threading.Event()
    def sample():
        while not stop.is_set():
            try:
                rss = subprocess.check_output(['ps', '-o', 'rss=', '-p', pid], text=True)
                samples.append(int(rss.strip()))
            except (ValueError, subprocess.CalledProcessError):
                break
            stop.wait(.05)
    worker = threading.Thread(target=sample) if pid else None
    if worker:
        worker.start()
        time.sleep(.1)
    try:
        result = request(api('/export'), 'POST', {'rev': p['rev']})
    finally:
        stop.set()
        if worker:
            worker.join()
    archive = ROOT / 'projects' / slug / 'exports' / result['name']
    assert result['size'] == archive.stat().st_size
    assert request(api())['rev'] == p['rev']
    with zipfile.ZipFile(archive) as z:
        edit = json.loads(z.read('edit.json'))
        expected = {'goal', 'goal_type', 'target_seconds', 'platform', 'pattern', 'logo', 'sources', 'shots'}
        assert set(edit) == expected, edit.keys()
        assert set(edit['pattern']) == {'id', 'name', 'shots'}
        assert set(edit['shots'][0]) == {'source', 'in', 'out', 'role', 'subtitle', 'keyframe'}
        assert edit['platform'] == 'ig_reels_1080x1920'
        used = {s['source'] for s in p['scenes']}
        assert {s['id'] for s in edit['sources']} == used == {'s1', 's2'}
        expected_names = {'edit.json', p['logo']['file']}
        expected_names.update(s['file'] for s in edit['sources'])
        expected_names.update('keyframes/shot-%02d.jpg' % (i + 1) for i in range(len(p['scenes'])))
        assert set(z.namelist()) == expected_names
        assert all(i.compress_type == zipfile.ZIP_STORED for i in z.infolist())
        for shot in edit['shots']:
            assert z.read(shot['keyframe'])[:2] == b'\xff\xd8'
    print('schema top-level:', json.dumps(sorted(edit)))
    print('schema shots[0]:', json.dumps(sorted(edit['shots'][0])))
    print('schema pattern:', json.dumps(sorted(edit['pattern'])))
    listing = request(api('/exports'))
    # Download to disk without reading the large HTTP response into memory.
    req = urllib.request.Request(BASE + api('/exports/' + result['name']), headers={'X-Editor-Token': TOKEN})
    count = 0
    with urllib.request.urlopen(req, timeout=600) as response:
        while True:
            block = response.read(1024 * 1024)
            if not block:
                break
            count += len(block)
    assert count == result['size']
    if samples:
        delta = max(samples) - samples[0]
        print('RSS server baseline=%d KiB peak=%d KiB delta=%d KiB (sampled 50ms)' % (samples[0], max(samples), delta))
        assert delta < 128 * 1024, '600 MiB export RSS grew >=128 MiB'
    else:
        print('UNVERIFIED RSS: set REELS_EDITOR_PID to the running Python server PID; ZIP_STORED streaming checked structurally')
    return '600 MiB source ZIP_STORED; exact schema/layout; unused s3 absent; all keyframes JPEG; old rev 409; rev unchanged; download %d bytes' % count


def main():
    global slug
    with tempfile.TemporaryDirectory(prefix='check-2b-') as tmp:
        work = Path(tmp)
        fixture(work / 'first.mp4')
        fixture(work / 'second.mp4', True)
        result = request('/api/projects', 'POST', {'name': 'check2b-' + uuid.uuid4().hex[:12]}, expected=201)
        slug = result['slug']
        assert (ROOT / 'projects' / slug / 'project.json').is_file(), 'REELS_ROOT does not match server'
        try:
            upload(api('/sources'), work / 'first.mp4', 'files')
            upload(api('/sources'), work / 'second.mp4', 'files')
            report(3, autocut_checks)
            report(4, locked_checks)
            report(8, frame_checks)
            report(7, lambda: export_checks(work))
        finally:
            result = request(api(), 'DELETE')
            print('Cleanup: test project moved to .trash:', result)
    if failures:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
