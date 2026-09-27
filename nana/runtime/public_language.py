"""Shared, context-aware Vietnamese address checks for every public guard.

Protect individual classifier/number tokens, never whole sentences. All other
privacy checks continue to see the original text. No state or I/O lives here.
"""
from __future__ import annotations

import re

_QUANTIFIERS = frozenset('một hai ba bốn tư năm sáu bảy tám chín mười những các vài mấy mỗi từng mọi cả'.split())
_CON_NOUNS = frozenset('người chữ số vật mèo thỏ chó đường sông suối thuyền tàu chim cá rồng hổ gà vịt bò dê cừu ong kiến chuột heo lợn ngựa robot pixel'.split())
_NUMBER_UNITS = frozenset('phần cánh ngày tháng năm giờ phút giây lần cái chiếc con chú người nhóm loại điều bước câu chữ dòng hàng ô viên miếng mét trăm nghìn ngàn triệu tỷ mươi tuổi vòng điểm bài tập quyển cuốn'.split())
_PREDICATES = frozenset('ơi à nhé nha nè yêu thích muốn cần biết nghĩ nhớ thấy nghe nói kể đã đang sẽ vẫn cũng có không chưa mình chúng'.split())


class PublicAddressPattern:
    """Regex-compatible search/sub interface with per-token grammar checks."""

    def __init__(self, word: str):
        self.word = word
        self.pattern = f'private_address:{word}'
        self._word = re.compile(rf'(?<!\w){word}(?!\w)', re.IGNORECASE)

    def _protected(self, match: re.Match) -> bool:
        before = re.findall(r'\w+', match.string[:match.start()].casefold())
        after = re.findall(r'^\s+(\w+)(?:\s+(\w+))?', match.string[match.end():].casefold())
        next_word, second_word = after[0] if after else ('', '')
        previous = before[-1] if before else ''
        if self.word == 'con':
            if next_word in {'nào', 'nấy', 'kia', 'này', 'đó', 'ấy'}:
                return True
            return (next_word in _CON_NOUNS or bool(next_word and next_word not in _PREDICATES
                    and (previous in _QUANTIFIERS or previous.isdigit())))
        if previous in {'thứ', 'tháng', 'số'}:
            return True
        if previous in {'cả', 'đủ', 'hơn', 'dưới', 'trên'} and match.group() == 'ba' and next_word != 'ơi':
            return True
        if previous in {'của', 'với', 'cho'}:
            return False
        if next_word == 'con' and (not second_word or second_word in _PREDICATES):
            return False
        return next_word in _NUMBER_UNITS

    def search(self, text: str):
        return next((match for match in self._word.finditer(text) if not self._protected(match)), None)

    def sub(self, replacement: str, text: str) -> str:
        return self._word.sub(lambda match: match.group() if self._protected(match) else replacement, text)


PUBLIC_CON_ADDRESS = PublicAddressPattern('con')
PUBLIC_BA_ADDRESS = PublicAddressPattern('ba')
