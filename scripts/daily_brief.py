#!/usr/bin/env python3
"""
/daily-brief — 每日晨間簡報自動化腳本
每日 05:00 Asia/Taipei 自動執行

必要環境變數：
  GOOGLE_CREDENTIALS_JSON  或  GOOGLE_APPLICATION_CREDENTIALS
  OPENWEATHERMAP_API_KEY   （可選，無則 weather fallback）
  CALENDAR_ID              （預設 luhueijen@gmail.com）
  EMAIL_RECIPIENT          （預設 luhueijen@gmail.com）
  ENABLE_STOCK_REPORT      true/false（預設 true）
  ENABLE_EMAIL_DRAFT       true/false（預設 false）
"""

import os
import sys
import json
import base64
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

# ── 相依套件（失敗給出明確提示）────────────────────────────────────────────────
try:
    import requests
    from bs4 import BeautifulSoup
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
except ImportError as e:
    print(f"[ERROR] 缺少相依套件：{e}")
    print("請執行：pip install google-api-python-client google-auth requests beautifulsoup4")
    sys.exit(1)

# ── 設定 ──────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

CALENDAR_ID     = os.getenv("CALENDAR_ID",     "luhueijen@gmail.com")
EMAIL_RECIPIENT = os.getenv("EMAIL_RECIPIENT",  "luhueijen@gmail.com")
LOCATION        = os.getenv("LOCATION",         "Douliu,TW")
TIMEZONE        = os.getenv("TIMEZONE",         "Asia/Taipei")
TODO_PATH       = Path(os.getenv("TODO_PATH",   "~/todo.md")).expanduser()
STOCK_PATH      = Path(os.getenv("STOCK_PATH",  "~/stock.md")).expanduser()
ENABLE_STOCK    = os.getenv("ENABLE_STOCK_REPORT", "true").lower()  == "true"
ENABLE_DRAFT    = os.getenv("ENABLE_EMAIL_DRAFT",  "false").lower() == "true"
FALLBACK        = "資料無法取得"
WEATHER_KEY     = os.getenv("OPENWEATHERMAP_API_KEY", "")

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.readonly",
]

# ── 憑證 ──────────────────────────────────────────────────────────────────────
def _credentials():
    """優先讀 JSON 字串（GitHub Secrets），次讀檔案路徑。"""
    raw = os.getenv("GOOGLE_CREDENTIALS_JSON")
    if raw:
        info = json.loads(raw)
        return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if path:
        return service_account.Credentials.from_service_account_file(path, scopes=SCOPES)
    raise RuntimeError(
        "未設定 Google 憑證，請設定 GOOGLE_CREDENTIALS_JSON 或 GOOGLE_APPLICATION_CREDENTIALS"
    )

# ── Step 1：福音讀經 ──────────────────────────────────────────────────────────
def fetch_gospel(date: datetime) -> dict:
    """抓取當日天主教彌撒讀經。"""
    date_str = date.strftime("%Y-%m-%d")
    headers  = {"User-Agent": "Mozilla/5.0 (DailyBriefBot/2.0)"}
    sources  = [
        "https://www.catholic-tc.org.tw/gospel.php",
        "https://www.ccreadbible.org/ccdaily",
    ]
    for url in sources:
        try:
            r = requests.get(url, headers=headers, timeout=10)
            if r.status_code != 200:
                continue
            text = BeautifulSoup(r.text, "html.parser").get_text("\n").strip()
            if len(text) > 300:
                log.info(f"福音讀經來源：{url}")
                return {
                    "liturgical_season": "復活期（自動判斷）",
                    "gospel_reference":  "（自動抓取）",
                    "gospel_text":       text[:2000],
                }
        except Exception as e:
            log.warning(f"福音抓取失敗 {url}: {e}")
    log.warning("福音資料無法取得，使用 fallback")
    return {
        "liturgical_season": FALLBACK,
        "gospel_reference":  FALLBACK,
        "gospel_text":       "福音資料無法取得，請手動查閱。",
    }

# ── Step 2：福音默想 ──────────────────────────────────────────────────────────
def generate_reflection(gospel: dict) -> dict:
    """產生默想文字。可接 LLM API 擴充。"""
    if "無法取得" in gospel["gospel_text"]:
        return {
            "reflection": "今日福音資料無法取得，請稍後手動查閱。",
            "prayer":     "主啊，請引導我度過今日。",
        }
    return {
        "reflection": (
            f"禮儀時節：{gospel['liturgical_season']}｜"
            f"讀經出處：{gospel['gospel_reference']}\n\n"
            "請靜心默想這段經文，思考如何將信仰融入今日的工作與生活。"
        ),
        "prayer": "主，賜我祢的平安，讓我輕盈地度過今天。阿們。",
    }

# ── Step 3：行事曆 ────────────────────────────────────────────────────────────
def list_events(cal, date: datetime) -> list:
    start = datetime(date.year, date.month, date.day, 0, 0).isoformat() + "+08:00"
    end   = datetime(date.year, date.month, date.day, 23, 59, 59).isoformat() + "+08:00"
    try:
        result = cal.events().list(
            calendarId=CALENDAR_ID,
            timeMin=start, timeMax=end,
            timeZone=TIMEZONE,
            singleEvents=True, orderBy="startTime",
        ).execute()
        return result.get("items", [])
    except HttpError as e:
        log.error(f"行事曆查詢失敗: {e}")
        return []

# ── Step 4：Gmail 待辦 ────────────────────────────────────────────────────────
_SKIP_KEYWORDS = ["unsubscribe", "退訂", "促銷", "廣告", "newsletter",
                  "OneDrive", "回憶", "Canva", "ChatGPT"]

def search_todos(gm, max_results: int = 20) -> list:
    queries = ["is:unread newer_than:1d", "is:starred newer_than:7d"]
    seen, todos = set(), []
    for q in queries:
        try:
            msgs = gm.users().messages().list(userId="me", q=q, maxResults=max_results).execute()
            for m in msgs.get("messages", []):
                if m["id"] in seen:
                    continue
                seen.add(m["id"])
                data = gm.users().messages().get(
                    userId="me", id=m["id"], format="metadata",
                    metadataHeaders=["Subject", "From", "Date"],
                ).execute()
                hdrs    = {h["name"]: h["value"] for h in data["payload"]["headers"]}
                subject = hdrs.get("Subject", "（無主旨）")
                if any(k.lower() in subject.lower() for k in _SKIP_KEYWORDS):
                    continue
                todos.append({
                    "subject": subject,
                    "sender":  hdrs.get("From", ""),
                    "snippet": data.get("snippet", ""),
                })
        except HttpError as e:
            log.error(f"Gmail 查詢失敗 ({q}): {e}")
    return todos

# ── Step 5：天氣 ──────────────────────────────────────────────────────────────
def get_weather() -> dict:
    if not WEATHER_KEY:
        log.warning("未設定 OPENWEATHERMAP_API_KEY")
        return {"desc": FALLBACK, "min": "--", "max": "--",
                "rain": "--", "clothing": "請手動確認"}
    try:
        url  = (
            f"https://api.openweathermap.org/data/2.5/weather"
            f"?q={LOCATION}&units=metric&lang=zh_tw&appid={WEATHER_KEY}"
        )
        data = requests.get(url, timeout=10).json()
        tmin = round(data["main"]["temp_min"])
        tmax = round(data["main"]["temp_max"])
        desc = data["weather"][0]["description"]
        rain = data.get("rain", {}).get("1h", 0)

        clothing = "短袖輕薄上衣"
        if tmax < 15:
            clothing = "厚外套 + 長褲"
        elif tmax <= 22:
            clothing = "薄外套或長袖"
        if rain > 0:
            clothing += "，記得帶傘"

        return {"desc": desc, "min": tmin, "max": tmax,
                "rain": f"{rain:.1f}mm/h", "clothing": clothing}
    except Exception as e:
        log.error(f"天氣查詢失敗: {e}")
        return {"desc": FALLBACK, "min": "--", "max": "--",
                "rain": "--", "clothing": "請手動確認"}

# ── Step 6：建立行事曆提醒（含去重）─────────────────────────────────────────
def create_reminder(cal, summary: str, start_iso: str, end_iso: str):
    try:
        existing = cal.events().list(
            calendarId=CALENDAR_ID, q=summary,
            timeMin=start_iso, timeMax=end_iso,
        ).execute()
        if existing.get("items"):
            log.info(f"提醒已存在，跳過：{summary}")
            return None
        event = {
            "summary":     summary,
            "description": "來自 /daily-brief 的自動提醒",
            "start":       {"dateTime": start_iso, "timeZone": TIMEZONE},
            "end":         {"dateTime": end_iso,   "timeZone": TIMEZONE},
            "reminders":   {
                "useDefault": False,
                "overrides":  [{"method": "popup", "minutes": 0},
                               {"method": "email", "minutes": 15}],
            },
        }
        r = cal.events().insert(calendarId=CALENDAR_ID, body=event).execute()
        log.info(f"提醒已建立：{summary}")
        return r
    except HttpError as e:
        log.error(f"提醒建立失敗 ({summary}): {e}")
        return None

# ── Step 7：寫入 todo.md ──────────────────────────────────────────────────────
_WEEKDAYS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]

def write_todo(date: datetime, events: list, todos: list,
               weather: dict, gospel: dict, reflection: dict):
    wd   = _WEEKDAYS[date.weekday()]
    dstr = date.strftime(f"%Y年%m月%d日（{wd}）")

    lines = [
        f"# 📋 每日晨報 — {dstr}", "",
        "---", "",
        "## ✝️ 今日福音",
        f"**禮儀時節：** {gospel['liturgical_season']}",
        f"**讀經出處：** {gospel['gospel_reference']}", "",
        reflection["reflection"], "",
        f"**祈禱：** {reflection['prayer']}", "",
        "---", "",
        "## ☀️ 天氣與穿著",
        f"- **天氣：** {weather['desc']}",
        f"- **氣溫：** {weather['min']}–{weather['max']}°C",
        f"- **降雨：** {weather['rain']}",
        f"- **穿著：** {weather['clothing']}", "",
        "---", "",
        "## 📅 今日行程",
    ]

    if not events:
        lines.append("- 今日無行程")
    else:
        for ev in events:
            t = ev["start"].get("dateTime", ev["start"].get("date", ""))
            t = t[11:16] if "T" in t else t
            loc = f"　@{ev['location']}" if ev.get("location") else ""
            lines.append(f"- [ ] {t}　{ev.get('summary','（無標題）')}{loc}")

    lines += ["", "---", "", "## 📬 信件待辦"]
    if not todos:
        lines.append("- 無待辦信件")
    else:
        for td in todos:
            lines.append(f"- [ ] **{td['subject']}**　（{td['sender']}）")
            if td["snippet"]:
                lines.append(f"  > {td['snippet'][:120]}")

    lines += ["", "---",
              f"*由 /daily-brief 自動產生 | {date.strftime('%Y-%m-%d')}*"]

    TODO_PATH.write_text("\n".join(lines), encoding="utf-8")
    log.info(f"todo.md 已寫入：{TODO_PATH}")

# ── Step 8：stock.md（yfinance）──────────────────────────────────────────────
def write_stock(date: datetime):
    try:
        import yfinance as yf
    except ImportError:
        log.warning("yfinance 未安裝，跳過 stock.md（pip install yfinance）")
        return
    tickers = {"^DJI": "道瓊", "^GSPC": "S&P500", "^IXIC": "那斯達克", "^TWII": "台股加權"}
    lines   = [
        f"# 📈 股市晨報 — {date.strftime('%Y-%m-%d')}", "",
        "| 指數 | 收盤 | 漲跌 | 漲跌幅 |",
        "|------|------|------|--------|",
    ]
    for ticker, name in tickers.items():
        try:
            df = yf.Ticker(ticker).history(period="2d")
            if len(df) >= 2:
                prev, close = df["Close"].iloc[-2], df["Close"].iloc[-1]
                chg = close - prev
                pct = chg / prev * 100
                arrow = "▲" if chg >= 0 else "▼"
                lines.append(f"| {name} | {close:,.2f} | {arrow} {abs(chg):,.2f} | {pct:+.2f}% |")
            else:
                lines.append(f"| {name} | {FALLBACK} | — | — |")
        except Exception as e:
            log.warning(f"{name} 資料失敗: {e}")
            lines.append(f"| {name} | {FALLBACK} | — | — |")
    lines += ["", f"*資料來源：Yahoo Finance | {date.strftime('%Y-%m-%d')}*"]
    STOCK_PATH.write_text("\n".join(lines), encoding="utf-8")
    log.info(f"stock.md 已寫入：{STOCK_PATH}")

# ── Step 9：Gmail 草稿 ────────────────────────────────────────────────────────
def create_draft(gm, date: datetime, events: list, todos: list,
                 weather: dict, gospel: dict, reflection: dict):
    wd      = _WEEKDAYS[date.weekday()]
    subject = (
        f"🌅 每日晨報 — {date.strftime('%Y-%m-%d')}（{wd}）"
        f"｜{gospel['liturgical_season']}"
    )
    events_rows = "".join(
        f"<tr><td>{ev['start'].get('dateTime','')[11:16] or ev['start'].get('date','')}</td>"
        f"<td>{ev.get('summary','')}</td><td>{ev.get('location','')}</td></tr>"
        for ev in events
    ) or "<tr><td colspan='3'>今日無行程</td></tr>"

    todos_li = "".join(
        f"<li>☐ <strong>{t['subject']}</strong>（{t['sender']}）"
        f"<br><small>{t['snippet'][:120]}</small></li>"
        for t in todos
    ) or "<li>無待辦信件</li>"

    html = f"""<html><body style="font-family:sans-serif;max-width:700px;margin:auto;">
<h1 style="color:#2c3e50;">每日晨報 — {date.strftime('%Y年%m月%d日')}（{wd}）</h1>
<hr>
<h2>✝️ 福音</h2>
<p><strong>{gospel['liturgical_season']}</strong>｜{gospel['gospel_reference']}</p>
<blockquote style="border-left:4px solid #3498db;padding:8px 16px;background:#f0f7ff;">
{gospel['gospel_text'][:500].replace(chr(10),'<br>')}
</blockquote>
<p>{reflection['reflection'].replace(chr(10),'<br>')}</p>
<p><em>{reflection['prayer']}</em></p>
<hr>
<h2>☀️ 天氣</h2>
<p>🌡️ {weather['min']}–{weather['max']}°C｜{weather['desc']}｜降雨：{weather['rain']}</p>
<p>👕 <strong>{weather['clothing']}</strong></p>
<hr>
<h2>📅 今日行程</h2>
<table border="1" cellpadding="6" cellspacing="0"
       style="border-collapse:collapse;width:100%;font-size:14px;">
<tr style="background:#ecf0f1;"><th>時間</th><th>行程</th><th>地點</th></tr>
{events_rows}
</table>
<hr>
<h2>📬 信件待辦</h2><ul>{todos_li}</ul>
<hr>
<p style="color:#bdc3c7;font-size:11px;">
由 /daily-brief 自動產生 | {date.strftime('%Y-%m-%d')}
</p>
</body></html>"""

    try:
        msg = MIMEMultipart("alternative")
        msg["From"]    = EMAIL_RECIPIENT
        msg["To"]      = EMAIL_RECIPIENT
        msg["Subject"] = subject
        msg.attach(MIMEText(html, "html", "utf-8"))
        raw   = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
        draft = gm.users().drafts().create(
            userId="me", body={"message": {"raw": raw}}
        ).execute()
        log.info(f"Gmail 草稿已建立：{draft['id']}")
    except HttpError as e:
        log.error(f"Gmail 草稿建立失敗: {e}")

# ── 主流程 ────────────────────────────────────────────────────────────────────
def main():
    today = datetime.now(ZoneInfo("Asia/Taipei"))
    log.info(f"=== /daily-brief 開始 {today.strftime('%Y-%m-%d %H:%M')} (Asia/Taipei) ===")

    try:
        creds = _credentials()
        cal   = build("calendar", "v3", credentials=creds)
        gm    = build("gmail",    "v1", credentials=creds)
    except Exception as e:
        log.error(f"Google API 初始化失敗: {e}")
        sys.exit(1)

    gospel     = fetch_gospel(today)
    reflection = generate_reflection(gospel)
    events     = list_events(cal, today)
    todos      = search_todos(gm)
    weather    = get_weather()

    # 對有時間的行程建立提前提醒
    for ev in events:
        dt_str = ev["start"].get("dateTime")
        if not dt_str:
            continue
        try:
            dt     = datetime.fromisoformat(dt_str)
            s_iso  = (dt - timedelta(minutes=60)).isoformat()
            e_iso  = (dt - timedelta(minutes=45)).isoformat()
            create_reminder(cal, f"⏰ 提醒：{ev.get('summary','行程')}", s_iso, e_iso)
        except Exception as e:
            log.warning(f"提醒略過: {e}")

    write_todo(today, events, todos, weather, gospel, reflection)

    if ENABLE_STOCK:
        write_stock(today)

    if ENABLE_DRAFT:
        create_draft(gm, today, events, todos, weather, gospel, reflection)

    log.info("=== /daily-brief 完成 ===")


if __name__ == "__main__":
    main()
