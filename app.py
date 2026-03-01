#!/usr/bin/env python3
import hmac
import os
from typing import Any, Dict

from dotenv import load_dotenv
from flask import Flask, redirect, render_template, request, session, url_for

from crawler import XSeleniumCrawler

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "change-this-secret-key")


def _is_web_auth_enabled() -> bool:
    return bool((os.getenv("WEB_PASSWORD") or "").strip())


def _is_authenticated() -> bool:
    if not _is_web_auth_enabled():
        return True
    return bool(session.get("web_auth_ok"))


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
    if not _is_authenticated():
        return redirect(url_for("web_login"))

    result = None
    error = ""

    if request.method == "POST":
        try:
            result = run_job(request.form.to_dict())
        except Exception as exc:
            error = str(exc)

    return render_template("index.html", result=result, error=error)


@app.route("/login", methods=["GET", "POST"])
def web_login():
    if not _is_web_auth_enabled():
        return redirect(url_for("index"))

    if _is_authenticated():
        return redirect(url_for("index"))

    error = ""
    if request.method == "POST":
        submitted = request.form.get("web_password", "")
        expected = os.getenv("WEB_PASSWORD", "")
        if hmac.compare_digest(submitted, expected):
            session["web_auth_ok"] = True
            return redirect(url_for("index"))
        error = "비밀번호가 올바르지 않습니다."

    return render_template("login.html", error=error)


@app.route("/logout", methods=["POST"])
def web_logout():
    session.pop("web_auth_ok", None)
    return redirect(url_for("web_login"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
