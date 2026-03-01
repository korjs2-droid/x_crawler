#!/usr/bin/env python3
import hmac
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict

from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, session, url_for

from crawler import XSeleniumCrawler

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "change-this-secret-key")
JOBS: Dict[str, Dict[str, Any]] = {}
JOBS_LOCK = threading.Lock()


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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _set_job(job_id: str, **updates: Any) -> None:
    with JOBS_LOCK:
        if job_id not in JOBS:
            JOBS[job_id] = {}
        JOBS[job_id].update(updates)


def _append_progress(job_id: str, message: str) -> None:
    timestamped = f"[{_utc_now()}] {message}"
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        progress = job.setdefault("progress", [])
        progress.append(timestamped)
        if len(progress) > 300:
            del progress[: len(progress) - 300]


def run_job(form: Dict[str, str], progress_cb=None) -> Dict[str, Any]:
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
        progress_cb=progress_cb,
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


def _run_job_async(job_id: str, form: Dict[str, str]) -> None:
    _set_job(
        job_id,
        status="running",
        started_at=_utc_now(),
        finished_at=None,
        error="",
    )
    _append_progress(job_id, "작업 시작")
    try:
        result = run_job(form, progress_cb=lambda msg: _append_progress(job_id, msg))
        _set_job(
            job_id,
            status="completed",
            result=result,
            finished_at=_utc_now(),
        )
        _append_progress(job_id, f"작업 완료: {result.get('count', 0)}건")
    except Exception as exc:
        _set_job(
            job_id,
            status="failed",
            error=str(exc),
            finished_at=_utc_now(),
        )
        _append_progress(job_id, f"작업 실패: {exc}")


@app.route("/", methods=["GET"])
def index():
    if not _is_authenticated():
        return redirect(url_for("web_login"))

    return render_template("index.html")


@app.route("/start", methods=["POST"])
def start_job():
    if not _is_authenticated():
        return jsonify({"error": "unauthorized"}), 401

    form = request.form.to_dict()
    job_id = uuid.uuid4().hex
    _set_job(
        job_id,
        status="queued",
        created_at=_utc_now(),
        started_at=None,
        finished_at=None,
        error="",
        result=None,
        progress=[],
    )
    thread = threading.Thread(target=_run_job_async, args=(job_id, form), daemon=True)
    thread.start()
    return jsonify({"job_id": job_id})


@app.route("/status/<job_id>", methods=["GET"])
def job_status(job_id: str):
    if not _is_authenticated():
        return jsonify({"error": "unauthorized"}), 401
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify({"error": "job_not_found"}), 404
        return jsonify(job)


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
