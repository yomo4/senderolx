"""
OLX.ro HTTP client.

Authenticates via browser-copied cookies, sends messages to ad listings,
and supports HTTP / SOCKS4 / SOCKS5 proxies through httpx[socks].
"""

import json
import logging
import re
from typing import Any, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)


class OLXError(Exception):
    """Raised for any OLX-specific failure."""


class OLXClient:
    BASE = "https://www.olx.ro"

    # Realistic desktop browser headers
    _HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ro-RO,ro;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;"
            "q=0.9,image/avif,image/webp,*/*;q=0.8"
        ),
        "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "document",
    }

    # ── Construction ──────────────────────────────────────────────────────────

    def __init__(self, cookie_string: str, proxy: Optional[str] = None):
        self._cookie_string = cookie_string
        self._proxy = proxy

        cookies = self._parse_cookies(cookie_string)

        # Try to find a bearer / access token stored inside a cookie
        self._bearer = (
            cookies.get("access_token")
            or cookies.get("bearerToken")
            or cookies.get("user-token")
            or cookies.get("Authorization")
        )
        # Strip surrounding quotes if present
        if self._bearer and self._bearer.startswith(('"', "'")):
            self._bearer = self._bearer.strip('"\'')

        # httpx accepts "socks5://..." directly when socksio is installed
        self._http = httpx.AsyncClient(
            cookies=cookies,
            headers=self._HEADERS,
            proxy=proxy or None,
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=True,
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_cookies(s: str) -> dict[str, str]:
        result: dict[str, str] = {}
        for part in s.split(";"):
            part = part.strip()
            if "=" in part:
                name, _, value = part.partition("=")
                result[name.strip()] = value.strip()
        return result

    @staticmethod
    def _extract_offer_id(url: str) -> Optional[str]:
        """
        OLX.ro URL patterns:
          /d/oferta/some-title-IDabc123.html
          /d/oferta/some-title-IDabc123/
        Returns the alphanumeric ID after 'ID'.
        """
        m = re.search(r"ID([a-zA-Z0-9]+?)(?:\.html|/|\?|$)", url)
        return m.group(1) if m else None

    # ── Page parsing ──────────────────────────────────────────────────────────

    async def _fetch_page(self, url: str) -> dict[str, Any]:
        """Fetch an HTML page and extract embedded Next.js / JSON data."""
        resp = await self._http.get(url, headers={**self._HEADERS, "Accept": "text/html,*/*"})
        resp.raise_for_status()
        html = resp.text
        result: dict[str, Any] = {"html": html, "status": resp.status_code}

        # Next.js server-side data
        m = re.search(
            r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
            html,
            re.DOTALL,
        )
        if m:
            try:
                result["next_data"] = json.loads(m.group(1))
            except json.JSONDecodeError:
                pass

        # CSRF token inside HTML
        m2 = re.search(r'csrfmiddlewaretoken["\s]+value="([^"]+)"', html)
        if m2:
            result["csrf"] = m2.group(1)

        # x-csrf or similar meta tags
        m3 = re.search(r'meta[^>]+name="csrf-token"[^>]+content="([^"]+)"', html)
        if m3:
            result.setdefault("csrf", m3.group(1))

        return result

    async def _get_offer_info(self, url: str) -> dict[str, Any]:
        offer_id = self._extract_offer_id(url)
        if not offer_id:
            raise OLXError(f"Не удалось извлечь ID объявления из URL: {url}")

        page = await self._fetch_page(url)
        info: dict[str, Any] = {"offer_id": offer_id, "url": url}

        # Try to pull internal numeric/string ID and seller info from Next.js data
        nd = page.get("next_data", {})
        try:
            pp = nd["props"]["pageProps"]
            ad = pp.get("ad") or pp.get("offer") or pp.get("data", {}).get("ad") or {}
            info["title"] = ad.get("title", "")
            info["internal_id"] = str(ad.get("id", offer_id))
            info["seller_id"] = str(ad.get("user", {}).get("id", ""))
        except (KeyError, AttributeError, TypeError):
            info["internal_id"] = offer_id

        if "csrf" in page:
            info["csrf"] = page["csrf"]

        return info

    # ── Message sending ───────────────────────────────────────────────────────

    async def send_message(self, offer_url: str, message: str) -> dict[str, Any]:
        """Public entry-point. Tries multiple strategies in order."""
        info = await self._get_offer_info(offer_url)
        offer_id = info["internal_id"]

        errors: list[str] = []

        # Strategy 1 – REST API (new platform)
        try:
            return await self._strategy_rest_api(offer_id, message, info, offer_url)
        except OLXError as exc:
            errors.append(f"REST API: {exc}")
            logger.debug("strategy_rest_api failed: %s", exc)

        # Strategy 2 – GraphQL
        try:
            return await self._strategy_graphql(offer_id, message, info)
        except OLXError as exc:
            errors.append(f"GraphQL: {exc}")
            logger.debug("strategy_graphql failed: %s", exc)

        # Strategy 3 – Legacy contact form
        try:
            return await self._strategy_contact_form(offer_id, message, offer_url, info)
        except OLXError as exc:
            errors.append(f"Form: {exc}")
            logger.debug("strategy_contact_form failed: %s", exc)

        raise OLXError("Все методы отправки не сработали:\n" + "\n".join(errors))

    # Strategy 1 – REST ───────────────────────────────────────────────────────

    async def _strategy_rest_api(
        self,
        offer_id: str,
        message: str,
        info: dict,
        referer: str,
    ) -> dict[str, Any]:
        api_headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Origin": self.BASE,
            "Referer": referer,
            "X-Requested-With": "XMLHttpRequest",
        }
        if self._bearer:
            api_headers["Authorization"] = f"Bearer {self._bearer}"
        if "csrf" in info:
            api_headers["X-CSRFToken"] = info["csrf"]

        # Candidate endpoints (tried in order)
        candidates = [
            (f"/api/v2/offers/{offer_id}/messages/", {"message": message}),
            (f"/api/v1/offers/{offer_id}/messages/", {"message": message}),
            (f"/api/v2/offers/{offer_id}/contact/", {"message": message, "phone": ""}),
            (f"/api/v1/messages/", {"offer_id": offer_id, "message": message}),
            # Thread-based approach: create thread then post
        ]

        last_err = "no endpoints tried"
        for path, payload in candidates:
            try:
                resp = await self._http.post(
                    self.BASE + path, json=payload, headers=api_headers
                )
                logger.debug("REST %s → %s", path, resp.status_code)
                if resp.status_code in (200, 201, 204):
                    data: dict = {}
                    try:
                        data = resp.json()
                    except Exception:
                        pass
                    return {"info": f"Статус {resp.status_code}", **data}
                last_err = f"HTTP {resp.status_code}: {resp.text[:200]}"
            except httpx.HTTPError as exc:
                last_err = str(exc)

        raise OLXError(last_err)

    # Strategy 2 – GraphQL ────────────────────────────────────────────────────

    async def _strategy_graphql(
        self, offer_id: str, message: str, info: dict
    ) -> dict[str, Any]:
        gql_url = f"{self.BASE}/api/graphql/"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Origin": self.BASE,
            "Referer": info.get("url", self.BASE),
        }
        if self._bearer:
            headers["Authorization"] = f"Bearer {self._bearer}"

        mutation = """
        mutation SendMessage($offerId: ID!, $message: String!) {
          sendMessage(offerId: $offerId, message: $message) {
            success
            threadId
          }
        }
        """
        payload = {
            "query": mutation,
            "variables": {"offerId": offer_id, "message": message},
        }
        try:
            resp = await self._http.post(gql_url, json=payload, headers=headers)
            logger.debug("GraphQL → %s", resp.status_code)
            if resp.status_code == 200:
                body = resp.json()
                if "errors" in body:
                    raise OLXError("GraphQL errors: " + str(body["errors"])[:200])
                return {"info": "GraphQL OK", **body.get("data", {})}
            raise OLXError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        except httpx.HTTPError as exc:
            raise OLXError(str(exc)) from exc

    # Strategy 3 – Legacy form ────────────────────────────────────────────────

    async def _strategy_contact_form(
        self, offer_id: str, message: str, referer: str, info: dict
    ) -> dict[str, Any]:
        # Try to load the contact page to grab CSRF
        contact_url = f"{self.BASE}/d/kontakt/{offer_id}/"
        try:
            page = await self._fetch_page(contact_url)
            csrf = page.get("csrf") or info.get("csrf", "")
        except Exception:
            csrf = info.get("csrf", "")

        payload: dict[str, str] = {"message": message}
        if csrf:
            payload["csrfmiddlewaretoken"] = csrf

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": referer,
            "Origin": self.BASE,
        }
        try:
            resp = await self._http.post(contact_url, data=payload, headers=headers)
            logger.debug("Form → %s", resp.status_code)
            if resp.status_code in (200, 201, 302):
                return {"info": f"Form статус {resp.status_code}"}
            raise OLXError(f"Form HTTP {resp.status_code}: {resp.text[:200]}")
        except httpx.HTTPError as exc:
            raise OLXError(str(exc)) from exc

    # ── Auth check ────────────────────────────────────────────────────────────

    async def check_auth(self) -> Tuple[bool, str]:
        """
        Returns (is_authenticated, description).
        Checks the /api/v1/users/me/ endpoint; falls back to page scraping.
        """
        for path in ("/api/v1/users/me/", "/api/v2/users/me/"):
            try:
                headers = {"Accept": "application/json"}
                if self._bearer:
                    headers["Authorization"] = f"Bearer {self._bearer}"
                resp = await self._http.get(self.BASE + path, headers=headers)
                if resp.status_code == 200:
                    try:
                        data = resp.json()
                        name = (
                            data.get("name")
                            or data.get("email")
                            or data.get("login")
                            or "пользователь"
                        )
                        return True, f"Авторизован как: {name}"
                    except Exception:
                        return True, "Авторизован (данные не распознаны)"
                if resp.status_code == 401:
                    return False, "Куки недействительны или истекли (401 Unauthorized)"
            except httpx.HTTPError:
                pass

        # Fallback: check main page for logout link (sign of logged-in session)
        try:
            resp = await self._http.get(self.BASE)
            html = resp.text.lower()
            if any(kw in html for kw in ("wyloguj", "logout", "deconectare", "moj-cont", "contul-meu")):
                return True, "Авторизован (обнаружен профиль на странице)"
            return False, "Не авторизован (сессия не найдена)"
        except httpx.HTTPError as exc:
            return False, f"Ошибка подключения: {exc}"

    # ── Cleanup ───────────────────────────────────────────────────────────────

    async def close(self) -> None:
        await self._http.aclose()
