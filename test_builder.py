import io
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import zipfile
from pathlib import Path
from fastapi.testclient import TestClient
import artifacts
import server
from legacy import parse_jdp, build_jdp_text


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=server.DATA)
        self.root = Path(self.temp.name)
        self.build = self.root / 'build'
        self.build.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def output(self, split=False):
        (self.build / 'App.jad').write_text('MIDlet-Name: App\nRIM-COD-Module-Dependencies: net_rim_cldc\nRIM-COD-URL: App.cod\nRIM-COD-Size: 1\nRIM-COD-SHA1: 00\n')
        if split:
            with zipfile.ZipFile(self.build / 'App.cod', 'w') as archive:
                archive.writestr('App.cod', b'first')
                archive.writestr('App-1.cod', b'second')
        else:
            (self.build / 'App.cod').write_bytes(b'first')
        return artifacts.package_release(self.build, self.root / 'ota', 'App')

    def test_single(self):
        manifest, package = self.output()
        self.assertEqual(manifest['modules'], 1)
        artifacts.validate_package(package, self.root / 'validated')

    def test_split_preserves_metadata_and_raw_modules(self):
        manifest, package = self.output(True)
        self.assertEqual(manifest['modules'], 2)
        artifacts.validate_package(package, self.root / 'validated')
        text = (self.root / 'validated/App.jad').read_text()
        self.assertIn('RIM-COD-Module-Dependencies: net_rim_cldc', text)
        self.assertIn('RIM-COD-URL-1: App-1.cod', text)
        self.assertEqual((self.root / 'validated/App.cod').read_bytes(), b'first')

    def test_container_traversal(self):
        (self.build / 'App.jad').write_text('MIDlet-Name: App')
        with zipfile.ZipFile(self.build / 'App.cod', 'w') as archive:
            archive.writestr('../App.cod', b'bad')
        with self.assertRaises(ValueError):
            artifacts.package_release(self.build, self.root / 'ota', 'App')

    def test_tampered_package(self):
        _, package = self.output(True)
        with zipfile.ZipFile(package) as archive:
            files = {n: archive.read(n) for n in archive.namelist()}
        files['App-1.cod'] = b'changed'
        bad = self.root / 'bad.zip'
        with zipfile.ZipFile(bad, 'w') as archive:
            for name, data in files.items():
                archive.writestr(name, data)
        with self.assertRaisesRegex(ValueError, 'integrity'):
            artifacts.validate_package(bad, self.root / 'validated')
        self.assertFalse((self.root / 'validated').exists())

    def test_metadata_after_list_close(self):
        path = self.root / 'App.jdp'
        path.write_text(build_jdp_text('App', ['Main.java'], [], 'Display', 'Author', '2.1'))
        info = parse_jdp(path)
        self.assertEqual(info['files'], ['Main.java'])
        self.assertEqual(info['Title'], 'Display')
        self.assertEqual(info['Version'], '2.1')

    def test_jad_missing_sibling_even_with_valid_manifest_hash(self):
        _, package = self.output(True)
        with zipfile.ZipFile(package) as archive:
            files = {n: archive.read(n) for n in archive.namelist()}
        files['App.jad'] = files['App.jad'].replace(b'RIM-COD-URL-1: App-1.cod', b'RIM-COD-URL-1: Missing.cod')
        manifest = json.loads(files['manifest.json'])
        for entry in manifest['files']:
            if entry['name'] == 'App.jad':
                entry['size'] = len(files['App.jad'])
                entry['sha256'] = __import__('hashlib').sha256(files['App.jad']).hexdigest()
        files['manifest.json'] = json.dumps(manifest).encode()
        bad = self.root / 'bad.zip'
        with zipfile.ZipFile(bad, 'w') as archive:
            for name, data in files.items():
                archive.writestr(name, data)
        with self.assertRaisesRegex(ValueError, 'reference'):
            artifacts.validate_package(bad, self.root / 'validated')


class APITests(unittest.TestCase):
    def setUp(self):
        for name, value in [('WEB_USER', ''), ('WEB_PASSWORD', ''), ('NATIVE_PICKER', True)]:
            setting = patch.object(server, name, value)
            setting.start()
            self.addCleanup(setting.stop)
        self.client = TestClient(server.app, base_url='http://127.0.0.1:5001')

    def test_foreign_origin_rejected(self):
        response = self.client.post('/api/open-dialog', headers={'Origin': 'https://example.com'})
        self.assertEqual(response.status_code, 403)

    def test_deployment_auth_and_headless_mode(self):
        with patch.object(server, 'WEB_USER', 'builder'), patch.object(server, 'WEB_PASSWORD', 'test-password'), patch.object(server, 'NATIVE_PICKER', False):
            self.assertEqual(self.client.get('/api/config').status_code, 401)
            response = self.client.get('/api/config', auth=('builder', 'test-password'))
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.json()['native_picker'])
            self.assertEqual(self.client.post('/api/open-dialog', auth=('builder', 'test-password')).status_code, 409)

    def test_windows_jdp_paths_normalized_and_midlet_detected(self):
        with tempfile.TemporaryDirectory(dir=server.DATA) as temp:
            root = Path(temp); (root / 'src').mkdir()
            (root / 'src/Main.java').write_text('import javax.microedition.midlet.MIDlet; class Main extends MIDlet {}')
            (root / 'Main.jdp').write_text('[Files\nsrc\\Main.java\n]\n')
            info = parse_jdp(root / 'Main.jdp')
            self.assertEqual(info['files'], ['src/Main.java'])
            self.assertEqual(server.find_midlet_class(str(root), info['files']), 'Main')

    def test_native_picker_outside_former_workspace(self):
        with tempfile.TemporaryDirectory(dir=server.DATA) as temp:
            root = Path(temp)
            jdp = root / 'App.jdp'
            jdp.write_text(build_jdp_text('App', ['Main.java'], [], 'Display', 'KCW', '1.0'))
            (root / 'Main.java').write_text('class Main {}')
            result = SimpleNamespace(returncode=0, stdout=json.dumps({'path':str(jdp)}).encode())
            with patch.object(server.subprocess, 'run', return_value=result):
                response = self.client.post('/api/open-dialog')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['location'], str(jdp.resolve()))
            self.assertEqual(response.json()['project']['title'], 'Display')
            self.assertEqual(response.json()['project']['files'][0]['path'], 'Main.java')

    def test_native_picker_cancel(self):
        with patch.object(server.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=b'{"path":""}')):
            self.assertEqual(self.client.post('/api/open-dialog').json(), {'cancelled':True})

    def test_native_picker_failure_releases_lock(self):
        with patch.object(server.subprocess, 'run', return_value=SimpleNamespace(returncode=1)):
            self.assertEqual(self.client.post('/api/open-dialog').status_code, 400)
        self.assertTrue(server.picker_lock.acquire(blocking=False))
        server.picker_lock.release()

    def test_zip_traversal(self):
        data = io.BytesIO()
        with zipfile.ZipFile(data, 'w') as archive:
            archive.writestr('../escape.txt', 'bad')
        response = self.client.post('/api/import', files={'files': ('project.zip', data.getvalue(), 'application/zip')})
        self.assertEqual(response.status_code, 400)

    def test_build_missing_file(self):
        with tempfile.TemporaryDirectory(dir=server.DATA) as temp:
            root = Path(temp)
            (root / 'App.jdp').write_text(build_jdp_text('App', ['Missing.java'], [], 'App', 'KCW', '1.0'))
            project = server.register(root, ['App.jdp'])
            response = self.client.post('/api/build', json={'project_id': project['id'], 'jdp': 'App.jdp', 'name': 'App', 'title': 'App', 'vendor': 'KCW', 'version': '1.0', 'files': ['Missing.java']})
            self.assertEqual(response.status_code, 400)

    def test_unconfigured_split_upload(self):
        server.jobs['test-split'] = {'id': 'test-split', 'status': 'complete', 'modules': 2, 'files': []}
        old_url = server.PACKAGE_URL
        server.PACKAGE_URL = ''
        try:
            self.assertEqual(self.client.post('/api/jobs/test-split/upload').status_code, 409)
        finally:
            server.PACKAGE_URL = old_url
            del server.jobs['test-split']

    def test_missing_upload_token_prevents_network_request(self):
        server.jobs['test-auth'] = {'id':'test-auth', 'status':'complete', 'modules':1, 'files':[]}
        try:
            with patch.dict(os.environ, {'BB_UPLOAD_TOKEN':''}), patch.object(server.request, 'urlopen') as send:
                self.assertEqual(self.client.post('/api/jobs/test-auth/upload').status_code, 409)
                send.assert_not_called()
                self.assertFalse(self.client.get('/api/config').json()['package_upload_ready'])
        finally:
            del server.jobs['test-auth']

    def test_store_list_and_delete_contract(self):
        for method, path, reply in [
            ('GET', '/api/store/apps', {'apps': [{'app_id': 'test-app', 'app_name': 'Test', 'versions': []}]}),
            ('DELETE', '/api/store/apps/test-app', {'status': 'deleted', 'app_id': 'test-app', 'app_name': 'Test', 'deleted_versions': 2}),
        ]:
            with patch.dict(os.environ, {'BB_UPLOAD_TOKEN': 'test-only-token'}), patch.object(server.request, 'urlopen', return_value=io.BytesIO(json.dumps(reply).encode())) as send:
                response = self.client.request(method, path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), reply)
                req = send.call_args.args[0]
                self.assertEqual(req.get_method(), method)
                self.assertEqual(req.get_header('Authorization'), 'Bearer test-only-token')
                self.assertEqual(req.full_url, server.APPS_URL + ('/test-app' if method == 'DELETE' else ''))

    def test_upload_error_preserves_reason_without_token(self):
        with tempfile.TemporaryDirectory(dir=server.JOBS) as temp:
            root = Path(temp); jid = root.name
            (root / 'ota').mkdir()
            (root / 'ota/App-ota.zip').write_bytes(b'test-package')
            (root / 'ota/manifest.json').write_text('{"jad":"App.jad"}')
            (root / 'ota/App.jad').write_text('MIDlet-Name: App\n')
            server.jobs[jid] = {'id': jid, 'status': 'complete', 'modules': 1, 'files': [], 'logs': [], 'package': 'App-ota.zip', 'request': {'title': 'App', 'icon': ''}}
            failure = server.error.HTTPError(server.PACKAGE_URL, 400, 'Bad Request', {}, io.BytesIO(b'{"detail":"Missing icon test-only-token"}'))
            try:
                with patch.dict(os.environ, {'BB_UPLOAD_TOKEN': 'test-only-token'}), patch.object(server.request, 'urlopen', side_effect=failure):
                    response = self.client.post(f'/api/jobs/{jid}/upload')
                    self.assertEqual(response.status_code, 502)
                    self.assertIn('HTTP 400', response.json()['detail'])
                    self.assertIn('Missing icon', response.json()['detail'])
                    self.assertNotIn('test-only-token', response.text)
                    self.assertNotIn('test-only-token', str(server.jobs[jid]['logs']))
            finally:
                server.jobs.pop(jid, None)

    def test_store_missing_token_and_invalid_acknowledgement(self):
        with patch.dict(os.environ, {'BB_UPLOAD_TOKEN': ''}), patch.object(server.request, 'urlopen') as send:
            self.assertEqual(self.client.get('/api/store/apps').status_code, 409)
            self.assertEqual(self.client.delete('/api/store/apps/test-app').status_code, 409)
            send.assert_not_called()
        with patch.dict(os.environ, {'BB_UPLOAD_TOKEN': 'test-only-token'}), patch.object(server.request, 'urlopen', return_value=io.BytesIO(b'{"status":"deleted","app_id":"other-app"}')):
            self.assertEqual(self.client.delete('/api/store/apps/test-app').status_code, 502)

    def test_package_upload_bearer_header_for_single_and_split(self):
        with tempfile.TemporaryDirectory(dir=server.JOBS) as temp:
            root=Path(temp); jid=root.name
            (root/'ota').mkdir()
            payload=b'OTA ZIP payload'
            (root/'ota/App-ota.zip').write_bytes(payload)
            (root/'ota/manifest.json').write_text('{"jad":"App.jad"}')
            (root/'ota/App.jad').write_text('MIDlet-Name: NativeModule\nMIDlet-1: Display Title,,\n')
            try:
                for modules in [1,5]:
                    server.jobs[jid]={'id':jid,'status':'complete','modules':modules,'files':[],
                                      'package':'App-ota.zip','request':{'title':'App','icon':''}}
                    reply=io.BytesIO(b'{"status":"published","jad_url":"http://example.com/App.jad"}')
                    with patch.dict(os.environ, {'BB_UPLOAD_TOKEN':'test-only-token'}), patch.object(server.request,'urlopen',return_value=reply) as send:
                        response=self.client.post(f'/api/jobs/{jid}/upload')
                        self.assertEqual(response.status_code,200)
                        req=send.call_args.args[0]
                        self.assertEqual(req.full_url,server.PACKAGE_URL)
                        self.assertEqual(req.get_method(),'POST')
                        self.assertEqual(req.get_header('Authorization'),'Bearer test-only-token')
                        self.assertIn(b'filename="App-ota.zip"',req.data)
                        self.assertIn(b'Content-Type: application/zip',req.data)
                        self.assertIn(payload,req.data)
                        self.assertIn(b'name="app_name"\r\n\r\nNativeModule\r\n', req.data)
                        self.assertNotIn(b'name="app_name"\r\n\r\nApp\r\n', req.data)
                        self.assertIn(b'blackberry-ota-v1',req.data)
                        self.assertNotIn('test-only-token',self.client.get('/api/config').text)
                    self.assertFalse(server.jobs[jid]['uploading'])
            finally:
                server.jobs.pop(jid,None)


@unittest.skipUnless(os.getenv('BB_TEST_RAPC') == '1', 'Set BB_TEST_RAPC=1 for real toolchain builds')
class RealBuildTests(unittest.TestCase):
    def build(self, resource_size):
        jid = 'test-' + __import__('uuid').uuid4().hex
        root = server.JOBS / jid
        source = root / 'build'
        source.mkdir(parents=True)
        try:
            (source / 'Hello.java').write_text('import net.rim.device.api.ui.UiApplication;\npublic class Hello extends UiApplication { public static void main(String[] args) { Hello app = new Hello(); app.enterEventDispatcher(); } }')
            files = ['Hello.java']
            if resource_size:
                # Multiple incompressible resources force real sibling modules.
                for i in range(resource_size // 20000):
                    name = f'resource{i}.dat'
                    (source / name).write_bytes(os.urandom(20000))
                    files.append(name)
            jdp = source / 'Hello.jdp'
            jdp.write_text(build_jdp_text('Hello', files, [], 'Hello', 'KCW', '1.0'))
            body = server.BuildRequest(project_id='test', jdp='Hello.jdp', name='Hello', title='Hello', vendor='KCW', version='1.0', files=files)
            server.jobs[jid] = {'id': jid, 'status': 'queued', 'logs': [], 'request': body.model_dump(), 'files': []}
            server.run_job(jid, jdp, body)
            job = server.jobs[jid]
            self.assertEqual(job['status'], 'complete', '\n'.join(job['logs']))
            artifacts.validate_package(root / 'ota' / job['package'], root / 'validated')
            return job['modules']
        finally:
            __import__('shutil').rmtree(root)
            server.jobs.pop(jid, None)

    def test_actual_small_app(self):
        self.assertEqual(self.build(0), 1)

    def test_actual_large_app(self):
        self.assertGreater(self.build(400000), 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
