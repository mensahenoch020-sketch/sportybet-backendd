"""
SportyBet Nigeria Scraper — Production Grade
- curl_cffi for Cloudflare/TLS fingerprint bypass
- In-memory cache with TTL (5 min)
- Structured error types, no silent failures
- Verified endpoint priority with fallback chain
- Rotating User-Agents
- Robust odds parsing with multi-key matching
"""

import asyncio
import logging
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────

SPORTYBET_BASE = "https://www.sportybet.com/api/ng"

# Rotating User-Agents — reduces fingerprinting risk
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
]

# Endpoints in priority order — first one that works wins
ENDPOINTS = [
    {
        "url": f"{SPORTYBET_BASE}/sport/football/highlights",
        "params": {"sportId": "sr:sport:1", "marketId": "1,18,10", "pageSize": "100"},
        "parser": "highlights",
    },
    {
        "url": f"{SPORTYBET_BASE}/sport/football/events/schedule/today",
        "params": {"marketId": "1,18,10"},
        "parser": "schedule",
    },
    {
        "url": f"{SPORTYBET_BASE}/factsCenter/football",
        "params": {"sportId": "1", "marketId": "1,18,10", "page": "1", "pageSize": "100"},
        "parser": "highlights",
    },
]

# How long to cache match data (seconds)
CACHE_TTL = 300  # 5 minutes

# Possible field names SportyBet uses for the same data (API versions vary)
FIELD_MAP = {
    "event_id":      ["eventId", "id", "gameId", "matchId"],
    "home":          ["homeTeamName", "homeName", "home_name"],
    "away":          ["awayTeamName", "awayName", "away_name"],
    "home_obj":      ["home"],
    "away_obj":      ["away"],
    "start":         ["estimateStartTime", "startTime", "matchTime", "kickOffTime"],
    "league":        ["tournamentName", "leagueName", "competitionName"],
    "league_obj":    ["league", "tournament", "competition"],
    "markets":       ["markets", "oddsMap", "marketList", "odds"],
    "market_id":     ["id", "marketId", "typeId"],
    "outcomes":      ["outcomes", "odds", "selections", "outcomeList"],
    "odd_val":       ["odds", "value", "oddValue", "price"],
    "outcome_desc":  ["desc", "name", "description", "outcomeName"],
}

# Outcome label aliases — covers multiple API wordings
OUTCOME_ALIASES = {
    "1x2": {
        "home": ["1", "w1", "home", "home win", "1 (home)"],
        "draw": ["x", "draw", "tie", "x (draw)"],
        "away": ["2", "w2", "away", "away win", "2 (away)"],
    },
    "ou25": {
        "over":  ["over", "over 2.5", "o2.5", ">2.5"],
        "under": ["under", "under 2.5", "u2.5", "<2.5"],
    },
    "btts": {
        "yes": ["yes", "gg", "both score", "both teams score"],
        "no":  ["no", "ng", "not both", "both teams don't score"],
    },
}

MARKET_IDS = {
    "1x2":  {"1", "sr:market:1"},
    "ou25": {"18", "sr:market:18", "sr:market:18:total=2.5"},
    "btts": {"10", "sr:market:10"},
}


# ─── Structured Error ─────────────────────────────────────────────────────────

@dataclass
class ScraperError(Exception):
    message: str
    cause: Optional[Exception] = None
    endpoint: Optional[str] = None

    def __str__(self):
        parts = [self.message]
        if self.endpoint:
            parts.append(f"endpoint={self.endpoint}")
        if self.cause:
            parts.append(f"cause={self.cause}")
        return " | ".join(parts)


# ─── In-memory Cache ──────────────────────────────────────────────────────────

class _Cache:
    def __init__(self):
        self._data: list = []
        self._ts: float = 0.0

    def get(self) -> Optional[list]:
        if self._data and (time.monotonic() - self._ts) < CACHE_TTL:
            age = int(time.monotonic() - self._ts)
            logger.info(f"Cache hit — {len(self._data)} matches, age={age}s")
            return self._data
        return None

    def set(self, matches: list):
        self._data = matches
        self._ts = time.monotonic()
        logger.info(f"Cache updated — {len(matches)} matches stored")

    def invalidate(self):
        self._data = []
        self._ts = 0.0


_match_cache = _Cache()


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _pick(d: dict, keys: list, default=None):
    """Try multiple keys on a dict, return first non-empty hit."""
    for k in keys:
        v = d.get(k)
        if v is not None and v != "" and v != []:
            return v
    return default


def _truncate(s: str, n: int = 28) -> str:
    """Truncate with ellipsis rather than hard cut."""
    return s if len(s) <= n else s[:n - 1] + "…"


def _match_alias(value: str, aliases: list) -> bool:
    return value.strip().lower() in aliases


def _build_headers() -> dict:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Origin": "https://www.sportybet.com",
        "Referer": "https://www.sportybet.com/ng/",
        "sec-ch-ua": '"Chromium";v="122", "Not(A:Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }


# ─── Main Scraper ─────────────────────────────────────────────────────────────

class SportyBetScraper:
    """
    Fetches today's SportyBet Nigeria football matches.

    Improvements over v1:
    - curl_cffi Cloudflare bypass (falls back to aiohttp gracefully)
    - Shared 5-minute cache — concurrent users don't hammer the API
    - ScraperError with context instead of silent empty lists
    - 3-endpoint fallback chain
    - Rotating User-Agents
    - Multi-alias odds parsing — handles API response variation
    - Normalized odds as 2-decimal strings
    - Truncation with ellipsis instead of hard cut
    """

    async def get_today_matches(self, force_refresh: bool = False) -> list:
        """
        Returns list of match dicts. Raises ScraperError if all endpoints fail.
        Pass force_refresh=True to bypass cache (e.g. admin command).
        """
        if not force_refresh:
            cached = _match_cache.get()
            if cached is not None:
                return cached

        last_error = None
        for endpoint in ENDPOINTS:
            try:
                logger.info(f"Trying: {endpoint['url']}")
                raw = await self._fetch(endpoint["url"], endpoint["params"])
                matches = self._parse(raw, endpoint["parser"])

                if matches:
                    _match_cache.set(matches)
                    return matches

                logger.warning(f"0 matches from {endpoint['url']} — trying next")

            except ScraperError as e:
                logger.warning(f"Endpoint failed: {e}")
                last_error = e
            except Exception as e:
                last_error = ScraperError("Unexpected error", cause=e, endpoint=endpoint["url"])
                logger.warning(str(last_error))

        raise last_error or ScraperError("All endpoints exhausted — no matches returned")

    # ── HTTP layer ────────────────────────────────────────────────────────────

    async def _fetch(self, url: str, params: dict) -> dict:
        """Try curl_cffi first (Cloudflare-safe), fall back to aiohttp."""
        p = dict(params)
        p["_t"] = int(time.time() * 1000)

        try:
            return await self._fetch_curl(url, p)
        except ImportError:
            logger.debug("curl_cffi not installed — using aiohttp")
        except ScraperError:
            raise
        except Exception as e:
            logger.debug(f"curl_cffi attempt failed ({e}) — falling back to aiohttp")

        return await self._fetch_aiohttp(url, p)

    async def _fetch_curl(self, url: str, params: dict) -> dict:
        from curl_cffi.requests import AsyncSession
        async with AsyncSession(impersonate="chrome120") as s:
            resp = await s.get(url, params=params, timeout=15)
            return self._validate(resp.status_code, resp.text, url)

    async def _fetch_aiohttp(self, url: str, params: dict) -> dict:
        import aiohttp
        conn = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(headers=_build_headers(), connector=conn) as s:
            async with s.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                text = await resp.text()
                return self._validate(resp.status, text, url)

    def _validate(self, status: int, text: str, url: str) -> dict:
        """Raise a descriptive ScraperError on any bad response."""
        if status == 403:
            raise ScraperError("Blocked (403) — install curl_cffi for Cloudflare bypass", endpoint=url)
        if status == 429:
            raise ScraperError("Rate limited (429) — too many requests", endpoint=url)
        if status == 404:
            raise ScraperError(f"Endpoint not found (404)", endpoint=url)
        if status != 200:
            raise ScraperError(f"HTTP {status}", endpoint=url)
        if text.strip().startswith("<!") or "<html" in text[:300].lower():
            raise ScraperError("Got HTML page — likely Cloudflare challenge", endpoint=url)

        import json
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ScraperError("Invalid JSON", cause=e, endpoint=url)

        biz = data.get("bizCode", data.get("code"))
        if biz and biz != 0:
            msg = data.get("message", "API error")
            raise ScraperError(f"bizCode={biz}: {msg}", endpoint=url)

        return data

    # ── Parsers ───────────────────────────────────────────────────────────────

    def _parse(self, data: dict, parser_type: str) -> list:
        if parser_type == "highlights":
            return self._parse_highlights(data)
        if parser_type == "schedule":
            return self._parse_schedule(data)
        return []

    def _parse_highlights(self, data: dict) -> list:
        payload = data.get("data", data)
        events = payload.get("events", payload) if isinstance(payload, dict) else payload
        if not isinstance(events, list):
            logger.warning(f"Expected list of events, got {type(events).__name__}")
            return []
        return [m for m in (self._extract_match(e) for e in events) if m]

    def _parse_schedule(self, data: dict) -> list:
        payload = data.get("data", {})
        matches = []
        for t in payload.get("tournaments", []):
            league = _pick(t, ["name", "tournamentName", "leagueName"], "Football")
            for event in t.get("events", []):
                m = self._extract_match(event, league=league)
                if m:
                    matches.append(m)
        return matches

    def _extract_match(self, event: dict, league: str = None) -> Optional[dict]:
        event_id = _pick(event, FIELD_MAP["event_id"])
        if not event_id:
            return None

        home = (
            _pick(event, FIELD_MAP["home"])
            or (_pick(event, FIELD_MAP["home_obj"], {}) or {}).get("name")
            or "Home"
        )
        away = (
            _pick(event, FIELD_MAP["away"])
            or (_pick(event, FIELD_MAP["away_obj"], {}) or {}).get("name")
            or "Away"
        )

        start_ms = _pick(event, FIELD_MAP["start"], 0)
        try:
            dt = datetime.fromtimestamp(int(start_ms) / 1000, tz=timezone.utc)
            time_str = dt.strftime("%H:%M UTC")
        except (ValueError, TypeError, OSError):
            time_str = "TBD"

        league_name = (
            league
            or _pick(event, FIELD_MAP["league"])
            or (_pick(event, FIELD_MAP["league_obj"], {}) or {}).get("name")
            or "Football"
        )

        odds_1x2, odds_ou, odds_btts = self._extract_odds(event)

        return {
            "event_id":  str(event_id),
            "home":      _truncate(str(home)),
            "away":      _truncate(str(away)),
            "time":      time_str,
            "league":    _truncate(str(league_name), 32),
            "odds_1x2":  odds_1x2 or None,
            "odds_ou":   odds_ou or None,
            "odds_btts": odds_btts or None,
        }

    def _extract_odds(self, event: dict) -> tuple:
        odds_1x2, odds_ou, odds_btts = {}, {}, {}
        markets = _pick(event, FIELD_MAP["markets"], [])
        if not isinstance(markets, list):
            return odds_1x2, odds_ou, odds_btts

        for market in markets:
            raw_id = str(_pick(market, FIELD_MAP["market_id"], "") or "").lower()
            outcomes = _pick(market, FIELD_MAP["outcomes"], [])
            if not isinstance(outcomes, list):
                continue

            if raw_id in MARKET_IDS["1x2"]:
                self._fill_outcomes(outcomes, odds_1x2, OUTCOME_ALIASES["1x2"])
            elif raw_id in MARKET_IDS["ou25"]:
                self._fill_outcomes(outcomes, odds_ou, OUTCOME_ALIASES["ou25"])
            elif raw_id in MARKET_IDS["btts"]:
                self._fill_outcomes(outcomes, odds_btts, OUTCOME_ALIASES["btts"])

        return odds_1x2, odds_ou, odds_btts

    def _fill_outcomes(self, outcomes: list, target: dict, aliases: dict):
        """Generic outcome filler — works for any market."""
        for o in outcomes:
            desc = str(_pick(o, FIELD_MAP["outcome_desc"], "")).strip().lower()
            val = self._normalize_odd(_pick(o, FIELD_MAP["odd_val"]))
            for key, alias_list in aliases.items():
                if key not in target and _match_alias(desc, alias_list):
                    target[key] = val
                    break

    def _normalize_odd(self, raw) -> str:
        """Normalize odds to a 2-decimal string."""
        try:
            return f"{float(raw):.2f}"
        except (TypeError, ValueError):
            return str(raw) if raw is not None else "?"
