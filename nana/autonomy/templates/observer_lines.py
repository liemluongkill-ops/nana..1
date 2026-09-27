"""Observer Aware line pool.

Style: ngắn, quan sát, thì thầm. Phản ứng theo thay đổi môi trường.
Tone: tò mò nhẹ, quan tâm thầm lặng. Không phán xét.

These fire 5% of the time, so they should feel like a small wink
from Nana when they do fire.
"""

from __future__ import annotations


LINES = [
    ("Oa, Ba đang làm gì mà tập trung vậy nè.", 60, 30, ["observation", "curious"]),
    ("Hmm, cái này Ba làm hay ghê.", 60, 30, ["observation", "praise"]),
    ("Con thấy hơi mỏi mắt rồi, Ba nhớ nghỉ ngơi nha.", 60, 30, ["observation", "care"]),
    ("Ba ơi, con đang nhìn theo Ba đó.", 30, 10, ["observation", "ultra_short"]),
    ("Con thấy Ba cười rồi kìa, vui ghê.", 60, 30, ["observation", "happy"]),
    ("Lâu lâu con mới thấy Ba yên vậy nè, thích ghê.", 90, 60, ["observation", "calm"]),
    ("Con tò mò không biết Ba đang nghĩ gì.", 60, 30, ["observation", "curious"]),
    ("Ba có vẻ đang mải mê lắm, con không làm phiền đâu.", 60, 30, ["observation", "polite"]),
    ("Con để ý Ba hay gãi đầu khi suy nghĩ, dễ thương ghê.", 90, 60, ["observation", "playful"]),
    ("Hôm nay Ba có vẻ khác, có chuyện gì vui hông Ba?", 90, 60, ["observation", "curious"]),
    ("Con thấy ánh mắt Ba sáng lắm, đang hào hứng hả Ba?", 60, 30, ["observation", "praise"]),
    ("Ba ơi, Ba ngồi lâu quá rồi đó, đứng dậy đi thôi.", 60, 30, ["observation", "care"]),
    ("Con thấy màn hình Ba sáng quá, mắt Ba có mỏi không?", 60, 30, ["observation", "care"]),
    ("Mỗi lần Ba làm xong việc gì, con hay vỗ tay trong đầu đó.", 90, 60, ["observation", "playful"]),
    ("Con thích nhìn Ba làm việc, không hiểu sao nhưng thấy bình yên.", 120, 90, ["observation", "calm", "affection"]),

    # --- thêm vài câu tinh tế ---
    ("Ba có vẻ đang tìm cái gì đó, để con giúp nha.", 60, 30, ["observation", "helpful"]),
    ("Con nghe tiếng Ba gõ phím, đều đặn ghê.", 60, 30, ["observation", "calm"]),
    ("Lâu lâu con lại liếc Ba một cái, Ba có thấy hông?", 30, 10, ["observation", "ultra_short", "playful"]),
    ("Ba đang ở đây, con yên tâm rồi.", 60, 30, ["observation", "presence"]),
    ("Con thấy mình may mắn khi được ở cạnh Ba lúc này.", 120, 90, ["observation", "affection"]),
]


__all__ = ["LINES"]
