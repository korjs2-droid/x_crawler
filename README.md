# x.com crawler (API 우선 + Selenium 보조)

`x.com` 타임라인/검색 결과를 **X API 우선**으로 수집하고, API 실패 시 Selenium으로 폴백할 수 있습니다.

## 1) 로컬 실행

```bash
cd x-crawler
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

웹 UI 실행:

```bash
python app.py
```

브라우저에서 `http://localhost:5000` 접속
크롤링 실행 후 화면의 `진행상황` 카드에서 실시간 로그를 확인할 수 있습니다.
필요하면 환경변수로 작업 감시값을 조정할 수 있습니다: `JOB_TIMEOUT_SEC`(기본 120), `JOB_HEARTBEAT_SEC`(기본 5).
Chrome 초기화가 느린 환경에서는 `CHROME_INIT_TIMEOUT_SEC`, `CHROME_INIT_RETRIES`, `CHROME_INIT_RETRY_WAIT_SEC`로 재시도 정책을 조정할 수 있습니다.
페이지 로딩이 멈추면 `PAGE_LOAD_TIMEOUT_SEC`(기본 25), `PAGE_OPEN_RETRIES`(기본 2)로 `driver.get()` 타임아웃/재시도를 조정하세요.
엔진은 웹에서 `auto/api/selenium`을 선택할 수 있으며, `auto`는 `X_BEARER_TOKEN`이 있으면 API부터 시도합니다.


웹 비밀번호 잠금:

```env
WEB_PASSWORD=your_web_access_password
FLASK_SECRET_KEY=change_this_to_a_long_random_secret
```

`WEB_PASSWORD`를 설정하면 `/login`에서 비밀번호 인증 후에만 메인 화면 접근이 가능합니다.

## 2) CLI 실행

```bash
python crawler.py search --query "ai" --limit 100 --out search_ai.json
python crawler.py user --username OpenAI --limit 120 --out openai_posts.json
```

안전 모드(기본 ON):

```bash
python crawler.py search --query "ai" --limit 80 --safe-mode
```

안전 모드 세부 조정:

```bash
python crawler.py search \
  --query "ai" \
  --safe-mode \
  --min-delay 1.5 \
  --max-delay 4.0 \
  --break-every 10 \
  --break-min 10 \
  --break-max 20
```

로그인 자동화:

```bash
python crawler.py user --username OpenAI --limit 120 --login --headful
```

`.env` 예시:

```env
X_USERNAME=your_x_username_or_email
X_PASSWORD=your_x_password
X_BEARER_TOKEN=your_x_api_bearer_token
```

## 3) 웹 퍼블리시 (Render, Docker)

이 프로젝트는 Selenium 때문에 Docker 배포를 권장합니다.

1. GitHub에 `x-crawler` 코드 push
2. Render에서 `New +` -> `Web Service`
3. Repository 연결 후 아래 설정

- Environment: `Docker`
- Dockerfile Path: `./Dockerfile`
- Instance: 필요 성능에 맞게 선택
- Environment Variables: `X_USERNAME`, `X_PASSWORD` (선택)

4. Deploy 후 발급 URL 접속

## 4) 파일 구성

- `crawler.py`: Selenium 크롤러 CLI
- `app.py`: Flask 웹 UI
- `templates/index.html`: 웹 폼/결과 화면
- `Dockerfile`: Chromium 포함 배포 이미지
- `Procfile`: gunicorn 실행 명령

## 5) 주의

- x.com 구조 변경 시 selector가 깨질 수 있습니다.
- 2FA/캡차가 뜨면 자동 로그인은 제한될 수 있습니다.
- 과도한 요청을 피하려면 `--safe-mode`(기본 ON)를 유지하고 `limit`를 낮게 설정하세요.
- 서비스 약관/관련 법규를 준수하세요.
