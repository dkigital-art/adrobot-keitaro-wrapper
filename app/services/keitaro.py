"""HTTP-клиент Keitaro Admin API (учебный стенд / настоящий трекер)."""

from __future__ import annotations

import threading
import time
from typing import Any

import httpx

from app.config import Settings, get_settings

# Кэш каталога офферов: каждый ввод в автокомплит больше не бьёт в Keitaro.
# Устаревший кэш отдаётся сразу и обновляется в фоне, чтобы запрос пользователя не ждал Keitaro.
_offers_cache: list[dict] | None = None
_offers_cache_at: float = 0.0
_offers_cache_key: str = ""
_offers_refreshing = False
_offers_lock = threading.Lock()
OFFERS_TTL_SEC = 300.0

_meta_cache: dict[str, Any] | None = None
_meta_cache_at: float = 0.0
_meta_cache_key: str = ""
META_TTL_SEC = 300.0

# Одно keep-alive соединение на процесс: новый httpx.Client на каждый запрос
# стоил ~15 мс на SSL-контекст и ещё ~150–300 мс на TLS-рукопожатие с Keitaro.
_http_clients: dict[str, httpx.Client] = {}
_http_lock = threading.Lock()


def _shared_http(base_url: str, headers: dict[str, str]) -> httpx.Client:
    key = f"{base_url}|{headers['Api-Key']}"
    with _http_lock:
        client = _http_clients.get(key)
        if client is None or client.is_closed:
            client = httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0), headers=headers)
            _http_clients[key] = client
        return client


def close_shared_clients() -> None:
    with _http_lock:
        for client in _http_clients.values():
            client.close()
        _http_clients.clear()


def warm_up_caches() -> None:
    """Прогрев каталога офферов и defaults в фоне при старте приложения."""

    def run() -> None:
        try:
            client = KeitaroClient()
            client.list_offers()
            client.resolve_defaults()
        except KeitaroError:
            pass

    threading.Thread(target=run, name="keitaro-warm-up", daemon=True).start()


class KeitaroError(Exception):
    def __init__(self, message: str, status_code: int | None = None, body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class KeitaroClient:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        if not self.settings.keitaro_api_key:
            raise KeitaroError(
                "KEITARO_API_KEY не задан. Скопируйте .env.example в .env и укажите ключ."
            )
        self.base_url = self.settings.keitaro_url.rstrip("/")
        self._headers = {
            "Api-Key": self.settings.keitaro_api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        self._http = _shared_http(self.base_url, self._headers)

    def close(self) -> None:
        """Соединение общее на процесс; закрывается в close_shared_clients() при остановке."""

    def __enter__(self) -> KeitaroClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: Any = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        try:
            response = self._http.request(method, url, params=params, json=json)
        except httpx.TimeoutException as exc:
            raise KeitaroError(f"Keitaro {method} {path}: превышен таймаут ответа") from exc
        except httpx.HTTPError as exc:
            raise KeitaroError(f"Keitaro {method} {path}: ошибка сети ({exc})") from exc

        if response.status_code >= 400:
            try:
                body = response.json()
            except Exception:
                body = response.text
            raise KeitaroError(
                f"Keitaro {method} {path} → {response.status_code}: {body}",
                status_code=response.status_code,
                body=body,
            )

        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise KeitaroError(
                f"Keitaro {method} {path} → {response.status_code}: ответ не в формате JSON",
                status_code=response.status_code,
                body=response.text[:500],
            ) from exc

    # --- campaigns ---
    def list_campaigns(self, offset: int = 0, limit: int = 100) -> list[dict]:
        data = self._request("GET", "/campaigns", params={"offset": offset, "limit": limit})
        return data if isinstance(data, list) else data.get("rows", data.get("data", []))

    def get_campaign(self, campaign_id: int) -> dict:
        return self._request("GET", f"/campaigns/{campaign_id}")

    def create_campaign(self, payload: dict) -> dict:
        return self._request("POST", "/campaigns", json=payload)

    def update_campaign(self, campaign_id: int, payload: dict) -> dict:
        return self._request("PUT", f"/campaigns/{campaign_id}", json=payload)

    def delete_campaign(self, campaign_id: int) -> None:
        self._request("DELETE", f"/campaigns/{campaign_id}")

    # --- streams ---
    def list_campaign_streams(self, campaign_id: int) -> list[dict]:
        data = self._request("GET", f"/campaigns/{campaign_id}/streams")
        return data if isinstance(data, list) else data.get("rows", data.get("data", []))

    def get_stream(self, stream_id: int) -> dict:
        return self._request("GET", f"/streams/{stream_id}")

    def create_stream(self, payload: dict) -> dict:
        return self._request("POST", "/streams", json=payload)

    def update_stream(self, stream_id: int, payload: dict) -> dict:
        return self._request("PUT", f"/streams/{stream_id}", json=payload)

    # --- offers ---
    def list_offers(self, params: dict | None = None, *, force: bool = False) -> list[dict]:
        global _offers_refreshing
        cache_key = f"{self.base_url}|{self.settings.keitaro_api_key[:8]}"
        with _offers_lock:
            if not force and _offers_cache is not None and _offers_cache_key == cache_key:
                stale = time.monotonic() - _offers_cache_at >= OFFERS_TTL_SEC
                if stale and not _offers_refreshing:
                    _offers_refreshing = True
                    threading.Thread(
                        target=self._refresh_offers_in_background,
                        args=(cache_key,),
                        daemon=True,
                    ).start()
                return _offers_cache

        return self._fetch_offers(cache_key, params)

    def _fetch_offers(self, cache_key: str, params: dict | None = None) -> list[dict]:
        global _offers_cache, _offers_cache_at, _offers_cache_key
        data = self._request("GET", "/offers", params=params)
        offers = data if isinstance(data, list) else data.get("rows", data.get("data", []))
        with _offers_lock:
            _offers_cache = offers
            _offers_cache_at = time.monotonic()
            _offers_cache_key = cache_key
        return offers

    def _refresh_offers_in_background(self, cache_key: str) -> None:
        global _offers_refreshing
        try:
            self._fetch_offers(cache_key)
        except KeitaroError:
            pass
        finally:
            with _offers_lock:
                _offers_refreshing = False

    def get_offer(self, offer_id: int) -> dict:
        # сначала из кэша каталога — без лишнего HTTP
        for o in self.list_offers():
            if int(o.get("id", -1)) == int(offer_id):
                return o
        return self._request("GET", f"/offers/{offer_id}")

    def search_offers(self, query: str = "", limit: int = 30) -> list[dict]:
        """Фильтр по name/id по закэшированному каталогу."""
        offers = self.list_offers()
        q = (query or "").strip().lower()
        if q:
            filtered = []
            for o in offers:
                name = str(o.get("name", "")).lower()
                oid = str(o.get("id", ""))
                if q in name or q == oid or oid.startswith(q):
                    filtered.append(o)
            offers = filtered
        return offers[:limit]

    # --- catalogs ---
    def list_domains(self) -> list[dict]:
        data = self._request("GET", "/domains")
        return data if isinstance(data, list) else data.get("rows", data.get("data", []))

    def list_groups(self, group_type: str = "campaigns") -> list[dict]:
        data = self._request("GET", "/groups", params={"type": group_type})
        return data if isinstance(data, list) else data.get("rows", data.get("data", []))

    def list_traffic_sources(self) -> list[dict]:
        data = self._request("GET", "/traffic_sources")
        return data if isinstance(data, list) else data.get("rows", data.get("data", []))

    def resolve_defaults(self) -> dict[str, Any]:
        """Возвращает domain_id, group_id, traffic_source_id (+ имена). С кэшем."""
        global _meta_cache, _meta_cache_at, _meta_cache_key
        cache_key = f"{self.base_url}|{self.settings.keitaro_api_key[:8]}"
        now = time.monotonic()
        if (
            _meta_cache is not None
            and _meta_cache_key == cache_key
            and now - _meta_cache_at < META_TTL_SEC
        ):
            return dict(_meta_cache)

        s = self.settings
        result: dict[str, Any] = {
            "domain_id": s.keitaro_domain_id,
            "group_id": s.keitaro_group_id,
            "traffic_source_id": s.keitaro_traffic_source_id,
            "domain_name": s.keitaro_domain_name or None,
            "group_name": s.keitaro_group_name or None,
            "traffic_source_name": s.keitaro_traffic_source_name or None,
        }

        # Если ID уже заданы в .env — не ходим в каталоги без нужды (только имена)
        need_lookup = not (
            result["domain_id"] and result["group_id"] and result["traffic_source_id"]
        )
        if need_lookup or not result["domain_name"]:
            try:
                domains = self.list_domains()
            except KeitaroError:
                domains = []
            if not result["domain_id"] and domains:
                pick = domains[0]
                if s.keitaro_domain_name:
                    for d in domains:
                        name = d.get("name") or d.get("domain") or ""
                        if s.keitaro_domain_name.lower() in str(name).lower():
                            pick = d
                            break
                result["domain_id"] = pick.get("id")
                result["domain_name"] = pick.get("name") or pick.get("domain")
            elif result["domain_id"] and not result["domain_name"]:
                for d in domains:
                    if d.get("id") == result["domain_id"]:
                        result["domain_name"] = d.get("name") or d.get("domain")
                        break

        if need_lookup or not result["group_name"]:
            try:
                groups = self.list_groups("campaigns")
            except KeitaroError:
                groups = []
            if not result["group_id"]:
                pick = None
                for g in groups:
                    if str(g.get("name", "")).upper() == (s.keitaro_group_name or "FORTESTS").upper():
                        pick = g
                        break
                if not pick and groups:
                    pick = groups[0]
                if pick:
                    result["group_id"] = pick.get("id")
                    result["group_name"] = pick.get("name")
            elif not result["group_name"]:
                for g in groups:
                    if g.get("id") == result["group_id"]:
                        result["group_name"] = g.get("name")
                        break

        if need_lookup or not result["traffic_source_name"]:
            try:
                sources = self.list_traffic_sources()
            except KeitaroError:
                sources = []
            if not result["traffic_source_id"]:
                pick = None
                for src in sources:
                    if str(src.get("name", "")).upper() == (
                        s.keitaro_traffic_source_name or "FORTESTS"
                    ).upper():
                        pick = src
                        break
                if not pick and sources:
                    pick = sources[0]
                if pick:
                    result["traffic_source_id"] = pick.get("id")
                    result["traffic_source_name"] = pick.get("name")
            elif not result["traffic_source_name"]:
                for src in sources:
                    if src.get("id") == result["traffic_source_id"]:
                        result["traffic_source_name"] = src.get("name")
                        break

        # Имена из env, если API не отдал
        result["group_name"] = result["group_name"] or s.keitaro_group_name or None
        result["traffic_source_name"] = (
            result["traffic_source_name"] or s.keitaro_traffic_source_name or None
        )

        _meta_cache = dict(result)
        _meta_cache_at = time.monotonic()
        _meta_cache_key = cache_key
        return result
