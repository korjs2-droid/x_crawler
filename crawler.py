#!/usr/bin/env python3
import argparse
import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set
from urllib.parse import quote_plus

from dotenv import load_dotenv
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

TWEET_ID_RE = re.compile(r"/status/(\d+)")


class XSeleniumCrawler:
    def __init__(
        self,
        headless: bool = True,
        page_wait_sec: int = 20,
        scroll_pause_sec: float = 1.5,
        profile_dir: Optional[str] = None,
        profile_name: Optional[str] = None,
    ):
        self.page_wait_sec = page_wait_sec
        self.scroll_pause_sec = scroll_pause_sec
        self.driver = self._build_driver(headless, profile_dir, profile_name)

    def _build_driver(
        self,
        headless: bool,
        profile_dir: Optional[str],
        profile_name: Optional[str],
    ) -> webdriver.Chrome:
        options = Options()
        chrome_bin = os.getenv("CHROME_BIN")
        if chrome_bin:
            options.binary_location = chrome_bin
        if headless:
            options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1400,2200")

        if profile_dir:
            options.add_argument(f"--user-data-dir={profile_dir}")
        if profile_name:
            options.add_argument(f"--profile-directory={profile_name}")

        # Selenium Manager가 적절한 ChromeDriver를 자동으로 관리한다.
        return webdriver.Chrome(options=options)

    def close(self) -> None:
        self.driver.quit()

    def login(
        self,
        username: str,
        password: str,
        manual_wait_sec: int = 90,
    ) -> None:
        self.driver.get("https://x.com/i/flow/login")

        user_input = self._find_first(
            [
                (By.NAME, "text"),
                (By.CSS_SELECTOR, "input[autocomplete='username']"),
            ],
            timeout=20,
        )
        if user_input is None:
            raise RuntimeError("로그인 화면에서 username 입력창을 찾지 못했습니다.")

        user_input.clear()
        user_input.send_keys(username)
        user_input.send_keys(Keys.ENTER)
        time.sleep(1.2)

        # 추가 식별(전화번호/username 재확인)이 필요한 계정이 있다.
        extra_user_input = self._find_first(
            [
                (By.NAME, "text"),
                (By.CSS_SELECTOR, "input[autocomplete='on']"),
            ],
            timeout=4,
        )
        if extra_user_input is not None:
            extra_user_input.clear()
            extra_user_input.send_keys(username)
            extra_user_input.send_keys(Keys.ENTER)
            time.sleep(1.0)

        pass_input = self._find_first(
            [
                (By.NAME, "password"),
                (By.CSS_SELECTOR, "input[type='password']"),
            ],
            timeout=20,
        )
        if pass_input is None:
            raise RuntimeError("비밀번호 입력창을 찾지 못했습니다. 로그인 플로우가 변경되었을 수 있습니다.")

        pass_input.clear()
        pass_input.send_keys(password)
        pass_input.send_keys(Keys.ENTER)

        if self._is_logged_in(timeout=12):
            return

        if manual_wait_sec > 0:
            print(
                f"[login] 추가 인증(2FA/캡차) 처리 대기 중... 최대 {manual_wait_sec}초",
            )
            deadline = time.time() + manual_wait_sec
            while time.time() < deadline:
                if self._is_logged_in(timeout=2):
                    return
                time.sleep(1)

        raise RuntimeError("로그인 확인 실패: 추가 인증이 완료되지 않았거나 인증 정보가 올바르지 않습니다.")

    def crawl_search(
        self,
        query: str,
        limit: int,
        max_scrolls: int,
    ) -> Dict[str, Any]:
        url = f"https://x.com/search?q={quote_plus(query)}&src=typed_query&f=live"
        tweets = self._crawl_timeline(url=url, limit=limit, max_scrolls=max_scrolls)
        return {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "mode": "search",
            "query": query,
            "count": len(tweets),
            "tweets": tweets,
        }

    def crawl_user(
        self,
        username: str,
        limit: int,
        max_scrolls: int,
    ) -> Dict[str, Any]:
        url = f"https://x.com/{username}"
        tweets = self._crawl_timeline(url=url, limit=limit, max_scrolls=max_scrolls)
        return {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "mode": "user",
            "username": username,
            "count": len(tweets),
            "tweets": tweets,
        }

    def _crawl_timeline(
        self,
        url: str,
        limit: int,
        max_scrolls: int,
    ) -> List[Dict[str, Any]]:
        self.driver.get(url)
        self._wait_for_page()

        seen_ids: Set[str] = set()
        collected: List[Dict[str, Any]] = []

        same_height_count = 0
        last_height = self._page_height()

        for _ in range(max_scrolls):
            if len(collected) >= limit:
                break

            for article in self.driver.find_elements(By.CSS_SELECTOR, "article[data-testid='tweet']"):
                tweet = self._extract_tweet(article)
                if not tweet:
                    continue
                tweet_id = tweet.get("tweet_id")
                if not tweet_id or tweet_id in seen_ids:
                    continue

                seen_ids.add(tweet_id)
                collected.append(tweet)

                if len(collected) >= limit:
                    break

            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(self.scroll_pause_sec)

            current_height = self._page_height()
            if current_height == last_height:
                same_height_count += 1
            else:
                same_height_count = 0
                last_height = current_height

            # 더 이상 로딩이 진행되지 않으면 종료
            if same_height_count >= 3:
                break

        return collected[:limit]

    def _wait_for_page(self) -> None:
        wait = WebDriverWait(self.driver, self.page_wait_sec)
        try:
            wait.until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "article[data-testid='tweet'], div[data-testid='primaryColumn']")
                )
            )
        except TimeoutException:
            # 로그인 벽/일시적 로딩 이슈에서도 이후 추출을 시도한다.
            pass

    def _find_first(
        self,
        locators: List[tuple],
        timeout: int,
    ):
        end = time.time() + timeout
        while time.time() < end:
            for by, value in locators:
                elements = self.driver.find_elements(by, value)
                if elements:
                    return elements[0]
            time.sleep(0.2)
        return None

    def _is_logged_in(self, timeout: int = 5) -> bool:
        markers = [
            (By.CSS_SELECTOR, "a[data-testid='AppTabBar_Home_Link']"),
            (By.CSS_SELECTOR, "a[aria-label='Home']"),
            (By.CSS_SELECTOR, "button[data-testid='SideNav_AccountSwitcher_Button']"),
        ]
        return self._find_first(markers, timeout=timeout) is not None

    def _page_height(self) -> int:
        height = self.driver.execute_script("return document.body.scrollHeight")
        return int(height) if isinstance(height, (int, float)) else 0

    def _extract_tweet(self, article) -> Optional[Dict[str, Any]]:
        anchors = article.find_elements(By.CSS_SELECTOR, "a[href*='/status/']")
        status_url = ""
        tweet_id = ""

        for a in anchors:
            href = a.get_attribute("href") or ""
            match = TWEET_ID_RE.search(href)
            if match:
                status_url = href
                tweet_id = match.group(1)
                break

        if not tweet_id:
            return None

        text_parts = article.find_elements(By.CSS_SELECTOR, "div[data-testid='tweetText']")
        text = "\n".join(t.text.strip() for t in text_parts if t.text.strip())

        handle = ""
        user_name = ""
        time_iso = ""

        user_links = article.find_elements(By.CSS_SELECTOR, "a[role='link'][href^='/']")
        for ul in user_links:
            href = ul.get_attribute("href") or ""
            if "/status/" in href:
                continue
            path = href.rstrip("/").split("/")[-1]
            if path and path not in {"home", "explore", "notifications", "messages"}:
                handle = path
                break

        user_name_els = article.find_elements(By.CSS_SELECTOR, "div[data-testid='User-Name'] span")
        if user_name_els:
            user_name = user_name_els[0].text.strip()

        time_els = article.find_elements(By.TAG_NAME, "time")
        if time_els:
            time_iso = time_els[0].get_attribute("datetime") or ""

        return {
            "tweet_id": tweet_id,
            "url": status_url,
            "username": handle,
            "display_name": user_name,
            "created_at": time_iso,
            "text": text,
        }


def save_json(path: str, payload: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="x.com Selenium 크롤러")

    parser.add_argument("--headful", action="store_true", help="브라우저 UI 표시 (기본은 headless)")
    parser.add_argument("--scroll-pause", type=float, default=1.5, help="스크롤 후 대기 시간(초)")
    parser.add_argument("--max-scrolls", type=int, default=60, help="최대 스크롤 횟수")
    parser.add_argument("--profile-dir", default=None, help="Chrome user-data-dir 경로")
    parser.add_argument("--profile-name", default=None, help="Chrome profile name (예: Default)")
    parser.add_argument("--login", action="store_true", help="크롤링 전 x.com 로그인 자동화 실행")
    parser.add_argument("--x-username", default=None, help="로그인 계정 (없으면 .env의 X_USERNAME 사용)")
    parser.add_argument("--x-password", default=None, help="로그인 비밀번호 (없으면 .env의 X_PASSWORD 사용)")
    parser.add_argument("--manual-login-wait", type=int, default=90, help="2FA/캡차 수동 처리 대기 시간(초)")

    sub = parser.add_subparsers(dest="command", required=True)

    p_search = sub.add_parser("search", help="검색 결과(최신) 수집")
    p_search.add_argument("--query", required=True, help="검색어")
    p_search.add_argument("--limit", type=int, default=100, help="수집 개수")
    p_search.add_argument("--out", default="search_result.json", help="출력 JSON 파일")

    p_user = sub.add_parser("user", help="특정 유저 타임라인 수집")
    p_user.add_argument("--username", required=True, help="@ 제외 유저명")
    p_user.add_argument("--limit", type=int, default=100, help="수집 개수")
    p_user.add_argument("--out", default="user_result.json", help="출력 JSON 파일")

    return parser


def main() -> int:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args()

    crawler = XSeleniumCrawler(
        headless=not args.headful,
        scroll_pause_sec=args.scroll_pause,
        profile_dir=args.profile_dir,
        profile_name=args.profile_name,
    )

    try:
        if args.login:
            username = args.x_username or os.getenv("X_USERNAME")
            password = args.x_password or os.getenv("X_PASSWORD")
            if not username or not password:
                raise RuntimeError("로그인 옵션 사용 시 X_USERNAME/X_PASSWORD(또는 CLI 인자)가 필요합니다.")
            crawler.login(
                username=username,
                password=password,
                manual_wait_sec=args.manual_login_wait,
            )

        if args.command == "search":
            result = crawler.crawl_search(
                query=args.query,
                limit=args.limit,
                max_scrolls=args.max_scrolls,
            )
        elif args.command == "user":
            result = crawler.crawl_user(
                username=args.username,
                limit=args.limit,
                max_scrolls=args.max_scrolls,
            )
        else:
            parser.print_help()
            return 1

        save_json(args.out, result)
        print(f"완료: {result.get('count', 0)}건 -> {args.out}")
        return 0
    except Exception as e:
        print(f"실패: {e}")
        return 2
    finally:
        crawler.close()


if __name__ == "__main__":
    raise SystemExit(main())
