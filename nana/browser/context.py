import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlparse

from nana.config import BROWSER_CDP_ENDPOINTS


@dataclass
class BrowserSnapshot:
    available: bool = False
    browser: str | None = None
    url: str | None = None
    title: str | None = None
    kind: str = "unknown"
    reason: str = "not_checked"
    page_heading: str | None = None
    meta_description: str | None = None
    selected_text: str | None = None
    dom_debug: str | None = None
    site_signals: dict | None = None
    social_post_text: str | None = None
    social_comments: list[str] | None = None
    social_vibe: str | None = None
    local_summary: str | None = None
    local_helper_debug: str | None = None


class BrowserContextReader:
    def __init__(self, endpoints=None, timeout=0.4):
        self.endpoints = endpoints or BROWSER_CDP_ENDPOINTS
        self.timeout = timeout
        self.request_id = 1

    def read(self):
        last_reason = "debug_port_unavailable"
        for browser_name, endpoint in self.endpoints:
            try:
                pages = self._fetch_pages(endpoint)
            except Exception as exc:
                last_reason = f"{browser_name}: {exc}"
                continue

            page = self._select_page(pages)
            if not page:
                last_reason = f"{browser_name}: no_page"
                continue

            dom_info = self._read_dom_snapshot(page)
            return BrowserSnapshot(
                available=True,
                browser=browser_name,
                url=page.get("url"),
                title=page.get("title"),
                kind=self.classify_page(page.get("url"), page.get("title")),
                reason="ok",
                page_heading=dom_info.get("page_heading"),
                meta_description=dom_info.get("meta_description"),
                selected_text=dom_info.get("selected_text"),
                dom_debug=dom_info.get("dom_debug"),
                site_signals=dom_info.get("site_signals"),
                social_post_text=dom_info.get("social_post_text"),
                social_comments=dom_info.get("social_comments"),
                social_vibe=dom_info.get("social_vibe"),
            )

        return BrowserSnapshot(available=False, reason=last_reason)

    def from_active_window(self, app_name, title, reason="window_title_fallback"):
        if app_name != "msedge":
            return BrowserSnapshot(available=False, reason="not_edge")
        return BrowserSnapshot(
            available=True,
            browser="edge",
            url=None,
            title=self._clean_edge_title(title),
            kind=self.classify_page(None, title),
            reason=reason,
        )

    def classify_page(self, url, title):
        lowered_title = (title or "").lower()
        parsed = urlparse(url or "")
        host = (parsed.hostname or "").lower()
        path = parsed.path.lower()
        query = parsed.query.lower()
        combined = " ".join([host, path, query, lowered_title])

        if any(domain in host for domain in ["chatgpt.com", "gemini.google.com", "claude.ai", "copilot.microsoft.com", "perplexity.ai"]):
            return "ai_tools"
        if any(domain in host for domain in ["shopee.", "lazada.", "tiki.vn", "amazon.", "ebay."]):
            return "shopping"
        if any(domain in host for domain in ["music.youtube.com", "open.spotify.com", "soundcloud.com"]):
            return "music"
        if "youtube.com" in host or "youtu.be" in host:
            return "youtube"
        if "github.com" in host:
            return "github"
        if host in {"localhost", "127.0.0.1"}:
            return "local"
        if any(domain in host for domain in ["google.", "bing.com", "duckduckgo.com"]):
            if "search" in path or "q=" in query or "search" in lowered_title:
                return "search"
            return "search_home"
        if any(token in combined for token in ["docs", "documentation", "developer", "reference", "api"]):
            return "docs"
        if any(domain in host for domain in ["bilibili.com", "b23.tv"]):
            return "video"
        if any(domain in host for domain in ["x.com", "twitter.com", "facebook.com", "messenger.com", "m.me", "reddit.com", "discord.com"]):
            return "social"
        if any(token in combined for token in ["video", "watch", "stream"]):
            return "video"
        return "unknown"

    def _fetch_pages(self, endpoint):
        with urllib.request.urlopen(endpoint, timeout=self.timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
        return json.loads(body)

    def _read_dom_snapshot(self, page):
        websocket_url = page.get("webSocketDebuggerUrl")
        if not websocket_url:
            return {"dom_debug": "no_websocket_debugger_url"}
        try:
            import websocket
        except Exception:
            return {"dom_debug": "websocket_client_not_installed"}

        script = """
(() => {
  const firstText = (selector) => {
    const node = document.querySelector(selector);
    return node ? (node.innerText || node.textContent || "").trim() : "";
  };
  const selectors = [
    "main h1",
    "article h1",
    "[role='main'] h1",
    "main h2",
    "article h2",
    "#readme h1",
    "#readme h2",
    "h1",
    "h2"
  ];
  const heading = selectors.map(firstText).find(Boolean) || "";
  const meta = document.querySelector('meta[name="description"]');
  const selected = (window.getSelection && window.getSelection().toString()) || "";
  const socialPostText = extractSocialPostText();
  const socialComments = extractSocialComments();
  const repoName = firstText('strong[itemprop="name"] a') || firstText('[data-testid="repository-name"] a');
  const readmeHeading = firstText('#readme h1') || firstText('#readme h2');
  const docsSection = firstText('main h2') || firstText('main h3') || firstText('article h2');
  return JSON.stringify({
    page_heading: heading,
    meta_description: meta ? (meta.content || "").trim() : "",
    selected_text: selected.trim().slice(0, 280),
    social_post_text: socialPostText,
    social_comments: socialComments,
    site_signals: {
      repo_name: repoName,
      readme_heading: readmeHeading,
      docs_section: docsSection
    }
  });

  function isSocialUiText(text) {
    return /^(repost|reply|like|view|show more|hiển thị thêm|đăng câu trả lời|post your reply|trả lời|replying to)$/i.test(text);
  }

  function extractSocialPostText() {
    const host = location.hostname.toLowerCase();
    if (!host.includes('x.com') && !host.includes('twitter.com') && !host.includes('facebook.com')) {
      return "";
    }
    const currentStatus = (location.pathname.match(/\\/status\\/(\\d+)/) || [])[1] || '';
    const articles = Array.from(document.querySelectorAll('article, [role="article"]'));
    let article = null;
    if (currentStatus) {
      article = articles.find((candidate) => isCurrentStatusArticle(candidate, currentStatus));
    }
    if (!article) {
      article = articles.find((candidate) => {
        const rect = candidate.getBoundingClientRect();
        return rect.width >= 180 && rect.height >= 80 && rect.bottom > 0 && rect.top < window.innerHeight;
      });
    }
    if (!article) return "";
    const nodes = Array.from(article.querySelectorAll('[data-testid="tweetText"], [lang]'));
    for (const node of nodes) {
      const raw = (node.innerText || node.textContent || '').trim().replace(/\\s+/g, ' ');
      if (!raw || raw.length < 3 || isSocialUiText(raw)) continue;
      return raw.slice(0, 420);
    }
    return "";

    function isCurrentStatusArticle(article, statusId) {
      if (!statusId) return false;
      const links = Array.from(article.querySelectorAll('a[href*="/status/"]'));
      return links.some((link) => (link.getAttribute('href') || '').includes('/status/' + statusId));
    }
  }

  function extractSocialComments() {
    const host = location.hostname.toLowerCase();
    if (!host.includes('x.com') && !host.includes('twitter.com') && !host.includes('facebook.com')) {
      return [];
    }
    const seen = new Set();
    const perHandle = new Map();
    const comments = [];
    const currentStatus = (location.pathname.match(/\\/status\\/(\\d+)/) || [])[1] || '';
    const articles = Array.from(document.querySelectorAll('article, [role="article"]'));
    for (const article of articles) {
      if (isCurrentStatusArticle(article, currentStatus)) continue;
      const node = article.querySelector('[data-testid="tweetText"]') || article.querySelector('[lang]');
      if (!node) continue;
      const raw = (node.innerText || node.textContent || '').trim().replace(/\\s+/g, ' ');
      if (!raw || raw.length < 3 || raw.length > 180) continue;
      if (isSocialUiText(raw)) continue;
      const handle = extractHandle(article);
      const handleKey = (handle || 'unknown').toLowerCase();
      const handleCount = perHandle.get(handleKey) || 0;
      if (handleCount >= 1) continue;
      const key = raw.toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      perHandle.set(handleKey, handleCount + 1);
      comments.push({ handle, text: raw });
      if (comments.length >= 8) break;
    }
    return comments;

    function isCurrentStatusArticle(article, statusId) {
      if (!statusId) return false;
      const links = Array.from(article.querySelectorAll('a[href*="/status/"]'));
      return links.some((link) => (link.getAttribute('href') || '').includes('/status/' + statusId));
    }

    function extractHandle(article) {
      const anchors = Array.from(article.querySelectorAll('a[href^="/"]'));
      for (const anchor of anchors) {
        const text = (anchor.innerText || anchor.textContent || '').trim();
        const mention = text.match(/@[A-Za-z0-9_]+/);
        if (mention) return mention[0];
      }
      for (const anchor of anchors) {
        const href = anchor.getAttribute('href') || '';
        const match = href.match(/^\\/([^/?#]+)$/);
        if (match && !['home', 'explore', 'notifications', 'messages', 'i'].includes(match[1])) {
          return '@' + match[1];
        }
      }
      return 'unknown';
    }

  }
})()
"""
        try:
            ws = websocket.create_connection(
                websocket_url,
                timeout=self.timeout,
                suppress_origin=True,
            )
            try:
                request_id = self._next_request_id()
                ws.send(json.dumps({
                    "id": request_id,
                    "method": "Runtime.evaluate",
                    "params": {
                        "expression": script,
                        "returnByValue": True,
                    },
                }))
                payload = self._recv_response(ws, request_id)
            finally:
                ws.close()
            if payload.get("error"):
                return {"dom_debug": f"runtime_error: {payload['error'].get('message', 'unknown')}"}
            value = payload.get("result", {}).get("result", {}).get("value")
            if not value:
                return {"dom_debug": "runtime_evaluate_returned_empty"}
            data = json.loads(value)
            social_post_text = self._clean_text(data.get("social_post_text"), max_len=420)
            social_comments = self._clean_social_comments(data.get("social_comments"))
            return {
                "page_heading": self._clean_text(data.get("page_heading")),
                "meta_description": self._clean_text(data.get("meta_description"), max_len=280),
                "selected_text": self._clean_text(data.get("selected_text"), max_len=280),
                "dom_debug": "ok",
                "site_signals": self._clean_site_signals(data.get("site_signals")),
                "social_post_text": social_post_text,
                "social_comments": social_comments,
                "social_vibe": self._summarize_social_vibe(social_comments, post_text=social_post_text),
            }
        except TimeoutError:
            return {"dom_debug": "dom_timeout_waiting_for_response"}
        except websocket.WebSocketBadStatusException as exc:
            return {"dom_debug": f"dom_websocket_status: {exc.status_code}"}
        except Exception as exc:
            return {"dom_debug": f"dom_exception: {type(exc).__name__}"}

    def _select_page(self, pages):
        if not isinstance(pages, list):
            return None
        candidates = [
            page for page in pages
            if page.get("type") == "page"
            and page.get("url")
            and not page.get("url", "").startswith(("devtools://", "chrome://", "edge://"))
        ]
        if not candidates:
            return None
        return candidates[0]

    def _clean_edge_title(self, title):
        if not title:
            return None
        suffixes = [" - Microsoft Edge", " — Microsoft Edge"]
        cleaned = title
        for suffix in suffixes:
            if cleaned.endswith(suffix):
                cleaned = cleaned[: -len(suffix)]
        return cleaned.strip() or title

    def _clean_text(self, text, max_len=160):
        if not text:
            return None
        cleaned = " ".join(str(text).split())
        if not cleaned:
            return None
        return cleaned[:max_len]

    def _next_request_id(self):
        self.request_id += 1
        return self.request_id

    def _recv_response(self, ws, request_id, max_messages=12):
        for _ in range(max_messages):
            raw = ws.recv()
            payload = json.loads(raw)
            if payload.get("id") == request_id:
                return payload
        raise TimeoutError("cdp_response_not_found")

    def _clean_site_signals(self, signals):
        if not isinstance(signals, dict):
            return {}
        return {
            "repo_name": self._clean_text(signals.get("repo_name")),
            "readme_heading": self._clean_text(signals.get("readme_heading")),
            "docs_section": self._clean_text(signals.get("docs_section")),
        }

    def _clean_social_comments(self, comments):
        if not isinstance(comments, list):
            return []
        cleaned = []
        seen = set()
        per_handle = {}
        for comment in comments:
            handle = "unknown"
            raw = comment
            if isinstance(comment, dict):
                handle = self._clean_text(comment.get("handle"), max_len=40) or "unknown"
                raw = comment.get("text")
            text = self._clean_text(raw, max_len=120)
            if not text:
                continue
            if self._looks_like_social_ui_text(text):
                continue
            handle_key = handle.lower()
            if per_handle.get(handle_key, 0) >= 1:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            per_handle[handle_key] = per_handle.get(handle_key, 0) + 1
            cleaned.append(f"{handle}: {text}" if handle != "unknown" else text)
            if len(cleaned) >= 8:
                break
        return cleaned

    def _looks_like_social_ui_text(self, text):
        lowered = text.strip().lower()
        blocked = {
            "repost",
            "reply",
            "like",
            "view",
            "show more",
            "hiển thị thêm",
            "dang cau tra loi",
            "đăng câu trả lời",
            "post your reply",
            "trả lời",
        }
        return lowered in blocked

    def _summarize_social_vibe(self, comments, post_text=None):
        comments = comments or []
        post_text = self._clean_text(post_text, max_len=220)
        if not comments and not post_text:
            return None
        joined = " ".join(comments).lower()
        stats = {
            "location": self._count_social_markers(joined, ["ở đâu", "o dau", "đâu vậy", "dau vay", "quán nào", "quan nao", "địa chỉ", "dia chi"]),
            "busy": self._count_social_markers(joined, ["đông", "dong", "vui", "náo nhiệt", "nao nhiet", "đi dạo", "di dao", "xôm", "xom", "chill"]),
            "playful": self._count_social_markers(joined, ["hihi", "haha", "kk", "🤣", "😂"]),
            "praise": self._count_social_markers(joined, ["đẹp", "dep", "xinh", "ngon", "thích", "thich", "hay", "ấm", "am"]),
            "uncertain": self._count_social_markers(joined, ["không quen", "khong quen", "không chắc", "khong chac", "lạ", "la "]),
            "morning": self._count_social_markers(joined, ["gm", "chào buổi sáng", "chao buoi sang", "ngày mới", "ngay moi", "năng lượng", "nang luong", "bình minh", "binh minh"]),
            "motivation": self._count_social_markers(joined, ["quyết tâm", "quyet tam", "chinh phục", "chinh phuc", "tiến bộ", "tien bo", "cùng chiến", "cung chien"]),
        }
        signals = []
        post_topic = self._summarize_post_topic(post_text)
        if stats["location"]:
            signals.append("nhiều người hỏi địa điểm")
        if stats["busy"]:
            signals.append("vibe đông vui/đi dạo phố")
        if stats["playful"]:
            signals.append("giọng đùa nhẹ")
        if stats["praise"]:
            signals.append("có khen cảnh/người/không khí")
        if stats["uncertain"]:
            signals.append("có chút tò mò/không chắc")
        if stats["morning"]:
            signals.append("vibe chào buổi sáng/năng lượng tích cực")
        if stats["motivation"]:
            signals.append("vibe động viên/cùng cố gắng")
        sample = "; ".join(comments[:3])
        stat_text = ", ".join(f"{name}={count}" for name, count in stats.items() if count)
        if not stat_text:
            stat_text = "neutral=1"
        crowd_summary = ", ".join(signals) if signals else "vibe trung tính, phản hồi ngắn là hợp"
        if post_topic:
            summary = f"bám bài gốc: {post_topic}; crowd chỉ phụ: {crowd_summary}"
            post_part = f" | post_topic={post_topic}"
        else:
            summary = crowd_summary
            post_part = ""
        sample_part = f" | samples({min(len(comments), 3)}/{len(comments)}): {sample}" if comments else ""
        return f"role=người ngoài hóng chuyện{post_part} | stats: {stat_text} | take: {summary}{sample_part}"

    def _summarize_post_topic(self, text):
        if not text:
            return None
        lowered = text.lower()
        normalized = self._strip_vietnamese_marks(lowered)
        match_text = f"{lowered} {normalized}"
        topic_markers = [
            ("vấn đề thuốc/thực phẩm giả và minh bạch", ["thuốc giả", "thuoc gia", "thực phẩm", "thuc pham", "minh bạch", "minh bach", "người tiêu dùng", "nguoi tieu dung"]),
            ("xô xát/tranh cãi nơi công cộng", ["cãi vã", "cai va", "xô xát", "xo xat", "ẩu đả", "au da", "đánh nhau", "danh nhau", "đưa tay", "dua tay", "phản ứng mạnh", "phan ung manh", "không chịu đựng", "khong chiu dung"]),
            ("tình huống nguy hiểm/sốc", ["gây sốc", "gay soc", "nguy hiểm", "nguy hiem", "tai nạn", "tai nan", "máu", "mau", "băng bó", "bang bo", "truyền dịch", "truyen dich"]),
            ("động vật đang đối đầu", ["đại chiến", "dai chien", "đối đầu", "doi dau", "giằng co", "giang co", "mèo", "meo", "cáo", "quạ"]),
            ("động vật/cute", ["mèo", "meo", "cat", "chó", "dog", "cute", "dễ thương", "de thuong"]),
            ("chào buổi sáng/năng lượng", ["chào buổi sáng", "chao buoi sang", "ngày mới", "ngay moi", "năng lượng", "nang luong"]),
            ("đời sống/phố xá", ["đi dạo", "di dao", "phố", "pho", "quán", "quan", "đông khách", "dong khach"]),
        ]
        hits = []
        for label, markers in topic_markers:
            count = self._count_social_markers(match_text, markers)
            if count:
                hits.append((count, label))
        if hits:
            hits.sort(reverse=True)
            return hits[0][1]
        return "chủ đề trong bài gốc"

    def _strip_vietnamese_marks(self, text):
        import unicodedata

        normalized = unicodedata.normalize("NFD", text or "")
        return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")

    def _count_social_markers(self, text, markers):
        return sum(1 for marker in markers if marker in text)
