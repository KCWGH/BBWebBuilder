"""Exercise a running authenticated prefix deployment without appstore writes."""
import base64
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from urllib import request, error
import zipfile
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import dotenv_values
from artifacts import validate_package
from legacy import build_jdp_text, parse_jdp

base = os.getenv('BB_TEST_URL', 'http://127.0.0.1:5052/builder/')
settings = dotenv_values(Path(__file__).resolve().parent.parent / '.env')
auth = base64.b64encode(f"{settings['BB_WEB_USER']}:{settings['BB_WEB_PASSWORD']}".encode()).decode()


def call(path, data=None, method='GET', content_type='application/json'):
    if isinstance(data, dict):
        data = json.dumps(data).encode()
    req = request.Request(base + path, data=data, method=method, headers={'Authorization': 'Basic ' + auth, 'Content-Type': content_type})
    with request.urlopen(req, timeout=200) as response:
        content = response.read()
    return json.loads(content) if path.startswith('api/') else content


try:
    request.urlopen(base + 'api/config')
    raise AssertionError('Unauthenticated access accepted')
except error.HTTPError as exc:
    assert exc.code == 401
assert b'src="app.js"' in call('')
assert b'fetch(' in call('app.js')
config = call('api/config')
assert config['tools_ready'] and not config['native_picker']

fixtures = []
for name, midlet, resource in [('SmokeCLDC', False, False), ('SmokeMIDlet', True, False), ('SmokeSplit', False, True)]:
    source = (f'import javax.microedition.midlet.MIDlet; public class {name} extends MIDlet {{ protected void startApp() {{}} protected void pauseApp() {{}} protected void destroyApp(boolean b) {{}} }}' if midlet else f'import net.rim.device.api.ui.UiApplication; public class {name} extends UiApplication {{ public static void main(String[] a) {{ {name} app=new {name}(); app.enterEventDispatcher(); }} }}')
    entries = {f'src/{name}.java': source.encode()}
    if resource:
        entries.update({f'res/part{i}.dat': os.urandom(20000) for i in range(20)})
    entries[name + '.jdp'] = build_jdp_text(name, list(entries), [], name, 'Test', '1.0').replace('/', '\\').encode()
    fixtures.append((name, entries, 2 if resource else 1))
if len(sys.argv) > 1:
    jdp = Path(sys.argv[1]); info = parse_jdp(jdp)
    entries = {p: (jdp.parent / p).read_bytes() for p in info['files'] + info['icons']}
    entries[jdp.name] = jdp.read_bytes()
    fixtures.append((jdp.stem, entries, 4))

last_job = None
for name, entries, minimum_modules in fixtures:
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w') as package:
        for path, content in entries.items():
            package.writestr(path, content)
    boundary = 'SmokeBoundary'
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="project.zip"\r\nContent-Type: application/zip\r\n\r\n'.encode() + archive.getvalue() + f'\r\n--{boundary}--\r\n'.encode())
    imported = call('api/import', body, 'POST', f'multipart/form-data; boundary={boundary}')
    project = imported['project']
    build = {key: project[key] for key in ['jdp', 'name', 'title', 'vendor', 'version', 'icon']}
    build.update(project_id=imported['id'], files=[f['path'] for f in project['files'] if f['selected']])
    jid = call('api/build', build, 'POST')['id']
    deadline = time.monotonic() + 120
    while True:
        job = call('api/jobs/' + jid)
        if job['status'] in {'complete', 'failed'}:
            break
        assert time.monotonic() < deadline, 'Build timed out'
        time.sleep(0.5)
    assert job['status'] == 'complete', job['logs']
    assert job['modules'] >= minimum_modules, job
    payload = call(f"releases/{jid}/{job['package']}")
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / 'package.zip'; path.write_bytes(payload)
        validate_package(path, Path(temp) / 'validated')
    print(f"{name}: modules={job['modules']}, package verified")
    last_job = jid
(Path(__file__).resolve().parent.parent / 'data').mkdir(exist_ok=True)
(Path(__file__).resolve().parent.parent / 'data/smoke-job.txt').write_text(last_job)
print('Authentication, prefix, import, builds and downloads passed. No appstore writes.')
