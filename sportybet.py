import requests
import json
import time
import os
from datetime import datetime

# ─────────────────────────────────────────────
#  CONFIGURATION
# ─────────────────────────────────────────────

COUNTRY        = os.getenv('SPORTYBET_COUNTRY', 'ng')
SPORT_ID       = 'sr:sport:1'
MAX_SELECTIONS = int(os.getenv('MAX_SELECTIONS', '5'))
MIN_ODDS       = float(os.getenv('MIN_ODDS', '1.30'))
MAX_ODDS       = float(os.getenv('MAX_ODDS', '5.00'))
MARKET_NAME    = '1X2'
SELECTION_MODE = os.getenv('SELECTION_MODE', 'favourite')

# Webshare proxy config — fill in after signing up at webshare.io
PROXY_USER     = os.getenv('PROXY_USER', '')
PROXY_PASS     = os.getenv('PROXY_PASS', '')
PROXY_HOST     = os.getenv('PROXY_HOST', 'p.webshare.io')
PROXY_PORT     = os.getenv('PROXY_PORT', '80')

BASE_URL = 'https://www.sportybet.com/api/' + COUNTRY

HEADERS = {
    'Content-Type': 'application/json',
    'Accept': 'application/json, text/plain, */*',
    'Origin': 'https://www.sportybet.com',
    'Referer': 'https://www.sportybet.com/' + COUNTRY + '/',
    'User-Agent': 'Mozilla/5.0 (Linux; Android 11; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36',
}


def get_proxies():
    if PROXY_USER and PROXY_PASS:
        proxy_url = 'http://' + PROXY_USER + ':' + PROXY_PASS + '@' + PROXY_HOST + ':' + PROXY_PORT
        return {'http': proxy_url, 'https': proxy_url}
    return None


def fetch_json(url, method='GET', payload=None, retries=3):
    proxies = get_proxies()
    for attempt in range(1, retries + 1):
        try:
            if method == 'POST':
                r = requests.post(url, headers=HEADERS, json=payload, proxies=proxies, timeout=15)
            else:
                r = requests.get(url, headers=HEADERS, proxies=proxies, timeout=15)
            r.raise_for_status()
            return r.json()
        except requests.exceptions.HTTPError as e:
            print('HTTP Error [' + str(attempt) + '/' + str(retries) + ']: ' + str(e))
            if r.status_code == 403:
                print('403 Forbidden — SportyBet is blocking this IP. Add proxy credentials in .env')
                break
        except requests.exceptions.ConnectionError:
            print('Connection error [' + str(attempt) + '/' + str(retries) + ']. Retrying...')
        except requests.exceptions.Timeout:
            print('Timeout [' + str(attempt) + '/' + str(retries) + ']. Retrying...')
        except Exception as e:
            print('Unexpected error: ' + str(e))
            break
        time.sleep(2 ** attempt)
    return None


def fetch_events(country=None):
    base = 'https://www.sportybet.com/api/' + (country or COUNTRY)
    endpoints = [
        base + '/factsCenter/sports/' + SPORT_ID + '/highlights?pageSize=50&pageNum=1',
        base + '/factsCenter/sports/' + SPORT_ID + '/tournaments?pageSize=20&pageNum=1',
    ]
    for url in endpoints:
        data = fetch_json(url)
        if data:
            events = (
                data.get('data', {}).get('events') or
                data.get('bizData', {}).get('events') or
                data.get('data', []) or
                []
            )
            if events:
                return events
    return []


def extract_selections(events, max_sel=None, min_odds=None, max_odds=None, mode=None):
    max_sel   = max_sel  or MAX_SELECTIONS
    min_odds  = min_odds or MIN_ODDS
    max_odds  = max_odds or MAX_ODDS
    mode      = mode     or SELECTION_MODE
    selections = []

    for event in events:
        if len(selections) >= max_sel:
            break

        event_id = event.get('eventId') or event.get('id')
        if not event_id:
            continue

        markets = event.get('markets') or event.get('betMarkets') or []

        for market in markets:
            market_name = market.get('name', '')
            if MARKET_NAME.upper() not in market_name.upper():
                continue

            market_id = market.get('id') or market.get('marketId')
            outcomes  = market.get('outcomes') or market.get('selections') or []

            valid = [
                o for o in outcomes
                if min_odds <= float(o.get('odds', 0) or o.get('price', 0)) <= max_odds
            ]

            if not valid:
                continue

            if mode == 'favourite':
                chosen = min(valid, key=lambda o: float(o.get('odds', 0) or o.get('price', 0)))
            elif mode == 'home':
                chosen = next((o for o in valid if o.get('name', '').lower() in ['1', 'home']), valid[0])
            else:
                import random
                chosen = random.choice(valid)

            outcome_id   = chosen.get('id') or chosen.get('outcomeId')
            outcome_name = chosen.get('name', '?')
            odds_val     = float(chosen.get('odds', 0) or chosen.get('price', 0))
            home_team    = event.get('homeTeamName') or event.get('home', {}).get('name', 'Home')
            away_team    = event.get('awayTeamName') or event.get('away', {}).get('name', 'Away')

            selections.append({
                'eventId':    event_id,
                'marketId':   market_id,
                'outcomeId':  str(outcome_id),
                'specifiers': '',
                'odds':       odds_val,
                'match':      str(home_team) + ' vs ' + str(away_team),
                'pick':       outcome_name,
            })
            break

    return selections


def generate_code(selections, country=None):
    if not selections:
        return None

    country = country or COUNTRY
    base    = 'https://www.sportybet.com/api/' + country

    api_selections = [
        {
            'eventId':    s['eventId'],
            'marketId':   s['marketId'],
            'outcomeId':  s['outcomeId'],
            'specifiers': s['specifiers'],
            'odds':       s['odds'],
        }
        for s in selections
    ]

    payload   = {'selections': api_selections}
    endpoints = [
        base + '/orders/share',
        base + '/betslip/share',
        base + '/slip/share',
    ]

    for url in endpoints:
        data = fetch_json(url, method='POST', payload=payload)
        if data:
            share_code = (
                data.get('shareCode') or
                data.get('data', {}).get('shareCode') or
                data.get('bizData', {}).get('shareCode') or
                data.get('code')
            )
            if share_code:
                return {
                    'shareCode': share_code,
                    'deepLink':  'https://www.sportybet.com/' + country + '/?shareCode=' + share_code,
                }

    return None


def load_code(share_code, country=None):
    country = country or COUNTRY
    base    = 'https://www.sportybet.com/api/' + country
    endpoints = [
        base + '/orders/share/' + share_code,
        base + '/betslip/share/' + share_code,
    ]
    for url in endpoints:
        data = fetch_json(url)
        if data:
            return data
    return None


def run_full_generation(country=None, max_sel=None, min_odds=None, max_odds=None, mode=None):
    country = country or COUNTRY
    print('[' + datetime.now().strftime('%H:%M:%S') + '] Starting generation for ' + country.upper())

    events = fetch_events(country)
    if not events:
        return {'error': 'Could not fetch events. SportyBet may be blocking this IP. Add proxy credentials.'}

    selections = extract_selections(events, max_sel, min_odds, max_odds, mode)
    if not selections:
        return {'error': 'No valid selections found with current odds filters.'}

    result = generate_code(selections, country)
    if not result:
        return {'error': 'Could not generate booking code. API endpoint may have changed.'}

    return {
        'success':     True,
        'shareCode':   result['shareCode'],
        'deepLink':    result['deepLink'],
        'country':     country.upper(),
        'selections':  selections,
        'generatedAt': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }
