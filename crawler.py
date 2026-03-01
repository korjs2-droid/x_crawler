#!/usr/bin/env python3
import argparse
import json
import os
import random
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Set
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
        safe_mode: bool = True,
        min_delay_sec: float = 1.2,
        max_delay_sec: float = 3.5,
        break_every_scrolls: int = 12,
        break_min_sec: float = 8.0,
        break_max_sec: float = 16.0,
        profile_dir: Optional[str] = None,
        profile_name: Optional[str] = None,
        progress_cb: Optional[Callable[[str], None]] = None,
    ):
        self.page_wait_sec = page_wait_sec
        self.scroll_pause_sec = scroll_pause_sec
        self.safe_mode = safe_mode
        self.min_delay_sec = min_delay_sec
        self.max_delay_sec = max_delay_sec
        self.break_every_scrolls = break_every_scrolls
        self.break_min_sec = break_min_sec
        self.break_max_sec = break_max_sec
        self.progress_cb = progress_cb
        self.page_load_timeout_sec = int(os.getenv("PAGE_LOAD_TIMEOUT_SEC", "25"))
        self.page_open_retries = int(os.getenv("PAGE_OPEN_RETRIES", "2"))
        self.driver = self._build_driver(headless, profile_dir, profile_name)

    def _emit_progress(self, message: str) -> None:
        if self.progress_cb:
            self.progress_cb(message)

    def _build_driver(
        self,
        headless: bool,
        profile_dir: Optional[str],
        profile_name: Optional[str],
    ) -> webdriver.Chrome:
        options = Options()
        options.page_load_strategy = "eager"
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
        driver = webdriver.Chrome(options=options)
        driver.set_page_load_timeout(self.page_load_timeout_sec)
        return driver

    def _safe_get(self, url: str) -> bool:
        last_error = None
        for attempt in range(1, self.page_open_retries + 1):
            try:
                self._emit_progress(f"페이지 열기 시도 {attempt}/{self.page_open_retries}: {url}")
                self.driver.get(url)
                return True
            except TimeoutException as exc:
                last_error = exc
                self._emit_progress(
                    f"페이지 열기 timeout ({self.page_load_timeout_sec}s), 재시도 {attempt}/{self.page_open_retries}"
                )
                # 로딩이 길게 걸릴 때 중단해서 다음 단계로 진행 가능하게 한다.
                self.driver.execute_script("window.stop();")
                if attempt < self.page_open_retries:
                    time.sleep(0.8)
        self._emit_progress(f"페이지 열기 실패(계속 진행): {url} ({last_error})")
        return False

    def close(self) -> None:
        self.driver.quit()

    def login(
        self,
        username: str,
        password: str,
        manual_wait_sec: int = 90,
    ) -> None:
        self._emit_progress("로그인 페이지로 이동")
        loaded = self._safe_get("https://x.com/i/flow/login")
        if not loaded:
            raise RuntimeError("로그인 페이지 로딩 실패")

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
        self._emit_progress("아이디 입력 완료")
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
        self._emit_progress("비밀번호 제출 완료")

        if self._is_logged_in(timeout=12):
            self._emit_progress("로그인 성공")
            return

        if manual_wait_sec > 0:
            print(
                f"[login] 추가 인증(2FA/캡차) 처리 대기 중... 최대 {manual_wait_sec}초",
            )
            self._emit_progress(f"추가 인증 대기 중 (최대 {manual_wait_sec}초)")
            deadline = time.time() + manual_wait_sec
            while time.time() < deadline:
                if self._is_logged_in(timeout=2):
                    self._emit_progress("추가 인증 후 로그인 성공")
                    return
                time.sleep(1)

        raise RuntimeError("로그인 확인 실패: 추가 인증이 완료되지 않았거나 인증 정보가 올바르지 않습니다.")

    def crawl_search(
        self,
        query: str,
        limit: int,
        max_scrolls: int,
    ) -> Dict[str, Any]:
        self._emit_progress(f"search 모드 시작: query='{query}'")
        url = self._open_search_with_input(query)
        tweets = self._crawl_timeline(url=url, limit=limit, max_scrolls=max_scrolls)
        return {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "mode": "search",
            "query": query,
            "count": len(tweets),
            "tweets": tweets,
        }

    def _open_search_with_input(self, query: str) -> str:
        # 검색창 입력을 먼저 시도해서 실제 사용자 플로우와 동일하게 진입한다.
        search_input = None
        attempts = [
            "https://x.com/explore",
            "https://x.com/home",
            "https://x.com/explore",
        ]
        for idx, target_url in enumerate(attempts, start=1):
            self._emit_progress(f"검색창 탐색 시도 {idx}/{len(attempts)}")
            self._safe_get(target_url)
            search_input = self._find_first(
                [
                    (By.CSS_SELECTOR, "input[data-testid='SearchBox_Search_Input']"),
                    (By.CSS_SELECTOR, "input[aria-label='Search query']"),
                    (By.CSS_SELECTOR, "input[placeholder='Search']"),
                ],
                timeout=self.page_wait_sec,
            )
            if search_input is not None:
                break
            print(f"[search] 검색창 탐색 재시도 {idx}/{len(attempts)} 실패")
            time.sleep(1.0)

        if search_input is None:
            # Render/headless 환경에서 검색 입력창이 차단되는 경우 URL 진입으로 폴백한다.
            fallback_url = f"https://x.com/search?q={quote_plus(query)}&src=typed_query&f=live"
            print("[search] 검색창 탐색 실패 -> URL 검색 폴백 사용")
            self._emit_progress("검색창 탐색 실패, URL 검색 폴백으로 전환")
            return fallback_url

        search_input.clear()
        search_input.send_keys(query)
        search_input.send_keys(Keys.ENTER)
        self._emit_progress("검색어 입력 후 결과 페이지 이동")

        # 탭 UI가 보이면 최신(Latest)으로 전환 시도
        latest_tab = self._find_first(
            [
                (By.CSS_SELECTOR, "a[href*='f=live']"),
                (By.XPATH, "//span[normalize-space()='Latest']/ancestor::a[1]"),
                (By.XPATH, "//span[normalize-space()='최신']/ancestor::a[1]"),
            ],
            timeout=5,
        )
        if latest_tab is not None:
            try:
                latest_tab.click()
                time.sleep(0.6)
                self._emit_progress("Latest(최신) 탭 전환")
            except Exception:
                pass

        return self.driver.current_url

    def crawl_user(
        self,
        username: str,
        limit: int,
        max_scrolls: int,
    ) -> Dict[str, Any]:
        self._emit_progress(f"user 모드 시작: @{username}")
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
        self._emit_progress(f"타임라인 수집 시작: limit={limit}, max_scrolls={max_scrolls}")
        self._safe_get(url)
        self._wait_for_page()

        seen_ids: Set[str] = set()
        collected: List[Dict[str, Any]] = []

        same_height_count = 0
        last_height = self._page_height()

        for scroll_idx in range(max_scrolls):
            if len(collected) >= limit:
                break
            if self._is_access_challenge():
                raise RuntimeError("접근 제한/인증 페이지가 감지되어 수집을 중단했습니다.")

            candidates = self.driver.find_elements(By.CSS_SELECTOR, "article[data-testid='tweet']")
            if not candidates:
                candidates = self.driver.find_elements(By.CSS_SELECTOR, "div[data-testid='cellInnerDiv']")

            self._emit_progress(
                f"DOM 후보 수: {len(candidates)} (scroll={scroll_idx + 1})"
            )

            for container in candidates:
                tweet = self._extract_tweet(container)
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
            self._sleep_between_scrolls()
            self._emit_progress(
                f"진행중: scroll={scroll_idx + 1}/{max_scrolls}, collected={len(collected)}/{limit}"
            )

            if self.safe_mode and self.break_every_scrolls > 0 and (scroll_idx + 1) % self.break_every_scrolls == 0:
                break_time = random.uniform(self.break_min_sec, self.break_max_sec)
                print(f"[safe-mode] 장기 대기 {break_time:.1f}초")
                self._emit_progress(f"안전 모드 휴식: {break_time:.1f}초")
                time.sleep(break_time)

            current_height = self._page_height()
            if current_height == last_height:
                same_height_count += 1
            else:
                same_height_count = 0
                last_height = current_height

            # 더 이상 로딩이 진행되지 않으면 종료
            if same_height_count >= 3:
                break

        if not collected:
            # 최후 폴백: 현재 페이지의 status 링크를 직접 수집한다.
            self._emit_progress("트윗 카드 0건 -> status 링크 폴백 시도")
            collected = self._extract_from_status_links(limit)

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

    def _is_access_challenge(self) -> bool:
        text = (self.driver.title or "").lower()
        if any(x in text for x in ["suspicious", "unusual activity", "verify", "captcha"]):
            return True

        markers = [
            (By.XPATH, "//*[contains(text(),'Try again later')]"),
            (By.XPATH, "//*[contains(text(),'Please verify')]"),
            (By.XPATH, "//*[contains(text(),'unusual traffic')]"),
            (By.XPATH, "//*[contains(text(),'Suspicious activity')]"),
            (By.CSS_SELECTOR, "iframe[src*='captcha']"),
        ]
        return self._find_first(markers, timeout=1) is not None

    def _sleep_between_scrolls(self) -> None:
        if self.safe_mode:
            min_s = max(0.2, self.min_delay_sec)
            max_s = max(min_s, self.max_delay_sec)
            time.sleep(random.uniform(min_s, max_s))
            return
        time.sleep(self.scroll_pause_sec)

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

    def _extract_from_status_links(self, limit: int) -> List[Dict[str, Any]]:
        links = self.driver.find_elements(By.CSS_SELECTOR, "a[href*='/status/']")
        seen: Set[str] = set()
        rows: List[Dict[str, Any]] = []
        for a in links:
            href = a.get_attribute("href") or ""
            match = TWEET_ID_RE.search(href)
            if not match:
                continue
            tweet_id = match.group(1)
            if tweet_id in seen:
                continue
            seen.add(tweet_id)

            username = ""
            parts = href.split("/")
            if len(parts) >= 4:
                username = parts[3]

            rows.append(
                {
                    "tweet_id": tweet_id,
                    "url": href,
                    "username": username,
                    "display_name": "",
                    "created_at": "",
                    "text": "",
                }
            )
            if len(rows) >= limit:
                break

        self._emit_progress(f"status 링크 폴백 수집: {len(rows)}건")
        return rows


def save_json(path: str, payload: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="x.com Selenium 크롤러")

    parser.add_argument("--headful", action="store_true", help="브라우저 UI 표시 (기본은 headless)")
    parser.add_argument("--scroll-pause", type=float, default=1.5, help="스크롤 후 대기 시간(초)")
    parser.add_argument("--max-scrolls", type=int, default=60, help="최대 스크롤 횟수")
    parser.add_argument(
        "--safe-mode",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="안전 모드(랜덤 지연/주기적 휴식) 사용",
    )
    parser.add_argument("--min-delay", type=float, default=1.2, help="안전 모드 최소 지연(초)")
    parser.add_argument("--max-delay", type=float, default=3.5, help="안전 모드 최대 지연(초)")
    parser.add_argument("--break-every", type=int, default=12, help="안전 모드에서 휴식 전 스크롤 횟수")
    parser.add_argument("--break-min", type=float, default=8.0, help="안전 모드 휴식 최소 시간(초)")
    parser.add_argument("--break-max", type=float, default=16.0, help="안전 모드 휴식 최대 시간(초)")
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
        safe_mode=args.safe_mode,
        min_delay_sec=args.min_delay,
        max_delay_sec=args.max_delay,
        break_every_scrolls=args.break_every,
        break_min_sec=args.break_min,
        break_max_sec=args.break_max,
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
