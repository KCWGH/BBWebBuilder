#!/bin/bash
# Run with sudo; compile a temporary copy, never upload or modify job metadata.
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose exec -T builder python - <<'PY'
import glob, hashlib, json, os, shutil, subprocess, tempfile
from pathlib import Path
state = json.loads(Path('/data/state.json').read_text())
failed = [(jid, job) for jid, job in state['jobs'].items() if job['status'] == 'failed' and any('preverifier failed' in line for line in job['logs'])]
if not failed:
    raise SystemExit('No failed preverify job found')
jid, job = failed[-1]
print('Failed job:', jid)
for filename in ['/opt/bin/preverify', '/tools/JDE71/bin/rapc.jar', '/tools/JDE71/lib/net_rim_api.jar']:
    print(Path(filename).name, hashlib.sha256(Path(filename).read_bytes()).hexdigest())
with tempfile.TemporaryDirectory(prefix='bb-diagnostic-') as temp:
    folder = Path(temp) / 'build'
    shutil.copytree(Path('/data/jobs') / jid / 'build', folder)
    wrapper_dir = Path(temp) / 'bin'
    wrapper_dir.mkdir()
    wrapper = wrapper_dir / 'preverify'
    wrapper.write_text('#!/bin/sh\nexec /opt/bin/preverify -verbose "$@"\n')
    wrapper.chmod(0o755)
    environment = dict(os.environ, PATH=str(wrapper_dir) + ':' + os.environ['PATH'])
    # Diagnostics do not need application credentials in compiler processes.
    environment.pop('BB_UPLOAD_TOKEN', None)
    environment.pop('BB_WEB_PASSWORD', None)
    name = job['request']['name']
    for option in [[], ['-Xint']]:
        command = ['/opt/java6/bin/java', *option, '-Xmx512m', '-jar', '/tools/JDE71/bin/rapc.jar', '-import=/tools/JDE71/lib/net_rim_api.jar', '-quiet', 'codename=' + name, name + '.rapc', '@_inputs.txt']
        print('Java mode:', 'interpreter' if option else 'default', flush=True)
        result = subprocess.run(command, cwd=folder, env=environment, timeout=180, capture_output=True, text=True)
        lines = (result.stdout + result.stderr).splitlines()
        print('\n'.join(lines[-80:]), flush=True)
        print('Compiler exit:', result.returncode, flush=True)
PY
