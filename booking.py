"""
SportyBet Nigeria Booking Code Generator
Submits a betslip to SportyBet's share/booking API to obtain a real code.
"""

import aiohttp
import logging

logger = logging.getLogger(__name__)

SPORTYBET_BOOKING_API = "https://www.sportybet.com/api/ng/orders/share"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json;charset=UTF-8",
    "Origin": "https://www.sportybet.com",
    "Referer": "https://www.sportybet.com/ng/",
}

# Market outcome ID mappings (SportyBet internal IDs)
MARKET_OUTCOME_MAP = {
    "1X2": {
        "1": {"marketId": "1", "specifier": "", "outcomeId": "1"},
        "X": {"marketId": "1", "specifier": "", "outcomeId": "2"},
        "2": {"marketId": "1", "specifier": "", "outcomeId": "3"},
    },
    "OU25": {
        "Over": {"marketId": "18", "specifier": "total=2.5", "outcomeId": "12"},
        "Under": {"marketId": "18", "specifier": "total=2.5", "outcomeId": "13"},
    },
    "BTTS": {
        "Yes": {"marketId": "10", "specifier": "", "outcomeId": "74"},
        "No": {"marketId": "10", "specifier": "", "outcomeId": "76"},
    }
}


class BookingCodeGenerator:
    async def generate(self, selections: list) -> dict:
        """
        Generate a SportyBet booking code from a list of selections.

        Each selection dict should have:
          - event_id: str
          - market: str  (1X2 / OU25 / BTTS)
          - pick: str    (1/X/2 / Over/Under / Yes/No)
          - odd: float
        """
        try:
            bets = self._build_bets(selections)
            if not bets:
                return {"success": False, "error": "Could not build bet payload"}

            payload = {"bets": bets, "source": "H5", "oddsType": 1}

            async with aiohttp.ClientSession(headers=HEADERS) as session:
                async with session.post(
                    SPORTYBET_BOOKING_API,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    body = await resp.json(content_type=None)
                    return self._parse_response(body)

        except aiohttp.ClientError as e:
            logger.error(f"Network error: {e}")
            return {"success": False, "error": f"Network error: {str(e)}"}
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            return {"success": False, "error": str(e)}

    def _build_bets(self, selections):
        """Build SportyBet bet payload from selections."""
        bets = []
        for sel in selections:
            market = sel.get('market')
            pick = sel.get('pick')
            event_id = sel.get('event_id')
            odd = sel.get('odd', 1.0)
            # odd may be a float or a normalized string like "2.35" or "?" from scraper
            try:
                odd_float = float(odd)
                if odd_float <= 1.0:
                    odd_float = 1.0  # guard against bad values
            except (TypeError, ValueError):
                logger.warning(f"Invalid odd value '{odd}' for {event_id}, defaulting to 1.0")
                odd_float = 1.0

            mapping = MARKET_OUTCOME_MAP.get(market, {}).get(pick)
            if not mapping:
                logger.warning(f"Unknown market/pick: {market}/{pick}")
                continue

            bet = {
                "eventId": event_id,
                "marketId": mapping['marketId'],
                "outcomeId": mapping['outcomeId'],
                "specifier": mapping['specifier'],
                "odds": int(odd_float * 100),  # SportyBet uses integer odds * 100
            }
            bets.append(bet)

        return bets

    def _parse_response(self, body):
        """Parse SportyBet API response to extract booking code."""
        # SportyBet returns: {"bizCode": 0, "message": "success", "data": {"shareCode": "XXXXXX"}}
        biz_code = body.get('bizCode', -1)

        if biz_code == 0:
            data = body.get('data', {})
            code = data.get('shareCode') or data.get('bookingCode') or data.get('code')
            if code:
                return {"success": True, "code": str(code).upper()}
            else:
                return {"success": False, "error": "Code not found in response"}

        else:
            msg = body.get('message', 'SportyBet returned an error')
            error_map = {
                1001: "Session expired - try again",
                1002: "Odds have changed - please refresh",
                1003: "Event no longer available",
                1010: "Bet amount too low",
                2001: "Service temporarily unavailable",
            }
            friendly = error_map.get(biz_code, msg)
            return {"success": False, "error": f"[{biz_code}] {friendly}"}
