#!/usr/bin/env python3
import os
from typing import Any, Dict

from dotenv import load_dotenv
from flask import Flask, render_template, request

from crawler import XSeleniumCrawler

load_dotenv()

app = Flask(__name__)


def _to_int(value: str, default: int) -> int:
    try:
        parsed = int(value)
        return parsed if parsed > 0 else default
    except Exception:
        return default


def run_job(form: Dict[str, str]) -> Dict[str, Any]:
    mode = form.get("mode", "search")
    limit = _to_int(form.get("limit", "50"), 50)
    max_scrolls = _to_int(form.get("max_scrolls", "60"), 60)
    scroll_pause = float(form.get("scroll_pause", "1.5") or 1.5)

    login = form.get("login") == "on"
    username = (form.get("x_username") or os.getenv("X_USERNAME") or "").strip()
    password = (form.get("x_password") or os.getenv("X_PASSWORD") or "").strip()

    crawler = XSeleniumCrawler(
        headless=True,
        scroll_pause_sec=scroll_pause,
        profile_dir=(form.get("profile_dir") or None),
        profile_name=(form.get("profile_name") or None),
    )

    try:
        if login:
            if not username or not password:
                raise RuntimeError("로그인 옵션 사용 시 아이디/비밀번호가 필요합니다.")
            crawler.login(username=username, password=password, manual_wait_sec=30)

        if mode == "user":
            target = (form.get("username") or "").strip().lstrip("@")
            if not target:
                raise RuntimeError("username을 입력하세요.")
            return crawler.crawl_user(username=target, limit=limit, max_scrolls=max_scrolls)

        query = (form.get("query") or "").strip()
        if not query:
            raise RuntimeError("query를 입력하세요.")
        return crawler.crawl_search(query=query, limit=limit, max_scrolls=max_scrolls)
    finally:
        crawler.close()


@app.route("/", methods=["GET", "POST"])
def index():
    result = None
    error = ""

    if request.method == "POST":
        try:
            result = run_job(request.form.to_dict())
        except Exception as exc:
            error = str(exc)

    return render_template("index.html", result=result, error=error)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
