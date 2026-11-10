"""P项专用契约。无启动、凭证读取或解锁副作用；时间必须另行批准。"""

from datetime import timedelta, timezone
import hashlib
import json
import math

import legal_return
from model_reasoning_v2 import TEXTILE_SELECTION_STRATEGY, build_request, validate_settings

POLICY = "textile-one-send-window-thinking-disabled-v2"
GENERATION_STRATEGY = TEXTILE_SELECTION_STRATEGY
PRICE_POLICY = "flash-cny-peak-envelope-v1"
QUERY = "请整理这套床单的退货材料和沟通准备。"
INVITE = "textile-validation"
ORDER = ("missing", "conflict", "normal")
KEYS = dict(zip(ORDER, ("textilemissing", "textileconflict", "textile")))
BJ = timezone(timedelta(hours=8))
ANSWER_POLICY = "仅选择最多两条材料完整原句，text必须等于quote。不得作法律适用、退货资格、责任、金额或期限结论。无法摘录返回空claims。"


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def scoped(settings):
    return settings.get("billing_policy") == POLICY


def prepared_settings():
    # 未批准模板：没有生效时间或永久有效的10月22日价格授权。
    return {
        "billing_policy": POLICY, "billing_price_version": "",
        "generation_strategy": GENERATION_STRATEGY,
        "allow_real_calls": False, "window_approved": False,
        "window_start": None, "window_expires": None,
        "price_checked_at": None, "activity_expires": 0.0,
        "global_tasks": 3, "invite_tasks": 3, "max_invites": 1,
        "max_sessions_per_invite": 1, "max_session_history": 1,
        "global_budget_microcny": 6_330_000,
        "invite_budget_microcny": 6_330_000,
    }


def require(settings, now):
    start, end, checked = (settings.get(k) for k in
                            ("window_start", "window_expires", "price_checked_at"))
    if settings.get("window_approved") is not True or any(
        type(v) not in (int, float) or not math.isfinite(v)
        for v in (start, end, checked, now)
    ):
        raise ValueError("新窗口未批准或未设置执行前核价/起止时间")
    if (settings.get("billing_price_version") != PRICE_POLICY
            or not 0 < end - start <= 1800
            or not 0 <= start - checked <= 600
            or settings.get("activity_expires") != end
            or not start <= now < end):
        raise ValueError("新窗口或执行前核价期限无效/过期")
    expected = prepared_settings()
    if settings.get("generation_strategy") != expected["generation_strategy"]:
        raise ValueError("新窗口生成策略不匹配")
    for key in ("global_tasks", "invite_tasks", "max_invites",
                "max_sessions_per_invite", "max_session_history",
                "global_budget_microcny", "invite_budget_microcny"):
        if type(settings.get(key)) is not int or settings[key] != expected[key]:
            raise ValueError("新窗口次数、金额或身份策略不匹配")


def contract(settings):
    return digest({**{k: settings.get(k) for k in prepared_settings()
                       if k != "allow_real_calls"},
                   "materials": legal_return.fixtures(),
                   "laws": legal_return.law_pack(), "order": ORDER,
                   "query": QUERY, "invite": INVITE, "max_sends": 1})


def facts_for(kind):
    if kind == "missing":
        return {"values": {}, "confirmations": {}}
    confirmations = {k: "一致" for k in legal_return.LABELS if k != "issue_detail"}
    values = {}
    if kind == "conflict":
        confirmations["washed"] = "有不同"
        values["washed"] = "未清洗"
    return {"values": values, "confirmations": confirmations}


def generation_payload(query, facts, flow, evidence):
    return {"query": query, "evidence": evidence,
            "return_facts": facts.model_dump(), "legal_sources": flow["laws"],
            "answer_policy": ANSWER_POLICY}


def binding(doc, payload, config):
    validate_settings(config)
    if (config.get("request_strategy") != GENERATION_STRATEGY
            or config.get("reasoning_effort") != "none"
            or config.get("stage_max_output_tokens", {}).get("generate") != 1200):
        raise ValueError("新窗口请求与关闭思考策略不匹配")
    kind = next((kind for kind, key in KEYS.items()
                 if legal_return.fixtures()[key]["sha256"] == doc.get("sha256")), None)
    if (kind is None or doc.get("flow") != legal_return.VERSION
            or payload.get("document_id") != doc.get("document_id")
            or payload.get("query") != QUERY or payload.get("audit", False) is not False):
        raise ValueError("新窗口仅允许三指定合成家纺任务")
    facts = legal_return.ReturnFacts.model_validate(payload.get("return_facts"))
    if facts.model_dump() != facts_for(kind):
        raise ValueError("新窗口事实确认与指定任务不匹配")
    flow, evidence = legal_return.prepare(doc, facts)
    ready = not any(flow[k] for k in ("gaps", "conflicts", "scope_mismatch", "quality_uncertain"))
    if ((kind == "normal") != ready
            or (kind == "missing" and (flow["records"] or not flow["gaps"]))
            or (kind == "conflict" and not flow["conflicts"])):
        raise ValueError("指定任务事实门槛不符")
    body = build_request(config, "generate", generation_payload(QUERY, facts, flow, evidence))
    return {"kind": kind, "material_sha256": doc["sha256"],
            "facts_sha256": digest(facts.model_dump()),
            "body_sha256": digest(body) if ready else None}
