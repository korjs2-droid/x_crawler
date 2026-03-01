#!/usr/bin/env python3
import hmac
import os
import threading
import time
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

    crawler_kwargs = {
        "headless": True,
        "scroll_pause_sec": scroll_pause,
        "profile_dir": (form.get("profile_dir") or None),
        "profile_name": (form.get("profile_name") or None),
    }
    try:
        crawler = XSeleniumCrawler(
            **crawler_kwargs,
            progress_cb=progress_cb,
        )
    except TypeError as exc:
        # 구버전 crawler.py(진행 콜백 미지원)와도 호환되게 동작한다.
        if "progress_cb" not in str(exc):
            raise
        crawler = XSeleniumCrawler(**crawler_kwargs)

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
    timeout_sec = _to_int(os.getenv("JOB_TIMEOUT_SEC", "120"), 120)
    heartbeat_sec = _to_int(os.getenv("JOB_HEARTBEAT_SEC", "5"), 5)

    _set_job(
        job_id,
        status="running",
        started_at=_utc_now(),
        finished_at=None,
        error="",
    )
    _append_progress(job_id, f"작업 시작 (timeout={timeout_sec}s)")

    done = threading.Event()
    result_holder: Dict[str, Any] = {}
    error_holder: Dict[str, str] = {}

    def _worker() -> None:
        try:
            result_holder["result"] = run_job(form, progress_cb=lambda msg: _append_progress(job_id, msg))
        except Exception as exc:
            error_holder["error"] = str(exc)
        finally:
            done.set()

    worker = threading.Thread(target=_worker, daemon=True)
    worker.start()

    start_ts = time.monotonic()
    next_heartbeat = start_ts + heartbeat_sec

    while not done.is_set():
        now = time.monotonic()
        elapsed = int(now - start_ts)
        if now >= next_heartbeat:
            _append_progress(job_id, f"작업 진행중... {elapsed}s 경과")
            next_heartbeat = now + heartbeat_sec

        if elapsed >= timeout_sec:
            _set_job(
                job_id,
                status="failed",
                error=f"작업 시간 초과 ({timeout_sec}s)",
                finished_at=_utc_now(),
            )
            _append_progress(job_id, f"작업 실패: timeout {timeout_sec}s")
            return

        done.wait(timeout=1.0)

    if "error" in error_holder:
        exc = error_holder["error"]
        _set_job(
            job_id,
            status="failed",
            error=exc,
            finished_at=_utc_now(),
        )
        _append_progress(job_id, f"작업 실패: {exc}")
        return

    result = result_holder.get("result")
    if result is None:
        _set_job(
            job_id,
            status="failed",
            error="알 수 없는 오류: 결과가 비어있습니다.",
            finished_at=_utc_now(),
        )
        _append_progress(job_id, "작업 실패: 결과 없음")
        return

    _set_job(
        job_id,
        status="completed",
        result=result,
        finished_at=_utc_now(),
    )
    _append_progress(job_id, f"작업 완료: {result.get('count', 0)}건")


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
