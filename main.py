from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional
from pydantic import BaseModel, ConfigDict


import os
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from sportybet import run_full_generation, load_code

# ─────────────────────────────────────────────
#  App Setup
# ─────────────────────────────────────────────

app = FastAPI(
    title='SportyBet Code Generator API',
    description='Auto-generates SportyBet booking codes daily',
    version='1.0.0'
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

CODES_DIR  = './generated_codes'
DAILY_TIME = os.getenv('DAILY_RUN_TIME', '07:00')
os.makedirs(CODES_DIR, exist_ok=True)


# ─────────────────────────────────────────────
#  Models
# ─────────────────────────────────────────────

class GenerateRequest(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    country: Optional[str] = 'ng'
    max_selections: Optional[int] = 5
    min_odds: Optional[float] = 1.30
    max_odds: Optional[float] = 5.00
    mode: Optional[str] = 'favourite'




# ─────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────

def save_code(result: dict):
    today    = datetime.now().strftime('%Y-%m-%d')
    country  = result.get('country', 'NG').lower()
    filename = os.path.join(CODES_DIR, today + '_' + country + '.json')
    with open(filename, 'w') as f:
        json.dump(result, f, indent=2)
    return filename


def load_saved_codes(limit=10):
    files = sorted(
        [f for f in os.listdir(CODES_DIR) if f.endswith('.json')],
        reverse=True
    )[:limit]

    codes = []
    for fname in files:
        with open(os.path.join(CODES_DIR, fname)) as f:
            try:
                codes.append(json.load(f))
            except Exception:
                pass
    return codes


def daily_job():
    print('[Scheduler] Running daily generation at ' + datetime.now().strftime('%H:%M:%S'))
    countries = os.getenv('AUTO_COUNTRIES', 'ng').split(',')
    for country in countries:
        result = run_full_generation(country=country.strip())
        if result.get('success'):
            save_code(result)
            print('[Scheduler] Generated code for ' + country.upper() + ': ' + result['shareCode'])
        else:
            print('[Scheduler] Failed for ' + country.upper() + ': ' + result.get('error', 'Unknown error'))


# ─────────────────────────────────────────────
#  Scheduler — runs daily automatically
# ─────────────────────────────────────────────

scheduler = BackgroundScheduler()
hour, minute = DAILY_TIME.split(':')
scheduler.add_job(daily_job, 'cron', hour=int(hour), minute=int(minute))
scheduler.start()
print('[Scheduler] Started — daily generation at ' + DAILY_TIME)


# ─────────────────────────────────────────────
#  API Routes
# ─────────────────────────────────────────────

@app.get('/')
def root():
    return {
        'status':  'running',
        'message': 'SportyBet Code Generator API',
        'docs':    '/docs',
        'endpoints': [
            'POST /api/generate  - Generate a new booking code',
            'GET  /api/codes     - Get previously generated codes',
            'GET  /api/load/{code} - Decode an existing booking code',
            'GET  /api/status    - Check API status',
        ]
    }


@app.get('/api/status')
def status():
    return {
        'status':     'online',
        'time':       datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'scheduler':  'running at ' + DAILY_TIME + ' daily',
        'codes_saved': len([f for f in os.listdir(CODES_DIR) if f.endswith('.json')]),
    }


@app.post('/api/generate')
def generate(req: GenerateRequest):
    result = run_full_generation(
        country   = req.country,
        max_sel   = req.max_selections,
        min_odds  = req.min_odds,
        max_odds  = req.max_odds,
        mode      = req.mode,
    )

    if not result.get('success'):
        raise HTTPException(status_code=500, detail=result.get('error', 'Generation failed'))

    save_code(result)
    return result


@app.get('/api/codes')
def get_codes(limit: int = Query(default=10, le=50)):
    codes = load_saved_codes(limit)
    return {'count': len(codes), 'codes': codes}


@app.get('/api/codes/today')
def get_today(country: str = Query(default='ng')):
    today    = datetime.now().strftime('%Y-%m-%d')
    filename = os.path.join(CODES_DIR, today + '_' + country.lower() + '.json')
    if os.path.exists(filename):
        with open(filename) as f:
            return json.load(f)
    raise HTTPException(status_code=404, detail='No code generated yet for today. Try POST /api/generate')


@app.get('/api/load/{share_code}')
def decode_code(share_code: str, country: str = Query(default='ng')):
    data = load_code(share_code, country)
    if not data:
        raise HTTPException(status_code=404, detail='Could not load this booking code.')
    return data


@app.post('/api/trigger-daily')
def trigger_daily():
    daily_job()
    return {'message': 'Daily generation triggered manually.'}
