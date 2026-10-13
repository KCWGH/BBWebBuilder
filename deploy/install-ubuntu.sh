#!/bin/bash
# Run with sudo on the target server. Does not change /appstore/ routes.
set -euo pipefail
test "$(id -u)" = 0 || { echo 'Run: sudo bash deploy/install-ubuntu.sh'; exit 1; }
repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repo_dir"
test -s .env && test -s tools/JDE71/bin/rapc.jar && test -s tools/JDE71/lib/net_rim_api.jar || { echo 'Missing .env or JDE tools'; exit 1; }
chgrp 10001 .env
chmod 640 .env
chmod 755 tools tools/JDE71 tools/JDE71/bin tools/JDE71/lib
chmod 644 tools/JDE71/bin/rapc.jar tools/JDE71/lib/net_rim_api.jar
if ! command -v docker >/dev/null; then
    apt-get update
    apt-get install -y ca-certificates curl
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    . /etc/os-release
    cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${UBUNTU_CODENAME:-$VERSION_CODENAME}
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
    apt-get update
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
docker compose version
systemctl enable --now docker
docker compose up -d --build
# Verify the backend before touching Nginx; credentials stay in the process.
python3 - <<'PY'
import base64, time, urllib.request
from pathlib import Path
values = dict(line.split('=', 1) for line in Path('.env').read_text().splitlines() if line and not line.startswith('#') and '=' in line)
auth = base64.b64encode((values['BB_WEB_USER'] + ':' + values['BB_WEB_PASSWORD']).encode()).decode()
for attempt in range(30):
    try:
        req = urllib.request.Request('http://127.0.0.1:5002/api/config', headers={'Authorization': 'Basic ' + auth})
        with urllib.request.urlopen(req, timeout=3) as response:
            import json
            assert json.load(response)['tools_ready']
        break
    except Exception:
        if attempt == 29: raise
        time.sleep(2)
print('Backend and native tools ready')
PY
target=/etc/nginx/sites-available/zenio07
test -f "$target"
backup="${target}.before-builder-$(date +%Y%m%d%H%M%S)"
cp -p "$target" "$backup"
mkdir -p /etc/nginx/bbwebbuilder
install -m 644 deploy/nginx-builder.conf /etc/nginx/bbwebbuilder/https.conf
cat > /etc/nginx/bbwebbuilder/http.conf <<'EOF'
location = /builder { return 301 https://zenio07.duckdns.org/builder/; }
location /builder/ { return 301 https://zenio07.duckdns.org$request_uri; }
EOF
python3 - <<'PY'
from pathlib import Path
import re
path = Path('/etc/nginx/sites-available/zenio07')
text = path.read_text()
for port, filename in [(80, 'http'), (443, 'https')]:
    directive = f'include /etc/nginx/bbwebbuilder/{filename}.conf;'
    if directive in text: continue
    if re.search(r'location\s+(?:=\s+)?/builder(?:/|\s)', text):
        raise SystemExit('Existing /builder route: merge manually before continuing')
    expression = rf'(^\s*listen\s+{port}(?:\s+ssl)?;\s*$)'
    text, count = re.subn(expression, lambda m: m.group(0) + '\n    ' + directive, text, flags=re.M)
    if count != 1: raise SystemExit(f'Expected one listen {port} in zenio07 site; refusing ambiguous modification')
path.write_text(text)
PY
if ! nginx -t; then
    cp -p "$backup" "$target"
    echo "Nginx validation failed; restored $backup"
    exit 1
fi
if ! systemctl reload nginx; then
    cp -p "$backup" "$target"
    nginx -t && systemctl reload nginx
    echo 'Reload failed; restored previous site'
    exit 1
fi
echo 'Deployment ready: https://zenio07.duckdns.org/builder/'
echo "Nginx backup: $backup"
