# -*- coding: utf-8 -*-
"""
TAZHINI Bot Dashboard — v2
- Dark/Lite theme
- Admin login (admin/admin)
- Squad generator (calls https://gan-accs.up.railway.app/gen)
- Clan join requests (RequestJoinClan on every account)
"""

import asyncio
import json
import os
import time
import secrets
from typing import Dict, List, Any, Optional

import httpx
from aiohttp import web
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

TEMPLATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")

# ─── CONFIG ────────────────────────────────────────────────────────────────
ADMIN_USER = "admin"
ADMIN_PASS = "admin"

GEN_API_BASE = "https://gan-accs.up.railway.app"
GEN_API_GEN = f"{GEN_API_BASE}/gen"
GEN_API_JOB = f"{GEN_API_BASE}/job"      # /job/<job_id>

ACCOUNTS_FILE = "accounts.json"
CLAN_URL = "https://clientbp.ppmainecoonghj.com/RequestJoinClan"

AES_KEY = bytes([89,103,38,116,99,37,68,69,117,104,54,37,90,99,94,56])
AES_IV  = bytes([54,111,121,90,68,114,50,50,69,51,121,99,104,106,77,37])

# ─── EXP TABLE (same as before) ────────────────────────────────────────────
EXP_TABLE: Dict[int, int] = {
    1: 0, 2: 48, 3: 202, 4: 544, 5: 1012, 6: 1844, 7: 2792, 8: 3800,
    9: 4870, 10: 6004, 11: 7192, 12: 8448, 13: 9760, 14: 11140, 15: 12566,
    16: 14060, 17: 15610, 18: 17224, 19: 18902, 20: 20632, 21: 22424, 22: 24278,
    23: 26192, 24: 28166, 25: 30200, 26: 32294, 27: 34448, 28: 37804, 29: 41274,
    30: 44870, 31: 48582, 32: 53394, 33: 58566, 34: 64096, 35: 69994, 36: 76460,
    37: 83506, 38: 91128, 39: 99322, 40: 108092, 41: 120144, 42: 133266, 43: 147472,
    44: 162760, 45: 179126, 46: 196572, 47: 215368, 48: 235516, 49: 257010, 50: 279860,
    51: 304056, 52: 348318, 53: 394982, 54: 444044, 55: 495508, 56: 549364, 57: 633756,
    58: 721744, 59: 813336, 60: 908522, 61: 1041438, 62: 1180352, 63: 1325266,
    64: 1476184, 65: 1634300, 66: 1840946, 67: 2056594, 68: 2281242, 69: 2514880,
    70: 2757530, 71: 3059506, 72: 3372284, 73: 3699456, 74: 4041030, 75: 4397002,
    76: 4829104, 77: 5282204, 78: 5756304, 79: 6251408, 80: 6776502, 81: 7381324,
    82: 8043154, 83: 8752982, 84: 9510808, 85: 10316338, 86: 11277190, 87: 12291748,
    88: 13360304, 89: 14482858, 90: 15659418, 91: 17026708, 92: 18453950, 93: 19941280,
    94: 21488570, 95: 23095858, 96: 24763138, 97: 26490428, 98: 28378704, 99: 30124996,
    100: 32032884,
}

def calculate_level_progress(level: int, current_exp: int) -> Dict[str, Any]:
    level = max(1, level)
    next_level = min(100, level + 1)
    base_exp = EXP_TABLE.get(level, 0)
    target_exp = EXP_TABLE.get(next_level, base_exp + 50000)
    needed_for_level = max(1, target_exp - base_exp)
    earned_in_level = max(0, current_exp - base_exp)
    remaining_exp = max(0, target_exp - current_exp)
    progress_pct = min(100.0, max(0.0, (earned_in_level / needed_for_level) * 100.0))
    return {
        "next_level": next_level, "base_exp": base_exp, "target_exp": target_exp,
        "needed_for_level": needed_for_level, "earned_in_level": earned_in_level,
        "remaining_exp": remaining_exp, "progress_pct": round(progress_pct, 1),
    }

# ─── BOT STATE ────────────────────────────────────────────────────────────
class BotState:
    def __init__(self):
        self.accounts: Dict[str, Dict[str, Any]] = {}
        self.logs: List[Dict[str, Any]] = []
        self.max_logs = 200
        self.total_matches = 0
        self.total_matches_started = 0
        self.total_gained_exp = 0
        self.start_time = time.time()
        self.account_workers: Dict[str, asyncio.Task] = {}
        self.paused_accounts: set = set()
        self.refresh_callbacks: Dict[str, Any] = {}
        self.account_credentials: Dict[str, Dict[str, Any]] = {}
        self.active_writers: Dict[str, set] = {}
        self.sessions: Dict[str, float] = {}          # session_token -> expiry
        self.jobs: Dict[str, Dict[str, Any]] = {}     # gen jobs

    def log(self, message: str, level: str = "info", uid: Optional[str] = None):
        entry = {
            "time": time.strftime("%H:%M:%S"), "level": level,
            "message": message, "uid": str(uid) if uid else None,
        }
        self.logs.append(entry)
        if len(self.logs) > self.max_logs:
            self.logs.pop(0)

    def register_account(self, uid: str, nickname: str, region: str, level: int, exp: int,
                         likes: int = 0, token: Optional[str] = None):
        uid_str = str(uid)
        prog = calculate_level_progress(level or 1, exp)
        lvl_val = level or 1
        acc_mode = "BR" if lvl_val < 3 else "LONE_WOLF"
        if uid_str not in self.accounts:
            self.accounts[uid_str] = {
                "uid": uid_str, "nickname": nickname or f"Player_{uid_str[:6]}",
                "region": region or "ME", "level": lvl_val, "next_level": prog["next_level"],
                "mode": acc_mode, "initial_exp": exp, "current_exp": exp,
                "gained_exp": 0, "remaining_exp": prog["remaining_exp"],
                "progress_pct": prog["progress_pct"], "likes": likes or 0,
                "status": "ONLINE", "matches_played": 0, "active_matches": 0,
                "token": token or "", "start_time": time.time(),
                "is_paused": False, "total_pause_duration": 0.0,
                "last_updated": time.strftime("%H:%M:%S"),
            }
        else:
            acc = self.accounts[uid_str]
            acc["current_exp"] = exp
            acc["level"] = lvl_val
            acc["gained_exp"] = max(0, exp - acc["initial_exp"])
            acc.update({
                "next_level": prog["next_level"], "remaining_exp": prog["remaining_exp"],
                "progress_pct": prog["progress_pct"], "mode": acc_mode,
                "last_updated": time.strftime("%H:%M:%S"),
            })
        self.recalc_totals()

    def recalc_totals(self):
        self.total_gained_exp = sum(a.get("gained_exp", 0) for a in self.accounts.values())

    def increment_match(self, uid: str):
        uid_str = str(uid)
        self.total_matches += 1
        if uid_str in self.accounts:
            self.accounts[uid_str]["matches_played"] += 1
            self.accounts[uid_str]["last_updated"] = time.strftime("%H:%M:%S")

    def update_exp(self, uid: str, current_exp: int, level: Optional[int] = None):
        uid_str = str(uid)
        if uid_str in self.accounts:
            acc = self.accounts[uid_str]
            acc["current_exp"] = current_exp
            if level is not None and level > 0:
                acc["level"] = level
            acc["gained_exp"] = max(0, current_exp - acc["initial_exp"])
            prog = calculate_level_progress(acc["level"], current_exp)
            acc["next_level"] = prog["next_level"]
            acc["remaining_exp"] = prog["remaining_exp"]
            acc["progress_pct"] = prog["progress_pct"]
            self.recalc_totals()

    def update_status(self, uid: str, status: str, active_matches: Optional[int] = None):
        uid_str = str(uid)
        if uid_str in self.accounts:
            self.accounts[uid_str]["status"] = status
            if active_matches is not None:
                self.accounts[uid_str]["active_matches"] = active_matches
            self.accounts[uid_str]["last_updated"] = time.strftime("%H:%M:%S")

    def is_paused(self, uid: str) -> bool:
        return str(uid) in self.paused_accounts

    def toggle_pause(self, uid: str) -> bool:
        uid_str = str(uid)
        if uid_str in self.paused_accounts:
            self.paused_accounts.discard(uid_str)
            if uid_str in self.accounts:
                self.accounts[uid_str]["status"] = "ONLINE"
                self.accounts[uid_str]["is_paused"] = False
            return False
        self.paused_accounts.add(uid_str)
        if uid_str in self.accounts:
            self.accounts[uid_str]["status"] = "PAUSED"
            self.accounts[uid_str]["is_paused"] = True
        return True

    # ─── Sessions ───
    def new_session(self) -> str:
        tok = secrets.token_urlsafe(32)
        self.sessions[tok] = time.time() + 12 * 3600   # 12h
        return tok

    def check_session(self, tok: str) -> bool:
        exp = self.sessions.get(tok)
        if not exp:
            return False
        if time.time() > exp:
            self.sessions.pop(tok, None)
            return False
        return True

bot_state = BotState()

# ─── HELPERS ──────────────────────────────────────────────────────────────
def _encrypt_clan_payload(clan_id: int) -> bytes:
    # MyMessage { field_1 = clan_id }  => tag 0x08 + varint
    # Simple manual protobuf: field 1, varint
    n = int(clan_id)
    out = bytearray([0x08])
    while True:
        b = n & 0x7F; n >>= 7
        if n: b |= 0x80
        out.append(b)
        if not n: break
    cipher = AES.new(AES_KEY, AES.MODE_CBC, AES_IV)
    return cipher.encrypt(pad(bytes(out), AES.block_size))

# ─── AUTH MIDDLEWARE ──────────────────────────────────────────────────────
def _extract_token(request: web.Request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return request.headers.get("X-Session", "")

def require_auth(handler):
    async def wrapper(request: web.Request):
        tok = _extract_token(request)
        if not tok or not bot_state.check_session(tok):
            return web.json_response({"status": "error", "error": "unauthorized"}, status=401)
        return await handler(request)
    return wrapper

# ─── HANDLERS ─────────────────────────────────────────────────────────────
async def handle_index(request: web.Request) -> web.Response:
    content = "<h1>index.html missing</h1>"
    if os.path.exists(TEMPLATE_PATH):
        with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
            content = f.read()
    return web.Response(text=content, content_type="text/html", charset="utf-8")

async def handle_login(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"status": "error", "error": "bad json"}, status=400)
    u = str(data.get("username", "")).strip()
    p = str(data.get("password", "")).strip()
    if u == ADMIN_USER and p == ADMIN_PASS:
        tok = bot_state.new_session()
        bot_state.log("Admin logged in", "success")
        return web.json_response({"status": "ok", "token": tok})
    bot_state.log(f"Failed login attempt: {u}", "warning")
    return web.json_response({"status": "error", "error": "invalid credentials"}, status=401)

@require_auth
async def handle_get_stats(request: web.Request) -> web.Response:
    accounts_data = list(bot_state.accounts.values())
    accounts_data.sort(key=lambda x: x.get("gained_exp", 0), reverse=True)
    uptime_sec = max(1, int(time.time() - bot_state.start_time))
    total_gained = bot_state.total_gained_exp
    exp_per_hour = int((total_gained / uptime_sec) * 3600)
    total_active = sum(a.get("active_matches", 0) for a in accounts_data)
    for acc in accounts_data:
        acc["is_paused"] = bot_state.is_paused(acc.get("uid", ""))
        acc["uptime_seconds"] = max(0, int(time.time() - acc.get("start_time", time.time())))
    return web.json_response({
        "total_accounts": len(bot_state.accounts),
        "total_matches": bot_state.total_matches,
        "total_matches_started": bot_state.total_matches_started,
        "total_active_matches": total_active,
        "total_gained_exp": total_gained,
        "exp_per_hour": exp_per_hour,
        "accounts": accounts_data,
        "logs": bot_state.logs[-80:],
        "uptime": uptime_sec,
        "jobs": list(bot_state.jobs.values()),
    })

@require_auth
async def handle_gen_squad(request: web.Request) -> web.Response:
    """
    Body: { "name": "yazan", "region": "ME", "squads": 1 }
    - Requests squads*4 + 1 accounts from gan-accs.up.railway.app
    - Saves them into accounts.json (replacing old ones that came from the API)
    - Registers in bot_state so main bot can start them
    """
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"status": "error", "error": "bad json"}, status=400)

    name = str(data.get("name", "squad")).strip() or "squad"
    region = str(data.get("region", "ME")).upper().strip() or "ME"
    try:
        squads = int(data.get("squads", 1))
    except Exception:
        return web.json_response({"status": "error", "error": "squads must be int"}, status=400)
    if squads < 1 or squads > 10:
        return web.json_response({"status": "error", "error": "squads must be 1..10"}, status=400)

    needed = squads * 4
    request_count = needed + 1   # احتياطي

    job_id = secrets.token_hex(8)
    bot_state.jobs[job_id] = {
        "id": job_id, "kind": "gen_squad", "name": name, "region": region,
        "squads": squads, "needed": needed, "requested": request_count,
        "status": "running", "accounts": [], "error": None,
        "started_at": time.time(), "updated_at": time.time(),
    }
    bot_state.log(f"[GEN] Squad job {job_id}: {squads} squads ({needed} accs) region={region}", "info")

    # Run in background
    asyncio.create_task(_run_gen_job(job_id))
    return web.json_response({"status": "ok", "job_id": job_id})

async def _run_gen_job(job_id: str):
    job = bot_state.jobs.get(job_id)
    if not job:
        return
    try:
        async with httpx.AsyncClient(verify=False, timeout=30.0) as cli:
            # 1) start gen
            r = await cli.post(GEN_API_GEN, json={
                "name": job["name"], "region": job["region"], "count": job["requested"],
            })
            if r.status_code not in (200, 202):
                raise RuntimeError(f"gen API HTTP {r.status_code}: {r.text[:200]}")
            jd = r.json()
            api_job = jd.get("job_id")
            if not api_job:
                raise RuntimeError(f"gen API no job_id: {jd}")
            job["api_job"] = api_job
            job["updated_at"] = time.time()
            bot_state.log(f"[GEN] API job {api_job} started", "info")

            # 2) poll
            for _ in range(600):   # max 10 min
                await asyncio.sleep(2.0)
                jr = await cli.get(f"{GEN_API_JOB}/{api_job}")
                if jr.status_code != 200:
                    continue
                jdata = jr.json()
                results = jdata.get("results", []) or []
                job["accounts"] = results
                job["updated_at"] = time.time()
                status = jdata.get("status", "")
                if status in ("done", "stopped") or len(results) >= job["needed"]:
                    break
                if status == "running" and len(results) >= job["requested"]:
                    break

        # 3) pick needed accounts
        picked = job["accounts"][: job["needed"]]
        if len(picked) < job["needed"]:
            raise RuntimeError(f"only got {len(picked)}/{job['needed']} accounts")
        job["picked"] = picked

        # 4) register in bot_state + write to accounts.json
        saved = []
        for acc in picked:
            uid = str(acc.get("uid", "")).strip()
            pw = acc.get("password", "")
            acc_id = str(acc.get("account_id", "")).strip() or uid
            if not uid or not pw:
                continue
            bot_state.register_account(
                uid=acc_id, nickname=acc.get("name", f"Player_{acc_id}"),
                region=acc.get("region", job["region"]), level=1, exp=0,
            )
            saved.append({"uid": uid, "password": pw, "name": acc.get("name"),
                          "account_id": acc_id, "region": acc.get("region", job["region"])})

        # merge into accounts.json
        existing = []
        if os.path.exists(ACCOUNTS_FILE):
            try:
                with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
                    existing = json.load(f) or []
            except Exception:
                existing = []
        existing_by_uid = {str(a.get("uid", "")): a for a in existing if isinstance(a, dict)}
        for s in saved:
            existing_by_uid[s["uid"]] = s
        with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
            json.dump(list(existing_by_uid.values()), f, indent=2)

        job["status"] = "done"
        job["updated_at"] = time.time()
        bot_state.log(f"[GEN] Job {job_id} DONE: {len(saved)} accounts saved", "success")

        # 5) trigger bot workers for new accounts
        cb = bot_state.refresh_callbacks.get("on_account_added")
        if cb:
            for s in saved:
                try:
                    asyncio.create_task(cb({"uid": s["uid"], "password": s["password"]}))
                except Exception:
                    pass
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)
        job["updated_at"] = time.time()
        bot_state.log(f"[GEN] Job {job_id} ERROR: {e}", "error")

@require_auth
async def handle_job_status(request: web.Request) -> web.Response:
    job_id = request.match_info.get("job_id", "")
    job = bot_state.jobs.get(job_id)
    if not job:
        return web.json_response({"status": "error", "error": "job not found"}, status=404)
    return web.json_response(job)

@require_auth
async def handle_clan_requests(request: web.Request) -> web.Response:
    """
    Body: { "clan_id": 1234567 }
    Sends RequestJoinClan to every account in accounts.json (using uid/password → token).
    We use the API account credentials (uid/password) with the JWT endpoint
    or fall back to stored bot_state tokens.
    """
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"status": "error", "error": "bad json"}, status=400)
    clan_id = data.get("clan_id")
    if not clan_id:
        return web.json_response({"status": "error", "error": "clan_id required"}, status=400)
    try:
        clan_id_int = int(clan_id)
    except Exception:
        return web.json_response({"status": "error", "error": "clan_id must be int"}, status=400)

    job_id = secrets.token_hex(8)
    bot_state.jobs[job_id] = {
        "id": job_id, "kind": "clan", "clan_id": clan_id_int,
        "status": "running", "results": [], "error": None,
        "started_at": time.time(), "updated_at": time.time(),
    }
    bot_state.log(f"[CLAN] Sending join requests to clan {clan_id_int} for all accounts", "info")
    asyncio.create_task(_run_clan_job(job_id, clan_id_int))
    return web.json_response({"status": "ok", "job_id": job_id})

async def _run_clan_job(job_id: str, clan_id: int):
    job = bot_state.jobs.get(job_id)
    if not job:
        return
    try:
        # load accounts
        accounts = []
        if os.path.exists(ACCOUNTS_FILE):
            with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
                accounts = json.load(f) or []
        if not accounts:
            raise RuntimeError("no accounts in accounts.json")

        payload = _encrypt_clan_payload(clan_id)
        sem = asyncio.Semaphore(20)

        async def send_one(acc: Dict[str, Any]) -> Dict[str, Any]:
            async with sem:
                uid = str(acc.get("uid", ""))
                pw = str(acc.get("password", ""))
                if not uid or not pw:
                    return {"uid": uid, "ok": False, "error": "missing creds"}
                # 1) get token via jwt api
                token = None
                try:
                    async with httpx.AsyncClient(verify=False, timeout=20.0) as c:
                        tr = await c.get("https://ags-community-jwt-api.vercel.app/token",
                                         params={"uid": uid, "password": pw})
                        if tr.status_code == 200:
                            tj = tr.json()
                            token = tj.get("token")
                except Exception:
                    token = None
                if not token:
                    return {"uid": uid, "ok": False, "error": "no token"}
                # 2) send join
                headers = {
                    "User-Agent": "UnityPlayer/2018.4.12f1 (UnityWebRequest/1.0, libcurl/8.5.0-DEV)",
                    "Connection": "Keep-Alive", "Accept-Encoding": "gzip",
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Expect": "100-continue",
                    "X-Unity-Version": "2018.4.12f1",
                    "X-GA": "v1 1", "X-GA-SV": str(int(time.time())),
                    "ReleaseVersion": "OB55",
                }
                try:
                    async with httpx.AsyncClient(verify=False, timeout=20.0) as c:
                        jr = await c.post(CLAN_URL, content=payload, headers=headers)
                        return {"uid": uid, "ok": jr.status_code == 200, "http": jr.status_code}
                except Exception as e:
                    return {"uid": uid, "ok": False, "error": str(e)}

        tasks = [asyncio.create_task(send_one(a)) for a in accounts]
        done = 0
        for fut in asyncio.as_completed(tasks):
            res = await fut
            job["results"].append(res)
            job["updated_at"] = time.time()
            done += 1
            if done % 5 == 0:
                bot_state.log(f"[CLAN] {done}/{len(accounts)} done", "info")

        ok_count = sum(1 for r in job["results"] if r.get("ok"))
        job["status"] = "done"
        job["updated_at"] = time.time()
        bot_state.log(f"[CLAN] Job {job_id} DONE: {ok_count}/{len(accounts)} ok", "success")
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)
        job["updated_at"] = time.time()
        bot_state.log(f"[CLAN] Job {job_id} ERROR: {e}", "error")

@require_auth
async def handle_toggle_pause(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"status": "error", "error": "bad json"}, status=400)
    uid = str(data.get("uid", "")).strip()
    if not uid:
        return web.json_response({"status": "error", "error": "uid required"}, status=400)
    paused = bot_state.toggle_pause(uid)
    return web.json_response({"status": "ok", "is_paused": paused})

@require_auth
async def handle_clear_logs(request: web.Request) -> web.Response:
    bot_state.logs.clear()
    return web.json_response({"status": "ok"})

# ─── APP ──────────────────────────────────────────────────────────────────
async def start_web_dashboard(host: str = "0.0.0.0", port: int = 8080):
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_post("/api/login", handle_login)
    app.router.add_get("/api/stats", handle_get_stats)
    app.router.add_post("/api/gen_squad", handle_gen_squad)
    app.router.add_get("/api/job/{job_id}", handle_job_status)
    app.router.add_post("/api/clan/send_requests", handle_clan_requests)
    app.router.add_post("/api/account/pause", handle_toggle_pause)
    app.router.add_post("/api/logs/clear", handle_clear_logs)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    return runner
