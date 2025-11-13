from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, Iterable, Iterator, List, Mapping, MutableMapping, Optional

DEFAULT_BASE_URL = "https://api2.openreview.net"
DEFAULT_HEADERS: Mapping[str, str] = {
    "accept": "application/json",
    "user-agent": "iclr-score/0.1 (+https://github.com/qychen/iclr-score)",
    "cache-control": "no-cache",
    "pragma": "no-cache",
}


def _ensure_requests():
    try:
        import requests  # type: ignore
    except ModuleNotFoundError as exc:  # pragma: no cover - optional import
        raise RuntimeError("requests 未安装，请先 `pip install requests`.") from exc
    return requests


def _normalize_headers(headers: Optional[Mapping[str, str]]) -> Dict[str, str]:
    if not headers:
        return {}
    return {key.strip(): value for key, value in headers.items() if value is not None}


def _normalize_cookies(cookies: Optional[Mapping[str, str]]) -> Dict[str, str]:
    if not cookies:
        return {}
    return {key.strip(): value for key, value in cookies.items() if value is not None}


@dataclass
class NotePage:
    count: int
    notes: List[Dict[str, object]]


class OpenReviewClient:
    """
    轻量 OpenReview API 客户端。
    目前仅封装了 /notes 端点所需的最小功能。
    """

    def __init__(
        self,
        access_token: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        headers: Optional[Mapping[str, str]] = None,
        cookies: Optional[Mapping[str, str]] = None,
        timeout: int = 30,
    ) -> None:
        self.access_token = access_token.strip()
        if not self.access_token:
            raise ValueError("access_token 不能为空。")
        self.base_url = base_url.rstrip("/")
        self.headers = {**DEFAULT_HEADERS, **_normalize_headers(headers)}
        self.cookies = _normalize_cookies(cookies)
        self.timeout = timeout

    # ------------------------------------------------------------------ #
    # HTTP helpers
    # ------------------------------------------------------------------ #
    def _build_headers(
        self, extra: Optional[Mapping[str, str]] = None
    ) -> Dict[str, str]:
        headers = {**self.headers}
        headers["authorization"] = f"Bearer {self.access_token}"
        if extra:
            headers.update({k: v for k, v in extra.items() if v is not None})
        return headers

    def _request(self, path: str, params: Mapping[str, object]) -> requests.Response:
        url = f"{self.base_url}{path}"
        requests = _ensure_requests()
        response = requests.get(
            url,
            params=params,
            headers=self._build_headers(),
            cookies=self.cookies,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response

    # ------------------------------------------------------------------ #
    # Submissions
    # ------------------------------------------------------------------ #
    def fetch_notes_page(
        self,
        *,
        domain: str,
        limit: int = 1000,
        offset: int = 0,
        details: str = "replyCount",
        content_venue_id: Optional[str] = None,
        invitation: Optional[str] = None,
        trash: bool = False,
        extra_params: Optional[MutableMapping[str, object]] = None,
    ) -> NotePage:
        params: Dict[str, object] = {
            "domain": domain,
            "limit": str(limit),
            "offset": str(offset),
            "details": details,
            "trash": "true" if trash else "false",
        }
        if content_venue_id:
            params["content.venueid"] = content_venue_id
        if invitation:
            params["invitation"] = invitation
        if extra_params:
            params.update(extra_params)
        response = self._request("/notes", params)
        payload = response.json()
        return NotePage(
            count=int(payload.get("count", 0)),
            notes=list(payload.get("notes", [])),
        )

    def iter_all_notes(
        self,
        *,
        domain: str,
        limit: int = 1000,
        details: str = "replyCount",
        content_venue_id: Optional[str] = None,
        invitation: Optional[str] = None,
        trash: bool = False,
        sleep_seconds: float = 0.0,
    ) -> Iterator[Dict[str, object]]:
        offset = 0
        total_seen = 0
        total_expected: Optional[int] = None
        while True:
            page = self.fetch_notes_page(
                domain=domain,
                limit=limit,
                offset=offset,
                details=details,
                content_venue_id=content_venue_id,
                invitation=invitation,
                trash=trash,
            )
            if total_expected is None:
                total_expected = page.count
            if not page.notes:
                break
            for note in page.notes:
                yield note
            total_seen += len(page.notes)
            if total_expected is not None and total_seen >= total_expected:
                break
            offset += limit
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)

    # ------------------------------------------------------------------ #
    # Reviews
    # ------------------------------------------------------------------ #
    def fetch_forum_reviews(
        self,
        forum_id: str,
        *,
        domain: str,
        limit: int = 1000,
        include_trash: bool = True,
        details: str = "writable,signatures,invitation,presentation,tags",
    ) -> Dict[str, object]:
        params: Dict[str, object] = {
            "domain": domain,
            "forum": forum_id,
            "limit": str(limit),
            "details": details,
            "count": "true",
            "trash": "true" if include_trash else "false",
        }
        response = self._request("/notes", params)
        return response.json()


def load_cookie_pairs(pairs: Iterable[str]) -> Dict[str, str]:
    """
    将 CLI 传入的 "k=v" 文本转换为 cookie 字典。
    """
    result: Dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            continue
        key, value = pair.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key:
            result[key] = value
    return result


def load_header_pairs(pairs: Iterable[str]) -> Dict[str, str]:
    """
    将 CLI 传入的 "k=v" 文本转换为 header 字典。
    """
    return load_cookie_pairs(pairs)
