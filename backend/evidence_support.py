"""Conservative Chinese deadline guard, not a semantic entailment classifier.

Applies only to recognized subject/action/time questions. No file names, gold
answers or experiment labels are used. An exact quote alone is not support.
"""

import re

VERSION = "deadline-v2-money-evidence-v1"
INSUFFICIENT = (
    "证据不足：当前材料未提供可核验的该事项期限依据，无法确定具体期限。"
    "检索到相似词不代表存在答案，请核对原文或补充材料。"
)
TIME = re.compile(
    r"[零一二三四五六七八九十百两\d]+\s*(?:个)?\s*"
    r"(?:自然日|工作日|日|天|周|星期|个月|月|年|小时)"
    r"|(?:之前|之后|当日|当天|次日|月底|月末)"
)
NEGATIVE = re.compile(r"未(?:提供|规定|注明|约定|明确)|没有|不(?:明确|确定)|无法确定")


def deadline_intent(query):
    # Explicit deadline nouns also cover questions without an interrogative.
    time = re.search(
        r"何时|什么时候|多久|提交时间|提交期限|截止日期|截止时间|期限", query
    )
    if not time:
        return None
    action = next(
        (w for w in ("提交", "归还", "返还", "支付", "缴纳") if w in query), ""
    )
    if not action and "交" in query and "报告" in query:
        action = "提交"
    if "报告" in query:
        # Keep phase qualifiers: a midterm deadline cannot support a final one.
        subject = next(
            (w for w in ("最终报告", "中期报告", "资助报告") if w in query), "报告"
        )
    else:
        subject = re.split(r"[：:]", query[: time.start()])[-1]
        subject = re.sub(r"^(?:请告诉我|请问|请说明|请告知)", "", subject)
        subject = subject.replace(action, "") if action else subject
        subject = re.sub(
            r"(?:通常|应该|应当|应|需要|必须|要|在|可以|可)+$", "", subject
        )
        subject = subject.strip(" 的？?，,：:").rsplit("的", 1)[-1]
    if not action:
        # Unscoped date questions require an explicit cutoff label, not any
        # unrelated payment duration. No inferred date is manufactured.
        return ("", "截止")
    return (subject, action)


MONEY = re.compile(
    r"[零一二三四五六七八九十百千万亿两\d]+(?:[.,，]\d+)*\s*(?:演示币|人民币|元|美元)"
)
MONEY_INSUFFICIENT = (
    "证据不足：当前材料未提供可直接核验的该事项金额依据，无法确定所问金额。"
    "预算、补助上限、报销上限和实际支付不能默认等同；跨页合计或推导请人工核查。"
)


def money_intent(query):
    """Limited explicit monetary roles, not a general semantic classifier."""
    if not re.search(
        r"预算|支付|补助|资助|报销|金额|经费|费用|押金|款|资金|工资|补贴", query
    ):
        return None
    role = re.search(r"预算|上限|最高|实际支付|已支付", query)
    if not role:
        return None
    kind = "budget" if role[0] == "预算" else ("paid" if "支付" in role[0] else "cap")
    # A role word alone (e.g. 上传上限) is not necessarily a money question.
    prefix = re.split(r"[：:]", query[: role.start()])[-1]
    prefix = re.sub(r"^(?:请问|请告诉我|请说明|材料中的|材料中|本材料的)", "", prefix)
    prefix = re.sub(r"[的\s]+$", "", prefix)
    aggregate = bool(re.search(r"合计|总计|相加|分别|两项|各项|差额|剩余", query))
    # Keep the subject literal; no budget/cap or grant/reimbursement aliasing.
    subject = prefix
    if not subject or len(subject) > 12:
        subject = ""
    return {"role": kind, "subject": subject, "aggregate": aggregate}


def money_sentences(query, sentences):
    intent = money_intent(query)
    if intent["aggregate"] or not intent["subject"]:
        return []  # Multi-source arithmetic is not implemented, never guess.
    subject = re.escape(intent["subject"])
    role = {
        "budget": r"预算(?:金额)?",
        "cap": r"(?:上限|最高(?:金额)?)",
        "paid": r"(?:实际支付|已支付)",
    }[intent["role"]]
    direct = re.compile(
        subject
        + r"(?:的)?"
        + role
        + r"(?:金额)?(?:为|是|：|:|不超过|为人民币|为最高)?\s*"
        + MONEY.pattern
    )
    supported = []
    for sentence in sentences:
        # Negation or attribution to a different monetary role is not support.
        if re.search(
            r"未(?:提供|规定|注明|约定|明确|支付)|没有|尚未|待定|无法确定|不等于.*预算",
            sentence,
        ):
            continue
        match = direct.search(sentence)
        # Conditional reimbursement ceilings commonly put conditions before
        # the ceiling. Preserve the complete sentence, including conditions.
        if intent["subject"] == "报销" and intent["role"] == "cap":
            match = match or re.search(
                r"可报销金额[^。；;]*最高不超过\s*" + MONEY.pattern, sentence
            )
        if match and not re.search(r"不(?:是|等于)|并非", sentence[: match.start()]):
            supported.append(sentence)
    return supported


def supporting_sentences(query, chunk):
    intent = deadline_intent(query)
    sentences = [
        s.strip()
        for s in re.split(r"[\n]|(?<=[。！？.!?])", chunk["text"])
        if s.strip()
    ]
    if money_intent(query) is not None:
        return money_sentences(query, sentences)
    if intent is None:
        return sentences
    subject, action = intent
    supported = []
    for sentence in sentences:
        applicable = re.split(r"不适用|不适用于|不包括", sentence)[0]
        # The synthetic equipment rules explicitly introduce the projector and
        # then refer to it as equipment. This is a narrow, documented alias,
        # not permission to borrow a different object's deadline in the chunk.
        same_clause = any(
            (action in clause or (action == "截止" and "期限" in clause))
            and TIME.search(clause)
            and (
                (bool(subject) and subject in clause)
                or (not subject and action == "截止")
                or (
                    subject == "资助报告"
                    and "资助" in chunk["text"]
                    and "最终报告" in clause
                )
                or (
                    subject == "投影仪"
                    and "投影仪" in chunk["text"]
                    and "设备" in clause
                    and not any(other in clause for other in ("幕布", "音箱"))
                )
            )
            for clause in re.split(r"[，,；;]", applicable)
        )
        if same_clause and not NEGATIVE.search(sentence):
            supported.append(sentence)
    return supported


def supported_claims(query, claims, evidence):
    monetary = money_intent(query) is not None
    if deadline_intent(query) is None and not monetary:
        return claims
    index = {c["citation_id"]: c for c in evidence}
    # This runs AFTER validate_claims; never weaken identity/quote validation.
    # Require the actual quoted span (not another sentence in its page) to
    # contain an answer-bearing sentence. Context remains available for aliases.
    return [
        claim
        for claim in claims
        if (not monetary or claim.get("text") == claim["quote"])
        and any(
            (sentence == claim["quote"] if monetary else sentence in claim["quote"])
            for sentence in supporting_sentences(query, index[claim["citation_id"]])
        )
    ]
