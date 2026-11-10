"""有限材料整理；不作法律适用、期限或责任判断。"""

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

ROOT = Path(__file__).resolve().parents[1]
VERSION = "textile-local-preparation-v1"
LAW_PATH = ROOT / "samples/legal/return-law-2020.json"
GUIDANCE_LAW_PATH = ROOT / "samples/legal/return-law-2026-research.json"

from pydantic import model_validator

# 枚举是可描述的事实，不要求用户评价法律标准。
OPTIONS = {
    "purpose": ["生活自用", "经营采购"],
    "seller": ["企业店铺线索", "个人卖家线索"],
    "issue": ["不喜欢", "有异常现象", "两者不确定"],
    "notice": ["已发送", "未发送"],
    "opened": ["已拆封", "未拆封"],
    "washed": ["已清洗", "未清洗"],
    "used": ["已铺床使用", "未铺床使用"],
    "labels": ["齐全未剪", "缺件或标识已摘剪"],
    "click": ["主动勾选或点击", "默认选中", "未操作", "未找到记录"],
}
LABELS = {
    "purpose": "购买用途",
    "seller": "卖方身份线索",
    "issue": "想退货的现象",
    "issue_detail": "异常现象与发生经过",
    "received": "签收记录日期",
    "notice": "退货通知是否发出",
    "notice_record": "通知时间、渠道与记录",
    "opened": "拆封情况",
    "washed": "清洗情况",
    "used": "铺床使用情况",
    "labels": "配件与商标标识",
    "page_words": "页面具体文字",
    "page_step": "文字出现步骤",
    "click": "勾选或点击经过",
}


class ReturnFacts(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    values: dict[str, str] = Field(default_factory=dict, max_length=14)
    confirmations: dict[str, str] = Field(default_factory=dict, max_length=14)

    @field_validator("confirmations")
    @classmethod
    def check_confirmations(cls, values):
        if any(k not in LABELS or v not in ("一致", "有不同", "不清楚") for k, v in values.items()):
            raise ValueError("确认字段或选项无效")
        return values

    @field_validator("values")
    @classmethod
    def check_values(cls, values):
        for key, value in values.items():
            if key not in LABELS or len(value) > 180:
                raise ValueError("事实字段或长度无效")
            if key in OPTIONS and value not in OPTIONS[key] + ["不知道", "有争议"]:
                raise ValueError("事实选项无效")
        return values


class ReturnContext(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    original_query: str = Field(default="", max_length=2000)
    goal: str | None = Field(default=None, max_length=20)
    response_status: str | None = Field(default=None, max_length=20)
    response_kind: str | None = Field(default=None, max_length=20)
    response_text: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def check_goal_and_response(self):
        if self.goal is not None and self.goal not in ("了解条件", "准备首次沟通", "整理已有分歧"):
            raise ValueError("沟通目标选项无效")
        if self.response_status is not None and self.response_status not in (
            "尚未沟通", "已沟通暂未回应", "收到回复"
        ):
            raise ValueError("回应状态选项无效")
        if self.response_kind is not None and self.response_kind not in (
            "明确不同意", "提出问题或条件", "其他回复"
        ):
            raise ValueError("回应类型选项无效")
        if self.response_status != "收到回复" and (self.response_kind is not None or self.response_text):
            raise ValueError("只有收到回复时才能填写回复内容")
        if self.response_status == "收到回复" and self.response_text and self.response_kind is None:
            raise ValueError("填写回复内容时需选择回复类型")
        return self

def fixtures():
    return json.loads(
        (ROOT / "samples/textile-manifest.json").read_text(encoding="utf-8")
    )


def law_pack():
    return json.loads(LAW_PATH.read_text(encoding="utf-8"))


def guidance_law_pack():
    return json.loads(GUIDANCE_LAW_PATH.read_text(encoding="utf-8"))


def public_guidance_law_pack():
    pack = guidance_law_pack()
    return {
        "name": pack["name"],
        "pack_version": pack["pack_version"],
        "version": pack["version"],
        "status": pack["status"],
        "accessed": pack["accessed"],
        "url": pack["url"],
        "sha256": pack["source_hashes"]["official_pdf_sha256"],
        "amendment": pack["amendment"],
        "articles": pack["articles"],
    }


def catalog_metadata(key):
    item = fixtures()[key]
    return {
        "flow": VERSION,
        "variant": item["variant"],
        "fact_labels": LABELS,
        "fact_options": OPTIONS,
        "material_records": item["records"],
        "material_highlights": [
            {"page": r["page"], "quote": r["quote"], "rect": r["rect"]}
            for r in item["records"]
        ],
    }


def prepare(doc, facts):
    item = next((x for x in fixtures().values() if x["sha256"] == doc["sha256"]), None)
    if item is None or doc.get("flow") != VERSION:
        raise ValueError("不属于预置家纺材料")
    from retrieval import chunks

    evidence = chunks(doc)
    records = []
    for record in item["records"]:
        hit = next(
            (
                c
                for c in evidence
                if c["page"] == record["page"] and record["quote"] in c["text"]
            ),
            None,
        )
        if hit is None:
            raise ValueError("合成记录未通过材料原句校验")
        records.append({**record, "citation_id": hit["citation_id"], "type": "D"})
    # 适用性由服务端根据本次已确认/补充的退货现象最终判断。客户端可以隐藏
    # 不适用字段，但不能借此绕过服务端的事实门槛。
    issue_record = next((r for r in records if r["field"] == "issue"), None)
    issue_confirmation = facts.confirmations.get("issue")
    issue_value = facts.values.get("issue", "").strip()
    if issue_confirmation == "一致" and issue_record:
        issue_path = issue_record["value"]
    elif issue_confirmation == "有不同" and issue_value:
        issue_path = issue_value
    else:
        issue_path = ""
    issue_detail_applicable = issue_path in ("有异常现象", "两者不确定")
    issue_detail_not_applicable = issue_path == "不喜欢"

    users = []
    for key, label in LABELS.items():
        record = next((r for r in records if r["field"] == key), None)
        if key == "issue_detail" and issue_detail_not_applicable:
            # 这是状态表达，不是把材料或用户话语伪造成U陈述。
            users.append({"type": "A", "field": key, "label": label,
                          "value": "本路径不适用", "confirmation": None,
                          "applicability": "not_applicable",
                          "source": "系统整理",
                          "material_citation_id": record["citation_id"] if record else None})
            continue
        confirmation = facts.confirmations.get(key)
        value = facts.values.get(key, "").strip()
        # 确认仅引用本次经原句校验的D，不信任客户端回传的材料值。
        if confirmation == "一致" and record and not value:
            value = record["value"]
        elif confirmation == "不清楚" and not value:
            value = "不知道"
        elif confirmation == "有不同" and not value:
            value = "待补充不同说法"
        users.append({"type": "U", "field": key, "label": label,
                      "value": value or "尚未确认", "confirmation": confirmation,
                      "applicability": "required" if key != "issue_detail" or issue_detail_applicable else "pending",
                      "material_citation_id": record["citation_id"] if record else None})
    gaps, conflicts, fact_status = [], [], []
    for user in users:
        record = next((r for r in records if r["field"] == user["field"]), None)
        statuses = []
        if user["field"] == "issue_detail" and user["value"] == "本路径不适用":
            fact_status.append({"field": user["field"], "label": user["label"],
                                "statuses": ["本路径不适用"], "applicability": "not_applicable"})
            continue
        if record is None:
            gaps.append(f"D缺少{user['label']}记录；U陈述也不能代替材料凭证。")
            statuses.append("材料未记载")
        if user["value"] == "尚未确认":
            gaps.append(f"{user['label']}：用户尚未确认，材料记载不等于已查明事实。")
            statuses.append("用户尚未确认")
        if user["value"] in ("不知道", "有争议") or user["confirmation"] == "不清楚":
            gaps.append(
                f"U的{user['label']}为{user['value']}；请核对对应页面或记录，不据此认定事实。"
            )
            statuses.append("用户不清楚" if user["value"] == "不知道" or user["confirmation"] == "不清楚" else "说法待核对")
        elif user["field"] == "click" and user["value"] == "未找到记录":
            gaps.append("尚未找到勾选或点击的操作记录；不据此判断是否完成购买时确认。")
        elif record and user["value"] != "尚未确认" and (user["value"] != record["value"] or user["confirmation"] == "有不同"):
            conflicts.append(
                {
                    "field": user["field"],
                    "label": user["label"],
                    "U": user["value"],
                    "D": record["value"],
                    "citation_id": record["citation_id"],
                }
            )
            statuses.append("双方说法不同")
        fact_status.append({"field": user["field"], "label": user["label"], "statuses": statuses,
                            "applicability": user.get("applicability", "required")})
    scope = any(
        u["field"] in ("purpose", "seller")
        and u["value"] in ("经营采购", "个人卖家线索")
        for u in users
    ) or any(
        r["field"] in ("purpose", "seller")
        and r["value"] in ("经营采购", "个人卖家线索")
        for r in records
    )
    quality = any(
        u["field"] == "issue" and u["value"] in ("有异常现象", "两者不确定")
        for u in users
    ) or any(r["field"] == "issue" and r["value"] in ("有异常现象", "两者不确定") for r in records)
    state = (
        "范围待核对"
        if scope
        else "记录冲突"
        if conflicts
        else "信息待补"
        if gaps
        else "材料已整理"
    )
    recorded = {r["field"]: r["value"] for r in records}
    user_by_field = {u["field"]: u for u in users}

    def describe(key):
        label = LABELS[key]
        user = user_by_field[key]
        material = f"材料记载{label}「{recorded[key]}」" if key in recorded else f"材料未记载{label}"
        if user["value"] == "本路径不适用":
            return material + "；当前退货现象路径不涉及异常经过，本项标为本路径不适用。"
        if user["value"] == "尚未确认":
            return material + "；你尚未确认这条记录。"
        if user["confirmation"] == "一致" and user["value"] == recorded.get(key):
            return material + "；你确认与材料说法一致，尚未核实。"
        if user["confirmation"] == "不清楚" or user["value"] in ("不知道", "有争议", "待补充不同说法"):
            return material + f"；你补充的状态为「{user['value']}」，仍待核对。"
        return material + f"；你补充「{user['value']}」。"

    overview = [describe("issue"), describe("notice_record")]
    if conflicts:
        # 只并列来源，不从差异推导哪一方属实或法律效果。
        overview.append("主要不同说法：" + "；".join(
            f"{c['label']}，材料记载「{c['D']}」，你补充「{c['U']}」，两者尚不一致"
            for c in conflicts[:2]
        ) + (f"；另有{len(conflicts) - 2}项可在核对详情查看。" if len(conflicts) > 2 else "。"))
    elif user_by_field["notice"]["value"] != "尚未确认":
        overview.append(describe("notice"))
    else:
        overview.append("目前没有已记录的不同说法；尚未确认的事项仍保留为待确认，不代表双方意见一致。")

    notice_action = (
        f"核对通知消息原文、发送时间、渠道和回执；材料列有「{recorded['notice_record']}」，保留你另行补充的说法。"
        if recorded.get("notice_record") else
        "先找退货通知消息原文、发送时间、渠道和回执；找不到时保留为未提供，不推算期限。"
    )
    page_action = (
        f"沟通时把商品页文字「{recorded.get('page_words', '未记载')}」、出现步骤和操作记录放在一起，可询问卖方依据的具体商品说明。"
    )
    state_action = "逐项整理拆封、清洗、使用、配件与标识记录，标明记录人和时间；不据此判断是否可退。"
    candidates = [
        (0 if conflicts else 9, "先并列核对不同说法的来源、时间及原句；核对前不选择其中一方作为事实。"),
        (1 if any(s["field"] in ("notice", "notice_record") and s["statuses"] for s in fact_status) else 5, notice_action),
        (2 if any(s["field"] == "received" and "材料未记载" in s["statuses"] for s in fact_status) else 9,
         "查找签收记录，保留原文与来源；没有记录时明确标为未提供，不计算法律期限。"),
        (2 if any(c["field"] in ("opened", "washed", "used", "labels") for c in conflicts) else 4, state_action),
        (3, page_action),
    ]
    actions = [text for priority, text in sorted(candidates, key=lambda item: item[0]) if priority < 9]
    if scope:
        actions = [
            "存在经营采购或个人卖家线索，超出本生活消费演示范围。仅整理身份、用途和交易记录，不进入其他法律流程。"
        ]
    elif quality:
        actions.insert(
            0,
            "存在异常现象或退货原因不确定时，先整理现象、发生时间及卖方回应；本版不判断质量责任，也不强迫选择法律路径。",
        )
    return {
        "flow_version": VERSION,
        "fact_contract_version": "material-confirmation-v2",
        "state": state,
        "users": users,
        "records": records,
        "gaps": gaps,
        "conflicts": conflicts,
        "fact_status": fact_status,
        "applicability": {"issue_detail": "not_applicable" if issue_detail_not_applicable else
                           "required" if issue_detail_applicable else "pending"},
        "scope_mismatch": scope,
        "quality_uncertain": quality,
        "overview": overview,
        "actions": actions,
        "primary_action_count": min(3, len(actions)),
        "laws": law_pack(),
        "labels": LABELS,
        "boundary": "仅整理预置合成材料与沟通准备；所列法源仅核对到官方PDF标注的2020修订文本，后续修订链及2026适用状态未确认。法律解释为内部研究草案、无专业法律评审；不判断必然可退、违法、赔偿、胜算或个案期限。",
    }, evidence


def public_text(flow):
    lines = ["A 本次整理摘要"] + flow.get("overview", [flow["state"]])
    lines += ["A 优先沟通准备"] + flow["actions"][:flow.get("primary_action_count", 3)]
    if len(flow["actions"]) > flow.get("primary_action_count", 3):
        lines += ["A 其他准备"] + flow["actions"][flow.get("primary_action_count", 3):]
    lines += [f"A 核对详情：{flow['state']}；材料记录{len(flow['records'])}条（不代表已核实）。",
              flow["boundary"], "U 用户陈述（未核实）"]
    lines += [
        f"{u['label']}：{u['value']}（来源：{u.get('source') or '用户陈述'}；确认：{u.get('confirmation') or '未选择'}；关联材料：{u.get('material_citation_id') or '无'}）"
        for u in flow["users"]
    ]
    lines += ["D 材料记载（不等于已查证事实）"]
    lines += [
        f"第{r['page']}页：{r['quote']} [{r['citation_id']}]" for r in flow["records"]
    ]
    lines += ["A 缺口与冲突"] + flow["gaps"]
    lines += [
        f"{c['label']}：U={c['U']} / D={c['D']}；尚未确认。" for c in flow["conflicts"]
    ]
    guidance = flow.get("legal_guidance")
    communication = flow.get("communication")
    if guidance:
        lines += ["A 本次目标：" + (guidance["goal"]["selected"] or "尚未选择；未视为用户确认。")]
        if guidance["goal"].get("original_query"):
            lines.append("U 用户原话：" + guidance["goal"]["original_query"])
        lines.append("A 逐项理解")
        for issue in guidance["issues"]:
            lines += [issue["question"], issue["why"], "相关事实："]
            for fact in issue["facts"]:
                lines.append(
                    f"{fact['field']}：U用户陈述={fact['U']}；D材料记载={fact['D']}；"
                    f"冲突={'是' if fact['conflict'] else '否'}；"
                    + (f"D来源第{fact['page']}页 [{fact['citation_id']}]" if fact["citation_id"] else "D无对应记录")
                )
            lines += ["相关依据："] + [
                f"{law['title']}：{law['summary']}（摘要，非原文；版本 {law['source_version']}；{law['source_url']}）"
                for law in issue["laws"]
            ] + [issue["why"], "当前：" + issue["current"],
                      "条件成立时：" + issue["if_supported"], "条件不成立时：" + issue["if_not_supported"],
                      "仍未知：" + "；".join(issue["unknowns"]), "边界：" + issue["restriction"]]
        lines += ["A 回应状态：" + guidance["followup"]["status"], guidance["followup"]["next"]]
        for option in guidance["options"]:
            lines += ["A 下一步：" + option["title"], "前提：" + option["premise"],
                      "所需材料：" + "、".join(option["materials"]), "停止或转向：" + option["stop_or_redirect"]]
        if communication:
            lines += ["A 可发给商家的正文（当前可见稿；" + communication["source_label"] + ("；由用户修改" if communication["edited_by_user"] else "") + ")",
                      communication["current_text"]]
            if communication.get("generated_text") != communication.get("initial_generated_text"):
                lines += ["A 当前选项的规则生成稿（留档）", communication["generated_text"]]
            if communication.get("initial_generated_text") != communication.get("current_text"):
                lines += ["A 最初规则生成稿（留档）", communication["initial_generated_text"]]
            previous = communication.get("previous_user_draft")
            if previous and previous.get("current_text"):
                lines += ["A 上一份用户编辑稿（仅保留一份，可在工作台恢复）", previous["current_text"]]
            lines += ["A 发送前事实核对提醒"] + communication.get("fact_check_reminders", [])
            source_notes = communication.get("source_notes", {})
            if source_notes:
                lines += ["L/A 来源、规则版本与未知说明",
                          "沟通规则版本：" + source_notes.get("rules_version", "未记录"),
                          "法源名称：" + source_notes.get("law_name", "未记录"),
                          "法源数据版本：" + source_notes.get("law_pack_version", "未记录"),
                          "核对文本版本：" + source_notes.get("law_version", "未记录"),
                          "核对状态：" + source_notes.get("law_status", "未记录"),
                          "核对日期：" + source_notes.get("checked_at", "未记录"),
                          "官方来源：" + source_notes.get("official_url", "未记录"),
                          "相关法源："]
                lines += [f"{law['title']}：{law['summary']}（{law['source_version']}；{law['source_url']}）"
                          for law in source_notes.get("laws", [])]
                lines += ["相关事实来源："]
                lines += [f"{fact['field']}：U={fact['U']}（{fact.get('confirmation') or '未确认'}）；D={fact['D']}；"
                          + (f"第{fact['page']}页 [{fact['citation_id']}]" if fact.get("citation_id") else "D无对应材料")
                          for fact in source_notes.get("facts", [])]
                lines += ["仍未知："] + source_notes.get("unknowns", [])
            lines += ["A 其他待确认事项"] + communication.get("pending", [])
    return "\n\n".join(lines)


SELECTION_FAILURE_NOTICE = "模型选句失败；以下仅为本地材料整理，本次未产生有效模型选句。"


def generation_label(result):
    if result.get("status") == "failed":
        return SELECTION_FAILURE_NOTICE
    return result["generation_mode"]


def _markdown_literal(value):
    value = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    for char in "\\`*_{}[]()#+.!|~-":
        value = value.replace(char, "\\" + char)
    return value


def export_lines(result, markdown_safe=False):
    flow = result["legal_preparation"]
    summary_flow = {**flow}
    summary_flow.pop("communication", None)
    summary = public_text(summary_flow)
    lines = [
        "# 家纺退货材料核对与沟通准备",
        f"结果编号：{result['result_id']}",
        f"问题：{_markdown_literal(result['query']) if markdown_safe else result['query']}",
        f"流程版本：{flow['flow_version']}",
        "生成模式：" + generation_label(result),
        f"材料：{result['filename']} / SHA-256 {result['source_sha256']}",
        _markdown_literal(summary) if markdown_safe else summary,
        "L 官方来源（独立于材料页码，不作适用结论）",
    ]
    pack = flow["laws"]
    lines += [
        pack["name"],
        "法源数据包版本：" + pack.get("pack_version", "历史版本未记录"),
        pack["version"],
        pack["status"],
        "查阅日期：" + pack["accessed"],
        "官方URL：" + pack["url"],
        "官方PDF SHA-256：" + pack["sha256"],
    ]
    amendment = pack.get("amendment")
    if amendment:
        lines += [
            "修订依据：" + amendment["name"],
            "修订公布与施行：" + amendment["published"] + "；" + amendment["effective"],
            "修订差异：" + amendment["rule_specific_change"],
            "修订决定来源：" + amendment["url"],
        ]
    for article in pack["articles"]:
        lines += [
            article["id"] + " / " + article["title"],
            "逐字原文（仅排版空白归一）：" + article["verbatim"],
            "摘要（非原文）：" + article["summary"],
        ]
    if result.get("communication"):
        item = result["communication"]
        generated = item.get("generated_text", item["initial_generated_text"])
        current = item.get("current_text", generated)
        encode = _markdown_literal if markdown_safe else lambda x: x
        lines += ["A 可发给商家的正文（当前可见稿；" + item["source_label"] + ("；由用户修改" if item["edited_by_user"] else "") + ")",
                  encode(current), "A 规则生成稿（留档）", encode(generated),
                  "A 用户修改仅表示编辑，不是经核实事实或法律判断。",
                  "A 发送前事实核对提醒"]
        lines += [encode(text) for text in item.get("fact_check_reminders", [])]
        if item.get("initial_generated_text") != generated:
            lines += ["A 最初规则生成稿（留档）", encode(item["initial_generated_text"])]
        previous = item.get("previous_user_draft")
        if previous and previous.get("current_text"):
            lines += ["A 上一份用户编辑稿（仅保留一份，可在工作台恢复）", encode(previous["current_text"])]
        source_notes = item.get("source_notes", {})
        if source_notes:
            lines += ["L/A 来源、规则版本与未知说明",
                      "沟通规则版本：" + source_notes.get("rules_version", "未记录"),
                      "法源名称：" + source_notes.get("law_name", "未记录"),
                      "法源数据版本：" + source_notes.get("law_pack_version", "未记录"),
                      "核对文本版本：" + source_notes.get("law_version", "未记录"),
                      "法源状态：" + source_notes.get("law_status", "未记录"),
                      "核对日期：" + source_notes.get("checked_at", "未记录"),
                      "官方来源：" + source_notes.get("official_url", "未记录"),
                      "相关法源："]
            lines += [f"{law['title']}：{law['summary']}（{law['source_version']}；{law['source_url']}）"
                      for law in source_notes.get("laws", [])]
            lines += ["相关事实来源："]
            lines += [encode(f"{fact['field']}：U={fact['U']}（{fact.get('confirmation') or '未确认'}）；D={fact['D']}；"
                             + (f"第{fact['page']}页 [{fact['citation_id']}]" if fact.get("citation_id") else "D无对应材料"))
                      for fact in source_notes.get("facts", [])]
            lines += ["仍未知："] + [encode(text) for text in source_notes.get("unknowns", [])]
        lines += ["附件准备：" + "、".join(item["attachments"]),
                  "其他待确认：" + "；".join(item["pending"])]
    lines += [
        "网页返回上下文由本会话保存；导出文件不携带会话访问令牌。请在原会话按材料引用ID返回原PDF页，不能跨会话打开私有对象。"
    ]
    return "\n\n".join(lines)
