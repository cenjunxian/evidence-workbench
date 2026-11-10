"""Dedicated 2026-10-29 deployment boundary, independent of old validation state.

Business pipeline modules are copied byte-for-byte from the original demo.
Only this factory is public; inner FastAPI apps have per-session storage and
are invoked in-process through an explicit route allowlist.
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sys
import threading
import time
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))
from app import create_app, parse_pdf  # noqa: E402
import legal_return  # noqa: E402
from model_reasoning_v2 import CONFIG_VERSION, TEXTILE_SELECTION_STRATEGY  # noqa: E402
from pdf_pages import summary  # noqa: E402

COOKIE = "aic_online_20261029"
ID = r"[a-f0-9]{32}"
POLICY = "aic-original-online-20261029-v1"
LOGIN = """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>循证文档研究工作台</title><style>body{font:18px/1.7 system-ui;max-width:30em;margin:10vh auto;padding:24px;color:#163e3c}input,button{font:inherit;padding:12px;width:100%;box-sizing:border-box;margin:12px 0}button{background:#12675f;color:white;border:0}#error{color:#973c27}</style><h1>循证文档研究工作台</h1><p>基础民事纠纷问答与材料研究 · 支持文字PDF/UTF-8 TXT<br>模型辅助提取待核记录并整理争点、缺口及沟通稿，程序核验引文原文。事实真实性、法律适用未独立核验。</p><form><label>评审访问码<input name="code" type="password" autocomplete="off" minlength="12" maxlength="128" required></label><button>进入独立工作区</button><p id="error" role="alert"></p></form><p>请仅上传本人有权处理的最少必要材料，避免敏感信息。材料和问题会发送至DeepSeek处理。会话相互隔离，24小时后清理，请及时导出。</p><script src="/online-login.js"></script></html>"""
LOGIN_JS = """document.querySelector('form').onsubmit=async e=>{e.preventDefault();const f=e.target.elements;const code=f.namedItem('code').value;f.namedItem('code').value='';try{const r=await fetch('/online-login',{method:'POST',headers:{'Content-Type':'application/json','X-Demo-Request':'1'},body:JSON.stringify({code})});if(!r.ok)throw Error((await r.json()).detail);localStorage.removeItem('contest-active-document');location.replace('/');}catch(err){document.querySelector('#error').textContent=err.message;}};"""


def checked_path(root, name):
    path = root / name
    if (
        path.is_symlink()
        or getattr(path, "is_junction", lambda: False)()
        or path.resolve().parent != root.resolve()
    ):
        raise ValueError("unsafe dedicated deployment path")
    return path


class Boundary:
    def __init__(
        self,
        root,
        origin,
        access_code,
        *,
        key="",
        close_at=None,
        allow_model=False,
        test=False,
        clock=time.time,
        transport=None,
    ):
        parsed = urlsplit(origin)
        if (
            parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise ValueError("Origin must be a bare HTTPS origin")
        if parsed.scheme != "https" and not (
            test and parsed.scheme == "http" and parsed.hostname == "127.0.0.1"
        ):
            raise ValueError("HTTPS required")
        if len(access_code) < 12 or len(access_code) > 128:
            raise ValueError("Dedicated access code missing or invalid")
        if allow_model and (not key or not close_at):
            raise ValueError(
                "Model calls require a new key and explicit future closing time"
            )
        self.root = Path(root).resolve()
        if self.root.is_symlink() or getattr(self.root, "is_junction", lambda: False)():
            raise ValueError("Storage cannot be a link")
        self.root.mkdir(parents=True, exist_ok=True)
        marker = self.root / "deployment-policy.txt"
        if not marker.exists():
            if any(self.root.iterdir()):
                raise ValueError(
                    "Refusing to reuse nonempty storage without this deployment marker"
                )
            marker.write_text(POLICY, encoding="ascii")
        elif marker.read_text(encoding="ascii") != POLICY:
            raise ValueError("Refusing another deployment storage")
        self.origin, self.access_code, self.clock, self.test = (
            origin,
            access_code,
            clock,
            test,
        )
        self.allow_model, self.close_at, self.transport = (
            allow_model,
            close_at,
            transport,
        )
        self.lock = threading.RLock()
        self.http_lock = asyncio.Lock()
        self.apps = {}
        self.buckets = {}
        self.meta = self.root / "control.json"
        self.state = (
            json.loads(self.meta.read_text(encoding="utf-8"))
            if self.meta.exists()
            else {"sessions": {}, "jobs": {}, "policy": POLICY}
        )
        if self.state.get("policy") != POLICY:
            raise ValueError("Independent deployment metadata mismatch")
        for job in self.state["jobs"].values():
            if job["status"] == "running":
                job["status"] = "interrupted"
        self.persist()
        self.config = {
            "mode": "real" if allow_model else "offline",
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "review_model": "deepseek-v4-flash",
            "base_url": "https://api.deepseek.com",
            "api_key": key if allow_model else "",
            "timeout_seconds": 30,
            "reasoning_effort": "none",
            "config_version": CONFIG_VERSION,
            "request_strategy": TEXTILE_SELECTION_STRATEGY,
            "stage_max_output_tokens": {
                "generate": 1200,
                "review": 4096,
                "revise": 2048,
            },
        }
        fixture = legal_return.fixtures()
        if set(fixture) != {"textile"}:
            raise ValueError("Only the original public textile fixture is allowed")
        self.fixture = fixture["textile"]
        self.pdf = ROOT / "samples/pdf" / self.fixture["filename"]
        if hashlib.sha256(self.pdf.read_bytes()).hexdigest() != self.fixture["sha256"]:
            raise ValueError("Original synthetic PDF hash mismatch")

    def persist(self):
        temporary = self.root / "control.pending"
        temporary.write_text(
            json.dumps(self.state, ensure_ascii=False), encoding="utf-8"
        )
        temporary.replace(self.meta)

    def rate(self, bucket, limit, period=60):
        now = self.clock()
        key = (bucket, period, int(now // period))
        self.buckets = {
            k: v for k, v in self.buckets.items() if k[2] == int(now // k[1])
        }
        self.buckets[key] = self.buckets.get(key, 0) + 1
        if self.buckets[key] > limit:
            raise HTTPException(429, "访问过于频繁，请稍后再试。")

    def session(self, token):
        hashed = hashlib.sha256((token or "").encode()).hexdigest()
        session = self.state["sessions"].get(hashed)
        if not session or session["expires"] <= self.clock():
            raise HTTPException(401, "演示会话已过期，请重新输入评审访问码。")
        return session

    def path(self, sid):
        if not re.fullmatch(ID, sid):
            raise ValueError("invalid session ID")
        return checked_path(self.root, sid)

    def calls_enabled(self):
        return bool(self.allow_model and self.close_at and self.clock() < self.close_at)

    def running_job(self, sid):
        with self.lock:
            jobs = [
                key
                for key, val in self.state["jobs"].items()
                if val["sid"] == sid and val["status"] == "running"
            ]
            if len(jobs) != 1:
                raise ValueError("Task was not admitted")
            return jobs[0]

    def finish(self, sid, identity):
        with self.lock:
            job = self.state["jobs"][identity]
            turn_path = self.path(sid) / (identity + "-turn.json")
            turn = json.loads(turn_path.read_text(encoding="utf-8"))
            job["status"] = turn.get("status", "failed")
            allowed = (
                "stage",
                "status",
                "requested_model",
                "actual_model",
                "usage",
                "elapsed_ms",
                "http_status",
                "error_type",
                "finish_reason",
            )
            job["calls"] = [
                {k: call[k] for k in allowed if k in call}
                for call in turn.get("calls", [])
            ]
            job["finished"] = self.clock()
            self.persist()
            with (self.root / "calls.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(
                        {
                            "policy": POLICY,
                            "task": identity,
                            "status": job["status"],
                            "finished": job["finished"],
                            "calls": job["calls"],
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )

    def app_for(self, sid):
        if sid not in self.apps:
            target = self.path(sid)
            target.mkdir(exist_ok=True)
            boundary = self

            class Guard(httpx.AsyncBaseTransport):
                contest_execution_source = (
                    "external_http"
                    if boundary.allow_model and boundary.transport is None
                    else "mock_interface"
                )

                async def handle_async_request(self, request):
                    if not boundary.calls_enabled():
                        raise httpx.ConnectError("Model calls disabled or expired")
                    if str(request.url) != "https://api.deepseek.com/chat/completions":
                        raise httpx.ConnectError("Destination blocked")
                    body = json.loads(request.content)
                    if (
                        len(request.content) > 65536
                        or body.get("model") != "deepseek-v4-flash"
                        or body.get("max_tokens") != 1200
                        or body.get("thinking") != {"type": "disabled"}
                        or "tools" in body
                    ):
                        raise httpx.ConnectError("Request outside deployment contract")
                    with boundary.lock:
                        identity = boundary.running_job(sid)
                        job = boundary.state["jobs"][identity]
                        if job.get("sent"):
                            raise httpx.ConnectError("Automatic retries are disabled")
                        job["sent"] = True
                        boundary.persist()
                    transport = boundary.transport or httpx.AsyncHTTPTransport(
                        retries=0
                    )
                    try:
                        response = await transport.handle_async_request(request)
                        data = bytearray()
                        async for piece in response.aiter_bytes():
                            data.extend(piece)
                            if len(data) > 262144:
                                raise httpx.ReadError("Response too large")
                        # aiter_bytes has decoded content-encoding already.
                        headers = {
                            key: value
                            for key, value in response.headers.items()
                            if key.lower() not in (
                                "content-encoding", "content-length", "transfer-encoding"
                            )
                        }
                        return httpx.Response(
                            response.status_code,
                            headers=headers,
                            content=bytes(data),
                            request=request,
                        )
                    finally:
                        if boundary.transport is None:
                            await transport.aclose()

            self.apps[sid] = create_app(
                self.config,
                target,
                Guard(),
                on_task_finished=lambda identity: self.finish(sid, identity),
                task_identity=lambda: self.running_job(sid),
                strict_material_selection=True,
            )
        return self.apps[sid]

    async def forward(self, sid, method, path, payload=None, headers=None):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app_for(sid)),
            base_url="http://internal",
        ) as client:
            return await client.request(method, path, json=payload, headers=headers)

    def storage_check(self, sid):
        target = self.path(sid)
        target.mkdir(exist_ok=True)
        size = sum(p.stat().st_size for p in target.iterdir() if p.is_file())
        total = sum(p.stat().st_size for p in self.root.glob("*/*") if p.is_file())
        if size + 524288 > 5 * 1024**2 or total + 524288 > 128 * 1024**2:
            raise HTTPException(507, "演示存储已达上限，请先导出并联系团队。")

    def sweep(self):
        with self.lock:
            for token, session in list(self.state["sessions"].items()):
                sid = session["sid"]
                if session["expires"] > self.clock():
                    continue
                if any(
                    j["sid"] == sid and j["status"] == "running"
                    for j in self.state["jobs"].values()
                ):
                    continue
                path = self.path(sid)
                if path.exists():
                    children = [checked_path(path, p.name) for p in path.iterdir()]
                    if any(not p.is_file() for p in children):
                        raise ValueError("Unexpected session storage item")
                    for p in children:
                        p.unlink()
                    path.rmdir()
                self.apps.pop(sid, None)
                del self.state["sessions"][token]
            self.persist()


def create_online(root, origin, access_code, **options):
    boundary = Boundary(root, origin, access_code, **options)

    @asynccontextmanager
    async def lifespan(api):
        # One worker only; same advisory-lock mechanism as the existing controlled demo.
        handle = (boundary.root / "process.lock").open("a+b")
        if os.name == "nt":
            import msvcrt

            handle.write(b"0")
            handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)

        async def janitor():
            while True:
                await asyncio.sleep(30)
                async with boundary.http_lock:
                    boundary.sweep()

        cleaner = asyncio.create_task(janitor())
        try:
            yield
        finally:
            cleaner.cancel()
            await asyncio.gather(cleaner, return_exceptions=True)
            handle.close()

    api = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    api.state.boundary = boundary
    research_mutation = asyncio.Lock()

    @api.middleware("http")
    async def protect(request, call_next):
        try:
            if request.url.path != "/health/live":
                if request.headers.get("host") != urlsplit(origin).netloc:
                    raise HTTPException(400, "访问地址不正确。")
                boundary.rate("global-requests", 600)
                client = request.client.host if request.client else "unknown"
                boundary.rate("requests:" + client, 180)
                if request.method not in ("GET", "HEAD"):
                    if request.headers.get("origin") not in (None, origin):
                        raise HTTPException(403, "不接受跨站操作。")
                    if request.headers.get("x-demo-request") != "1":
                        raise HTTPException(403, "请求缺少受控操作标识。")
                    try:
                        length = int(request.headers.get("content-length", "0"))
                    except ValueError:
                        raise HTTPException(400, "请求长度无效。") from None
                    body_limit = 1048576 if request.url.path == '/api/research/upload' else 65536 if request.url.path == '/api/research/draft/save' else 16384
                    if not 0 <= length <= body_limit:
                        raise HTTPException(413, "请求内容过长。")
                    if request.url.path not in (
                        "/online-login",
                    ) and request.url.path.startswith("/api/"):
                        if request.url.path == "/api/contest/upload":
                            raise HTTPException(403, "本演示禁止任意文件上传。")
                    raw = bytearray()
                    async for chunk in request.stream():
                        raw.extend(chunk)
                        if len(raw) > body_limit:
                            raise HTTPException(413, "请求内容过长。")
                    request._body = bytes(raw)
            if request.url.path not in (
                "/",
                "/online-login",
                "/online-login.js",
                "/health/live",
            ):
                with boundary.lock:
                    request.state.session = boundary.session(
                        request.cookies.get(COOKIE)
                    )
            if request.url.path.startswith('/api/research/') and request.method not in ('GET','HEAD'):
                if research_mutation.locked():
                    raise HTTPException(409, '当前正在处理材料或问题，请稍后手动提交。')
                async with research_mutation:
                    response = await call_next(request)
            else:
                response = await call_next(request)
        except HTTPException as exc:
            response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        except Exception:
            response = JSONResponse(
                {"detail": "演示服务暂不可用，已保留本会话数据，请联系团队。"},
                status_code=503,
            )
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; worker-src 'self' blob:; img-src 'self' data: blob:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            }
        )
        return response

    @api.get("/health/live")
    def live():
        return {"status": "ok", "deployment": POLICY}

    @api.get("/online-login.js")
    def script():
        return Response(LOGIN_JS, media_type="text/javascript")

    @api.post("/online-login")
    async def login(request: Request):
        boundary.rate("login:global", 20, 300)
        try:
            data = await request.json()
        except ValueError:
            raise HTTPException(400, "请求格式无效。") from None
        if (
            not isinstance(data, dict)
            or set(data) != {"code"}
            or not isinstance(data["code"], str)
            or len(data["code"]) > 128
        ):
            raise HTTPException(400, "访问码格式无效。")
        if not hmac.compare_digest(data["code"].encode(), access_code.encode()):
            raise HTTPException(401, "评审访问码无效。")
        with boundary.lock:
            boundary.sweep()
            if len(boundary.state["sessions"]) >= 64:
                raise HTTPException(429, "活动会话已达上限，请稍后再试。")
            token = secrets.token_urlsafe(32)
            sid = secrets.token_hex(16)
            session = {
                "sid": sid,
                "expires": boundary.clock() + 86400,
                "tasks": 0,
                "last_task": 0,
            }
            boundary.state["sessions"][hashlib.sha256(token.encode()).hexdigest()] = (
                session
            )
            boundary.persist()
        result = JSONResponse({"expires_at": session["expires"]})
        result.set_cookie(
            COOKIE,
            token,
            max_age=86400,
            secure=not boundary.test,
            httponly=True,
            samesite="strict",
            path="/",
        )
        return result

    @api.api_route("/api/contest/{tail:path}", methods=["GET", "POST", "DELETE"])
    async def dispatch(tail: str, request: Request):
        async with boundary.http_lock:
            with boundary.lock:
                session = boundary.session(request.cookies.get(COOKIE))
            sid = session["sid"]
            method = request.method
            path = request.url.path
            if tail == "version" and method == "GET":
                response = await boundary.forward(sid, method, path)
                return {
                    **response.json(),
                    "controlled_demo": True,
                    "real_calls_enabled": boundary.calls_enabled(),
                    "test_transport": False,
                    "storage_notice": "浏览器独立会话 · 24小时后清理 · 原创合成案例",
                    "model_scope": "仅条件满足时选取材料原句；争点、条件行动与沟通稿为规则输出",
                }
            if tail == "catalog" and method == "GET":
                return {
                    "materials": [
                        {"id": "textile", "filename": boundary.fixture["filename"]}
                    ]
                }
            if tail == "catalog/textile" and method == "POST":
                target = boundary.path(sid)
                boundary.storage_check(sid)
                for p in target.glob("*.json"):
                    if re.fullmatch(ID + r"\.json", p.name):
                        doc = json.loads(p.read_text(encoding="utf-8"))
                        if doc.get("sha256") == boundary.fixture["sha256"]:
                            return {**doc, "page_count": len(doc["pages"])}
                identity = secrets.token_hex(16)
                raw = boundary.pdf.read_bytes()
                pages = parse_pdf(raw)
                doc = {
                    "document_id": identity,
                    "filename": boundary.fixture["filename"],
                    "sha256": boundary.fixture["sha256"],
                    "created_at": boundary.clock(),
                    "pages": pages,
                    "parse_summary": summary(pages),
                    **legal_return.catalog_metadata("textile"),
                }
                (target / (identity + ".pdf")).write_bytes(raw)
                (target / (identity + ".json")).write_text(
                    json.dumps(doc, ensure_ascii=False), encoding="utf-8"
                )
                return {**doc, "page_count": len(pages), "chunk_count": 0}
            allowed = (
                method == "GET"
                and (
                    tail in ("workspace", "pipeline-capabilities")
                    or re.fullmatch(
                        r"tasks/" + ID + r"|documents/" + ID + r"/pdf|export/" + ID,
                        tail,
                    )
                )
            ) or (
                method == "POST"
                and (
                    tail == "tasks"
                    or re.fullmatch(
                        r"results/" + ID + r"/communication|tasks/" + ID + r"/cancel",
                        tail,
                    )
                )
            )
            if not allowed:
                raise HTTPException(
                    403, "仅开放原创预置家纺体验；上传、管理及其他生成入口均关闭。"
                )
            payload = None
            if method == "POST":
                try:
                    raw = await request.body()
                    payload = json.loads(raw) if raw else {}
                except ValueError:
                    raise HTTPException(422, "请求JSON无效。") from None
                if not isinstance(payload, dict):
                    raise HTTPException(422, "请求格式无效。")
            identity = None
            if tail == "tasks" and method == "POST":
                if (
                    set(payload)
                    - {
                        "document_id",
                        "query",
                        "audit",
                        "top_k",
                        "return_facts",
                        "return_context",
                    }
                    or payload.get("audit", False) is not False
                    or not isinstance(payload.get("query"), str)
                    or not 1 <= len(payload["query"]) <= 500
                ):
                    raise HTTPException(422, "本演示只支持当前家纺流程，问题限500字。")
                docid = payload.get("document_id", "")
                if (
                    not isinstance(docid, str)
                    or not re.fullmatch(ID, docid)
                    or not (boundary.path(sid) / (docid + ".json")).is_file()
                ):
                    raise HTTPException(404, "文件不属于本会话。")
                idem = request.headers.get("idempotency-key", "")
                if not re.fullmatch(r"[A-Za-z0-9_-]{16,80}", idem):
                    raise HTTPException(422, "提交缺少有效幂等编号。")
                digest = hashlib.sha256(
                    json.dumps(payload, sort_keys=True).encode()
                ).hexdigest()
                with boundary.lock:
                    for key, job in boundary.state["jobs"].items():
                        if job["sid"] == sid and job["idem"] == idem:
                            if job["fingerprint"] != digest:
                                raise HTTPException(
                                    409, "同一提交编号不能用于不同情况。"
                                )
                            return JSONResponse({"turn_id": key}, status_code=202)
                    if any(
                        j["status"] == "running"
                        for j in boundary.state["jobs"].values()
                    ):
                        raise HTTPException(409, "当前演示正在整理，请稍后手动提交。")
                    if session["tasks"] >= 25 or len(boundary.state["jobs"]) >= 500:
                        raise HTTPException(429, "本次演示任务额度已用完。")
                    if boundary.clock() - session["last_task"] < 10:
                        raise HTTPException(429, "请间隔10秒后再提交。")
                    if boundary.allow_model and not boundary.calls_enabled():
                        raise HTTPException(503, "本次模型调用授权已到期，未发送请求。")
                    boundary.storage_check(sid)
                    identity = secrets.token_hex(16)
                    boundary.state["jobs"][identity] = {
                        "sid": sid,
                        "idem": idem,
                        "fingerprint": digest,
                        "status": "running",
                        "sent": False,
                        "created": boundary.clock(),
                    }
                    session["tasks"] += 1
                    session["last_task"] = boundary.clock()
                    boundary.persist()
            try:
                response = await boundary.forward(
                    sid,
                    method,
                    path + ("?" + request.url.query if request.url.query else ""),
                    payload,
                )
            except Exception:
                if identity:
                    with boundary.lock:
                        boundary.state["jobs"][identity]["status"] = "interrupted"
                        boundary.persist()
                raise
            if identity and response.status_code != 202:
                with boundary.lock:
                    boundary.state["jobs"][identity]["status"] = "rejected"
                    boundary.persist()
            return Response(
                response.content,
                status_code=response.status_code,
                headers={
                    k: v
                    for k, v in response.headers.items()
                    if k.lower() in ("content-type", "content-disposition")
                },
            )

    import consumer_server
    consumer_server.install(api, boundary)

    @api.get("/{path:path}")
    def assets(path: str, request: Request):
        if not path:
            try:
                boundary.session(request.cookies.get(COOKIE))
            except HTTPException:
                return HTMLResponse(LOGIN)
        if not path:
            return FileResponse(ROOT / 'consumer_web/index.html')
        if path == 'example':
            raise HTTPException(404, "独立源码仓库不包含旧示例前端。")
        static = ROOT / "frontend/dist"
        candidate = (static / (path or "index.html")).resolve()
        if (
            not candidate.is_relative_to(static.resolve())
            or not candidate.is_file()
            or candidate.suffix == ".map"
        ):
            raise HTTPException(404, "资源不存在。")
        return FileResponse(candidate)

    return api


def deployment_app():
    data = os.environ.get("AIC_ONLINE_DATA", "")
    origin = os.environ.get("AIC_ONLINE_ORIGIN", "")
    code = os.environ.get("AIC_ONLINE_ACCESS_CODE", "")
    enabled = os.environ.get("AIC_ONLINE_MODEL_ENABLED") == "1"
    closing = os.environ.get("AIC_ONLINE_CALLS_CLOSE_AT", "")
    close_at = None
    if closing:
        parsed = datetime.fromisoformat(closing)
        if parsed.tzinfo is None:
            raise ValueError("Closing time must include its time zone")
        close_at = parsed.timestamp()
    if not data or not origin:
        raise ValueError("Explicit dedicated storage and HTTPS origin required")
    return create_online(
        data,
        origin,
        code,
        key=os.environ.get("AIC_ONLINE_DEEPSEEK_KEY", ""),
        close_at=close_at,
        allow_model=enabled,
    )
