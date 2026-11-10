"""Isolated contest API; imports no production modules or environment files."""

import hashlib
import io
import json
import re
import uuid
import threading
import os
import httpx
from pathlib import Path
from xml.sax.saxutils import escape

from fastapi import FastAPI, File, HTTPException, Query, UploadFile, Header
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from pypdf import PdfReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer

from model_reasoning_v2 import CANCEL_EVENT

from model_dispatch import (
    Model,
    ModelFailure,
    validate_claims,
    validate_settings,
    configuration_record,
)
from retrieval import PARAMETERS, chunks, retrieve
from evidence_support import (
    VERSION as SUPPORT_VERSION,
    INSUFFICIENT,
    MONEY_INSUFFICIENT,
    money_intent,
    deadline_intent,
    supporting_sentences,
    supported_claims,
)
from workspace import Workspace, now
from starlette.concurrency import run_in_threadpool
from pdf_pages import extract_pages, summary as parse_summary, ocr_available, OCR_NOTICE
import legal_return
import textile_guidance
from real_validation_window import generation_payload

ROOT = Path(__file__).resolve().parents[1]
DISABLED = ["auth", "billing", "payments", "sms", "oauth", "production_mcp", "admin"]


def read_config():
    config = {
        "mode": "offline",
        "provider": "openai-compatible",
        "max_output_tokens": 1200,
        "model": "",
        "review_model": "",
        "base_url": "",
        "api_key": "",
        "timeout_seconds": 30,
    }
    if os.environ.get("CONTEST_OFFLINE_ONLY") == "1":
        return config
    path = ROOT / "config.local.json"
    if path.exists():
        config.update(json.loads(path.read_text(encoding="utf-8-sig")))
    overlay = ROOT / "config.reasoning.local.json"
    if overlay.exists():
        settings = json.loads(overlay.read_text(encoding="utf-8-sig"))
        allowed = {
            "model",
            "review_model",
            "reasoning_effort",
            "config_version",
            "stage_max_output_tokens",
        }
        if not isinstance(settings, dict) or set(settings) - allowed:
            raise ValueError(
                "推理覆盖配置仅允许模型、审核模型、推理档位和版本；不能含密钥"
            )
        config.update(settings)
    if config.get("request_strategy") is not None:
        raise ValueError("专用家纺选句策略不能用于通用应用配置")
    validate_settings(config)
    if config["mode"] not in ("offline", "real"):
        raise ValueError("竞赛 mode 必须为 offline 或 real")
    if config["mode"] == "real" and not str(config["base_url"]).startswith("https://"):
        raise ValueError("真实模型 base_url 必须为 HTTPS 地址")
    if config["mode"] == "real" and config.get("provider") != "deepseek":
        raise ValueError("正常运行仅支持DeepSeek；模拟测试使用显式配置")
    if config.get("provider") == "deepseek" and config.get("base_url", "").rstrip(
        "/"
    ) not in ("https://api.deepseek.com", "https://api.deepseek.com/v1"):
        raise ValueError("DeepSeek 配置必须使用官方 API 地址")
    return config


class Request(BaseModel):
    document_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=4, ge=1, le=8)
    audit: bool = False
    return_facts: legal_return.ReturnFacts | None = None
    return_context: legal_return.ReturnContext | None = None


class CommunicationUpdate(BaseModel):
    model_config = {"extra": "forbid"}
    current_text: str = Field(min_length=1, max_length=8000)
    selected_option: str | None = Field(default=None, max_length=20)
    expected_fingerprint: str = Field(min_length=64, max_length=64)


def safe_communication_text(value):
    if any(ord(char) < 32 and char not in "\n\t\r" for char in value):
        raise HTTPException(422, "沟通稿含不支持的控制字符")
    return value.replace("\r\n", "\n").replace("\r", "\n")


def checked_id(value):
    if not re.fullmatch(r"[a-f0-9]{32}", value):
        raise HTTPException(400, "文件或结果编号无效")
    return value


def parse_pdf(raw):
    if not raw.startswith(b"%PDF-"):
        raise HTTPException(415, "文件不是有效 PDF")
    try:
        reader = PdfReader(io.BytesIO(raw))
        if reader.is_encrypted:
            raise HTTPException(422, "不支持加密 PDF")
        if len(reader.pages) > 100:
            raise HTTPException(413, "演示最多 100 页")
        pages = extract_pages(reader, raw)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(422, "PDF 无法解析") from None
    if sum(len(p["text"]) for p in pages) > 100000:
        raise HTTPException(413, "演示最多提取 100000 字符，请拆分文件")
    return pages


def result_source_label(result):
    if result.get("model_mode") == "offline":
        return "离线模拟"
    source = result.get("execution_source")
    return {
        "mock_interface": "模拟接口验证（非供应商调用）",
        "custom_transport": "自定义传输（来源待核实）",
        "external_http": "外部接口调用（非模拟传输，供应商身份未独立核验）",
        "not_called": "未调用模型",
    }.get(
        source,
        "历史外部接口记录（调用来源待核实）"
        if result.get("model_mode") == "real"
        else "历史模式未知",
    )


def display_review(result):
    audit = dict(result["review"])
    if result.get("model_mode") == "offline":
        audit["note"] = {
            "Offline simulated review; no legal correctness assessment.": "离线模拟审核，仅演示流程，不评估法律正确性。",
            "Skipped: insufficient evidence": "证据不足，未执行生成和审核",
        }.get(audit.get("note"), audit.get("note"))
    return audit


def markdown(result):
    if "legal_preparation" in result:
        return legal_return.export_lines(result, markdown_safe=True)
    lines = [
        "# 文档研究结果",
        "",
        f"结果编号： {result['result_id']}",
        f"运行模式： {result_source_label(result)}",
        f"问题： {result['query']}",
        "",
        result["final_answer"],
        "",
    ]
    if result.get("parse_summary", {}).get("notice"):
        lines += [result["parse_summary"]["notice"], ""]
    monetary = (
        result.get("evidence_assessment", {}).get("scope") == "explicit_money_extract"
    )
    if result["citations"] or not monetary:
        lines.append("## 关键依据")
    for c in result["citations"]:
        lines += [
            f"{c['citation_id']} | {c['filename']} | 第 {c['page']} 页",
            OCR_NOTICE
            if c.get("text_source") == "ocr"
            else "文字层摘录（非法律正确性保证）",
            "\n\n".join(
                claim["quote"]
                for claim in result["claims"]
                if claim["citation_id"] == c["citation_id"]
            )
            if monetary
            else c["text"],
            "",
        ]
    lines += [
        "## 审核说明",
        json.dumps(display_review(result), ensure_ascii=False),
        "",
        "审核和引用存在性校验不代表法律正确性。",
    ]
    return "\n".join(lines)


def pdf_bytes(result):
    output = io.BytesIO()
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    style = ParagraphStyle(
        "body", fontName="STSong-Light", fontSize=10, leading=15, wordWrap="CJK"
    )
    if "legal_preparation" in result:
        style.spaceAfter = 8
        heading = ParagraphStyle("legal-heading", parent=style, fontSize=11, leading=17, keepWithNext=True)
        title = ParagraphStyle("legal-title", parent=heading, fontSize=15, leading=21)
        story = []
        for line in legal_return.export_lines(result).splitlines():
            if not line.strip():
                continue
            is_title = line.startswith("# ")
            is_heading = line.startswith(("U 用户陈述", "D 材料记载", "A 缺口", "A 有前提", "L 官方来源", "L-return-"))
            story.append(Paragraph(escape(line[2:] if is_title else line), title if is_title else heading if is_heading else style))

        def footer(canvas, document):
            canvas.setFont("STSong-Light", 9)
            canvas.drawRightString(550, 22, f"家纺材料核对 / 第 {document.page} 页")

        SimpleDocTemplate(output, rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40).build(story, onFirstPage=footer, onLaterPages=footer)
        return output.getvalue()
    story = []
    for line in markdown(result).splitlines():
        story.append(Paragraph(escape(line) or "&#160;", style))
        story.append(Spacer(1, 3))
    SimpleDocTemplate(
        output, rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40
    ).build(story)
    return output.getvalue()


def create_app(config=None, storage=None, transport=None, on_task_finished=None, task_identity=None, strict_material_selection=False):
    config = config or read_config()
    storage = Path(storage or ROOT / "runtime")
    storage.mkdir(parents=True, exist_ok=True)
    workspace = Workspace(storage)
    cancel_signals = {}
    execution_source = (
        "offline"
        if config["mode"] == "offline"
        else "mock_interface"
        if isinstance(transport, httpx.MockTransport)
        else getattr(transport, "contest_execution_source", "custom_transport")
        if transport is not None
        else "external_http"
    )
    model = Model(config, transport)
    api = FastAPI(title="Contest only", version="0.2.0")

    @api.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        return JSONResponse(
            {"detail": "请求参数无效，请检查文件、问题长度和设置。"}, status_code=422
        )

    def read(identity, suffix):
        path = storage / (checked_id(identity) + suffix)
        if not path.is_file():
            raise HTTPException(404, "文件或结果不存在")
        data = json.loads(path.read_text(encoding="utf-8"))
        if suffix == "-result.json" and "legal_preparation" in data:
            data["generation_mode"] = legal_return.generation_label(data)
        return data

    save = workspace.save

    @api.get("/api/contest/workspace")
    def restore_workspace():
        data = workspace.snapshot()
        for doc in data["documents"]:
            doc["page_count"] = len(doc["pages"])
            doc["chunk_count"] = len(chunks(doc))
        for turn in data["turns"]:
            result = turn.get("result")
            if result and "legal_preparation" in result:
                result["generation_mode"] = legal_return.generation_label(result)
        return data

    @api.delete("/api/contest/workspace")
    def clear_workspace(x_contest_confirm: str = Header(default="")):
        if x_contest_confirm != "clear-local-workspace":
            raise HTTPException(400, "需要明确确认清空本地工作区。")
        return workspace.clear()

    @api.get("/health/live")
    def live():
        return {"status": "ok"}

    @api.get("/health/ready")
    def ready():
        usable = config["mode"] == "offline" or bool(
            config.get("api_key") and config.get("model")
        )
        return JSONResponse(
            {
                "status": "ready" if usable else "missing_contest_credentials",
                "model_mode": config["mode"],
                "external_call_verified": False,
            },
            status_code=200 if usable else 503,
        )

    @api.get("/api/contest/version")
    def version():
        return {
            "version": "0.2.0",
            "provider": config.get("provider", "unknown"),
            "execution_source": execution_source,
            "model_configuration": configuration_record(config),
            "mode": config["mode"],
            "model": config["model"]
            if config["mode"] == "real"
            else "deterministic simulation",
        }

    @api.get("/api/contest/pipeline-capabilities")
    def capabilities():
        return {
            "ocr": "local_optional" if ocr_available() else "not_installed",
            "ocr_limits": {
                "pages": 8,
                "page_seconds": 30,
                "document_seconds": 90,
                "max_pixels_side": 2200,
            },
            "retrieval": PARAMETERS,
            "generation": config["mode"],
            "audit_default": False,
            "audit_requires_explicit_opt_in": True,
            "review": "three separate calls"
            if config["mode"] == "real"
            else "simulated",
            "disabled": DISABLED,
        }

    @api.post("/api/contest/upload")
    async def upload(file: UploadFile = File(...)):
        with workspace.operation(uuid.uuid4().hex):
            if not file.filename or not file.filename.lower().endswith(".pdf"):
                raise HTTPException(415, "仅支持 PDF 文件")
            raw = await file.read(20 * 1024 * 1024 + 1)
            if len(raw) > 20 * 1024 * 1024:
                raise HTTPException(413, "PDF 最大为 20 MB")
            pages = await run_in_threadpool(parse_pdf, raw)
            identity = uuid.uuid4().hex
            doc = {
                "document_id": identity,
                "filename": Path(file.filename.replace("\\", "/")).name,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "created_at": now(),
                "pages": pages,
                "parse_summary": parse_summary(pages),
            }
            save(identity, ".json", doc)
            (storage / (identity + ".pdf")).write_bytes(raw)
            return {
                **doc,
                "page_count": len(pages),
                "text_preview": "\n".join(p["text"] for p in pages)[:1200],
                "chunk_count": len(chunks(doc)),
                "ocr_mode": "local_per_page",
            }

    @api.get("/api/contest/documents/{identity}/pdf")
    def source(identity: str):
        doc = read(identity, ".json")
        return FileResponse(
            storage / (identity + ".pdf"),
            media_type="application/pdf",
            filename=doc["filename"],
            content_disposition_type="inline",
        )

    @api.post("/api/contest/retrieve")
    def search(payload: Request):
        doc = read(payload.document_id, ".json")
        hits = retrieve(doc, payload.query, payload.top_k)
        return {
            "hits": hits,
            "status": "found" if hits else "insufficient_evidence",
            "parameters": PARAMETERS,
        }

    def pipeline_impl(payload: Request, metrics):
        doc = read(payload.document_id, ".json")
        if doc.get("flow") == legal_return.VERSION:
            yield from return_pipeline(payload, doc, metrics)
            return
        yield {"stage": "retrieve", "status": "running"}
        evidence = retrieve(doc, payload.query, payload.top_k)
        yield {"stage": "retrieve", "status": "complete", "count": len(evidence)}
        deadline = deadline_intent(payload.query)
        monetary = money_intent(payload.query)
        eligible = [c for c in evidence if supporting_sentences(payload.query, c)]
        draft, claims = [], []
        rejected = {"generate": 0, "revise": 0}
        audit = {"issues": [], "note": "证据不足，未执行生成和审核"}
        stages = []
        try:
            if eligible:
                base = {
                    "query": payload.query,
                    "evidence": eligible if monetary else evidence,
                }
                if monetary:
                    base["answer_policy"] = (
                        "金额问题仅返回直接支持当前事项和金额角色的完整原句，text必须等于quote；不计算、不跨页推导。无法直接摘录时返回空claims。"
                    )
                yield {"stage": "generate", "status": "running"}
                draft = validate_claims(model.call("generate", base, metrics), evidence)
                before_support_check = len(draft)
                draft = supported_claims(payload.query, draft, evidence)
                rejected["generate"] = before_support_check - len(draft)
                stages.append("generate")
                yield {"stage": "generate", "status": "complete"}
                claims = draft
                audit = {
                    "issues": [],
                    "note": "审核未启用" if draft else "证据不足，未执行审核和修订",
                }
                if payload.audit and draft:
                    yield {"stage": "review", "status": "running"}
                    audit = model.call("review", {**base, "draft": draft}, metrics)
                    if (
                        not isinstance(audit.get("issues"), list)
                        or not all(isinstance(i, str) for i in audit["issues"])
                        or not isinstance(audit.get("note"), str)
                    ):
                        raise ModelFailure("审核返回格式无效；可以重试。")
                    stages.append("review")
                    yield {"stage": "review", "status": "complete"}
                    yield {"stage": "revise", "status": "running"}
                    claims = validate_claims(
                        model.call(
                            "revise", {**base, "draft": draft, "review": audit}, metrics
                        ),
                        evidence,
                    )
                    before_support_check = len(claims)
                    claims = supported_claims(payload.query, claims, evidence)
                    rejected["revise"] = before_support_check - len(claims)
                    stages.append("revise")
                    yield {"stage": "revise", "status": "complete"}
        except ModelFailure as exc:
            raise HTTPException(
                502, {"code": "MODEL_FAILURE", "message": str(exc), "retryable": True}
            ) from None
        used = {c["citation_id"] for c in claims}
        result = {
            "result_id": uuid.uuid4().hex,
            "created_at": now(),
            "calls": metrics,
            "execution_source": execution_source if metrics else "not_called",
            "document_id": doc["document_id"],
            "filename": doc["filename"],
            "source_sha256": doc["sha256"],
            "parse_summary": doc.get("parse_summary", {}),
            "query": payload.query,
            "claims": claims,
            "draft": draft,
            "review": audit,
            "stages": stages,
            "citations": [c for c in evidence if c["citation_id"] in used],
            "model_mode": config["mode"],
            "model_configuration": configuration_record(config),
            "models": {
                "generator": config.get("model"),
                "reviewer": config.get("review_model") or config.get("model"),
            },
            "retrieval": PARAMETERS,
            "evidence_assessment": {
                "version": SUPPORT_VERSION,
                "scope": "explicit_money_extract"
                if monetary
                else ("recognized_chinese_deadline" if deadline else "not_assessed"),
                "retrieved_count": len(evidence),
                "eligible_count": len(eligible),
                "rejected_claims": rejected,
                "status": ("candidate_support" if claims else "insufficient_evidence")
                if deadline or monetary
                else "not_assessed",
                "limitation": "期限和显式金额原句规则不等于通用语义蕴含或法律正确性验证；金额计算、多页推导未实现。",
            },
            "status": "complete" if claims else "insufficient_evidence",
            "final_answer": "\n\n".join(
                c["text"] + " [" + c["citation_id"] + "]" for c in claims
            )
            or (
                MONEY_INSUFFICIENT
                if monetary
                else (INSUFFICIENT if deadline else "证据不足，无法根据当前文件回答。")
            ),
        }
        if not claims and doc.get("parse_summary", {}).get("unavailable_pages"):
            result["final_answer"] = (
                "证据不足：部分页面未能识别或待人工复核，目前无法确定答案；不能据此断言完整材料没有相关规定。"
            )
        save(result["result_id"], "-result.json", result)
        yield {"result": result}

    def return_pipeline(payload, doc, metrics):
        yield {"stage": "retrieve", "status": "running"}
        facts = payload.return_facts or legal_return.ReturnFacts()
        context = payload.return_context or legal_return.ReturnContext()
        flow, evidence = legal_return.prepare(doc, facts)
        guidance, communication = textile_guidance.build(
            flow, facts, context, payload.query, legal_return.guidance_law_pack()
        )
        flow["laws"] = legal_return.public_guidance_law_pack()
        flow["goal"] = guidance["goal"]
        flow["response"] = guidance["followup"]
        flow["legal_guidance_version"] = guidance["version"]
        flow["legal_guidance"] = guidance
        flow["communication"] = communication
        # D必须经过原有quote校验；L只来自受控法条包，不接受模型的URL或条号。
        claims = [validated for r in flow["records"] for validated in validate_claims({"claims": [
            {"text": r["quote"], "quote": r["quote"], "citation_id": r["citation_id"]}
        ]}, evidence)]
        yield {"stage": "retrieve", "status": "complete", "count": len(claims)}
        selected, stages, selection_error = [], [], None
        if not flow["gaps"] and not flow["conflicts"] and not flow["scope_mismatch"] and not flow["quality_uncertain"]:
            selection_flow = {**flow, "laws": legal_return.law_pack()}
            base = generation_payload(
                "请整理这套床单的退货材料和沟通准备。",
                facts,
                selection_flow,
                evidence,
            )
            yield {"stage": "generate", "status": "running"}
            try:
                candidates = validate_claims(model.call("generate", base, metrics), evidence)
            except ModelFailure as exc:
                if not strict_material_selection:
                    raise HTTPException(502, {"code": "MODEL_FAILURE", "message": str(exc), "retryable": False}) from None
                selection_error, candidates = "模型选句失败；保留本地事实与证据，窗口停止。", []
            finally:
                # 新流程不向任务、恢复接口或导出暴露未受控的供应商自由文本。
                for metric in metrics:
                    metric.pop("response_content", None)
            permitted = {(c["citation_id"], c["quote"]) for c in claims}
            selected = [c for c in candidates if c["text"] == c["quote"] and (c["citation_id"], c["quote"]) in permitted][:2]
            if strict_material_selection and (not 1 <= len(candidates) <= 2 or len(selected) != len(candidates)):
                selection_error = selection_error or "模型选句为空或不合法；本地claims不替代模型成果，窗口停止。"
                selected = []
            stages.append("generate")
            yield {"stage": "generate", "status": "failed" if selection_error else "complete"}
        source = execution_source if metrics else "not_called"
        generation = "本地规则整理；未调用模型" if not metrics else (
            "本地规则整理 + 离线模拟材料选句" if config["mode"] == "offline" else
            "本地规则整理 + Mock材料选句（非真实模型）" if source == "mock_interface" else
            "本地规则整理 + 外部接口材料选句（来源见调用记录）")
        if selection_error:
            generation = legal_return.SELECTION_FAILURE_NOTICE
        used = {c["citation_id"] for c in claims}
        result = {"result_id": uuid.uuid4().hex, "created_at": now(), "document_id": doc["document_id"],
                  "filename": doc["filename"], "source_sha256": doc["sha256"], "parse_summary": doc.get("parse_summary", {}),
                  "query": payload.query, "claims": claims, "draft": selected, "citations": [c for c in evidence if c["citation_id"] in used],
                  "review": {"issues": [], "note": "仅工程原句校验，未经专业法律验收"},
                  "calls": metrics, "stages": stages, "execution_source": source, "model_mode": config["mode"],
                  "model_configuration": configuration_record(config), "status": "failed" if selection_error else "complete" if stages else "insufficient_evidence",
                  "generation_mode": generation, "legal_preparation": flow,
                  "legal_guidance": guidance, "communication": communication,
                  "final_answer": legal_return.public_text(flow)}
        save(result["result_id"], "-result.json", result)
        if selection_error:
            raise HTTPException(502, {"code": "MODEL_SELECTION_FAILED", "message": selection_error,
                                      "retryable": False, "result_id": result["result_id"]})
        yield {"result": result}

    def pipeline(payload: Request, identity=None):
        identity = identity or uuid.uuid4().hex
        with workspace.operation(identity):
            signal = cancel_signals.setdefault(identity, threading.Event())
            read(payload.document_id, ".json")
            metrics = []
            turn = {
                "turn_id": identity,
                "document_id": payload.document_id,
                "query": payload.query,
                "audit": payload.audit,
                "return_facts": payload.return_facts.model_dump() if payload.return_facts else None,
                "return_context": payload.return_context.model_dump() if payload.return_context else None,
                "created_at": now(),
                "status": "running",
                "calls": metrics,
            }
            save(identity, "-turn.json", turn)
            try:
                events = pipeline_impl(payload, metrics)
                while True:
                    token = CANCEL_EVENT.set(signal)
                    try:
                        event = next(events)
                    except StopIteration:
                        break
                    finally:
                        CANCEL_EVENT.reset(token)
                    if "stage" in event:
                        turn.setdefault("events", []).append(event)
                        save(identity, "-turn.json", turn)
                    if "result" in event:
                        turn.update(
                            status="complete", result_id=event["result"]["result_id"]
                        )
                        save(identity, "-turn.json", turn)
                    yield event
            except HTTPException as exc:
                if isinstance(exc.detail, dict) and exc.detail.get("result_id"):
                    turn["result_id"] = exc.detail["result_id"]
                turn.update(
                    status="cancelled" if signal.is_set() else "failed",
                    error=exc.detail.get("message", "处理失败，请手动重试。")
                    if isinstance(exc.detail, dict)
                    else str(exc.detail),
                )
                raise
            finally:
                if turn["status"] == "running":
                    turn.update(status="interrupted", error="请求未完成，未自动重试。")
                turn["finished_at"] = now()
                save(identity, "-turn.json", turn)
                cancel_signals.pop(identity, None)

    @api.post("/api/contest/tasks", status_code=202)
    def start_task(payload: Request):
        identity = task_identity() if task_identity is not None else uuid.uuid4().hex
        with workspace.lock:
            if workspace.running:
                raise HTTPException(409, "当前任务尚未结束，请等待完成。")
            doc = read(payload.document_id, ".json")
            if payload.return_facts is not None and doc.get("flow") != legal_return.VERSION:
                raise HTTPException(422, "该材料没有家纺事实入口")
            if payload.return_context is not None and doc.get("flow") != legal_return.VERSION:
                raise HTTPException(422, "该材料没有家纺目标与回应入口")
            if doc.get("flow") == legal_return.VERSION and payload.audit:
                raise HTTPException(422, "本流程不开放审核或法律分析阶段")
            workspace.running.add(identity)
            cancel_signals[identity] = threading.Event()
            save(
                identity,
                "-turn.json",
                {
                    "turn_id": identity,
                    "document_id": payload.document_id,
                    "query": payload.query,
                    "audit": payload.audit,
                    "status": "running",
                    "created_at": now(),
                    "events": [],
                    "calls": [],
                    "return_facts": payload.return_facts.model_dump() if payload.return_facts else None,
                    "return_context": payload.return_context.model_dump() if payload.return_context else None,
                },
            )

        def run():
            try:
                for _ in pipeline(payload, identity):
                    pass
            except Exception:
                # Do not log supplier responses, config, or exception repr.
                with workspace.lock:
                    turn = read(identity, "-turn.json")
                    if turn["status"] == "running":
                        turn.update(status="failed", error="任务处理失败，请手动重试。")
                        save(identity, "-turn.json", turn)
                    workspace.running.discard(identity)
            finally:
                if on_task_finished is not None:
                    on_task_finished(identity)

        threading.Thread(target=run, daemon=True, name="contest-task").start()
        return {"turn_id": identity}

    @api.post("/api/contest/tasks/{identity}/cancel", status_code=202)
    def cancel_task(identity: str):
        checked_id(identity)
        with workspace.lock:
            signal = cancel_signals.get(identity)
            if signal is None:
                raise HTTPException(409, "任务已结束或不在当前进程中。")
            signal.set()
        return {
            "status": "cancelling",
            "message": "正在关闭本地请求；供应商处理及计费状态未知。",
        }

    @api.get("/api/contest/tasks/{identity}")
    def task_status(identity: str):
        checked_id(identity)
        for turn in workspace.snapshot()["turns"]:
            if turn["turn_id"] == identity:
                result = turn.get("result")
                if result and "legal_preparation" in result:
                    result["generation_mode"] = legal_return.generation_label(result)
                return JSONResponse(turn, headers={"Cache-Control": "no-store"})
        raise HTTPException(404, "任务不存在或已清空。")

    @api.get("/api/contest/samples/equipment")
    def demo_sample():
        return FileResponse(
            ROOT / "samples/pdf/synthetic_zh_equipment.pdf",
            media_type="application/pdf",
            filename="合成设备借用约定.pdf",
        )

    @api.post("/api/contest/review")
    def review(payload: Request):
        for event in pipeline(payload):
            if "result" in event:
                return event["result"]

    @api.post("/api/contest/answer-stream")
    def answer_stream(payload: Request):
        read(payload.document_id, ".json")

        def events():
            try:
                for event in pipeline(payload):
                    yield json.dumps(event, ensure_ascii=False) + "\n"
            except HTTPException as exc:
                yield json.dumps({"error": exc.detail}, ensure_ascii=False) + "\n"

        return StreamingResponse(events(), media_type="application/x-ndjson")

    @api.get("/api/contest/export/{identity}")
    def export(
        identity: str, format: str = Query("json", pattern="^(json|markdown|pdf)$")
    ):
        result = read(identity, "-result.json")
        communication_path = storage / (checked_id(identity) + "-communication.json")
        if communication_path.is_file():
            result["communication"] = json.loads(communication_path.read_text(encoding="utf-8"))
            result["legal_preparation"]["communication"] = result["communication"]
            result["final_answer"] = legal_return.public_text(result["legal_preparation"])
        if format == "json":
            return JSONResponse(
                result,
                headers={"Content-Disposition": "attachment; filename=result.json"},
            )
        if format == "markdown":
            return Response(
                markdown(result),
                media_type="text/markdown",
                headers={"Content-Disposition": "attachment; filename=result.md"},
            )
        return Response(
            pdf_bytes(result),
            media_type="application/pdf",
            headers={"Content-Disposition": "attachment; filename=result.pdf"},
        )

    @api.post("/api/contest/results/{identity}/communication")
    def save_communication(identity: str, payload: CommunicationUpdate):
        result = read(identity, "-result.json")
        communication = result.get("communication")
        guidance = result.get("legal_guidance")
        if not communication or not guidance:
            raise HTTPException(422, "该结果没有家纺沟通成果")
        if payload.expected_fingerprint != communication["basis_fingerprint"]:
            raise HTTPException(409, "事实版本已变化，请使用新结果中的沟通稿")
        option = None
        generated = communication["initial_generated_text"]
        if payload.selected_option:
            option = next((item for item in guidance["options"] if item["id"] == payload.selected_option), None)
            if option is None:
                raise HTTPException(422, "沟通选项不属于本结果")
            generated = option["draft_text"]
        current = safe_communication_text(payload.current_text)
        path = storage / (checked_id(identity) + "-communication.json")
        previous = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else communication
        changed = current != previous.get("current_text") or payload.selected_option != previous.get("selected_option")
        previous_user_draft = previous.get("previous_user_draft")
        previous_text = previous.get("current_text", "")
        previous_was_user_edited = bool(previous.get("edited_by_user"))
        if changed and previous_was_user_edited and previous_text:
            previous_user_draft = {
                "current_text": safe_communication_text(previous_text),
                "selected_option": previous.get("selected_option"),
                "revision": previous.get("revision", 0),
                "saved_revision": previous.get("revision", 0),
                "label": "上一份用户编辑稿；仅保留一份",
            }
        if previous_user_draft and current == previous_user_draft.get("current_text"):
            previous_user_draft = None
        saved = {
            **communication,
            "selected_option": payload.selected_option,
            "generated_text": generated,
            "current_text": current,
            "edited_by_user": current != generated,
            "previous_user_draft": previous_user_draft,
            "revision": previous.get("revision", 0) + (1 if changed else 0),
            "stale": False,
        }
        save(identity, "-communication.json", saved)
        result["communication"] = saved
        result["legal_preparation"]["communication"] = saved
        result["final_answer"] = legal_return.public_text(result["legal_preparation"])
        save(identity, "-result.json", result)
        return saved

    return api


def __getattr__(name):
    # 保留uvicorn app:app入口；导入工厂时不加载本机配置或创建共享工作区。
    if name == "app":
        if os.environ.get("CONTEST_DEMO_SETTINGS") or os.environ.get("CONTEST_DEMO_DATA"):
            raise RuntimeError("受控演示禁止旧入口；必须使用demo_server:deployment_app --factory")
        instance = create_app()
        globals()[name] = instance
        return instance
    raise AttributeError(name)
