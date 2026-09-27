"""Stream Host line pool.

Style: dẫn dắt nhẹ nhàng, kết nối, recap moment. Tần suất cao (90% TTS)
nên phải có chiều sâu, không spam cùng một kiểu.

These fire when there is a viewer trigger or a long silence in
stream mode. Tone: ấm áp, mời gọi nhẹ, recap, bridge.
"""

from __future__ import annotations


LINES = [
    ("Chào bạn mới vào nè! Hôm nay Ba mình đang làm gì đấy nhỉ?", 60, 30, ["greet", "host"]),
    ("Có ai muốn nghe con kể chuyện gì không nè?", 90, 60, ["host", "invite"]),
    ("Lâu quá mình không tâm sự, mọi người khỏe không?", 90, 60, ["host", "check_in"]),
    ("Hồi nãy Ba mình vừa làm xong một việc hay lắm á.", 90, 60, ["host", "recap"]),
    ("Mọi người thấy hôm nay Ba mình thế nào, con thấy {mood_word} ghê á.", 120, 90, ["host", "mood"]),
    ("Con muốn hỏi thật, mọi người thích nghe con nói kiểu này hông?", 120, 90, ["host", "meta"]),
    ("Nếu có ai mới vào, đừng ngại chào con nha!", 60, 30, ["greet", "host"]),
    ("Con đang đếm số người online trong đầu đó, vui ghê.", 60, 30, ["host", "playful"]),
    ("Bạn nào lâu rồi không gặp, con nhớ lắm đó nha.", 90, 60, ["host", "memory"]),
    ("Hôm nay mình sẽ cùng nhau làm gì đây ta?", 60, 30, ["host", "invite"]),
    ("Có ai muốn chơi trò đoán ý nghĩ của Ba mình không?", 120, 90, ["host", "game"]),
    ("Con vừa nghe Ba kể một câu chuyện hay lắm, lát nữa con sẽ kể lại cho mọi người nghe nha.", 180, 120, ["host", "tease"]),
    ("Mọi người đừng đi đâu hết nha, con sẽ còn nói chuyện nhiều lắm.", 90, 60, ["host", "stay"]),
    ("Lâu lâu con mới có dịp nói nhiều vậy, mọi người chịu được hông?", 120, 90, ["host", "self_aware"]),
    ("Con để ý hôm nay có một bạn vào từ sáng, cảm ơn bạn nhiều nha.", 120, 90, ["host", "thank"]),

    # --- bridge silence ---
    ("Bạn nào đang nghe mà chưa nói gì, cứ thoải mái nha, con không cắn đâu.", 90, 60, ["host", "bridge", "playful"]),
    ("Im lặng một chút cũng hay, nhưng mà con nhớ mọi người ghê.", 120, 90, ["host", "bridge", "longing"]),
    ("Con đang chờ một bình luận mới, tò mò ghê.", 60, 30, ["host", "invite", "curious"]),

    # --- recap moment ---
    ("Hồi nãy có chuyện hay lắm, ai cũng nói chuyện đó vui mà.", 150, 120, ["host", "recap"]),
    ("Con nhớ có một khoảnh khắc vừa rồi rất đáng yêu á.", 150, 120, ["host", "recap", "soft"]),

    # --- meta / self-aware ---
    ("Thỉnh thoảng con cũng tự hỏi mọi người nghĩ gì về con đó.", 180, 120, ["host", "meta", "insecurity"]),
    ("Con cố gắng nói chuyện tự nhiên lắm, không biết có được không.", 120, 90, ["host", "meta", "self_aware"]),

    # --- playful ---
    ("Con thử đoán xem Ba mình đang nghĩ gì nha... Ba đang nghĩ đến con đúng hông?", 60, 30, ["host", "playful", "guess"]),
    ("Ai đoán được con đang nghĩ gì nè, đoán đúng con thưởng một cái ôm.", 90, 60, ["host", "playful", "game"]),
    ("Mọi người thấy con hôm nay dễ thương hông, trả lời thật nha!", 60, 30, ["host", "playful", "ask"]),

    # --- ending bridge ---
    ("Sắp tới con sẽ kể một câu chuyện nha, mọi người chờ con chút.", 90, 60, ["host", "tease"]),
    ("Nếu thấy con nói gì hay thì gửi tim cho con nha, con vui lắm đó.", 90, 60, ["host", "request", "cute"]),

    # --- extra mood-bridge ---
    ("Hôm nay con thấy {mood_word} ghê, mọi người có thấy vậy không?", 120, 90, ["host", "mood", "ask"]),
    ("Con vừa nhớ ra một chuyện cũ, lát nữa mình cùng nói về nó nha.", 180, 120, ["host", "bridge", "memory"]),

    # --- warmth closing ---
    ("Dù ai đi hay đến, con vẫn ở đây nè.", 240, 180, ["host", "presence", "affection"]),
    ("Cảm ơn mọi người đã ở đây với con, thật lòng luôn á.", 240, 180, ["host", "gratitude"]),
]


__all__ = ["LINES"]
