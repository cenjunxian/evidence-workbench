"""单一合成家纺案例的确定性解释与沟通成果，不进行模型法律推理。"""

import hashlib
import json

VERSION = "textile-guidance-r20261026-v3"
GOALS = ("了解条件", "准备首次沟通", "整理已有分歧")
RESPONSES = ("尚未沟通", "已沟通暂未回应", "收到回复")


def fingerprint(facts, context, query):
    return hashlib.sha256(json.dumps({"facts": facts.model_dump(), "context": context.model_dump(), "query": query},
                                    sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def build(flow, facts, context, query, pack):
    """所有解释基于本次U/D快照；字段缺失不补为事实，法源未闭合不下资格结论。"""
    users = {u["field"]: u for u in flow["users"]}
    records = {r["field"]: r for r in flow["records"]}
    conflicts = {c["field"] for c in flow["conflicts"]}

    def known(k):
        u = users[k]
        return (u.get("confirmation") in ("一致", "有不同") and k not in conflicts
                and u["value"] not in ("不知道", "有争议", "尚未确认", "待补充不同说法", "未找到记录"))

    def refs(keys):
        return [{"field": k, "U": users[k]["value"], "confirmation": users[k].get("confirmation"),
                 "D": records[k]["value"] if k in records else "材料未记载",
                 "quote": records[k]["quote"] if k in records else None,
                 "page": records[k]["page"] if k in records else None,
                 "citation_id": records[k]["citation_id"] if k in records else None,
                 "conflict": k in conflicts} for k in keys]

    scope, quality = flow["scope_mismatch"], flow["quality_uncertain"]
    notice_unknown = not all(known(k) for k in ("received", "notice", "notice_record"))
    state_conflict = bool(conflicts & {"opened", "washed", "used", "labels"})
    articles = {a["id"]: a for a in pack["articles"]}

    def laws(ids):
        return [{**articles["L-return-" + k], "source_url": pack["url"],
                 "source_version": pack["version"], "applicability_status": pack["applicability_status"],
                 "source_pack_version": pack["pack_version"], "checked_at": pack["accessed"]} for k in ids]

    limited = "存在经营采购或个人卖家线索，本次仅核对事实，不将生活消费规则套用于该交易。" if scope else (
        "存在异常现象或原因不确定，本次不判断质量责任，也不以无理由路径替代质量问题核对。" if quality else "")
    legal_limit = "原文及2020修订施行已核；2020后完整修订链未闭合，以下仅解释事实与已核文本的关系，不认定2026个案退货权或计算期限。"
    issues = [
        {"id": "notice", "question": "签收和退货通知记录能否互相核对？", "facts": refs(["received", "notice", "notice_record"]),
         "laws": laws(["十", "十一"]),
         "why": "通知规则关联签收与向卖方发出通知。想退货的意愿、材料中的日期、实际发送及回执是不同信息。",
         "if_supported": "若原签收与通知全文、时间、对象能核对，可继续整理退回流程与联系信息；仍不能由确认齐全推定法律条件成立。",
         "if_not_supported": "通知不清楚时先找原消息和回执，不把D中的日期写成已发送事实；若本人确认未发送，先准备真实的首次请求。",
         "current": "通知或签收仍待核对，先找原记录。" if notice_unknown else "已有用户确认记录，可整理接收对象及流程问题；尚未核实原件。",
         "unknowns": [users[k]["label"] + "仍待核对" for k in ("received", "notice", "notice_record") if not known(k)] + ["原件真实性、接收情况与2026适用文本仍不能确定。"],
         "restriction": limited or legal_limit},
        {"id": "condition", "question": "只是拆封查验，还是存在清洗、使用、标识或污损问题？",
         "facts": refs(["opened", "washed", "used", "labels", "issue", "issue_detail"]), "laws": laws(["八", "九"]),
         "why": "已核文本区分合理查验与超出查验后明显贬损；家纺标识、污损状态相关，拆封与清洗不能混成一件事。",
         "if_supported": "若查验经过和现状记录支持仅查看且状态未改变，可据此向卖方说明并询问检查标准，不能保证可退。",
         "if_not_supported": "若出现清洗、铺床、标识剪摘或污损，需整理具体经过与凭据；双方说法冲突时并列保存，不能认定任一方为真。",
         "current": "存在商品状态U/D冲突，先请求双方的操作记录与检验凭据。" if state_conflict else (
             "存在异常现象，转为整理现象、发生时间和卖方回应，不判断质量责任。" if quality else "按各项用户确认与实物照片核对商品状态，不作完好认定。"),
         "unknowns": ["实物现状、价值变化和检验依据并未由系统查明。"], "restriction": limited or legal_limit},
        {"id": "exclusion", "question": "页面排除说明针对什么商品性质，购买时如何确认？",
         "facts": refs(["purpose", "seller", "page_words", "page_step", "click"]), "laws": laws(["二", "六", "七", "二十"]),
         "why": "D中的商家条款不是L法律结论。已核文本分别涉及生活消费范围、例外商品、商品性质和购买时确认，不能凭一句页面文字结束判断。",
         "if_supported": "若完整商品说明与购买确认流程支持某种排除理由，应核对其性质与确认条件，并据具体理由沟通。",
         "if_not_supported": "仅详情页提示、未找到操作记录或默认选中，不能直接证明已作购买确认；同样不能据此跳过商品性质和其他条件宣布必退。",
         "current": limited or "请卖方说明商品性质、提示位置与单次购买确认记录，并保存完整页面。",
         "unknowns": ["完整购买流程、例外性质及卖方主体尚未得到独立核实。"], "restriction": limited or legal_limit},
    ]

    def option(key, title, materials, premise, stop, draft_request):
        return {"id": key, "title": title, "materials": materials, "premise": premise,
                "stop_or_redirect": stop, "request": draft_request}

    if scope:
        options = [option("scope", "核对用途、卖方主体与交易约定", ["订单主体信息", "购买用途与交易约定"],
                          "当前存在超出生活消费范围的线索", "主体或用途未清时，不套用普通无理由流程。", "请协助核对交易主体、购买用途和适用的交易约定。")]
    elif quality:
        options = [option("quality", "分开整理异常现象与处理诉求", ["异常照片与发生时间", "清洗使用经过", "卖方回应原文"],
                          "存在异常或原因不确定，先描述可观察现象", "涉及检验、损害或责任争议时需要对应核实；不由本版认定质量责任。", "请说明对所述异常的核查方式和处理意见；我会补充发生经过及现状记录。")]
    else:
        options = []
        if notice_unknown or users["notice"]["value"] == "未发送":
            options.append(option("notice", "先核对通知原消息与接收对象", ["签收原记录", "退货消息全文、发送对象、时间与回执"],
                                  "通知未知或本人尚未发送；不使用材料日期补成事实", "原记录找不到时继续标未知，不计算或宣称满足期限。", "请确认是否已收到我的退货请求及对应记录；若尚未收到，请告知准确接收途径。"))
        if state_conflict:
            options.append(option("conflict", "先核对清洗或使用的不同说法", ["本人操作经过", "实物及标识照片", "卖方检验记录"],
                                  "商品状态存在U/D分歧", "凭据不足时不选任一方为真，不把争议状态写成确定事实。", "请说明判断清洗或使用状态的具体观察、记录时间和凭据，先核清不同说法。"))
        options.append(option("page", "询问排除说明与购买确认依据", ["完整商品页", "购买必经步骤及单次确认记录"],
                              "希望理解页面限制或已记录相关分歧", "商品性质、购买确认及适用文本未核清时，不推断排除有效或无效。", "商品页有排除退货的说明，请提供针对本件商品的具体理由、完整提示及购买确认记录。"))
        if not notice_unknown and not state_conflict:
            options.append(option("process", "沟通退回流程与所需材料", ["订单", "签收与通知原件", "商品及配件现状", "页面与操作记录"],
                                  "相关记录已有本人确认且无状态冲突；这不代表权利成立", "出现异常、范围变化或新的事实冲突时转回核对，不按本步直接寄件。", "如同意办理，请提供真实退回地址、联系人、联系电话以及配件与凭证要求。"))

    response = context.response_status
    reply = context.response_text.strip() if response == "收到回复" else ""
    refusal_reported = response == "收到回复" and context.response_kind == "明确不同意"
    refusal_reason_provided = refusal_reported and bool(reply)
    notice_reported_sent = (
        users["notice"]["value"] == "已发送"
        or "已发送" in records.get("notice", {}).get("value", "")
        or "已发送" in records.get("notice_record", {}).get("value", "")
    )
    first_contact_notice_conflict = response == "尚未沟通" and notice_reported_sent
    if not scope and not quality and response == "已沟通暂未回应":
        options.insert(0, option("unanswered", "确认是否收悉并保存后续询问", ["原申请全文", "接收对象与渠道", "发送回执和后续询问"],
                                 "本人选择已沟通暂未回应；不推断拒绝或超期", "发现回复时按原文更新；无可靠时间记录不编造等待期限。", "我尚未看到对前次请求的回复，请协助确认接收对象和是否收悉；本次询问也会保留记录。"))
    if not scope and not quality and refusal_reason_provided:
        options.insert(0, option("reply", "针对实际不同意理由补充问题", ["回复全文及时间", "对应页面、状态或通知凭据"],
                                 "本人选择明确不同意且提供了具体回复内容；不代表卖方行为已核实", "原话含糊时先确认意思，不擅自当作拒绝；新证据到来后更新。", "请针对您回复中的理由说明对应事实、规则和凭据；我希望先把分歧逐项核清。"))

    if context.goal == "整理已有分歧" and not scope and not quality:
        options.sort(key=lambda x: 0 if x["id"] in ("conflict", "reply") else 1)
    followup = {"status": response or "尚未选择回应状态", "reply_kind": context.response_kind if response == "收到回复" else None,
                "user_reported_reply": bool(response == "收到回复"),
                "reply_text_provided": bool(reply),
                "refusal_reported": refusal_reported,
                "refusal_reason_provided": refusal_reason_provided,
                "next": "先准备首次请求，不编造已发通知或卖方回复。" if response == "尚未沟通" else
                "确认接收对象，保留原申请与后续询问；不推定对方拒绝，不编造等待期限。" if response == "已沟通暂未回应" else
                "你选择对方明确不同意，但没有提供具体回复内容；这里只记录用户自述状态，不分析具体理由。请补充原话或核对记录。" if refusal_reported and not reply else
                "回复已选择但尚未填写具体内容；请补充或核对原文，本版不推测回复理由。" if response == "收到回复" and not reply else
                "回复按用户转述单独保存；围绕已提供的内容提出问题，不将转述当作已查明事实。" if reply else
                "请主动选择回应状态；默认空白不代表尚未沟通或拒绝。",
                "channels_available": False,
                "channel_limit": "平台调解或主管投诉的2026适用状态尚未闭合，本版暂不推荐该渠道或提供自动提交；可继续中性询问和保全记录。"}
    pending = list(flow["gaps"])
    if context.goal is None: pending.insert(0, "尚未主动选择本次目标。")
    if response is None: pending.insert(0, "尚未主动选择回应状态。")
    pending += ["确认订单与接收对象，删除无依据句子；用户修改不等于事实核实或法律判断。"]
    goal = {
        "selected": context.goal,
        "confirmed": context.goal is not None,
        "original_query": context.original_query or None,
        "query": query,
    }
    guidance = {"version": VERSION, "goal": goal, "issues": issues, "options": options,
                "followup": followup, "legal_limit": legal_limit,
                "professional_help": ["实物损害或检验结论争议：本版不能鉴定，先保留现状和检验记录。",
                                      "涉及诉讼、重要期限或记录真伪：本版不能核定期限和证据效力，需针对该问题取得进一步帮助。"],
                "pending": pending, "fingerprint": fingerprint(facts, context, query)}

    def fact_check_reminders():
        reminders = ["请确认正文中的第一人称陈述仍符合你的实际记录；用户修改和材料记载都不自动成为已核实事实。"]
        if first_contact_notice_conflict:
            reminders.append("回应状态选择“尚未沟通”，但用户陈述或材料记载显示曾发出退货通知。请确认是否指不同渠道或不同环节；正文已改为中性核对，没有同时声称首次联系和已申请。")
        elif notice_unknown:
            reminders.append("退货通知是否发出、具体时间、接收对象和回执仍待核对；正文不补日期，也不把材料中的日期当成发送事实。")
        for c in flow["conflicts"]:
            if c["field"] in ("opened", "washed", "used", "labels", "notice", "notice_record"):
                r = records.get(c["field"])
                source = f"材料第{r['page']}页" if r else "现有材料"
                reminders.append(f"{c['label']}存在不同说法：你的陈述为“{c['U']}”，{source}记载“{c['D']}”。发送前核对双方记录来源和时间，不要把冲突改成确定事实。")
        if refusal_reported and not reply:
            reminders.append("你选择对方明确不同意，但没有填写具体回复正文；目前只记录你的状态选择，不存在可分析的具体拒绝理由。请先核对或补充原话。")
        elif reply:
            reminders.append("回复内容来自用户转述；发送前请对照原消息和时间，避免把转述误写成已独立核实的商家陈述。")
        if response == "收到回复" and not reply and not refusal_reported:
            reminders.append("已选择收到回复但未填写正文；请补充或核对原文，本稿没有推测回复理由。")
        return reminders

    def draft_text(selected=None):
        intent = ("了解这套床单的处理条件和所需材料" if context.goal == "了解条件" else
                  "逐项核对这套床单目前不同的记录" if context.goal == "整理已有分歧" else
                  "咨询这套床单的处理方式和所需材料")
        lines = ["您好，我想" + intent + "。"]
        if response == "尚未沟通":
            if first_contact_notice_conflict:
                lines.append("关于此前是否通过其他渠道提交过退货通知，我正在核对记录；请协助确认是否收悉，并告知本事项的处理入口。")
            elif known("notice") and users["notice"]["value"] == "未发送":
                lines.append("我这边尚未发出退货通知，请告知申请入口和需要的材料。")
            elif notice_unknown:
                lines.append("我还在核对是否通过其他渠道发出过退货通知，请协助确认是否收悉，并告知办理入口和所需材料。")
            else:
                lines.append("这是我第一次就此事联系，请告知申请入口和需要的材料。")
        elif response == "已沟通暂未回应":
            lines.append("我此前就此事联系过，目前还没有看到回复。请协助确认是否收悉；如未收到，请告知合适的接收途径和需要补充的材料。")
        elif reply:
            lines.append("我记录到的回复内容是：“" + reply + "”。")
        elif response == "收到回复":
            lines.append("关于我记录中此前收到的回复，我正在核对具体内容；请告知目前还需要补充哪些材料。")
        else:
            lines.append("我正在整理此前的沟通记录，想先核对这件事的处理方式和所需材料。")

        if scope:
            lines.append("我想先核对交易主体、购买用途和双方约定，再确认适用的处理方式。")
        elif quality:
            lines.append("我想先把发现的现象、发生时间和商品现状核对清楚，并了解你们会如何检查及需要哪些记录。")
        else:
            disputed = [c for c in flow["conflicts"] if c["field"] in ("opened", "washed", "used", "labels")]
            for c in disputed:
                r = records.get(c["field"])
                source = f"现有材料第{r['page']}页" if r else "现有材料"
                lines.append(f"我这边记录为“{c['U']}”；{source}记载为“{c['D']}”。请协助核对两边记录的来源和时间；核对前我不会把任一项当作已确认事实。")
            confirmed_state = [
                f"{users[k]['label']}{users[k]['value']}"
                for k in ("opened", "washed", "used", "labels")
                if users[k].get("confirmation") == "一致" and k not in conflicts
            ]
            if confirmed_state:
                lines.append("我已核对并确认目前商品情况为：" + "、".join(confirmed_state) + "。")
            if "page_words" in records:
                r = records["page_words"]
                lines.append(f"我看到商品页面有“{r['value']}”的说明，想了解它针对本件商品的具体依据，以及购买时的完整提示和确认记录。")

        if selected:
            lines.append(selected["request"])
        elif not scope and not quality and response != "收到回复":
            lines.append("请告知接下来可以怎样处理，以及还需要我提供哪些材料。")
        lines.append("谢谢。")
        return "\n\n".join(lines)

    generated = draft_text()
    for item in options: item["draft_text"] = draft_text(item)
    fact_sources = []
    for fact in (fact for issue in issues for fact in issue["facts"]):
        if fact["field"] not in {item["field"] for item in fact_sources}:
            fact_sources.append({"field": fact["field"], "U": fact["U"], "D": fact["D"],
                                 "page": fact["page"], "citation_id": fact["citation_id"],
                                 "confirmation": fact["confirmation"], "conflict": fact["conflict"]})
    law_sources = []
    for issue in issues:
        for law in issue["laws"]:
            if law["id"] not in {item["id"] for item in law_sources}:
                law_sources.append({key: law[key] for key in (
                    "id", "title", "summary", "source_url", "source_version",
                    "source_pack_version", "applicability_status", "checked_at"
                )})
    source_notes = {
        "rules_version": VERSION,
        "law_name": pack["name"],
        "law_pack_version": pack["pack_version"],
        "law_version": pack["version"],
        "law_status": pack["status"],
        "checked_at": pack["accessed"],
        "official_url": pack["url"],
        "facts": fact_sources,
        "laws": law_sources,
        "unknowns": [guidance["legal_limit"]] + list(dict.fromkeys(x for issue in issues for x in issue["unknowns"])),
    }
    communication = {"basis_fingerprint": guidance["fingerprint"], "generator_version": VERSION,
                     "content_role": "merchant_message_body",
                     "initial_generated_text": generated, "generated_text": generated, "current_text": generated,
                     "selected_option": None, "edited_by_user": False, "revision": 0, "stale": False,
                     "source_label": "确定性规则生成；未自动发送；不是模型法律推理",
                     "fact_check_reminders": fact_check_reminders(), "source_notes": source_notes,
                     "attachments": list(dict.fromkeys(x for o in options for x in o["materials"])), "pending": pending}
    return guidance, communication
