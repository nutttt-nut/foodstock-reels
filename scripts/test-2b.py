#!/usr/bin/env python3
"""Wizard API + 2A regressions with mocked ffmpeg/ffprobe (no media binaries).
Run: python3 scripts/test-2b.py
Optional REELS_TEST_SERVER_ROOT points at a different checkout's server.py.
Uses only temporary storage. Real media acceptance: scripts/check-2b.sh.
"""
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

SERVER_ROOT = Path(os.environ.get('REELS_TEST_SERVER_ROOT', Path(__file__).resolve().parents[1]))
spec = importlib.util.spec_from_file_location('reels_server_under_test', SERVER_ROOT / 'server.py')
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class WizardAPI(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='reels-api-check-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.calls = []
        self.slug = None
        self.fail_cuts = False
        self.patches = [patch.object(server, 'REELS_ROOT', self.root),
                        patch.object(server, 'PROJECTS', self.root / 'projects'),
                        patch.object(server, 'TRASH', self.root / '.trash'),
                        patch.object(server, 'run', self.fake_run),
                        patch.dict(os.environ, {'REELS_EDITOR_TOKEN': ''})]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        server.app.config['TESTING'] = True
        self.client = server.app.test_client()
        original_write = server.write_project
        def checked_project_write(folder, project):
            lock = server.project_lock(folder.name)
            acquired = lock.acquire(blocking=False)
            if acquired:
                lock.release()
            self.assertFalse(acquired, 'project.json writer did not hold project_lock')
            return original_write(folder, project)
        writer = patch.object(server, 'write_project', checked_project_write)
        writer.start()
        self.addCleanup(writer.stop)
        self.addCleanup(self.client.__exit__, None, None, None)
        self.p = self.call('post', '/api/projects', {'name': 'ทดสอบ'}, 201)['project']
        self.slug = 'ทดสอบ'
        self.folder = self.root / 'projects' / self.slug

    def assert_unlocked(self):
        if self.slug:
            lock = server.project_lock(self.slug)
            acquired = lock.acquire(blocking=False)
            self.assertTrue(acquired, 'long ffmpeg/zip work holds project_lock')
            if acquired:
                lock.release()

    def fake_run(self, cmd, cwd=None):
        self.assert_unlocked()
        self.calls.append(cmd)
        if cmd[0] == 'ffprobe':
            return subprocess.CompletedProcess(cmd, 0, json.dumps({'format': {'duration': '19'}, 'streams': [{'width': 270, 'height': 480}]}), '')
        if 'showinfo' in ' '.join(cmd):
            if self.fail_cuts:
                raise subprocess.CalledProcessError(1, cmd, stderr='mock decode failure')
            return subprocess.CompletedProcess(cmd, 0, '', 'pts_time:2 pts_time:7 pts_time:16')
        if cmd[0] == 'ffmpeg':
            Path(cmd[-1]).write_bytes(b'\xff\xd8' + str(cmd).encode() + b'\xff\xd9')
            return subprocess.CompletedProcess(cmd, 0, '', '')
        raise AssertionError('unexpected subprocess: ' + repr(cmd))

    def call(self, method, path='', data=None, code=200):
        url = path if path.startswith('/api/') else '/api/projects/' + self.slug + path
        kwargs = {'json': data} if data is not None else {}
        with getattr(self.client, method)(url, **kwargs) as response:
            self.assertEqual(response.status_code, code, response.get_data(as_text=True))
            return response.get_json()

    def upload(self, filename='one.mov', content=b'video', logo=False, code=None):
        with self.client.post('/api/projects/' + self.slug + ('/logo' if logo else '/sources'),
                              data={'file' if logo else 'files': (io.BytesIO(content), filename)}) as response:
            self.assertEqual(response.status_code, code or (200 if logo else 201), response.get_data(as_text=True))
            result = response.get_json()
            response.request.input_stream.close()
            return result

    def save(self, **changes):
        p = self.call('get')
        p.update(changes)
        return self.call('put', data=p)['project']

    def pattern(self):
        return {'id': 'example', 'name': 'ตัวอย่าง', 'description': 'placeholder', 'sample': True,
                'shots': [{'seconds': 1, 'role': 'hook'}, {'seconds': 1.5}, {'seconds': 2}, {'seconds': .8, 'role': 'cta'}]}

    def cut(self):
        self.upload()
        self.upload('two.mp4')
        self.save(pattern=self.pattern())
        result = self.call('post', '/autocut', {})
        self.save(scenes=result['scenes'])
        return result

    def test_02_patterns_fallback_custom_and_malformed(self):
        data = self.call('get', '/api/patterns')
        self.assertEqual(data['source'], 'patterns-sample')
        self.assertEqual([p['name'] for p in data['patterns']], ['ตัวอย่าง 1', 'ตัวอย่าง 2', 'ตัวอย่าง 3'])
        self.assertTrue(all(p['sample'] for p in data['patterns']))
        for pattern in data['patterns']:
            self.assertTrue(6 <= len(pattern['shots']) <= 10)
            self.assertTrue(all(.8 <= s['seconds'] <= 3 for s in pattern['shots']))
        folder = self.root / 'patterns'
        folder.mkdir()
        self.assertEqual(self.call('get', '/api/patterns')['source'], 'patterns-sample')
        (folder / 'example.json').write_text(json.dumps(self.pattern()))
        (folder / 'broken.json').write_text('{')
        (folder / 'goal-types.json').write_text('["test"]')
        data = self.call('get', '/api/patterns')
        self.assertEqual(data['source'], 'REELS_ROOT/patterns')
        self.assertEqual(len(data['patterns']), 1)
        self.assertEqual(data['errors'][0]['file'], 'broken.json')
        self.assertEqual(data['goal_types'], ['test'])

    def test_03_autocut_algorithm_cache_and_readonly_draft(self):
        self.call('post', '/autocut', {}, 400)
        self.upload()
        self.upload('two.mp4')
        before = (self.folder / 'project.json').read_bytes()
        result = self.call('post', '/autocut', {'pattern': self.pattern(), 'scenes': []})
        self.assertEqual([s['source'] for s in result['scenes']], ['s1', 's2', 's1', 's2'])
        self.assertEqual([s['in'] for s in result['scenes']], [2.1, 2.1, 7.1, 7.1])
        self.assertEqual([s['reason'] for s in result['scenes']], ['hook', '', '', 'cta'])
        for scene, shot in zip(result['scenes'], self.pattern()['shots']):
            self.assertAlmostEqual(scene['out'] - scene['in'], shot['seconds'])
            self.assertAlmostEqual(scene['subtitles'][0]['end'], shot['seconds'])
        count = len(self.calls)
        self.call('post', '/autocut', {'pattern': self.pattern()})
        self.assertEqual(count, len(self.calls), 'cache reran ffmpeg')
        self.assertEqual(before, (self.folder / 'project.json').read_bytes())

    def test_03_autocut_ffmpeg_failure_not_fatal(self):
        self.upload()
        self.fail_cuts = True
        result = self.call('post', '/autocut', {'pattern': self.pattern()})
        self.assertEqual(result['skipped_sources'], ['s1'])
        self.assertEqual(result['scenes'][0]['in'], .1)
        self.assertEqual(len(result['scenes']), 4)

    def test_04_locked_index_and_extra_preserved(self):
        result = self.cut()
        scenes = result['scenes']
        scenes[1]['locked'] = True
        extra = copy.deepcopy(scenes[0])
        extra.update(id='sc99', locked=True)
        scenes.append(extra)
        before = (self.folder / 'project.json').read_bytes()
        recut = self.call('post', '/autocut', {'scenes': scenes})['scenes']
        self.assertEqual(recut[1], scenes[1])
        self.assertEqual(recut[-1], extra)
        self.assertNotEqual(recut[0]['id'], scenes[0]['id'])
        self.assertEqual(before, (self.folder / 'project.json').read_bytes())

    def test_05_logo_validation_ownership_and_trash(self):
        data = self.upload('brand.png', b'png', logo=True)
        self.assertEqual(set(data), {'logo', 'rev'})
        self.assertEqual(data['logo'], {'file': 'logo.png', 'original_name': 'brand.png', 'position': 'tl', 'size_pct': 14})
        p = self.save(logo={'file': '../evil', 'original_name': 'evil', 'position': 'br', 'size_pct': 20})
        self.assertEqual(p['logo']['file'], 'logo.png')
        self.assertEqual(p['logo']['original_name'], 'brand.png')
        data = self.upload('new.svg', b'<svg/>', logo=True)
        self.assertEqual(data['logo']['position'], 'br')
        with self.client.get('/api/projects/' + self.slug + '/logo') as response:
            self.assertEqual(response.headers['Content-Security-Policy'], "default-src 'none'; style-src 'unsafe-inline'")
            self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
        self.upload('bad.txt', b'bad', logo=True, code=400)
        self.upload('huge.png', b'0' * 3 * 1024 * 1024, logo=True, code=400)
        self.assertIsNone(self.call('delete', '/logo')['logo'])
        self.assertFalse((self.folder / 'logo.svg').exists())
        trashed = [f.read_bytes() for f in (self.root / '.trash').rglob('*') if f.is_file()]
        self.assertIn(b'png', trashed)
        self.assertIn(b'<svg/>', trashed)

    def test_06_roundtrip_and_legacy_defaults(self):
        result = self.cut()
        self.upload('brand.png', b'png', logo=True)
        result['scenes'][0]['subtitles'][0]['text'] = 'ข้อความ'
        goal_type = self.call('get', '/api/patterns')['goal_types'][0]
        p = self.save(prompt='เป้าหมาย', goal_type=goal_type, pattern=self.pattern(),
                      scenes=result['scenes'], logo={'position': 'br', 'size_pct': 20}, step=5)
        self.assertEqual(self.call('get'), p)
        legacy = copy.deepcopy(p)
        for field in ('goal_type', 'pattern', 'logo', 'step'):
            legacy.pop(field)
        with server.project_lock(self.slug):
            server.write_project(self.folder, legacy)
        p = self.call('get')
        self.assertEqual(p['step'], 1)
        self.assertIsNone(p['pattern'])
        self.assertIsNone(p['goal_type'])
        self.assertIsNone(p['logo'])

    def test_06_validation_and_stale_rev(self):
        result = self.cut()
        self.upload('brand.png', b'png', logo=True)
        p = self.call('get')
        bad_values = [('goal_type', 'not-in-config'), ('step', 0), ('step', True),
                      ('step', 6), ('prompt', []), ('target_seconds', float('nan')),
                      ('pattern', {'id': 'x', 'name': 'x', 'shots': [{'seconds': 0}]}),
                      ('logo', {'position': 'invalid', 'size_pct': 14}),
                      ('logo', {'position': 'tl', 'size_pct': 25}), ('scenes', [None])]
        for key, value in bad_values:
            with self.subTest(key=key, value=value):
                draft = copy.deepcopy(p)
                draft[key] = value
                error = self.call('put', data=draft, code=400)['error']
                self.assertIn(key, error)
        draft = copy.deepcopy(p)
        draft['scenes'][0]['out'] = 99
        self.assertIn('scenes[0]', self.call('put', data=draft, code=400)['error'])
        self.save(prompt='new')
        self.call('put', data=p, code=409)

    def test_07_export_schema_version_download_rev_and_lock(self):
        self.call('post', '/export', {'rev': self.p['rev']}, 400)
        self.cut()
        self.upload('unused.mp4')
        self.upload('logo.png', b'png', logo=True)
        p = self.call('get')
        before = (self.folder / 'project.json').read_bytes()
        self.call('post', '/export', {'rev': p['rev'] - 1}, 409)
        original_write = zipfile.ZipFile.write
        def checked_write(archive, *args, **kwargs):
            self.assert_unlocked()
            return original_write(archive, *args, **kwargs)
        with patch.object(zipfile.ZipFile, 'write', checked_write):
            result = self.call('post', '/export', {'rev': p['rev']})
        second = self.call('post', '/export', {'rev': p['rev']})
        self.assertIn('-v1-', result['name'])
        self.assertIn('-v2-', second['name'])
        self.assertEqual(before, (self.folder / 'project.json').read_bytes())
        archive = self.folder / 'exports' / result['name']
        with zipfile.ZipFile(archive) as z:
            manifest = json.loads(z.read('edit.json'))
            self.assertEqual(set(manifest), {'goal', 'goal_type', 'target_seconds', 'platform', 'pattern', 'logo', 'sources', 'shots'})
            self.assertEqual(set(manifest['pattern']), {'id', 'name', 'shots'})
            self.assertEqual(set(manifest['pattern']['shots'][0]), {'seconds', 'role'})
            self.assertEqual(set(manifest['shots'][0]), {'source', 'in', 'out', 'role', 'subtitle', 'keyframe'})
            self.assertEqual(set(manifest['sources'][0]), {'id', 'file', 'original_name', 'duration'})
            self.assertIsNone(manifest['pattern']['shots'][1]['role'])
            expected = {'edit.json', 'sources/s1.mov', 'sources/s2.mp4', 'logo.png'}
            expected.update('keyframes/shot-%02d.jpg' % i for i in range(1, 5))
            self.assertEqual(set(z.namelist()), expected)
            self.assertTrue(all(x.compress_type == zipfile.ZIP_STORED for x in z.infolist()))
        self.assertEqual(manifest, self.call('get', '/edit.json'))
        self.assertEqual(len(self.call('get', '/exports')['exports']), 2)
        with self.client.get('/api/projects/' + self.slug + '/exports/' + result['name'], headers={'Range': 'bytes=0-3'}) as response:
            self.assertEqual(response.status_code, 206)
            self.assertEqual(response.data, b'PK\x03\x04')
        self.call('get', '/exports/bad.txt', code=404)

    def test_07_concurrent_save_during_zip_uses_snapshot(self):
        self.cut()
        p = self.save(prompt='snapshot before export')
        entered, release = threading.Event(), threading.Event()
        outcome = []
        original_write = zipfile.ZipFile.write
        def paused_write(archive, *args, **kwargs):
            if not entered.is_set():
                entered.set()
                self.assertTrue(release.wait(5), 'concurrent save blocked behind project_lock')
            return original_write(archive, *args, **kwargs)
        def export():
            try:
                with server.app.test_client() as client:
                    response = client.post('/api/projects/' + self.slug + '/export', json={'rev': p['rev']})
                    outcome.append((response.status_code, response.get_json()))
                    response.close()
            except Exception as exc:
                outcome.append(exc)
        with patch.object(zipfile.ZipFile, 'write', paused_write):
            worker = threading.Thread(target=export)
            worker.start()
            try:
                self.assertTrue(entered.wait(5))
                saved = self.save(prompt='saved while export running')
                self.assertEqual(saved['rev'], p['rev'] + 1)
            finally:
                release.set()
                worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(outcome[0][0], 200, outcome)
        with zipfile.ZipFile(self.folder / 'exports' / outcome[0][1]['name']) as archive:
            self.assertEqual(json.loads(archive.read('edit.json'))['goal'], p['prompt'])
        self.assertEqual(self.call('get')['prompt'], 'saved while export running')

    def test_08_frames_clamping_cache_and_source_trash_invalidation(self):
        self.upload()
        def frame(moment):
            with self.client.get('/api/projects/' + self.slug + '/sources/s1/frame?t=' + str(moment)) as response:
                self.assertEqual(response.status_code, 200)
                return response.data
        a, b = frame(1), frame(3)
        self.assertNotEqual(a, b)
        count = len(self.calls)
        self.assertEqual(a, frame(1.01))
        self.assertEqual(len(self.calls), count)
        frame(-10)
        self.assertEqual(self.calls[-1][self.calls[-1].index('-ss') + 1], '0')
        frame(100)
        self.assertLessEqual(float(self.calls[-1][self.calls[-1].index('-ss') + 1]), 18.95)
        self.call('get', '/sources/s1/frame?t=nan', code=400)
        analysis = self.folder / 'analysis' / 's1'
        self.assertTrue(analysis.exists())
        self.call('delete', '/sources/s1')
        self.assertFalse(analysis.exists())
        self.assertTrue(any((self.root / '.trash').rglob('*.jpg')))
        self.upload('replacement.mov')
        count = len(self.calls)
        frame(1)
        self.assertGreater(len(self.calls), count)

    def test_2a_sources_owned_conflict_and_project_trash(self):
        self.cut()
        p = self.call('get')
        sources = copy.deepcopy(p['sources'])
        saved = self.save(sources=[{'id': 'evil', 'file': '/etc/passwd'}])
        self.assertEqual(saved['sources'], sources)
        self.call('delete', '/sources/s1', code=409)
        self.save(scenes=[])
        self.call('delete', '/sources/s1')
        with self.client.get('/api/projects/' + self.slug + '/sources/s2', headers={'Range': 'bytes=0-1'}) as response:
            self.assertEqual(response.status_code, 206)
        self.call('delete')
        self.assertFalse(self.folder.exists())
        self.assertTrue(any((self.root / '.trash').rglob('project.json')))

    def test_2a_auth_and_missing_routes(self):
        with patch.dict(os.environ, {'REELS_EDITOR_TOKEN': 'test-secret'}):
            self.call('get', '/api/projects', code=401)
            with self.client.get('/api/projects', headers={'Authorization': 'Bearer test-secret'}) as response:
                self.assertEqual(response.status_code, 200)
        self.call('get', '/api/projects/nonexistent', code=404)
        self.call('post', '/render', {}, 404)
        self.call('post', '/transcribe', {}, 404)

    def test_legacy_settings_transcript_review_render_gate(self):
        remote = {'clip.json': {'restaurant': 'legacy', 'source_duration': 20, 'reviewed': False}}
        def get(name, file):
            return copy.deepcopy(remote.get(file))
        def put(name, file, value):
            remote[file] = copy.deepcopy(value)
        with patch.object(server, 'remote_json', get), patch.object(server, 'push_json', put), \
             patch.object(server, 'rendered_info', lambda *a, **k: {'reviewed': remote['clip.json']['reviewed']}), \
             patch.object(server, 'RENDER_DISABLED', False):
            self.call('put', '/api/clips/legacy/settings', {'trim_start': 1, 'trim_end': 10})
            self.call('put', '/api/clips/legacy/settings', {'trim_start': 0, 'trim_end': 50}, 400)
            self.call('put', '/api/clips/legacy/transcript', [{'start': 0, 'end': 1, 'text': 'test'}])
            self.assertEqual(self.call('get', '/api/clips/legacy/transcript')['segments'][0]['text'], 'test')
            self.call('post', '/api/clips/legacy/render', {}, 409)
            self.call('post', '/api/clips/legacy/review', {})
            self.assertTrue(remote['clip.json']['reviewed'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
