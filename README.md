# BlackBerry Web Builder

BlackBerry OS 7.1 JDP 프로젝트를 COD/JAD 및 OTA ZIP으로 빌드하는 웹 도구.
Linux 컨테이너에서 Java6, RAPC 7.1, native phoneME preverify를 사용한다. Wine과 데스크톱은 필요 없다.

## Ubuntu 서버 배포

Ubuntu x86_64 서버에 Docker Engine와 Compose plugin을 설치한다.
[공식 설치 문서](https://docs.docker.com/engine/install/ubuntu/).
32비트 정적 Linux preverify를 실행할 수 있는 커널이 필요하다.

```sh
mkdir -p ~/Services
cd ~/Services
git clone https://github.com/KCWGH/BBWebBuilder.git
cd BBWebBuilder
cp .env.example .env
chmod 600 .env
mkdir -p tools/JDE71/bin tools/JDE71/lib
```

보유한 BlackBerry JDE71에서 아래 두 파일을 복사한다. 독점 SDK 파일은 Git/이미지에 포함하지 않는다.

```text
tools/JDE71/bin/rapc.jar
tools/JDE71/lib/net_rim_api.jar
```

`.env`의 `BB_WEB_USER`, `BB_WEB_PASSWORD`를 실제 로그인 정보로 설정한다.
비밀번호는 `openssl rand -hex 24`로 생성할 수 있다. 업로드/삭제를 사용할 때는 `BB_UPLOAD_TOKEN`도 설정한다.
이 파일은 컨테이너에 읽기 전용으로 mount되며 이미지에 복사되지 않는다.
로그인 설정이 없으면 외부 호스트를 허용하는 서버는 시작하지 않는다.
컨테이너는 uid/gid 10001로 실행하므로 Linux bind mount의 읽기 권한도 맞춘다.
파일 소유자는 현재 사용자로 유지하고 컨테이너 그룹만 읽을 수 있게 한다.

```sh
sudo chgrp 10001 .env
chmod 640 .env
chmod 755 tools tools/JDE71 tools/JDE71/bin tools/JDE71/lib
chmod 644 tools/JDE71/bin/rapc.jar tools/JDE71/lib/net_rim_api.jar
```

```sh
docker compose build
docker compose up -d
docker compose ps
docker compose logs --tail 50 builder
```

Compose는 서버 localhost **5002**에만 포트를 연다. 외부에서 직접 접근하는 포트가 아니다.
`deploy/nginx-builder.conf`의 location들을 기존 **HTTPS** `zenio07.duckdns.org` server 블록 안에 추가한다.
기존 `/appstore/` 설정은 유지한다. Nginx가 host에서 실행되는 구성용이다.
Nginx도 컨테이너라면 localhost 대신 같은 Docker network의 builder:5001로 upstream을 연결해야 한다.

```sh
sudo nginx -t
sudo systemctl reload nginx
```

KimServer의 준비된 배포 경로는 `/home/zenio07/Services/BBWebBuilder`다.
SDK와 .env가 준비되어 있으면 다음 명령으로 Docker 설치·실행과 기존 Nginx 반영을 함께 처리한다.
스크립트는 Nginx 설정을 백업하고 검사 실패 시 복구한다.

```powershell
ssh.exe -t KimServer "cd ~/Services/BBWebBuilder && sudo bash deploy/install-ubuntu.sh"
```

접속: **https://zenio07.duckdns.org/builder/**.
Basic 로그인 비밀번호가 평문 HTTP로 전송되지 않도록 HTTPS에서만 사용한다.
기존 server가 HTTP라면 먼저 인증서를 설정하고 HTTP는 HTTPS로 redirect한다.
HTTPS 설정이나 기존 Nginx 파일은 이 저장소가 자동으로 변경하지 않는다.

기본 URL prefix는 `/builder/`. 바꾸면 Compose의 BB_ROOT_PATH와 Nginx location을 함께 변경한다.
서버에서는 JDP 선택 창 대신 PC의 **폴더 가져오기 / ZIP 가져오기**를 사용한다.
JDP 파일만 전송하면 소스와 리소스를 서버가 읽을 수 없으므로 전체 프로젝트를 가져온다.

## 업데이트와 운영

```sh
git pull --ff-only
docker compose up -d --build
```

- non-root(uid 10001), CPU 2개/메모리 2GiB 제한. Java heap은 512MiB.
- 빌드는 하나씩 처리한다. Uvicorn worker를 여러 개로 늘리지 않는다.
- 빌드 제한 시간은 600초. Linux에서는 compiler 자식 프로세스 그룹까지 종료한다.
- 프로젝트/작업/산출물은 `builder-data` volume에 저장하며 재시작 후 복구한다.
- 진행 중이던 빌드는 재시작 후 실패로 표시되며 다시 요청해야 한다.
- 업로드 토큰 변경은 .env 저장 후 적용된다. 로그인/주소/기타 설정 변경은 `docker compose restart builder`.
- 볼륨 자동 삭제/보관 기간은 없다. 디스크 사용량을 관리하고 백업한다.
- `docker compose down`은 데이터 볼륨을 유지한다. **down -v는 데이터를 삭제한다.**
- 로그와 공유 화면에 토큰을 노출하지 않는다. JDK6/구형 native 도구는 신뢰된 사용자 전용으로 운영한다.
- 처음부터 일반 공개 익명 빌드 서비스로 운영하는 구성은 아니다.

## 앱스토어

빌드 성공 후 브라우저 confirm에서 승인했을 때 OTA ZIP을 업로드한다.
앱스토어 영역은 펼칠 때 앱 목록을 조회하며 앱 삭제는 별도 confirm 후 실행한다.
토큰은 서버만 사용한다.

기본 API:

- POST http://zenio07.duckdns.org/appstore/api/upload-package
- GET http://zenio07.duckdns.org/appstore/api/apps
- DELETE http://zenio07.duckdns.org/appstore/api/apps/{app_id}

Bearer BB_UPLOAD_TOKEN. multipart file 필드에 OTA ZIP을 보낸다.
app_name은 JAD의 MIDlet-Name과 일치시킨다. 같은 버전에 다른 내용은 서버의 409 정책을 따른다.
COD 컨테이너는 실제 sibling 모듈로 추출하며 JAD의 URL/Size/SHA1과 manifest SHA256을 검증한다.

## Windows 로컬 실행

```powershell
python -m pip install -r requirements.txt
.\start.ps1
```

기본 도구 위치는 이 폴더의 두 단계 상위 폴더이며 필요하면 .env에 `BB_TOOLS_ROOT=C:/BlackBerryDev`를 설정한다.
PC 로컬 모드에서는 JDP 열기가 Windows 파일 선택 창을 사용한다.
네트워크 배포 설정은 Compose가 제공하며 Windows 로컬 실행과 별개다.

## 검증

```sh
python -m pip install -r requirements-dev.txt
python -m unittest discover -p test_builder.py
```

Docker/Nginx 통합 smoke test는 `deploy/smoke.py`에 있다.
BB_TEST_URL로 시험 주소를 지정하며 로컬 .env 로그인 정보로 접근한다.
서버에 시험 프로젝트/빌드 데이터를 생성하지만 앱스토어 업로드/삭제는 실행하지 않는다.
선택적으로 첫 인자에 실제 JDP 경로를 넘길 수 있다(TwoDots 4모듈 검증용).

실검증: Ubuntu26.04 이미지의 Linux Java6/native preverify로 CLDC 1모듈, MIDlet 1모듈,
큰 리소스 10모듈 및 TwoDots 4모듈 빌드와 다운로드 ZIP 무결성 통과.
Nginx /builder/ prefix, 로그인 거부/승인, 재시작 후 작업/다운로드 복구를 검증했다.
대상 실제 Ubuntu 서버와 BlackBerry 기기의 설치/실행 검증은 별도 단계다.

## 도구 및 라이선스

Dockerfile은 Azul Zulu Java6와 phoneME 소스 커밋을 고정하고 다운로드 SHA256을 검증한다.
preverify의 GPLv2 라이선스와 대응 소스 ZIP을 이미지의 `/usr/share/doc/preverify/`에 포함한다.
Java6의 라이선스/원본 포함 파일도 `/opt/java6/`에 유지한다.
BlackBerry RAPC/API는 사용자가 보유한 JDE에서 별도로 제공한다.
SDK 파일이나 비밀값을 공개 저장소에 추가하지 않는다.
