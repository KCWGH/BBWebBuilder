"""Local web GUI for the existing Windows Java6/JDE71 toolchain."""
import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import uuid
import zipfile
import secrets
import base64
import signal
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from urllib import request, error
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from dotenv import load_dotenv, dotenv_values

from legacy import parse_jdp, scan_project, build_jdp_text, find_midlet_class, write_rapc_manifest, ICON_MAX_BYTES
from artifacts import package_release

ROOT = Path(__file__).resolve().parent
_token_set_in_environment = 'BB_UPLOAD_TOKEN' in os.environ
CONFIG_FILE = Path(os.getenv('BB_CONFIG_FILE', str(ROOT / '.env')))
load_dotenv(CONFIG_FILE, override=False, encoding='utf-8-sig')
if not _token_set_in_environment:
    # Read file-backed credentials on each request rather than freezing them
    # into the process environment at startup.
    os.environ.pop('BB_UPLOAD_TOKEN', None)
DEV = ROOT.parent.parent
TOOLS = Path(os.getenv('BB_TOOLS_ROOT', str(DEV)))
DATA = Path(os.getenv('BB_DATA_DIR', str(ROOT / 'data')))
JAVA = Path(os.getenv('BB_JAVA', str(TOOLS / ('Java6/bin/java.exe' if os.name == 'nt' else 'Java6/bin/java'))))
NATIVE_PICKER = os.getenv('BB_NATIVE_PICKER', '1' if os.name == 'nt' else '0') == '1'
ALLOWED_HOSTS = set(os.getenv('BB_ALLOWED_HOSTS', '127.0.0.1,localhost').split(','))
WEB_USER = os.getenv('BB_WEB_USER', '')
WEB_PASSWORD = os.getenv('BB_WEB_PASSWORD', '')
PROJECTS = DATA / 'projects'
JOBS = DATA / 'jobs'
for folder in [PROJECTS, JOBS]:
    folder.mkdir(parents=True, exist_ok=True)
MAX_UPLOAD = 128 * 1024 * 1024
PACKAGE_URL = os.getenv('BB_PACKAGE_UPLOAD_URL', 'http://zenio07.duckdns.org/appstore/api/upload-package')
APPS_URL = os.getenv('BB_APPS_URL', PACKAGE_URL.rsplit('/', 1)[0] + '/apps')


def upload_token():
    value = os.environ.get('BB_UPLOAD_TOKEN')
    if value is None:
        value = dotenv_values(CONFIG_FILE, encoding='utf-8-sig').get('BB_UPLOAD_TOKEN')
    return (value or '').strip()
app = FastAPI(title='BlackBerry 7.1 Builder')
pool = ThreadPoolExecutor(max_workers=1)
projects, jobs = {}, {}
lock = threading.Lock()
picker_lock = threading.Lock()
STATE = DATA / 'state.json'


def save_state():
    # Call while holding lock; replace prevents partially written snapshots.
    payload = {'projects': {pid: {**p, 'root': str(p['root'])} for pid, p in projects.items()}, 'jobs': jobs}
    temporary = STATE.with_suffix('.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    temporary.replace(STATE)


if STATE.exists():
    persisted = json.loads(STATE.read_text(encoding='utf-8'))
    projects.update({pid: {**p, 'root': Path(p['root'])} for pid, p in persisted.get('projects', {}).items()})
    jobs.update(persisted.get('jobs', {}))
    for job in jobs.values():
        job['uploading'] = False
        if job['status'] in {'queued', 'building'}:
            job['status'] = 'failed'
            job['logs'].append('서버가 재시작되어 빌드가 중단됐습니다. 다시 빌드하세요.')

if any(host not in {'127.0.0.1', 'localhost'} for host in ALLOWED_HOSTS) and not (WEB_USER and WEB_PASSWORD):
    raise RuntimeError('외부 호스트를 허용하려면 BB_WEB_USER와 BB_WEB_PASSWORD를 설정하세요.')


@app.middleware('http')
async def local_only(req: Request, next_handler):
    # Local server: reject foreign browser origins and DNS rebinding.
    from urllib.parse import urlsplit
    host = req.headers.get('host', '').split(':')[0]
    origin = req.headers.get('origin')
    if host not in ALLOWED_HOSTS or (origin and urlsplit(origin).netloc != req.headers.get('host')):
        from fastapi.responses import JSONResponse
        return JSONResponse({'detail': '로컬 브라우저에서만 사용할 수 있습니다.'}, status_code=403)
    if WEB_USER or WEB_PASSWORD:
        try:
            scheme, encoded = req.headers.get('authorization', '').split(' ', 1)
            username, password = base64.b64decode(encoded, validate=True).decode().split(':', 1)
            valid = scheme.lower() == 'basic' and secrets.compare_digest(username.encode(), WEB_USER.encode()) and secrets.compare_digest(password.encode(), WEB_PASSWORD.encode())
        except (ValueError, UnicodeError):
            valid = False
        if not valid:
            from fastapi.responses import JSONResponse
            return JSONResponse({'detail': '로그인이 필요합니다.'}, status_code=401,
                                headers={'WWW-Authenticate': 'Basic realm="BlackBerry Builder"'})
    return await next_handler(req)


def safe_path(base, relative):
    if not relative or len(relative.encode('utf-8')) > 500 or '\n' in relative or '\r' in relative or ':' in relative:
        raise ValueError(f'잘못된 파일 경로: {relative}')
    path = (Path(base) / relative.replace('\\', '/')).resolve()
    if not path.is_relative_to(Path(base).resolve()) or path == Path(base).resolve():
        raise ValueError(f'프로젝트 밖의 경로: {relative}')
    return path


def get_project(pid):
    if pid not in projects:
        raise HTTPException(404, '프로젝트를 다시 열어 주세요.')
    return projects[pid]


def register(root, jdps):
    pid = uuid.uuid4().hex
    with lock:
        projects[pid] = {'root': Path(root), 'jdps': jdps}
        save_state()
    return {'id': pid, 'jdps': jdps}


def project_info(project, relative):
    jdp = safe_path(project['root'], relative)
    if relative not in project['jdps']:
        raise ValueError('등록되지 않은 JDP')
    info = parse_jdp(jdp)
    scanned = scan_project(str(jdp.parent))
    selected = {p.replace('\\', '/').lower() for p in info['files']}
    files = sorted(set(scanned['sources'] + scanned['resources'] + info['files'] + info['icons']))
    return {'jdp': relative, 'name': jdp.stem, 'title': info['Title'] or jdp.stem,
            'vendor': info['Vendor'], 'version': info['Version'],
            'icon': info['icons'][0].replace('\\', '/') if info['icons'] else '',
            'files': [{'path': p.replace('\\', '/'), 'selected': p.replace('\\', '/').lower() in selected,
                       'exists': safe_path(jdp.parent, p).is_file()} for p in files]}


@app.get('/')
def index():
    return FileResponse(ROOT / 'index.html')


@app.get('/app.js')
def ui_script():
    return FileResponse(ROOT / 'app.js', media_type='application/javascript')


@app.get('/jdp-import.js')
def import_script():
    return FileResponse(ROOT / 'jdp-import.js', media_type='application/javascript')


@app.get('/api/config')
def config():
    return {'tools_ready': JAVA.is_file() and all((TOOLS / p).is_file() for p in ['JDE71/bin/rapc.jar', 'JDE71/lib/net_rim_api.jar']),
            'native_picker': NATIVE_PICKER,
            'package_upload_ready': bool(PACKAGE_URL and upload_token())}


@app.get('/healthz')
def health():
    return {'status': 'ok', 'tools_ready': config()['tools_ready']}


def store_request(method, app_id=None):
    token = upload_token()
    if not token:
        raise HTTPException(409, '.env에 BB_UPLOAD_TOKEN을 설정하고 저장하세요.')
    url = APPS_URL.rstrip('/')
    if app_id is not None:
        url += '/' + quote(app_id, safe='')
    req = request.Request(url, method=method, headers={'Authorization': f'Bearer {token}', 'Accept': 'application/json'})
    try:
        with request.urlopen(req, timeout=30) as response:
            result = json.loads(response.read(1024 * 1024))
        if not isinstance(result, dict):
            raise ValueError('잘못된 응답 형식')
        if method == 'GET' and not isinstance(result.get('apps'), list):
            raise ValueError('앱 목록이 없습니다.')
        if method == 'DELETE' and (result.get('status') != 'deleted' or result.get('app_id') != app_id):
            raise ValueError('삭제 완료가 확인되지 않았습니다. 목록을 새로고침하세요.')
        return result
    except error.HTTPError as exc:
        messages = {401: '앱스토어 인증에 실패했습니다.', 403: '앱스토어 접근 권한이 없습니다.', 404: '앱 또는 API를 찾을 수 없습니다.'}
        raise HTTPException(exc.code if exc.code in messages else 502, messages.get(exc.code, '앱스토어 서버 요청에 실패했습니다.'))
    except (error.URLError, ValueError, TimeoutError):
        raise HTTPException(502, '앱스토어 응답을 확인할 수 없습니다. 목록을 새로고침해 상태를 확인하세요.')


@app.get('/api/store/apps')
def store_apps():
    return store_request('GET')


@app.delete('/api/store/apps/{app_id}')
def delete_store_app(app_id: str):
    if not app_id or app_id in {'.', '..'}:
        raise HTTPException(400, '잘못된 앱 식별자입니다.')
    return store_request('DELETE', app_id)


@app.post('/api/open-dialog')
def open_dialog():
    if not NATIVE_PICKER:
        raise HTTPException(409, '헤드리스 서버에서는 프로젝트 폴더 또는 ZIP을 가져오세요.')
    if not picker_lock.acquire(blocking=False):
        raise HTTPException(409, '이미 파일 선택 창이 열려 있습니다.')
    try:
        result = subprocess.run(
            [sys.executable, str(ROOT / 'picker.py')], capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
        )
        if result.returncode:
            raise ValueError('파일 선택 창을 열지 못했습니다.')
        selected = json.loads(result.stdout.decode('utf-8')).get('path', '')
        if not selected:
            return {'cancelled': True}
        # The native chooser is the authorization boundary: the user may choose
        # a JDP anywhere on this PC. Build inputs remain within its project root.
        jdp = Path(selected).resolve(strict=True)
        if not jdp.is_file() or jdp.suffix.lower() != '.jdp':
            raise ValueError('JDP 파일을 선택해 주세요.')
        data = register(jdp.parent, [jdp.name])
        data['project'] = project_info(projects[data['id']], jdp.name)
        data['location'] = str(jdp)
        return data
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc))
    finally:
        picker_lock.release()


@app.post('/api/import')
async def import_project(files: list[UploadFile] = File(...), paths: str = Form('[]')):
    root = PROJECTS / uuid.uuid4().hex
    root.mkdir()
    try:
        relative_paths = json.loads(paths)
        if relative_paths and len(relative_paths) != len(files):
            raise ValueError('파일 경로 개수가 맞지 않습니다.')
        total, seen = 0, set()
        for index, upload in enumerate(files):
            relative = relative_paths[index] if relative_paths else upload.filename
            data = bytearray()
            while chunk := await upload.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_UPLOAD:
                    raise ValueError('업로드 한도는 128 MiB입니다.')
                data.extend(chunk)
            if len(files) == 1 and relative.lower().endswith('.zip'):
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    if len(archive.infolist()) > 20000 or sum(m.file_size for m in archive.infolist()) > MAX_UPLOAD:
                        raise ValueError('압축 해제 한도 초과')
                    for member in archive.infolist():
                        if member.is_dir():
                            continue
                        target = safe_path(root, member.filename)
                        if target in seen:
                            raise ValueError('중복 파일 경로')
                        seen.add(target)
                        if (member.external_attr >> 16) & 0o170000 == 0o120000:
                            raise ValueError('심볼릭 링크는 지원하지 않습니다.')
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(archive.read(member))
            else:
                target = safe_path(root, relative)
                if target in seen:
                    raise ValueError('중복 파일 경로')
                seen.add(target)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
        jdps = sorted(p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and p.suffix.lower() == '.jdp')
        if not jdps:
            raise ValueError('JDP가 없습니다. JDP와 소스·리소스를 포함한 프로젝트 폴더/ZIP을 선택해 주세요.')
        result = register(root, jdps)
        result['project'] = project_info(projects[result['id']], jdps[0])
        return result
    except (ValueError, OSError, KeyError, zipfile.BadZipFile) as exc:
        shutil.rmtree(root)
        raise HTTPException(400, str(exc))
    finally:
        for upload in files:
            await upload.close()


@app.get('/api/projects/{pid}')
def inspect_project(pid: str, jdp: str):
    try:
        return project_info(get_project(pid), jdp)
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc))


class BuildRequest(BaseModel):
    project_id: str
    jdp: str
    name: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_]{0,63}$')
    title: str = Field(min_length=1, max_length=100)
    vendor: str = Field(min_length=1, max_length=100)
    version: str = Field(pattern=r'^\d+(?:\.\d+){0,3}$')
    icon: str = ''
    files: list[str]


@app.post('/api/build')
def build(body: BuildRequest):
    project = get_project(body.project_id)
    try:
        if body.jdp not in project['jdps'] or any('\n' in s or '\r' in s for s in [body.title, body.vendor]):
            raise ValueError('잘못된 프로젝트/메타데이터')
        original_jdp = safe_path(project['root'], body.jdp)
        # These settings require dependency builds or alternative RAPC flags.
        # Fail explicitly instead of silently changing the app semantics.
        original = original_jdp.read_text(encoding='utf-8')
        for section in ['Imports', 'DependsOn', 'AlxImports', 'CustomBuildRules']:
            match = re.search(r'\[' + section + r'\s*\n(.*?)^\]', original, re.M | re.S)
            if match and match.group(1).strip():
                raise ValueError(f'이 버전은 [{section}] 의존성이 있는 JDP를 지원하지 않습니다.')
        if re.search(r'^(?:Type=[12]|RunOnStartup=1|SystemModule=1|AutoRestart=1)\s*$', original, re.M):
            raise ValueError('라이브러리/시작 시 실행/시스템 모듈 JDP는 지원하지 않습니다.')
        if not body.files or not any(p.lower().endswith('.java') for p in body.files):
            raise ValueError('Java 소스를 선택해 주세요.')
        files = list(dict.fromkeys(body.files + ([body.icon] if body.icon else [])))
        for relative in files:
            source = safe_path(original_jdp.parent, relative)
            if not source.is_file():
                raise ValueError(f'누락된 입력 파일: {relative}')
            if source.suffix.lower() in {'.cod', '.jdp', '.jad', '.rapc', '.class', '.exe', '.jar'}:
                raise ValueError(f'지원하지 않는 빌드 입력: {relative}')
        if body.icon and safe_path(original_jdp.parent, body.icon).stat().st_size > ICON_MAX_BYTES:
            raise ValueError('아이콘은 16,384 bytes 이하여야 합니다.')
        jid = uuid.uuid4().hex
        build_dir = JOBS / jid / 'build'
        build_dir.mkdir(parents=True)
        # Snapshot before queuing so future edits cannot affect a queued build.
        for relative in files:
            destination = safe_path(build_dir, relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(safe_path(original_jdp.parent, relative), destination)
        build_jdp = build_dir / f'{body.name}.jdp'
        build_jdp.write_text(build_jdp_text(body.name, body.files, [body.icon] if body.icon else [], body.title, body.vendor, body.version), encoding='utf-8')
        with lock:
            jobs[jid] = {'id': jid, 'status': 'queued', 'logs': [], 'request': body.model_dump(), 'files': []}
            save_state()
        pool.submit(run_job, jid, build_jdp, body)
        return {'id': jid}
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc))


def run_job(jid, jdp, body):
    def log(text):
        with lock:
            jobs[jid]['logs'].append(text)
    try:
        with lock:
            jobs[jid]['status'] = 'building'
        info = parse_jdp(jdp)
        midlet = find_midlet_class(str(jdp.parent), info['files'])
        write_rapc_manifest(str(jdp.with_suffix('.rapc')), info, f'{body.name}.jar', midlet)
        inputs = list(dict.fromkeys(info['files'] + info['icons']))
        source_list = jdp.parent / '_inputs.txt'
        source_list.write_text('\n'.join(inputs) + '\n', encoding='utf-8')
        java = JAVA
        command = [str(java), '-Xmx512m', '-jar', str(TOOLS / 'JDE71/bin/rapc.jar'), f'-import={TOOLS / "JDE71/lib/net_rim_api.jar"}', '-quiet']
        if midlet:
            command.append('-midlet')
        command += [f'codename={body.name}', f'{body.name}.rapc', '@_inputs.txt']
        env = dict(os.environ)
        for key in ['BB_UPLOAD_TOKEN', 'BB_WEB_PASSWORD']:
            env.pop(key, None)
        env['PATH'] = str(java.parent) + os.pathsep + str(TOOLS / 'JDE71/bin') + os.pathsep + env.get('PATH', '')
        log(f'Java6 / RAPC 7.1 · {len(inputs)}개 입력 · {"MIDlet" if midlet else "CLDC 앱"}')
        proc = subprocess.Popen(command, cwd=jdp.parent, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, errors='replace',
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                                start_new_session=os.name != 'nt')
        # Watchdog kills hung tools without holding up every subsequent build.
        def kill_tools():
            if os.name == 'nt':
                proc.kill()
            else:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        timer = threading.Timer(600, kill_tools)
        timer.start()
        try:
            for line in proc.stdout:
                log(line.rstrip())
            proc.wait()
        finally:
            timer.cancel()
            proc.stdout.close()
        if proc.returncode:
            raise ValueError(f'RAPC 실패 (exit={proc.returncode})')
        manifest, package = package_release(jdp.parent, JOBS / jid / 'ota', body.name)
        log(f'완료: COD {manifest["modules"]}개 + JAD. OTA ZIP 준비됨.')
        with lock:
            jobs[jid].update(status='complete', modules=manifest['modules'], package=package.name,
                             files=[*manifest['files'], {'name': package.name, 'size': package.stat().st_size}])
            save_state()
    except Exception as exc:
        log(str(exc))
        with lock:
            jobs[jid]['status'] = 'failed'
            save_state()


@app.get('/api/jobs/{jid}')
def job_status(jid: str):
    with lock:
        if jid not in jobs:
            raise HTTPException(404, '빌드 작업 없음')
        return {k: v for k, v in jobs[jid].items() if k != 'request'}


@app.get('/releases/{jid}/{filename}')
def download(jid: str, filename: str):
    job = job_status(jid)
    if job['status'] != 'complete' or filename not in [f['name'] for f in job['files']]:
        raise HTTPException(404, '산출물 없음')
    mime = 'text/vnd.sun.j2me.app-descriptor' if filename.endswith('.jad') else 'application/vnd.rim.cod' if filename.endswith('.cod') else 'application/zip'
    return FileResponse(JOBS / jid / 'ota' / filename, media_type=mime)


@app.post('/api/jobs/{jid}/upload')
def upload_release(jid: str):
    job = job_status(jid)
    if job['status'] != 'complete':
        raise HTTPException(409, '완료된 빌드만 업로드할 수 있습니다.')
    if not PACKAGE_URL:
        raise HTTPException(409, 'BB_PACKAGE_UPLOAD_URL을 설정하세요.')
    token = upload_token()
    if not token:
        raise HTTPException(409, '빌더 폴더의 .env에 BB_UPLOAD_TOKEN을 설정하고 저장하세요.')
    with lock:
        if jobs[jid].get('uploading'):
            raise HTTPException(409, '이미 업로드 중입니다.')
        jobs[jid]['uploading'] = True
    try:
        body = jobs[jid]['request']
        folder = JOBS / jid / 'ota'
        path = folder / job['package']
        boundary = 'BBBuilder' + uuid.uuid4().hex
        parts = []
        def text_part(key, value):
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode('utf-8'))
        def file_part(key, path, mime):
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"; filename="{path.name}"\r\nContent-Type: {mime}\r\n\r\n'.encode('utf-8') + path.read_bytes() + b'\r\n')
        file_part('file', path, 'application/zip')
        manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
        jad = safe_path(folder, manifest['jad']).read_text(encoding='utf-8-sig')
        app_name = next((line.partition(':')[2].strip() for line in jad.splitlines()
                         if line.partition(':')[0] == 'MIDlet-Name'), '')
        if not app_name:
            raise HTTPException(409, 'JAD에 MIDlet-Name이 없어 업로드할 수 없습니다.')
        text_part('app_name', app_name)
        text_part('vendor', 'KCW')  # Existing store contract.
        text_part('package_format', 'blackberry-ota-v1')
        if body['icon']:
            file_part('icon', safe_path(JOBS / jid / 'build', body['icon']), 'image/png')
        parts.append(f'--{boundary}--\r\n'.encode())
        req = request.Request(PACKAGE_URL, data=b''.join(parts), method='POST',
                              headers={'Content-Type': f'multipart/form-data; boundary={boundary}',
                                       'Authorization': f'Bearer {token}'})
        try:
            with request.urlopen(req, timeout=180) as response:
                reply = response.read(1024 * 1024).decode('utf-8', 'replace')
                acknowledgement = json.loads(reply)
                if acknowledgement.get('status') != 'published' or not acknowledgement.get('jad_url'):
                    raise ValueError('서버가 게시 완료와 jad_url을 확인하지 않았습니다. 서버 상태를 확인하세요.')
                return {'status': 'uploaded', 'response': reply}
        except error.HTTPError as exc:
            reply = exc.read(16384).decode('utf-8', 'replace')
            try:
                payload = json.loads(reply)
                reason = payload.get('detail') or payload.get('error') or payload.get('message') or payload
                reason = reason if isinstance(reason, str) else json.dumps(reason, ensure_ascii=False)
            except ValueError:
                reason = reply or exc.reason
            # Never expose credentials if the upstream service echoes them.
            reason = str(reason).replace(token, '[토큰 숨김]')[:4000]
            message = f'앱스토어 업로드 거부 (HTTP {exc.code}): {reason}'
            with lock:
                jobs[jid]['logs'].append(message)
            raise HTTPException(502, message)
        except (error.URLError, ValueError) as exc:
            raise HTTPException(502, f'업로드 확인 실패: {exc}')
    finally:
        with lock:
            jobs[jid]['uploading'] = False


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host=os.getenv('BB_WEB_HOST', '127.0.0.1'), port=int(os.getenv('BB_WEB_PORT', '5001')), root_path=os.getenv('BB_ROOT_PATH', ''))
