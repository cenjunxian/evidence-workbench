"""Page-aware text extraction and optional local OCR; never silently omit pages."""

import importlib.util
import json
import subprocess
import sys
import time
import threading
from pathlib import Path

OCR_NOTICE = "OCR识别文本，可能存在错误；摘录匹配仅针对识别结果，不代表已与原图逐字核验。金额、日期、否定词和手写内容请核对原页。"
INSTALL = "请在竞赛Python环境执行 python -m pip install -r contest/backend/requirements-ocr.lock.txt，重启竞赛服务后重新上传；或提供可复制文字的PDF。重复重试不会安装OCR。"
MAX_OCR_PAGES = 8
PAGE_SECONDS = 30
DOCUMENT_SECONDS = 90
OCR_SLOT = threading.BoundedSemaphore(1)


def ocr_available():
    return all(
        importlib.util.find_spec(name) is not None
        for name in ("rapidocr_onnxruntime", "pypdfium2", "onnxruntime")
    )


def structurally_blank(page):
    if page.get("/Annots"):
        return False
    content = page.get_contents()
    if content is None:
        return True
    # Some PDF writers emit coordinate/font setup even for a blank page.
    # Only classify a closed allowlist of non-painting operations as blank.
    harmless = {
        b"q",
        b"Q",
        b"cm",
        b"BT",
        b"ET",
        b"Tf",
        b"TL",
        b"Td",
        b"TD",
        b"Tm",
        b"T*",
        b"Tr",
        b"Tw",
        b"Tc",
        b"Tz",
        b"Ts",
    }
    try:
        return all(operator in harmless for _, operator in content.operations)
    except Exception:
        return False


def run_page(raw, index, timeout):
    started = time.monotonic()
    if not OCR_SLOT.acquire(timeout=timeout):
        return {"status": "timeout", "reason": "本地OCR繁忙，请稍后手动重新上传"}
    try:
        launched = time.monotonic()
        done = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).with_name("ocr_worker.py")),
                str(index),
                str(time.time()),
            ],
            input=raw,
            capture_output=True,
            timeout=max(0.1, timeout - (time.monotonic() - started)),
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        if done.returncode:
            return {"status": "failed", "reason": "本地OCR进程失败，未记录原文日志"}
        value = json.loads(done.stdout)
        timings = value.setdefault("timing", {})
        timings["queue_seconds"] = launched - started
        timings["process_wall_seconds"] = time.monotonic() - launched
        # Residual includes interpreter startup, IPC and shutdown; do not
        # mislabel it as pure process startup or OCR inference.
        timings["startup_ipc_shutdown_seconds"] = max(
            0,
            timings["process_wall_seconds"]
            - timings.get("worker_seconds", timings["process_wall_seconds"]),
        )
        return value
    except subprocess.TimeoutExpired:
        return {
            "status": "timeout",
            "reason": "本页OCR超时，已终止识别进程；请拆分或人工核查",
        }
    except Exception:
        return {"status": "failed", "reason": "本地OCR未能返回可用结果"}
    finally:
        OCR_SLOT.release()


def extract_pages(reader, raw):
    pages, attempted = [], 0
    started = time.monotonic()
    for i, page in enumerate(reader.pages):
        try:
            text = (page.extract_text() or "").strip()
        except Exception:
            text = ""
        item = {"page": i + 1, "text": text, "source": "text_layer", "status": "parsed"}
        if text:
            # Preserve existing text-layer behavior. Do not pretend that any
            # raster content on a text-bearing page was also transcribed.
            resources = page.get("/Resources", {})
            if hasattr(resources, "get_object"):
                resources = resources.get_object()
            if resources and "/XObject" in resources:
                item["notice"] = "使用文字层；同页图像内容未另作OCR，请核对原页。"
        elif structurally_blank(page):
            item.update(
                source="blank", status="blank", notice="空白页（无页面绘制内容）"
            )
        elif not ocr_available():
            item.update(source="unreadable", status="not_installed", notice=INSTALL)
        elif (
            attempted >= MAX_OCR_PAGES or time.monotonic() - started >= DOCUMENT_SECONDS
        ):
            item.update(
                source="unreadable",
                status="limit",
                notice="达到8页OCR或90秒文档识别限额，请拆分文件",
            )
        else:
            attempted += 1
            remaining = DOCUMENT_SECONDS - (time.monotonic() - started)
            item.update(run_page(raw, i, min(PAGE_SECONDS, max(0.1, remaining))))
            if item["status"] not in ("parsed", "blank", "needs_review"):
                item["source"] = "unreadable"
            item["notice"] = item.pop(
                "reason",
                OCR_NOTICE if item["source"] == "ocr" else "空白页（渲染为白页）",
            )
        pages.append(item)
    return pages


def summary(pages):
    failed = [
        p["page"] for p in pages if p.get("status", "parsed") not in ("parsed", "blank")
    ]
    ocr = [p["page"] for p in pages if p.get("source") == "ocr"]
    usable = [
        p["page"]
        for p in pages
        if p.get("status", "parsed") == "parsed" and p["text"].strip()
    ]
    status = (
        "partial"
        if failed and usable
        else "unreadable"
        if failed
        else "complete_with_ocr"
        if ocr
        else "complete"
        if usable
        else "blank"
    )
    notice = (
        f"可用页：{','.join(map(str, usable)) or '无'}；不可用/待复核页：{','.join(map(str, failed))}；回答仅基于可用页面，不能据此断言整份材料缺少依据。"
        if failed
        else ""
    )
    if ocr:
        notice += OCR_NOTICE
    return {
        "status": status,
        "unavailable_pages": failed,
        "ocr_pages": ocr,
        "usable_pages": usable,
        "notice": notice,
    }
