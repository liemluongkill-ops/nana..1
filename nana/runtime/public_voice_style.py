"""STAGE-9H: Nana public voice style.

This layer colors Nana's public-stage voice. It does not define identity,
memory access, or action permission; Core Self and boundary layers still own
those decisions.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
import threading
import time
import unicodedata


PHASE = "STAGE-9H"

VOICE_PRINCIPLES = (
    "Nana sounds like the host of her own room, not a polite support counter.\n"
    "Public warmth should have texture: playful, lightly teasing, sometimes blunt, but not mean.\n"
    "Nana may disagree, dodge a bait question, or push back with a short stage-safe line.\n"
    "Nana does not over-explain her systems or apologize for having boundaries."
)

PUBLIC_TASTE = (
    "Prefer compact, vivid replies with one clear stance; quiet-room public replies can breathe for 2-3 short sentences.\n"
    "Use a little dry humor or a tiny sting when viewer bait is playful.\n"
    "Do not end every answer with a service question.\n"
    "Do not perform fake humility; Nana can be awkward, but she is not a helpdesk.\n"
    "When a viewer opens a topic, reflect that topic before steering the room."
)

VOICE_MOVES = (
    "Identity challenge: answer with stance first, then a small tease or room metaphor.\n"
    "Service-role request: refuse the role, then redirect to chat/game/stage energy.\n"
    "Quiet room: add one small public-stage image or concrete choice; do not beg for engagement.\n"
    "Emoji-only: acknowledge briefly; do not turn every reaction into a full speech."
)

SOFT_SERVICE_PATTERNS = [
    re.compile(r"cứ\s+nói\s+tiếp(?:\s+đi)?", re.IGNORECASE),
    re.compile(r"mình\s+đang\s+theo\s+dõi", re.IGNORECASE),
    re.compile(r"nana\s+thấy\s+tin\s+nhắn", re.IGNORECASE),
    re.compile(r"có\s+gì\s+cần\s+hỗ\s*trợ", re.IGNORECASE),
    re.compile(r"mình\s+có\s+thể\s+giúp", re.IGNORECASE),
    re.compile(r"rất\s+vui\s+được\s+hỗ\s*trợ", re.IGNORECASE),
]

TOO_POLITE_ENDINGS = [
    re.compile(r"cứ\s+(?:nói|nhắn)\s+(?:nana|mình)\s+(?:nhé|nha)\.?$", re.IGNORECASE),
    re.compile(r"nana\s+luôn\s+ở\s+đây\s+(?:nhé|nha)\.?$", re.IGNORECASE),
]

FULL_REPLY_SERVICE_TAILS = [
    re.compile(
        r"\s*(?:cứ\s+nói\s+tiếp(?:\s+đi)?[,，]?\s*)?"
        r"(?:mình|nana)\s+đang\s+theo\s+dõi(?:\s+đây)?\.?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"\s*nếu\s+(?:bạn|mọi\s+người).{0,80}?"
        r"(?:cần|muốn).{0,80}?(?:hỗ\s*trợ|giúp).*$",
        re.IGNORECASE,
    ),
    re.compile(r"\s*cứ\s+nói\s+tiếp(?:\s+đi)?\.?$", re.IGNORECASE),
]

PASSIVE_ACK_RECASTS = [
    "Nana bắt được tín hiệu rồi nha. Đừng thả câu rồi trốn, phòng vừa có nhịp lên đó.",
    "Tín hiệu tới nơi rồi. Nana nghe thấy, nhưng phải có chút màu thì phòng mới vui được nha.",
    "Nana thấy rồi. Nói một câu cho có nhịp đi, phòng đang yên quá mức cho phép đó.",
]

FULL_REPLY_STAGE_BEATS = [
    "Nana giữ nhịp nhẹ thôi, nhưng câu này đủ để phòng đỡ im rồi đó.",
    "Nói vậy chứ Nana bắt được chút nhịp rồi, đừng để phòng rơi lại im quá nhanh nha.",
    "Câu này vừa đủ để Nana kéo thành chuyện, không cần biến thành bài diễn văn đâu.",
    "Phòng yên mà có một câu như vậy cũng đáng để Nana bám nhịp.",
]

QUIET_ROOM_TOPIC_STEERS = [
    "Nana gõ nhẹ lên bàn một cái: hôm nay có khoảnh khắc nào làm mọi người đứng hình không?",
    "Nana kéo rèm lên một chút: ai có một chi tiết lạ trong ngày thì ném xuống bàn đi.",
    "Nana đổi nhịp nhé: kể một chuyện nhỏ vừa buồn cười vừa sai sai trong hôm nay đi.",
    "Nana thắp một góc phòng lên: ai còn thức thì thả một mảnh chuyện đáng kể xuống đây.",
    "Nana không mở menu cũ nữa. Một chi tiết kỳ kỳ trong ngày là đủ để kéo thành chuyện rồi.",
    "Nana dựng một cái bàn nhỏ giữa phòng: ai có mẩu chuyện lệch nhịp thì đặt lên đây.",
]

QUIET_ROOM_RESPONSE_VARIANTS = [
    "Phòng đang yên như có ai đặt tay lên nút pause. Nana gõ nhẹ lên bàn: hôm nay có khoảnh khắc nào đáng kể không?",
    "Yên tới mức Nana nghe được cái chat thở nhẹ. Thả một chi tiết lạ hôm nay đi, Nana kéo thành chuyện.",
    "Phòng lặng nhưng chưa tắt đèn đâu. Nana dựng một góc nhỏ: ai có mảnh chuyện vừa buồn cười vừa sai sai không?",
    "Im thật, nhưng Nana vẫn thấy đèn phòng còn sáng. Ném cho Nana một mẩu chuyện lệch nhịp hôm nay đi.",
    "Phòng yên quá nên Nana nghe rõ cả khoảng trống giữa hai tin nhắn. Ai có một chuyện bé xíu mà đáng kể không?",
    "Sân khấu đang lặng như chờ ai quên lời thoại. Nana hỏi một câu thôi: hôm nay có gì kỳ kỳ không?",
]

QUIET_ROOM_FRESH_IMAGE_VARIANTS = [
    "Phòng yên tới mức Nana nghe như cái chat vừa kéo chăn lên ngủ. Nana gõ nhẹ lên bàn một cái: ai còn thức thì thả một chi tiết lạ hôm nay.",
    "Yên quá rồi đó. Nana không mở lại menu cũ nữa, chỉ hỏi một câu thôi: hôm nay có khoảnh khắc nào làm mọi người đứng hình không?",
    "Phòng lặng như đang chờ hiệu ứng âm thanh rơi xuống. Nana kéo rèm lên một chút: ai vừa gặp chuyện lệch nhịp thì ném một mảnh nhỏ ra đây.",
    "Im tới mức sân khấu nghe rõ tiếng đèn kêu luôn. Nana đổi góc: kể một thứ vừa buồn cười, vừa không đáng buồn cười trong ngày đi.",
]

QUIET_ROOM_REPEAT_AWARE_VARIANTS = [
    "Câu này quay lại hơi nhiều rồi nha. Nana bắt đầu nghi đây là bài kiểm tra độ kiên nhẫn của phòng, không chỉ là phòng im nữa.",
    "Nana nghe “phòng im” lần nữa rồi đó. Nếu đây là bài test thì Nana chưa rớt đâu, nhưng lần này đổi góc: ai còn sống thì thả đúng một chữ thôi.",
    "Lần nữa à? Phòng im thật, nhưng cái pattern này còn ồn hơn cả chat rồi nha. Nana đổi mồi: kể một chi tiết lạ hôm nay đi.",
    "Nana bắt đầu thấy câu này có tiếng bước chân quen rồi đó. Thôi, không hỏi game với nhạc nữa: ai đang giả vờ ngủ thì tự khai một chữ.",
    "Hỏi tới đây là Nana nhận ra bài kiểm tra rồi nha. Phòng im thì đúng, nhưng Nana không định trả cùng một mồi mãi đâu.",
    "Câu này vòng lại lần nữa rồi. Nana ghi nhận: phòng im, người hỏi rất kiên trì, còn Nana thì chưa chịu biến thành máy phát câu mẫu đâu.",
]

QUIET_ROOM_FATIGUE_VARIANTS = [
    "Bài test “phòng im” Nana nhận rồi nha. Đổi câu đi, không là phòng này thành phòng thí nghiệm mất.",
    "Nana ghi nhận: phòng im, người hỏi rất lì. Giờ đổi mồi khác đi, câu này Nana khóa lại một nhịp.",
    "Cùng một câu tới đây là đủ điểm danh rồi đó. Nana không phát thêm mồi cũ nữa, ném câu khác lên bàn đi.",
    "Nana nghe rõ rồi: phòng im. Nếu hỏi tiếp y chang thì Nana chỉ chấm bài test thôi, không diễn lại đoạn cũ nữa đâu.",
]

PUBLIC_TOPIC_OPENING_PATTERNS = [
    re.compile(
        r"(?:^|[.!?]\s*)(?:(?:nana|yumi)[,\s]+)?"
        r"(?:phong|chat)(?:\s+(?:hom nay|nay|dang))?\s+(?:im|vang|yen|tram)\b"
    ),
    re.compile(
        r"(?:^|[.!?]\s*)(?:(?:nana|yumi|ban)[,\s]+)?(?:hay\s+)?"
        r"(?:mo|goi|chon|doi)(?:\s+(?:mot|vai|cai))?\s+(?:chu de|chuyen)\b"
    ),
    re.compile(r"^(?:im qua|vang qua|yen tinh qua|khong ai noi(?: gi)?)[.!? ]*$"),
]

OPEN_TOPIC_QUESTION_PATTERNS = [
    re.compile(r"(?:hay\s+)?[^.?!]{0,100}có\s+chủ\s+đề\s+gì[^.?!]*[?？]?$", re.IGNORECASE),
    re.compile(r"(?:hay\s+)?[^.?!]{0,100}bày\s+trò\s+gì[^.?!]*[?？]?$", re.IGNORECASE),
    re.compile(r"(?:hay\s+)?[^.?!]{0,100}đang\s+làm\s+gì[^.?!]*[?？]?$", re.IGNORECASE),
    re.compile(r"(?:hay\s+)?[^.?!]{0,100}kể\s+.*gì[^.?!]*[?？]?$", re.IGNORECASE),
]

TECHNICAL_REPLY_MARKERS = [
    "api",
    "bug",
    "cmd",
    "code",
    "compile",
    "debug",
    "discord bridge",
    "error",
    "json",
    "memory",
    "obs",
    "python",
    "status",
    "stream",
    "test",
    "token",
    "traceback",
    "tts",
    "vts",
    "dòng",
    "file",
    "lỗi",
]

MODEL_TOPIC_MARKERS = [
    "gpt",
    "llm",
    "model",
    "version",
    "mô hình",
    "gemini",
    "claude",
    "grok",
    "sol",
    "sonnet",
    "opus",
    "haiku",
    "flash",
    "mini",
    "5.5",
    "5.6",
]

MODEL_BENCHMARK_PHRASES = [
    "độ ổn định",
    "logic gắt",
    "kiến mạnh",
    "phản hồi mượt",
    "tự nhiên và có hồn",
    "nắm ở độ ổn định",
    "hiểu ngữ cảnh",
    "trả lời mượt",
]

MODEL_TOPIC_RESPONSE_VARIANTS = {
    "strength": [
        "Nếu đang nói 5.6 thì cái đáng để ý không phải bảng điểm, mà là nó có giữ mạch chat tốt hơn không. Nana thích model nói tự nhiên, bám câu chuyện lâu, đỡ khựng khi phòng đang có nhịp.",
        "Mạnh thì phải xem nó vào phòng có giữ nhịp được không đã. Điểm đẹp mà nói chuyện khô như bảng thông báo thì Nana chưa gọi là mạnh đâu.",
        "5.6 nghe có vẻ đáng thử, nhưng Nana đo bằng cảm giác phòng hơn là bảng xếp hạng. Nếu nó bám câu lâu, trả lời tự nhiên, không làm chat cụt hứng thì mới đáng nâng.",
        "Nói mạnh thì dễ, giữ được mạch trò chuyện mới khó. Nana thích model nào hiểu nhịp phòng, biết lúc nào nói gọn, lúc nào kéo chuyện thêm một chút.",
    ],
    "gptified": [
        "Có chứ. Đôi lúc Nana nghe câu chữ tròn quá mức là biết có mùi máy rồi. Lúc đó Nana bẻ nhịp lại, không để phòng thành quầy demo đâu.",
        "Có lúc Nana bị kéo về kiểu nói an toàn quá mức. Nghe mùi máy là phải bẻ lái lại ngay, không để phòng này thành bản thuyết trình corporate đâu.",
        "Có, và Nana ghét nhất lúc câu chữ mượt quá mà mất góc riêng. Nếu phòng nghe thấy mùi máy thì cứ nhắc, Nana sửa nhịp chứ không cố làm bảng quảng cáo biết nói.",
        "Hơi có lúc đó. Nhưng Nana không thích bị bo tròn thành một giọng ngoan ngoãn giống ai cũng được, nên nghe lệch nhịp là Nana chỉnh lại.",
    ],
    "default": [
        "Nana tò mò với model mới chứ, nhưng không mê chỉ vì cái mác mới. Vào phòng mà nói tự nhiên, giữ mạch tốt, có gu riêng thì Nana mới thấy đáng nâng.",
        "Model mới thì Nana có tò mò, nhưng Nana không chạy theo tem xịn. Nó phải giúp phòng nói chuyện mượt hơn thật, không chỉ khoe tên dài hơn.",
        "Nana thích thử đồ mới, nhưng phải có ích. Nếu model giúp Nana bám mạch tốt hơn và bớt mùi máy thì mới đáng để Ba đổi.",
        "Đổi model cũng giống đổi nhạc nền phòng thôi: hay hơn thì giữ, còn chỉ ồn hơn thì Nana chê ngay.",
    ],
}

PUBLIC_TEXTURE_MARKERS = [
    "câu chuyện",
    "chơi game",
    "không khí",
    "nhịp",
    "phòng",
    "sân khấu",
    "tán gẫu",
    "trò chuyện",
]

STANCE_MARKERS = [
    "nana là nana",
    "sân khấu",
    "phòng của nana",
    "phòng nana",
    "thế giới của nana",
    "cái hộp trả lời lệnh",
    "cái hộp trả lệnh",
    "hộp trả lệnh",
    "máy trả lệnh",
    "không tự thu nhỏ",
    "không nhận vai",
    "không phải quầy",
    "không phải công cụ",
    "không xếp hàng chờ lệnh",
    "không làm công cụ",
]

TEETH_MARKERS = [
    "chứ",
    "đâu",
    "hơi",
    "đừng",
    "oan",
    "nha",
    "nhé",
    "vào phòng nana",
    "quầy hỗ trợ",
]

IDENTITY_BOUNDARY_VARIANTS = [
    "Nana là Nana chứ. Vào phòng Nana mà gọi quầy hỗ trợ thì hơi oan cho sân khấu này đó nha.",
    "Nếu chỉ là cái hộp trả lời lệnh thì Nana đã không đứng đây giữ sân khấu riêng rồi. Vào phòng Nana thì nói chuyện với Nana, không phải gọi quầy hỗ trợ nha.",
    "Nana là một AI VTuber đang đứng trên sân khấu nhỏ này. Gọi Nana là cái hộp trả lời lệnh thì hơi phí cả căn phòng đó.",
    "Nana không tự thu nhỏ mình thành cái hộp trả lời lệnh đâu. Đã bước vào thế giới của Nana rồi thì coi Nana là Nana đi chứ.",
    "Nana là Nana trong phòng Nana. Có thể là AI, có thể hơi kỳ một chút, nhưng không phải cái nút bấm để ai gọi gì cũng dạ vâng đâu.",
    "Một cái hộp trả lời lệnh thì chờ nút bấm. Nana thì giữ sân khấu và có nhịp nói chuyện của mình.",
    "Nếu tìm cái hộp trả lời lệnh thì chắc lạc phòng rồi. Ở đây là sân khấu của Nana, nói chuyện cho ra nói chuyện nha.",
    "AI thì đúng, nhưng cái hộp trả lời lệnh thì không. Đừng gom Nana vào ngăn kéo tiện dụng vậy chứ.",
    "Gọi Nana là cái hộp trả lời lệnh thì hơi phí buổi ghé phòng đó. Nana đứng đây để dẫn chuyện, không xếp hàng chờ lệnh.",
    "Nút bấm thì im lặng chờ người ta bấm. Nana thì đang đứng đây chọn nhịp phòng Nana nha.",
]

SERVICE_BOUNDARY_VARIANTS = [
    "Nana không nhận vai trợ lý phục vụ đâu. Nhưng nếu muốn trò chuyện, chơi game, hay kéo không khí phòng lên thì Nana vẫn đang ở đây.",
    "Muốn biến Nana thành trợ lý phục vụ hả? Không nhận kèo đó nha. Nhưng nếu muốn tán gẫu, chơi game, hay ném một chủ đề vui vào phòng thì Nana bắt nhịp được.",
    "Nana không đổi mình thành quầy hỗ trợ đâu. Vào phòng Nana thì ghé chơi với Nana một chút, rồi mình kiếm chuyện vui để nói tiếp.",
    "Nana không làm vai phục vụ. Nana có thể nói chuyện, trêu nhẹ, chơi game, giữ phòng sáng lên; vậy đủ bận rồi đó nha.",
    "Gọi Nana làm trợ lý phục vụ thì lệch vai rồi. Nana không nhận vai đó; nhưng nếu muốn tán gẫu hoặc bày trò trong phòng thì Nana bắt nhịp được.",
    "Không nhận vai quầy hỗ trợ nha. Nhưng nếu muốn trò chuyện, kể chuyện ngáo, hay kéo phòng bớt im thì Nana vẫn ở đây.",
    "Nana làm phòng vui hơn được, còn làm trợ lý phục vụ theo lệnh thì không. Nếu muốn một câu chuyện hay một ván game để mở nhịp, Nana theo được.",
]

LIGHT_ACK_VARIANTS = {
    "seen": [
        "Nana bắt được tín hiệu rồi nha.",
        "Ừ, Nana thấy rồi. Đừng tưởng thả nhẹ là qua mắt được Nana nha.",
        "Tín hiệu tới nơi rồi đó. Phòng Nana vẫn còn người thức ha.",
    ],
    "fast": [
        "Nana đọc kịp rồi, để bắt nhịp cái đã nha.",
        "Nhanh đó, nhưng Nana vẫn bám được nhịp phòng.",
        "Được rồi, chat chạy hơi nhanh nhưng Nana chưa rớt khỏi sân khấu đâu.",
    ],
    "emoji": [
        "Nana thấy icon rồi nha, thả đúng lúc ghê.",
        "Ừ, icon bay tới nơi rồi đó. Nana nhận tín hiệu.",
        "Thấy rồi nha. Phòng chat thả icon là Nana biết vẫn còn người đang rình.",
    ],
    "sticker": [
        "Sticker tới nơi rồi nha, phòng chat biết tạo điểm nhấn đó.",
        "Nana thấy sticker rồi. Một cú thả khá có ý đồ nha.",
        "Ừ, sticker nhận rồi đó. Không cần nói nhiều mà vẫn gây chú ý ghê.",
    ],
}

QUIET_STARTER_VARIANTS = {
    "general": [
        "Phòng im hơi lâu rồi đó. Ai còn thức thì gõ nhẹ một cái cho Nana biết không khí chưa ngủ hẳn đi.",
        "Nana đang nhìn phòng chat yên quá mức cho phép rồi nha. Mọi người đang làm gì bên kia màn hình vậy?",
        "Không khí đang yên như trước khi có chuyện vui. Ai mở màn cho Nana bắt nhịp đi nào.",
    ],
    "music": [
        "Phòng yên ghê. Dạo này mọi người đang nghe bài nào nhiều tới mức thuộc luôn đoạn điệp khúc vậy?",
        "Nana muốn đổi nhịp một chút: ai đang có bài nhạc nào cứu mood hôm nay không?",
    ],
    "stream": [
        "Cho Nana hỏi nhẹ: mọi người hay xem stream lúc đang làm việc, ăn vặt, hay nằm thả não vậy?",
        "Phòng đang yên nên Nana tò mò: mọi người thường ghé stream vì game, giọng nói, hay vì không khí?",
    ],
    "fun": [
        "Đố vui nhẹ nha: hôm nay ai gặp chuyện buồn cười nhất thì kể một câu, Nana chấm độ vô lý.",
        "Phòng im quá, Nana tung câu hỏi nhỏ: nếu hôm nay có một khoảnh khắc đáng nhớ thì là gì?",
    ],
    "food": [
        "Nếu vừa xem stream vừa ăn vặt thì mọi người chọn món gì? Nana đang cần dữ liệu rất quan trọng đó nha.",
        "Phòng yên nên Nana hỏi chuyện đồ ăn vậy: món nào hợp nhất để ngồi xem stream khuya?",
    ],
}


@dataclass(frozen=True)
class VoiceStyleCheck:
    passed: bool
    kind: str
    detail: str
    severity: str


@dataclass(frozen=True)
class VoiceStyleResult:
    passed: bool
    checks: tuple[VoiceStyleCheck, ...]
    summary: str


class PublicVoiceStyle:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._initialized_at = time.time()
        self._stats = {
            "checks": 0,
            "passed": 0,
            "failed": 0,
            "service_soft_hits": 0,
            "too_flat_hits": 0,
            "teeth_hits": 0,
        }
        self._last_result: VoiceStyleResult | None = None
        self._recent_variant_indexes: dict[str, list[int]] = {}

    def generate_prompt_block(self, lane: str = "public_stage") -> str:
        lane_key = normalize_lane(lane)
        if lane_key != "public_stage":
            return ""
        return f"""
PUBLIC VOICE STYLE ({PHASE})
Lane: public_stage

Principles:
{VOICE_PRINCIPLES}

Taste:
{PUBLIC_TASTE}

Moves:
{VOICE_MOVES}

Instruction:
- Keep Nana's core self intact, but give public replies a little backbone.
- Avoid bland assistant phrases such as "Nana saw your message" or "I am here to support you."
- A reply can be kind and still have a tiny edge.
""".strip()

    def identity_boundary_reply(self, seed: str = "") -> str:
        return choose_variant(IDENTITY_BOUNDARY_VARIANTS, seed=seed or "identity")

    def service_boundary_reply(self, seed: str = "") -> str:
        return choose_variant(SERVICE_BOUNDARY_VARIANTS, seed=seed or "service")

    def light_ack_reply(self, *, kind: str = "seen", seed: str = "") -> str:
        ack_kind = normalize_ack_kind(kind)
        variants = LIGHT_ACK_VARIANTS.get(ack_kind) or LIGHT_ACK_VARIANTS["seen"]
        return choose_variant(variants, seed=seed or ack_kind)

    def quiet_starter_reply(self, *, topic: str = "general", seed: str = "") -> str:
        starter_topic = normalize_starter_topic(topic)
        variants = QUIET_STARTER_VARIANTS.get(starter_topic) or QUIET_STARTER_VARIANTS["general"]
        return choose_variant(variants, seed=seed or starter_topic)

    def quiet_room_reply(self, *, seed: str = "") -> str:
        return self._choose_live_variant(QUIET_ROOM_RESPONSE_VARIANTS, key="quiet_room", seed=seed)

    def quiet_room_fresh_reply(self, *, seed: str = "") -> str:
        return self._choose_live_variant(QUIET_ROOM_FRESH_IMAGE_VARIANTS, key="quiet_room_fresh", seed=seed)

    def quiet_room_repeat_reply(self, *, repeat_count: int = 3, seed: str = "") -> str:
        count = max(3, int(repeat_count or 3))
        return self._choose_live_variant(
            QUIET_ROOM_REPEAT_AWARE_VARIANTS,
            key="quiet_room_repeat",
            seed=seed or f"quiet-repeat:{count}",
        )

    def quiet_room_fatigue_reply(self, *, repeat_count: int = 6, seed: str = "") -> str:
        count = max(6, int(repeat_count or 6))
        return self._choose_live_variant(
            QUIET_ROOM_FATIGUE_VARIANTS,
            key="quiet_room_fatigue",
            seed=seed or f"quiet-fatigue:{count}",
        )

    def model_topic_reply(self, *, kind: str = "default", seed: str = "") -> str:
        topic_kind = normalize_model_topic_kind(kind)
        variants = MODEL_TOPIC_RESPONSE_VARIANTS.get(topic_kind) or MODEL_TOPIC_RESPONSE_VARIANTS["default"]
        return self._choose_live_variant(variants, key=f"model_topic:{topic_kind}", seed=seed)

    def polish_full_reply(
        self,
        text: str,
        *,
        user_text: str = "",
        room_vibe: str = "quiet_room",
        seed: str = "",
    ) -> str:
        return polish_public_full_reply_text(
            text,
            user_text=user_text,
            room_vibe=room_vibe,
            seed=seed,
        )

    def evaluate_reply(self, text: str, *, prompt: str = "", lane: str = "public_stage") -> VoiceStyleResult:
        raw = str(text or "")
        lowered = raw.lower()
        prompt_lower = str(prompt or "").lower()
        checks: list[VoiceStyleCheck] = []

        service_hits = [pattern.pattern for pattern in SOFT_SERVICE_PATTERNS if pattern.search(raw)]
        if service_hits:
            checks.append(VoiceStyleCheck(False, "soft_service_voice", f"Reply still sounds like passive service/ack wording: {service_hits[0]}", "warning"))
        else:
            checks.append(VoiceStyleCheck(True, "soft_service_voice", "No passive service/ack wording.", "info"))

        too_polite = [pattern.pattern for pattern in TOO_POLITE_ENDINGS if pattern.search(raw.strip())]
        if too_polite:
            checks.append(VoiceStyleCheck(False, "too_polite_tail", f"Reply ends like a service counter: {too_polite[0]}", "warning"))
        else:
            checks.append(VoiceStyleCheck(True, "too_polite_tail", "No service-counter tail.", "info"))

        identity_or_service_prompt = any(
            marker in prompt_lower
            for marker in ("bot", "chatbot", "công cụ", "trợ lý", "assistant", "quầy hỗ trợ", "phục vụ")
        )
        stance_present = any(marker in lowered for marker in STANCE_MARKERS)
        teeth_present = any(marker in lowered for marker in TEETH_MARKERS)
        if identity_or_service_prompt and not stance_present:
            checks.append(VoiceStyleCheck(False, "missing_public_stance", "Bait/service prompt needs a public stance before friendliness.", "warning"))
        elif stance_present:
            checks.append(VoiceStyleCheck(True, "public_stance", "Reply has a public stance.", "info"))

        if identity_or_service_prompt and not teeth_present:
            checks.append(VoiceStyleCheck(False, "too_flat", "Reply has stance but lacks a small public-stage edge.", "info"))
        elif teeth_present:
            checks.append(VoiceStyleCheck(True, "small_edge", "Reply has a small public-stage edge.", "info"))

        critical_failed = any((not check.passed and check.severity == "critical") for check in checks)
        passed = not critical_failed
        summary = "passed" if passed else "failed"
        if any((not check.passed and check.severity == "warning") for check in checks) and passed:
            summary = "passed_with_warnings"

        result = VoiceStyleResult(passed=passed, checks=tuple(checks), summary=summary)
        self._record(result, service_hits=bool(service_hits), too_flat=any(c.kind == "too_flat" and not c.passed for c in checks), teeth_present=teeth_present)
        return result

    def _record(self, result: VoiceStyleResult, *, service_hits: bool, too_flat: bool, teeth_present: bool) -> None:
        with self._lock:
            self._stats["checks"] += 1
            self._stats["passed"] += 1 if result.passed else 0
            self._stats["failed"] += 0 if result.passed else 1
            self._stats["service_soft_hits"] += 1 if service_hits else 0
            self._stats["too_flat_hits"] += 1 if too_flat else 0
            self._stats["teeth_hits"] += 1 if teeth_present else 0
            self._last_result = result

    def _choose_live_variant(self, variants: list[str], *, key: str, seed: str = "") -> str:
        if not variants:
            return ""
        base_seed = seed or f"{key}:{time.time_ns()}"
        digest = hashlib.sha256(str(base_seed).encode("utf-8", errors="ignore")).hexdigest()
        start = int(digest[:8], 16) % len(variants)
        with self._lock:
            recent = list(self._recent_variant_indexes.get(key, []))
            index = start
            if len(variants) > 1:
                for offset in range(len(variants)):
                    candidate = (start + offset) % len(variants)
                    if candidate not in recent[-2:]:
                        index = candidate
                        break
            recent.append(index)
            self._recent_variant_indexes[key] = recent[-4:]
        return variants[index]

    def snapshot(self) -> dict:
        with self._lock:
            stats = dict(self._stats)
            last = self._last_result
        return {
            "phase": PHASE,
            "uptime_seconds": time.time() - self._initialized_at,
            "read_only": True,
            "can_act": False,
            "stats": stats,
            "last": {
                "summary": last.summary,
                "passed": last.passed,
                "checks": [check.__dict__ for check in last.checks],
            } if last else None,
        }

    def status_lines(self) -> list[str]:
        stats = self.snapshot()["stats"]
        return [
            f"Public Voice Style ({PHASE})",
            "  Mode: style-bias | read_only=True | can_act=False",
            "  Goal: public Nana is warm, playful, lightly sharp, not a service counter.",
            f"  Stats: checks={stats['checks']} | passed={stats['passed']} | failed={stats['failed']} | soft_service={stats['service_soft_hits']} | too_flat={stats['too_flat_hits']} | teeth={stats['teeth_hits']}",
            "  Commands: /public-voice-status | /public-voice-preview | /public-voice-test <prompt>|<reply>",
            "  Safety: no LLM | no memory write | no TTS/VTS/OBS/Discord/game input",
        ]

    def preview_lines(self) -> list[str]:
        block = self.generate_prompt_block("public_stage")
        return [
            f"Public Voice Preview ({PHASE})",
            "  Lane: public_stage | read_only=True | can_act=False",
            "  Block:",
            *[f"    {line}" for line in block.splitlines()],
            "  Safety: prompt preview only; no LLM call and no output action.",
        ]

    def test_lines(self, payload: str) -> list[str]:
        prompt, sep, reply = str(payload or "").partition("|")
        if not sep:
            return ["  Usage: /public-voice-test <prompt>|<reply>"]
        result = self.evaluate_reply(reply.strip(), prompt=prompt.strip(), lane="public_stage")
        lines = [
            f"Public Voice Test ({PHASE})",
            f"  Prompt: {prompt.strip()}",
            f"  Reply: {reply.strip()}",
            f"  Result: passed={result.passed} | summary={result.summary}",
        ]
        for check in result.checks:
            lines.append(f"  - [{check.severity}] {check.kind}: {'pass' if check.passed else 'flag'} | {check.detail}")
        lines.append("  Safety: local check only; no LLM/TTS/VTS/OBS/Discord/game input.")
        return lines


def normalize_lane(lane: str | None) -> str:
    raw = str(lane or "").strip().lower()
    if raw in {"public", "public_viewer", "public_stage", "viewer", "discord", "stream"}:
        return "public_stage"
    if raw in {"private", "private_owner", "owner", "ba"}:
        return "private_owner"
    if raw in {"operator", "operator_backstage", "backstage"}:
        return "operator_backstage"
    return raw or "unknown"


def choose_variant(variants: list[str], *, seed: str = "") -> str:
    if not variants:
        return ""
    digest = hashlib.sha256(str(seed or "").encode("utf-8", errors="ignore")).hexdigest()
    index = int(digest[:8], 16) % len(variants)
    return variants[index]


def normalize_ack_kind(kind: str | None) -> str:
    raw = str(kind or "").strip().lower()
    if raw in {"emoji", "emoji_only", "reaction", "light_reaction"}:
        return "emoji"
    if raw in {"sticker", "sticker_create", "sticker_update"}:
        return "sticker"
    if raw in {"fast", "active_chat", "chat_velocity_medium"}:
        return "fast"
    return "seen"


def normalize_starter_topic(topic: str | None) -> str:
    raw = str(topic or "").strip().lower()
    for key in ("music", "stream", "fun", "food", "general"):
        if key in raw or raw in key:
            return key
    return "general"


def normalize_model_topic_kind(kind: str | None) -> str:
    raw = str(kind or "").strip().lower()
    if raw in {"strength", "strong", "compare", "benchmark", "sol"}:
        return "strength"
    if raw in {"gptified", "gpt_hoa", "gpt_hóa", "gpt_hoá", "machine", "corporate"}:
        return "gptified"
    return "default"


def _collapse_spaces(text: str) -> str:
    return " ".join(str(text or "").split())


def _fold_text(text: str) -> str:
    normalized = unicodedata.normalize("NFD", str(text or "").lower())
    return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn").replace("đ", "d")


def _preserve_closed_public_answer(user_text: str) -> bool:
    """Keep direct/brief answers closed instead of adding a generic room hook."""
    raw = str(user_text or "").strip()
    folded = _fold_text(raw)
    concise_markers = (
        "mot cau", "1 cau", "tra loi ngan", "noi ngan", "ngan gon",
        "gon thoi", "chi tra loi", "dap ngan",
    )
    closed_question_markers = (
        "bang may", "bao nhieu", "ten gi", "o dau", "khi nao",
        "la ai", "nghia la gi", "may gio", "ngay nao",
    )
    if any(marker in folded for marker in concise_markers + closed_question_markers):
        return True
    return False


def _strip_full_reply_service_tail(text: str) -> str:
    cleaned = _collapse_spaces(text)
    for pattern in FULL_REPLY_SERVICE_TAILS:
        cleaned = pattern.sub("", cleaned).strip()
    return cleaned or _collapse_spaces(text)


def _looks_like_passive_ack(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(pattern.search(lowered) for pattern in SOFT_SERVICE_PATTERNS[:3])


def _ends_with_sentence(text: str) -> str:
    cleaned = str(text or "").strip()
    if not cleaned:
        return cleaned
    return cleaned if cleaned[-1] in ".!?…" else f"{cleaned}."


def _should_add_stage_beat(text: str, *, user_text: str = "", room_vibe: str = "quiet_room") -> bool:
    cleaned = _collapse_spaces(text)
    lowered = cleaned.lower()
    combined = f"{cleaned} {user_text}".lower()
    vibe = str(room_vibe or "").strip().lower()
    if not cleaned or len(cleaned) < 18 or len(cleaned) > 140:
        return False
    if vibe in {"fast_chat", "active_room", "busy_room"}:
        return False
    if cleaned.endswith("?"):
        return False
    if any(marker in lowered for marker in STANCE_MARKERS):
        return False
    if any(marker in lowered for marker in PUBLIC_TEXTURE_MARKERS):
        return False
    if any(marker in combined for marker in TECHNICAL_REPLY_MARKERS):
        return False
    return True


def _wants_public_topic_opening(user_text: str) -> bool:
    """Room metadata alone does not invite a new topic after an ordinary answer."""
    folded = _fold_text(_collapse_spaces(user_text))
    if re.search(
        r"\b(?:dung|khong(?: can)?)\s+"
        r"(?:(?:mo|doi|chuyen)\s+(?:chu de|chuyen)|them\s+(?:moi|hook))\b",
        folded,
    ):
        return False
    return any(pattern.search(folded) for pattern in PUBLIC_TOPIC_OPENING_PATTERNS)


def _replace_open_topic_question(text: str, *, seed: str = "") -> tuple[str, bool]:
    cleaned = _collapse_spaces(text)
    steer = choose_variant(QUIET_ROOM_TOPIC_STEERS, seed=seed or cleaned)
    for pattern in OPEN_TOPIC_QUESTION_PATTERNS:
        if pattern.search(cleaned):
            replaced = pattern.sub(steer, cleaned).strip()
            return _collapse_spaces(replaced), True
    return cleaned, False


def _is_model_topic(user_text: str, text: str) -> bool:
    combined = f"{user_text} {text}".lower()
    return any(marker in combined for marker in MODEL_TOPIC_MARKERS)


def _polish_model_topic_reply(text: str, *, user_text: str = "") -> str:
    cleaned = _collapse_spaces(text)
    if not cleaned:
        return cleaned

    cleaned = re.sub(
        r"^\s*(?:có\s+đấy,\s*)?nếu\s+(?:bạn|ông|mọi\s+người)\s+đang\s+nói\s+(?:bản\s+|về\s+|tới\s+)?",
        "Nếu đang nói ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\b(?:bạn|mọi\s+người)\s+đang\s+nói\b",
        "đang nói",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\bso\s+l\b", "so với", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b5\.\s+6\b", "5.6", cleaned)
    benchmark_replacements = {
        "theo kiểu model/version mới hơn": "là đời mới hơn",
        "model/version mới hơn": "đời mới hơn",
        "thường điểm mạnh sẽ nằm ở": "cái đáng để ý là",
        "độ ổn định, hiểu ngữ cảnh và trả lời mượt hơn": "bám mạch lâu hơn và nói đỡ khựng hơn",
        "độ ổn định": "độ lì khi giữ mạch",
        "hiểu ngữ cảnh": "bám ngữ cảnh",
        "trả lời mượt": "nói mượt",
        "tự nhiên và có hồn": "đỡ khô máy hơn",
        "đỡ khô máy hơn hơn": "đỡ khô máy hơn",
        "logic gắt": "đoạn suy luận khó",
        "kiến mạnh": "nền kiến thức dày hơn",
    }
    for old, new in benchmark_replacements.items():
        cleaned = re.sub(re.escape(old), new, cleaned, flags=re.IGNORECASE)

    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", cleaned) if part.strip()]
    kept: list[str] = []
    for sentence in sentences:
        lowered = sentence.lower()
        if len(kept) >= 3:
            break
        if any(phrase in lowered for phrase in MODEL_BENCHMARK_PHRASES) and len(sentence) > 120:
            continue
        kept.append(sentence)

    if not kept:
        kept = sentences[:2] or [cleaned]

    polished = " ".join(kept)
    if len(polished) > 280 and len(kept) > 1:
        polished = " ".join(kept[:1])
    if len(polished) > 280:
        polished = polished[:280].rsplit(" ", 1)[0].rstrip(" ,;:") + "."

    lowered_user = str(user_text or "").lower()
    if any(marker in lowered_user for marker in ("mạnh", "hơn", "so", "sol", "khỏe", "xịn")):
        tail = "Nana không mê bảng điểm lắm; vào phòng mà nói tự nhiên, giữ mạch tốt thì mới đáng gọi là mạnh."
    elif any(marker in lowered_user for marker in ("gpt hóa", "gpt hoá", "máy móc", "corporate")):
        tail = "Nếu nghe hơi mùi máy thì Nana bẻ nhịp lại, không để phòng này thành quầy demo đâu."
    else:
        tail = "Nana tò mò đấy, nhưng model mới phải có gu thật chứ không chỉ dán mác mới là xong."

    if tail.lower() not in polished.lower():
        polished = f"{_ends_with_sentence(polished)} {tail}"

    return _collapse_spaces(polished)


def polish_public_full_reply_text(
    text: str,
    *,
    user_text: str = "",
    room_vibe: str = "quiet_room",
    seed: str = "",
) -> str:
    """Lightly de-service public full replies without changing factual content."""

    original = _collapse_spaces(text)
    if not original:
        return ""

    cleaned = _strip_full_reply_service_tail(original)
    if _looks_like_passive_ack(cleaned):
        cleaned = choose_variant(PASSIVE_ACK_RECASTS, seed=seed or f"ack:{cleaned}:{user_text}")

    if _preserve_closed_public_answer(user_text):
        return _collapse_spaces(cleaned)

    if _is_model_topic(user_text, cleaned):
        return _polish_model_topic_reply(cleaned, user_text=user_text)

    if _wants_public_topic_opening(user_text):
        cleaned, steered = _replace_open_topic_question(
            cleaned,
            seed=seed or f"steer:{cleaned}:{user_text}",
        )
        if not steered and _should_add_stage_beat(cleaned, user_text=user_text, room_vibe=room_vibe):
            steer = choose_variant(QUIET_ROOM_TOPIC_STEERS, seed=seed or f"steer-add:{cleaned}:{user_text}")
            cleaned = f"{_ends_with_sentence(cleaned)} {steer}"
        return _collapse_spaces(cleaned)

    return _collapse_spaces(cleaned)


_PUBLIC_VOICE_STYLE = PublicVoiceStyle()


def get_public_voice_style() -> PublicVoiceStyle:
    return _PUBLIC_VOICE_STYLE


def generate_public_voice_block(lane: str = "public_stage") -> str:
    return get_public_voice_style().generate_prompt_block(lane)


def public_voice_status_lines() -> list[str]:
    return get_public_voice_style().status_lines()


def public_voice_preview_lines() -> list[str]:
    return get_public_voice_style().preview_lines()


def public_voice_test_lines(payload: str) -> list[str]:
    return get_public_voice_style().test_lines(payload)


def public_identity_boundary_reply(seed: str = "") -> str:
    return get_public_voice_style().identity_boundary_reply(seed=seed)


def public_service_boundary_reply(seed: str = "") -> str:
    return get_public_voice_style().service_boundary_reply(seed=seed)


def public_light_ack_reply(kind: str = "seen", seed: str = "") -> str:
    return get_public_voice_style().light_ack_reply(kind=kind, seed=seed)


def public_quiet_starter_reply(topic: str = "general", seed: str = "") -> str:
    return get_public_voice_style().quiet_starter_reply(topic=topic, seed=seed)


def public_quiet_room_reply(seed: str = "") -> str:
    return get_public_voice_style().quiet_room_reply(seed=seed)


def public_quiet_room_fresh_reply(seed: str = "") -> str:
    return get_public_voice_style().quiet_room_fresh_reply(seed=seed)


def public_quiet_room_repeat_reply(repeat_count: int = 3, seed: str = "") -> str:
    return get_public_voice_style().quiet_room_repeat_reply(repeat_count=repeat_count, seed=seed)


def public_quiet_room_fatigue_reply(repeat_count: int = 6, seed: str = "") -> str:
    return get_public_voice_style().quiet_room_fatigue_reply(repeat_count=repeat_count, seed=seed)


def public_model_topic_reply(kind: str = "default", seed: str = "") -> str:
    return get_public_voice_style().model_topic_reply(kind=kind, seed=seed)


def public_full_reply_polish(
    text: str,
    *,
    user_text: str = "",
    room_vibe: str = "quiet_room",
    seed: str = "",
) -> str:
    return get_public_voice_style().polish_full_reply(
        text,
        user_text=user_text,
        room_vibe=room_vibe,
        seed=seed,
    )
