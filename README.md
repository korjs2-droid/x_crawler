# x.com crawler (Selenium + Web Publish)

`x.com` 타임라인/검색 결과를 Selenium으로 수집하고, Flask 웹 UI로 실행할 수 있습니다.

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

## 2) CLI 실행

```bash
python crawler.py search --query "ai" --limit 100 --out search_ai.json
python crawler.py user --username OpenAI --limit 120 --out openai_posts.json
```

로그인 자동화:

```bash
python crawler.py user --username OpenAI --limit 120 --login --headful
```

`.env` 예시:

```env
X_USERNAME=your_x_username_or_email
X_PASSWORD=your_x_password
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
- 서비스 약관/관련 법규를 준수하세요.
