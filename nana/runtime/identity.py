"""Identity loader + user resolver.

Loads identity schema (Nana) and known users (Ba, family) from JSON files.
Pure data layer — no prompt assembly, no LLM calls. Used by `gpt.py` to
inject identity context into the system prompt at runtime.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Optional

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
IDENTITY_PATH = DATA_DIR / "identity.json"
USERS_PATH = DATA_DIR / "users.json"
IDENTITY_EXAMPLE_PATH = DATA_DIR / "identity.example.json"
USERS_EXAMPLE_PATH = DATA_DIR / "users.example.json"

_lock = threading.RLock()
_cache: dict = {"identity": None, "users": None, "loaded_at": 0.0}


def _read_json(path: Path, default: dict) -> dict:
    if not path.exists():
        example = path.with_name(path.stem + ".example" + path.suffix)
        path = example if example.exists() else path
    if not path.exists():
        return dict(default)
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else dict(default)
    except (OSError, json.JSONDecodeError):
        return dict(default)


def load_identity(force: bool = False) -> dict:
    with _lock:
        if force or _cache["identity"] is None:
            _cache["identity"] = _read_json(IDENTITY_PATH, default={})
            _cache["loaded_at"] = _cache["loaded_at"] or 0.0
        return _cache["identity"]


def load_users(force: bool = False) -> dict:
    with _lock:
        if force or _cache["users"] is None:
            _cache["users"] = _read_json(USERS_PATH, default={})
        return _cache["users"]


def reload_all() -> dict:
    with _lock:
        _cache["identity"] = _read_json(IDENTITY_PATH, default={})
        _cache["users"] = _read_json(USERS_PATH, default={})
        return {"identity": _cache["identity"], "users": _cache["users"]}


def get_default_user_id() -> str:
    identity = load_identity()
    users = load_users()
    fallback = next(
        (uid for uid, u in users.items() if isinstance(u, dict) and u.get("is_default")),
        None,
    )
    if fallback is None and users:
        fallback = next(iter(users))
    return identity.get("default_user_id") or fallback or "owner"


def resolve_user(
    message: str = "",
    viewer_name: Optional[str] = None,
    stream_mode: bool = False,
) -> dict:
    """Resolve who Nana is talking to.

    Priority:
    1. Explicit `viewer_name` from stream context → viewer role, call by acc name.
    2. Alias match in message (ba/bố/owner) → ba role.
    3. `stream_mode` is on but no viewer_name → fallback viewer with anon.
    4. Default user (Ba).
    """
    identity = load_identity()
    users = load_users()
    addressing = identity.get("addressing_rules") or {}

    if viewer_name and viewer_name.strip():
        name = viewer_name.strip()
        return {
            "id": f"viewer:{name}",
            "name": name,
            "role": "viewer",
            "pronoun": identity.get("viewer_template", {}).get("pronoun", "em"),
            "address_rule": addressing.get("viewer", f"gọi '{name}', xưng 'em'"),
            "source": "stream_viewer",
        }

    if message:
        msg_norm = _normalize(message)
        for uid, user in users.items():
            if not isinstance(user, dict):
                continue
            aliases = user.get("aliases") or []
            if any(_normalize(alias) in msg_norm for alias in aliases if alias):
                return {
                    "id": uid,
                    "name": user.get("name", "Ba"),
                    "role": user.get("role", "ba"),
                    "pronoun": user.get("pronoun", "ba"),
                    "address_rule": addressing.get(
                        uid, f"gọi '{user.get('name', 'Ba')}', xưng 'con'"
                    ),
                    "source": "alias_match",
                }

    if stream_mode:
        return {
            "id": "viewer:anon",
            "name": "bạn",
            "role": "viewer",
            "pronoun": identity.get("viewer_template", {}).get("pronoun", "em"),
            "address_rule": addressing.get("viewer", "gọi 'bạn', xưng 'em'"),
            "source": "stream_default",
        }

    default_uid = get_default_user_id()
    default_user = users.get(default_uid, {}) if isinstance(users.get(default_uid), dict) else {}
    return {
        "id": default_uid,
        "name": default_user.get("name", "Ba"),
        "role": default_user.get("role", "ba"),
        "pronoun": default_user.get("pronoun", "ba"),
        "address_rule": addressing.get(
            default_uid,
            f"gọi '{default_user.get('name', 'Ba')}', xưng 'con'",
        ),
        "source": "default",
    }


def format_identity_block(
    current_user: dict,
    identity: Optional[dict] = None,
    persona_boundary=None,
) -> str:
    """Build the identity block for the system prompt.

    Reads ONLY from JSON. No hardcoded 'Ba' / 'Nana' / 'con' here.
    """
    if persona_boundary is not None and getattr(persona_boundary, "livestream", False):
        from nana.runtime.livestream_identity import stage_prompt_block
        return stage_prompt_block() + '\n- Private owner facts are withheld in livestream chat.'
    identity = identity or load_identity()
    self_name = identity.get("self_name", "Nana")
    self_pronoun = identity.get("self_pronoun", "con")
    role = identity.get("nana_role", "")
    birth = identity.get("nana_birth", "")
    facts = identity.get("facts") or []
    addressing = identity.get("addressing_rules") or {}

    user_name = current_user.get("name", "Ba")
    user_role = current_user.get("role", "ba")
    user_pronoun = current_user.get("pronoun", self_pronoun)
    address_rule = current_user.get("address_rule") or addressing.get(
        user_role, f"gọi '{user_name}'"
    )

    viewer_rules = addressing.get("viewer")
    default_user_rules = addressing.get("owner") or next(
        iter(addressing.values()), ""
    )

    facts_block = "\n".join(f"- {fact}" for fact in facts) if facts else "- (chưa có)"
    boundary_block = ""
    if persona_boundary is not None:
        public = bool(getattr(persona_boundary, "public", False))
        if public:
            facts_block = "- Private owner facts are withheld in public viewer chat."
        boundary_block = f"""

## Persona lane
- interaction_scope: {getattr(persona_boundary, 'interaction_scope', 'unknown')}
- persona_lane: {getattr(persona_boundary, 'persona_lane', 'unknown')}
- memory_policy: {getattr(persona_boundary, 'memory_policy', 'unknown')}
- boundary_rule: {getattr(persona_boundary, 'address_rule', '')}
""".rstrip()

    return f"""# Identity (load từ nana/data/identity.json)

## Về Nana
- Tên: {self_name}
- Vai trò: {role or 'AI companion'}
- Ngày sinh: {birth or 'không rõ'}
- Khi nói về bản thân: xưng "{self_pronoun}"

## Về người đang nói chuyện
- Tên: {user_name}
- Role: {user_role}
- Cách xưng hô: {address_rule}

## Quy tắc xưng hô
- Role = owner / family → {default_user_rules}
- Role = viewer → {viewer_rules or 'gọi đúng tên acc, xưng "em"'}
- KHÔNG tự ý gọi "anh/chị" trừ khi user yêu cầu
- Nếu tên acc viewer có trong message context, gọi đúng tên đó
- Khi không rõ viewer là ai, dùng role từ current_user ở trên
{boundary_block}

## Facts về Ba & Nana
{facts_block}
""".strip()


def _normalize(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"\s+", " ", text).strip()
    return text
