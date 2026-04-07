import json
import logging
from datetime import date

import aiohttp
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

IVASMS_BASE_URL = "https://www.ivasms.com"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/117.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

XHR_HEADERS = {
    "Accept": "text/html, */*; q=0.01",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": IVASMS_BASE_URL,
    "Referer": f"{IVASMS_BASE_URL}/portal/sms/received",
}


def parse_cookies(cookies_raw: str) -> dict:
    """Parse cookies from multiple formats:
    - JSON dict: {"laravel_session": "abc", ...}
    - JSON list: [{"name": "key", "value": "val"}, ...]
    - Cookie string: "key=value; key2=value2"
    """
    if not cookies_raw or not cookies_raw.strip():
        return {}

    stripped = cookies_raw.strip()

    # Try JSON format
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            data = json.loads(stripped)
            if isinstance(data, dict):
                return {k: v for k, v in data.items() if k and v}
            elif isinstance(data, list):
                result = {}
                for item in data:
                    if isinstance(item, dict) and "name" in item and "value" in item:
                        if item["name"]:
                            result[item["name"]] = item["value"]
                return result
        except Exception:
            pass

    # Try cookie string format: "key=value; key2=value2"
    result = {}
    for part in stripped.split(";"):
        part = part.strip()
        if "=" in part:
            key, _, value = part.partition("=")
            key = key.strip()
            value = value.strip()
            if key:
                result[key] = value
    if result:
        return result

    logger.warning(f"Gagal parse cookies — format tidak dikenal (len={len(cookies_raw)})")
    return {}


class IVASMSClient:
    def __init__(self, cookies_raw: str):
        self.cookies = parse_cookies(cookies_raw)
        self.csrf_token: str | None = None
        self.session: aiohttp.ClientSession | None = None

    async def open(self):
        if self.session and not self.session.closed:
            return
        self.session = aiohttp.ClientSession(headers=DEFAULT_HEADERS)

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()
        self.session = None
        self.csrf_token = None

    async def __aenter__(self):
        await self.open()
        return self

    async def __aexit__(self, *args):
        await self.close()

    def _apply_cookies(self):
        for name, value in self.cookies.items():
            self.session.cookie_jar.update_cookies({name: value})

    def get_updated_cookies_str(self) -> str:
        """Serialize current session cookies to JSON string.
        Merges original cookies with any updated ones from server responses.
        """
        if not self.session:
            return json.dumps(self.cookies) if self.cookies else ""
        merged = dict(self.cookies)
        for cookie in self.session.cookie_jar:
            if cookie.key and cookie.value:
                merged[cookie.key] = cookie.value
        return json.dumps(merged) if merged else ""

    async def login(self) -> bool:
        self._apply_cookies()
        try:
            async with self.session.get(
                f"{IVASMS_BASE_URL}/portal/sms/received",
                allow_redirects=True,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status != 200:
                    logger.error(f"Login check failed: HTTP {resp.status}")
                    return False
                html = await resp.text()
                soup = BeautifulSoup(html, "lxml")
                csrf_input = soup.find("input", {"name": "_token"})
                if csrf_input:
                    self.csrf_token = csrf_input.get("value")
                    logger.info("Login OK — CSRF token acquired")
                    return True
                logger.error("CSRF token not found — cookies mungkin expired")
                return False
        except Exception as e:
            logger.error(f"Login error: {e}")
            return False

    async def _get_ranges(self, from_date: str, to_date: str) -> list[dict]:
        payload = {
            "from": from_date,
            "to": to_date,
            "_token": self.csrf_token,
        }
        try:
            async with self.session.post(
                f"{IVASMS_BASE_URL}/portal/sms/received/getsms",
                data=payload,
                headers=XHR_HEADERS,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status != 200:
                    logger.error(f"getsms failed: HTTP {resp.status}")
                    return []
                html = await resp.text()
                soup = BeautifulSoup(html, "lxml")
                ranges = []
                for item in soup.select("div.item"):
                    col = item.select_one(".col-sm-4")
                    if col:
                        onclick = col.get("onclick", "")
                        parts = onclick.split("'")
                        if len(parts) > 1:
                            range_val = parts[1]
                        else:
                            range_val = col.text.strip()
                        if range_val:
                            ranges.append({"range": range_val})
                return ranges
        except Exception as e:
            logger.error(f"Error getting ranges: {e}")
            return []

    async def _get_numbers_for_range(
        self, phone_range: str, from_date: str, to_date: str
    ) -> list[dict]:
        payload = {
            "_token": self.csrf_token,
            "start": from_date,
            "end": to_date,
            "range": phone_range,
        }
        try:
            async with self.session.post(
                f"{IVASMS_BASE_URL}/portal/sms/received/getsms/number",
                data=payload,
                headers=XHR_HEADERS,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status != 200:
                    return []
                html = await resp.text()
                soup = BeautifulSoup(html, "lxml")
                numbers = []
                for card in soup.select("div.card.card-body"):
                    col = card.select_one(".col-sm-4")
                    if col:
                        phone_number = col.text.strip()
                        if phone_number:
                            numbers.append({
                                "phone_number": phone_number,
                                "range": phone_range,
                            })
                return numbers
        except Exception as e:
            logger.error(f"Error getting numbers for range {phone_range}: {e}")
            return []

    async def _get_otp_message(
        self, phone_number: str, phone_range: str, from_date: str, to_date: str
    ) -> str | None:
        payload = {
            "_token": self.csrf_token,
            "start": from_date,
            "end": to_date,
            "Number": phone_number,
            "Range": phone_range,
        }
        try:
            async with self.session.post(
                f"{IVASMS_BASE_URL}/portal/sms/received/getsms/number/sms",
                data=payload,
                headers=XHR_HEADERS,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status != 200:
                    return None
                html = await resp.text()
                soup = BeautifulSoup(html, "lxml")
                msg_el = soup.select_one(".col-9.col-sm-6 p")
                if msg_el:
                    return msg_el.text.strip()
                return None
        except Exception as e:
            logger.error(f"Error getting OTP for {phone_number}: {e}")
            return None

    async def get_all_otp_messages(
        self, from_date: str = "", to_date: str = ""
    ) -> list[dict]:
        if not from_date:
            from_date = date.today().strftime("%d/%m/%Y")
        if not to_date:
            to_date = from_date

        if not self.csrf_token:
            logger.error("No CSRF token — call login() first")
            return []

        ranges = await self._get_ranges(from_date, to_date)
        logger.info(f"Found {len(ranges)} active ranges")

        all_messages = []
        for r in ranges:
            phone_range = r["range"]
            numbers = await self._get_numbers_for_range(phone_range, from_date, to_date)
            for num in numbers:
                phone_number = num["phone_number"]
                otp_text = await self._get_otp_message(
                    phone_number, phone_range, from_date, to_date
                )
                if otp_text:
                    all_messages.append({
                        "phone_number": phone_number,
                        "range": phone_range,
                        "otp_message": otp_text,
                    })

        return all_messages
