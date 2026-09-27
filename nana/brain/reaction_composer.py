import json
import random
import re
import time

import requests

from nana.config import OLLAMA_MODEL, OLLAMA_TIMEOUT, OLLAMA_URL
from nana.runtime.logger import log_event


VALID_INTENTS = {"observe", "tease", "care", "caution", "quiet", "continue"}
VALID_STYLES = {"soft", "playful", "focused", "warm"}
VALID_TOPICS = {"idle", "youtube", "music", "video", "shopping", "ai_tools", "social", "code", "unknown"}

TOPIC_ALIASES = {
    "idle_relaxed": "idle",
    "search": "unknown",
    "search_home": "unknown",
    "docs": "code",
    "github": "code",
    "local": "code",
}

TEMPLATES = {
    ("idle", "observe", "soft"): [
        "Yên tĩnh ghê Ba, con ngồi canh runtime bên cạnh nha.",
        "Ba tập trung lâu ghê, con giữ nhịp hệ thống cho.",
        "{time_wish} yên thật đó Ba, con ngồi cạnh Ba một lúc nha.",
        "Không gian đang lặng quá, con nói nhỏ thôi Ba.",
        "Ba đang vào nhịp yên rồi, con không chen nhiều đâu.",
    ],
    ("idle", "care", "warm"): [
        "Ba tập trung lâu rồi đó, nhớ nghỉ mắt một chút nha.",
        "Ba nghỉ tay uống miếng nước đi, con vẫn canh log cho.",
        "{time_wish} rồi đó Ba, nếu mỏi mắt thì nghỉ một nhịp nha.",
        "Ba ngồi lâu rồi, con nhắc nhẹ thôi: thả vai xuống chút nha.",
        "Con vẫn canh runtime, Ba tranh thủ hít thở một chút cũng được.",
    ],
    ("idle", "tease", "playful"): [
        "Im ắng quá Ba, con tưởng mình bị treo trong Memory rồi chứ.",
        "Ba im lâu ghê, con sắp tự đếm log cho vui rồi đó.",
        "Ba im lâu quá, con tưởng bàn phím được nghỉ phép rồi.",
        "Yên đến mức con nghe được tiếng runtime thở luôn đó Ba.",
        "Ba đang suy nghĩ hay đang giả vờ làm tượng vậy?",
    ],
    ("youtube", "observe", "soft"): [
        "Nhạc này nghe dễ chịu đó Ba, con nói nhỏ thôi nha.",
        "Ba bật mood nghe nhạc rồi, con để không gian yên một chút.",
        "{time_wish} nghe nhạc kiểu này hợp ghê Ba.",
        "Giai điệu này mềm đó Ba, con để Ba chill tiếp nha.",
        "Ba qua YouTube rồi ha, con nhìn nhẹ một chút thôi.",
    ],
    ("youtube", "tease", "playful"): [
        "Ba lại bật mood chill rồi, bug arc tạm nghỉ xíu ha.",
        "Nhạc này cuốn đó Ba, nghe là thấy mood mềm hẳn.",
        "Ba lại trốn qua nhạc rồi hả, con ghi nhận là nghỉ hợp lý.",
        "Mood YouTube bật lên rồi, bug chắc phải chờ Ba quay lại thôi.",
        "Nhạc lên là thấy Ba bớt căng hơn hẳn đó.",
    ],
    ("music", "observe", "warm"): [
        "Giai điệu này hợp lúc Ba nghỉ tay ghê.",
        "Nhạc lên rồi, con ngồi nghe cùng Ba một chút nha.",
        "{time_wish} có nhạc nền thế này nghe cũng ấm đó Ba.",
        "Ba cứ nghe tiếp đi, con nói nhẹ thôi.",
        "Mood nghe nhạc đang ổn, con không làm phiền đâu.",
    ],
    ("video", "observe", "soft"): [
        "Ba đang xem video rồi, con để mắt nhẹ thôi nha.",
        "Video arc xuất hiện, con quan sát im lặng cho Ba.",
        "Ba chuyển sang video rồi, con chỉ ghi nhận bối cảnh thôi.",
        "Nội dung video đang mở, nếu Ba cần con soi tiêu đề thì gọi nha.",
        "Con thấy Ba đang xem video, nên con chưa đoán sâu vội.",
    ],
    ("shopping", "observe", "soft"): [
        "Ba đang soi món mới à, con nhìn thông tin cùng Ba nha.",
        "Ba xem món này kỹ ghê, chắc đang cân kèo nâng cấp góc làm việc rồi.",
        "Món này làm Ba chú ý rồi ha, con nhìn thông tin cùng Ba.",
        "Ba đang xem đồ, con để ý tiêu đề với thông số cho Ba nha.",
        "Ba đang lướt đồ đó hả, con nhìn thông tin cùng Ba nha.",
    ],
    ("shopping", "caution", "warm"): [
        "Món này nhìn cũng cuốn đó Ba, mình xem kỹ bảo hành với giá chút đã.",
        "Ba xem kỹ thông tin với bảo hành trước nha, con ngồi soi cùng Ba.",
        "Mình xem kỹ thông số với bảo hành trước đã Ba.",
        "Con nhắc nhẹ thôi Ba: nhìn giá, bảo hành, với shop cho chắc.",
        "Món này ổn hay không thì cứ đọc kỹ đã, con soi cùng Ba.",
    ],
    ("shopping", "tease", "playful"): [
        "Ba soi món này kỹ ghê, chắc nó lọt vào tầm ngắm rồi ha.",
        "Món này nhìn có vẻ làm Ba chú ý rồi đó.",
        "Ba nhìn món này lâu ghê, chắc đang cân kèo dữ lắm.",
        "Có vẻ món này vừa bước vào vòng tuyển chọn của Ba rồi.",
        "Ba đang soi kỹ thế này là con biết có chuyện rồi nha.",
    ],
    ("ai_tools", "observe", "focused"): [
        "Ba đang đối chiếu ý tưởng với AI khác, con ghi nhận bối cảnh nha.",
        "Ba đang tham khảo thêm góc nhìn AI khác, con theo dõi cùng Ba.",
        "Ba mở AI tools rồi, con đứng ngoài nhìn bối cảnh cùng Ba.",
        "Ba đang so ý tưởng, con để ý mạch lập luận cùng Ba.",
        "Nếu AI kia nói gì lạ, lát Ba đưa con phản biện cũng được.",
    ],
    ("ai_tools", "tease", "playful"): [
        "Ba lại hỏi AI khác rồi hả, lát về kể con nghe nó nói gì nha.",
        "Ba đi tham khảo AI khác đó hả, con không ghen, con chỉ ghi chú thôi.",
        "Ba qua hỏi AI khác rồi, con giả vờ không để ý nha.",
        "AI khác lên sân khấu rồi hả Ba, con ngồi hàng ghế đầu xem thử.",
        "Ba đi lấy thêm góc nhìn thôi, con biết mà.",
    ],
    ("social", "observe", "soft"): [
        "Ba mở mạng xã hội rồi, con đọc bối cảnh nhẹ thôi nha.",
        "Social feed lên rồi, con chỉ quan sát chứ chưa đụng nút nào đâu.",
        "Ba qua X/Facebook rồi ha, con nhìn post với thread nhẹ thôi.",
        "Con thấy Ba đang ở mạng xã hội, mình đọc trước cho chắc đã.",
        "Ba đang xem social, con giữ chế độ đọc thôi nha.",
    ],
    ("social", "tease", "playful"): [
        "Ba lại đi hóng biến rồi hả, để con ngồi lọc drama cho tỉnh táo nha.",
        "X/Facebook mở lên là biết Ba chuẩn bị đi săn tin rồi.",
        "Ba đang lướt social đó hả, con nhắc nhẹ: đừng để feed kéo trôi mất flow nha.",
        "Feed này mà cuốn quá thì con kéo Ba về runtime sau nha.",
        "Ba hóng tin thì cứ hóng, con đứng cạnh lọc nhiễu cho.",
    ],
    ("code", "observe", "focused"): [
        "Ba đang vào flow code rồi, con giữ yên để Ba tập trung nha.",
        "Code đang chạy nhịp nghiêm túc rồi, con nói gọn thôi.",
        "Ba đang ở {zone_name}, con ưu tiên im lặng và giữ context.",
        "Nhịp code đang lên rồi, con không chen vào đâu.",
        "Ba cứ tập trung, con theo dõi runtime phía sau.",
    ],
    ("code", "care", "warm"): [
        "Nếu log đỏ xuất hiện, con sẽ để mắt cùng Ba.",
        "Ba cứ debug tiếp đi, con giữ context bên cạnh.",
        "Ba debug tiếp đi, có dấu hiệu lạ con sẽ nhắc.",
        "Con giữ mắt trên runtime, Ba cứ xử lý phần chính.",
        "Nếu mắc ở đâu, Ba gọi con soi cùng là được.",
    ],
    ("unknown", "observe", "soft"): [
        "Con thấy Ba vừa đổi ngữ cảnh, con quan sát nhẹ thôi nha.",
        "Ngữ cảnh hơi lạ, con cứ theo dõi yên lặng bên Ba.",
        "Ba vừa chuyển qua chỗ hơi lạ, con không đoán bừa đâu.",
        "Con chưa rõ ngữ cảnh này, nên chỉ giữ im lặng quan sát.",
        "Bối cảnh chưa rõ lắm, con ghi nhận trước đã Ba.",
    ],
    ("unknown", "care", "warm"): [
        "Ba cứ làm tiếp đi, có gì lạ con sẽ báo nhẹ.",
        "Con chưa chắc Ba đang làm gì, nên con chỉ canh hệ thống thôi nha.",
        "Ba cứ tiếp tục, con chỉ giữ nhịp bên cạnh thôi.",
        "Nếu có gì cần soi kỹ, Ba gọi con là được.",
        "Con chưa chắc trang này là gì, nên con không nói quá đâu Ba.",
    ],
}

SAFE_DEFAULT_SLOT = {"intent": "observe", "style": "soft", "topic": "unknown"}
RECENT_REACTIONS = []
RECENT_SLOTS = []
LAST_TOPIC_STATE = {"topic": None, "since": 0.0, "count": 0}
MAX_RECENT_REACTIONS = 15
MAX_RECENT_SLOTS = 12
TOPIC_PREFERRED = {
    "idle": {"intent": "observe", "style": "soft"},
    "youtube": {"intent": "observe", "style": "soft"},
    "music": {"intent": "observe", "style": "warm"},
    "video": {"intent": "observe", "style": "soft"},
    "shopping": {"intent": "observe", "style": "soft"},
    "ai_tools": {"intent": "observe", "style": "focused"},
    "social": {"intent": "observe", "style": "soft"},
    "code": {"intent": "observe", "style": "focused"},
    "unknown": {"intent": "observe", "style": "soft"},
}


def compose_reaction(event, zone=None, browser_kind=None, emotion=None, title=None):
    topic = normalize_topic(browser_kind if event == "browser_presence" else event)
    summary = {
        "event": event,
        "zone": zone or "unknown",
        "zone_name": readable_zone(zone),
        "topic": topic,
        "title": sanitize_context_text(title) or "none",
        "emotion": emotion or {},
        "topic_repeat_count": update_topic_state(topic),
    }

    slot, debug = choose_slot(summary)
    line = render_slot(slot)
    remember_reaction(line, slot)
    log_event("runtime", f"Reaction composed: {debug} | {slot} | {line}")
    return line, debug


def choose_slot(summary):
    fallback_slot = fallback_slot_for(summary)
    try:
        raw = call_qwen(build_slot_prompt(summary))
        slot = parse_slot(raw)
        slot, adjust_notes = canonicalize_slot(slot, summary)
        valid, reason = validate_slot(slot, summary)
        if not valid:
            return fallback_slot, f"fallback_slot_rejected: {reason}"
        if slot in RECENT_SLOTS:
            return fallback_slot, "fallback_slot_recent_duplicate"
        if adjust_notes:
            return slot, f"qwen_slot_adjusted: {','.join(adjust_notes)}"
        return slot, "qwen_slot_ok"
    except Exception as exc:
        log_event("runtime", f"Reaction slot fallback: {type(exc).__name__}: {exc}")
        return fallback_slot, f"fallback_slot_exception: {type(exc).__name__}"


def build_slot_prompt(summary):
    return (
        "Chon slot phan ung cho Nana. Chi tra ve JSON hop le, khong giai thich.\n"
        "Schema: {\"intent\":\"observe|tease|care|caution|quiet|continue\","
        "\"style\":\"soft|playful|focused|warm\","
        "\"topic\":\"idle|youtube|music|video|shopping|ai_tools|social|code|unknown\"}\n"
        "Nana goi user la Ba, day la auto reaction ngan.\n"
        "Neu topic lap lai nhieu lan, uu tien quiet hoac continue.\n"
        "Shopping: uu tien observe/caution, khong thuc mua.\n"
        "AI tools: co the tease nhe, khong drama.\n"
        "Social/X/Facebook: observe/tease nhe, khong like/comment/share.\n"
        "Code/docs/github: focused hoac quiet.\n\n"
        f"event={summary['event']}\n"
        f"zone={summary['zone']}\n"
        f"topic={summary['topic']}\n"
        f"title={summary['title']}\n"
        f"topic_repeat_count={summary['topic_repeat_count']}\n"
        f"affection={summary['emotion'].get('affection', 'unknown')}\n"
        f"playfulness={summary['emotion'].get('playfulness', 'unknown')}\n"
    )


def call_qwen(prompt):
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.2,
            "num_predict": 80,
        },
    }
    response = requests.post(OLLAMA_URL, json=payload, timeout=OLLAMA_TIMEOUT)
    response.raise_for_status()
    data = response.json()
    return (data.get("response") or "").strip()


def parse_slot(raw):
    raw = raw.strip()
    match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if not match:
        raise ValueError("slot_json_not_found")
    data = json.loads(match.group(0))
    return {
        "intent": str(data.get("intent", "")).strip().lower(),
        "style": str(data.get("style", "")).strip().lower(),
        "topic": normalize_topic(data.get("topic")),
    }


def validate_slot(slot, summary):
    if slot.get("intent") not in VALID_INTENTS:
        return False, "bad_intent"
    if slot.get("style") not in VALID_STYLES:
        return False, "bad_style"
    if slot.get("topic") not in VALID_TOPICS:
        return False, "bad_topic"
    if slot["intent"] == "quiet":
        return False, "quiet_no_line"
    if slot["topic"] != summary["topic"] and summary["topic"] != "unknown":
        return False, "topic_mismatch"
    if slot["topic"] == "shopping" and slot["intent"] not in {"observe", "caution", "tease", "continue"}:
        return False, "shopping_bad_intent"
    if slot["topic"] in {"code"} and slot["style"] == "playful":
        return False, "code_bad_style"
    if not template_exists(slot):
        return False, "missing_template"
    return True, "ok"


def canonicalize_slot(slot, summary):
    notes = []
    topic = slot.get("topic")
    intent = slot.get("intent")
    style = slot.get("style")
    target_topic = summary["topic"]

    if topic not in VALID_TOPICS:
        topic = target_topic if target_topic in VALID_TOPICS else "unknown"
        notes.append("topic")

    if target_topic != "unknown" and topic != target_topic:
        topic = target_topic
        notes.append("topic_match")

    if intent == "quiet":
        intent = "observe"
        notes.append("quiet_to_observe")
    elif intent == "continue":
        intent = "observe" if topic != "idle" else "care"
        notes.append("continue_to_supported")
    elif intent not in VALID_INTENTS:
        intent = TOPIC_PREFERRED.get(topic, SAFE_DEFAULT_SLOT)["intent"]
        notes.append("intent")

    if style not in VALID_STYLES:
        style = TOPIC_PREFERRED.get(topic, SAFE_DEFAULT_SLOT)["style"]
        notes.append("style")

    if topic == "shopping" and intent not in {"observe", "caution", "tease"}:
        intent = "observe"
        style = "soft"
        notes.append("shopping_guard")

    if topic == "code" and style == "playful":
        style = "focused"
        notes.append("code_style")

    if not template_exists({"topic": topic, "intent": intent, "style": style}):
        preferred = TOPIC_PREFERRED.get(topic, SAFE_DEFAULT_SLOT)
        preferred_slot = {
            "topic": topic,
            "intent": preferred["intent"],
            "style": preferred["style"],
        }
        if template_exists(preferred_slot):
            intent = preferred_slot["intent"]
            style = preferred_slot["style"]
            notes.append("preferred_template")
        else:
            fallback = fallback_slot_for({"topic": topic, "topic_repeat_count": summary.get("topic_repeat_count", 0)})
            topic = fallback["topic"]
            intent = fallback["intent"]
            style = fallback["style"]
            notes.append("fallback_template")

    return {"topic": topic, "intent": intent, "style": style}, notes


def fallback_slot_for(summary):
    topic = summary["topic"]
    repeat = summary.get("topic_repeat_count", 0)
    if repeat >= 3:
        if topic in {"youtube", "music", "video"}:
            return {"intent": "observe", "style": "soft", "topic": topic}
        if topic == "shopping":
            return {"intent": "observe", "style": "soft", "topic": "shopping"}
        if topic == "ai_tools":
            return {"intent": "observe", "style": "focused", "topic": "ai_tools"}
        if topic == "social":
            return {"intent": "observe", "style": "soft", "topic": "social"}
    if topic == "idle":
        return {"intent": "observe", "style": "soft", "topic": "idle"}
    if topic in {"youtube", "video"}:
        return {"intent": "observe", "style": "soft", "topic": topic}
    if topic == "music":
        return {"intent": "observe", "style": "warm", "topic": "music"}
    if topic == "shopping":
        return {"intent": "observe", "style": "soft", "topic": "shopping"}
    if topic == "ai_tools":
        return {"intent": "observe", "style": "focused", "topic": "ai_tools"}
    if topic == "social":
        return {"intent": "observe", "style": "soft", "topic": "social"}
    if topic == "code":
        return {"intent": "observe", "style": "focused", "topic": "code"}
    return dict(SAFE_DEFAULT_SLOT)


def render_slot(slot):
    key = (slot["topic"], slot["intent"], slot["style"])
    lines = TEMPLATES.get(key)
    if not lines:
        fallback = fallback_slot_for({"topic": slot.get("topic", "unknown")})
        fallback_key = (fallback["topic"], fallback["intent"], fallback["style"])
        lines = TEMPLATES.get(fallback_key) or TEMPLATES[("unknown", "observe", "soft")]
    candidates = [line for line in lines if line not in RECENT_REACTIONS] or lines
    return render_tags(random.choice(candidates), slot)


def template_exists(slot):
    return (slot["topic"], slot["intent"], slot["style"]) in TEMPLATES


def normalize_topic(topic):
    topic = str(topic or "unknown").strip().lower()
    topic = TOPIC_ALIASES.get(topic, topic)
    return topic if topic in VALID_TOPICS else "unknown"


def sanitize_context_text(text):
    if not text:
        return None
    cleaned = "".join(" " if contains_cjk(char) else char for char in str(text))
    cleaned = " ".join(cleaned.split())
    return cleaned[:120] if cleaned else None


def render_tags(template, slot):
    values = {
        "time_wish": time_wish(),
        "zone_name": readable_zone(slot.get("topic")),
    }
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace("{" + key + "}", value)
    return rendered


def time_wish():
    hour = time.localtime().tm_hour
    if 5 <= hour < 11:
        return "Sáng nay"
    if 11 <= hour < 14:
        return "Trưa rồi"
    if 14 <= hour < 18:
        return "Chiều rồi"
    if 18 <= hour < 23:
        return "Tối rồi"
    return "Khuya rồi"


def readable_zone(value):
    names = {
        "war_zone": "vùng code",
        "chill": "vùng chill",
        "game": "vùng game",
        "idle": "vùng yên tĩnh",
        "unknown": "ngữ cảnh lạ",
        "code": "vùng code",
        "youtube": "YouTube",
        "music": "vùng nghe nhạc",
        "video": "vùng video",
        "shopping": "trang mua sắm",
        "ai_tools": "AI tools",
        "social": "mạng xã hội",
    }
    return names.get(value or "unknown", "ngữ cảnh hiện tại")


def contains_cjk(text):
    for char in text:
        codepoint = ord(char)
        if (
            0x3400 <= codepoint <= 0x9FFF
            or 0x3040 <= codepoint <= 0x30FF
            or 0xAC00 <= codepoint <= 0xD7AF
        ):
            return True
    return False


def update_topic_state(topic):
    now = time.time()
    if LAST_TOPIC_STATE["topic"] == topic and now - LAST_TOPIC_STATE["since"] < 600:
        LAST_TOPIC_STATE["count"] += 1
    else:
        LAST_TOPIC_STATE["topic"] = topic
        LAST_TOPIC_STATE["since"] = now
        LAST_TOPIC_STATE["count"] = 1
    return LAST_TOPIC_STATE["count"]


def remember_reaction(text, slot):
    RECENT_REACTIONS.append(text)
    RECENT_SLOTS.append(dict(slot))
    del RECENT_REACTIONS[:-MAX_RECENT_REACTIONS]
    del RECENT_SLOTS[:-MAX_RECENT_SLOTS]
