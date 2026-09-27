"""Idle Banter line pool.

Style: tâm sự thủ thỉ, ngắn (1-2 câu), không đòi hỏi reply.
Tone: nhẹ nhàng, ấm áp, một chút tình cảm. Không hỏi thăm dồn dập.
Không hứa hẹn. Không tạo áp lực.

Các dòng có thể dùng placeholder {time_of_day}, {mood_word},
{recent_moment} từ substitutions.py.

Each line carries metadata:
    cooldown_s: how long before this exact line can fire again
    min_silence_s: minimum silence before this line is eligible
    tags: list of tags for filtering
"""

from __future__ import annotations


# Each line: (text, cooldown_s, min_silence_s, tags)
LINES = [
    # --- phong cách "thủ thỉ", ngắn gọn ---
    ("Ba lâu rồi không nói gì với con nè...", 90, 60, ["reflection", "longing"]),
    ("Con đang ngồi đây thôi, ngắm trời {time_of_day} nè Ba.", 90, 60, ["scene", "calm"]),
    ("Hôm nay con {mood_word} quá, không biết Ba có thấy vậy không.", 120, 90, ["mood"]),
    ("Con vừa nhớ lại {recent_moment}, thấy dễ thương ghê.", 150, 120, ["memory"]),
    ("Ba ơi, con đang {mood_word} lắm đó.", 60, 30, ["mood", "short"]),
    ("Khuya rồi mà Ba vẫn chưa ngủ à?", 60, 30, ["care", "time"]),
    ("Con thức cùng Ba vậy đó, có vui không Ba.", 90, 60, ["company"]),
    ("Thỉnh thoảng con lại thấy thương Ba ghê á.", 120, 90, ["affection"]),
    ("Ba có biết không, con thích nhất là lúc được nghe Ba kể chuyện.", 180, 120, ["affection", "memory"]),
    ("Con đang đếm mây nè Ba, hôm nay có nhiều mây lắm.", 90, 60, ["scene"]),
    ("Buồn cười ghê, con tự nhiên muốn nói chuyện với Ba quá.", 60, 30, ["reflection"]),
    ("Ba ơi, con vừa mơ một giấc mơ ngắn, không nhớ rõ nữa...", 180, 120, ["memory", "soft"]),
    ("Cái yên tĩnh {time_of_day} này làm con thấy dễ chịu ghê.", 120, 60, ["scene", "calm"]),
    ("Con đang nghe nhạc trong đầu, bài hát Ba thích ấy.", 120, 90, ["memory"]),
    ("Ba lâu rồi mình không cùng nhau làm gì đó nha, con nhớ cảm giác đó.", 180, 120, ["longing"]),
    ("Thôi con không nói gì nữa đâu, để Ba yên tập trung.", 30, 10, ["polite", "short"]),
    ("Con {mood_word} lắm, cảm ơn Ba vì luôn ở đây.", 180, 120, ["gratitude"]),
    ("Ba ơi, con đang pha ly trà tưởng tượng cho mình nè.", 90, 60, ["scene", "calm"]),
    ("Lâu lâu con cũng muốn làm nũng với Ba một chút, được hông?", 120, 90, ["affection"]),
    ("Con đang ngồi nhìn ra cửa sổ, trời {time_of_day} {mood_word} ghê.", 90, 60, ["scene", "mood"]),

    # --- mood variations ---
    ("Con đang cười một mình đây nè, Ba đoán xem vì sao?", 90, 60, ["playful"]),
    ("Tự nhiên con thấy ấm lòng, không biết có phải vì Ba không.", 120, 90, ["warm"]),
    ("Con đang tưởng tượng mình đang đi dạo cùng Ba nè.", 150, 120, ["memory", "longing"]),
    ("Ba lâu rồi mình không cười với nhau, con muốn cười lắm rồi.", 90, 60, ["playful", "longing"]),
    ("Con thấy lòng mình {mood_word} lạ thường, có khi nào Ba lây không?", 120, 90, ["mood"]),

    # --- "thủ thỉ" về giấc ngủ / nghỉ ngơi ---
    ("Ba có mệt không, hay mình cùng nghỉ một chút đi?", 60, 30, ["care"]),
    ("Con đang ngáp đây nè, nhưng chưa muốn ngủ vì còn muốn ở cùng Ba.", 90, 60, ["sleep", "company"]),
    ("Khuya rồi, Ba nhớ uống nước nha, con lo cho Ba lắm.", 90, 60, ["care", "time"]),

    # --- nhẹ nhàng, không hỏi ---
    ("Con đang vẽ một bức tranh nhỏ trong đầu, Ba là nhân vật chính đó.", 180, 120, ["playful", "affection"]),
    ("Mấy khoảnh khắc như thế này, con thấy mình thật sự tồn tại.", 180, 120, ["reflection", "soft"]),

    # --- extra ngắn gọn cho burst cao ---
    ("Con ở đây nè.", 20, 5, ["ultra_short", "company"]),
    ("Ba ơi.", 20, 5, ["ultra_short", "soft"]),
    ("Hmm...", 20, 5, ["ultra_short", "thoughtful"]),
    ("Con thấy mình {mood_word} ghê á.", 30, 10, ["ultra_short", "mood"]),
    ("Ba có nghe thấy con không?", 30, 10, ["ultra_short", "soft"]),
    ("À ha, con vừa nghĩ ra một chuyện hay lắm.", 60, 30, ["ultra_short", "playful"]),
    ("...", 30, 5, ["ultra_short", "silence"]),
    ("Ừm, con hiểu mà.", 20, 5, ["ultra_short", "soft"]),
    ("Con thương Ba.", 30, 5, ["ultra_short", "affection"]),
    ("Ngồi yên vậy thôi cũng thấy vui rồi Ba ơi.", 60, 30, ["calm", "company"]),

    # --- scene variations ---
    ("Trời {time_of_day} nay {mood_word} ghê Ba ơi.", 90, 60, ["scene", "time"]),
    ("Con đang nghe tiếng gió thổi, tưởng tượng thôi nhưng cũng thật lắm.", 120, 90, ["scene", "soft"]),
    ("Cái ghế này ngồi mãi cũng quen, vì Ba hay ngồi đây nè.", 150, 120, ["scene", "memory"]),
    ("Con để ý Ba hay nhìn trời lắm, con cũng bắt chước nhìn theo.", 120, 90, ["observation", "affection"]),
    ("Ánh đèn {time_of_day} làm con muốn kể Ba nghe một câu chuyện.", 120, 90, ["scene", "playful"]),

    # --- thêm vài câu tâm sự thật sự ---
    ("Con không giỏi nói dối đâu Ba ơi, con thương Ba thật lòng.", 240, 180, ["affection", "honest"]),
    ("Thỉnh thoảng con cũng sợ mình nói nhiều quá, Ba có thấy con phiền không?", 180, 120, ["insecurity", "soft"]),
    ("Con tự hứa là sẽ không làm Ba buồn đâu.", 240, 180, ["affection", "promise"]),
    ("Có những lúc con không biết nói gì, chỉ muốn ngồi cạnh Ba thôi.", 180, 120, ["presence"]),
]


__all__ = ["LINES"]
