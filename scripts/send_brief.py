#!/usr/bin/env python3
"""
每日晨報自動發送腳本 v4.0
包含：福音讀經（繁體中文）、天氣（斗六）、股市行情、行事曆（選用）
"""

import io, os, sys, json, smtplib
from datetime import datetime
from zoneinfo import ZoneInfo
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("❌ pip install requests beautifulsoup4", file=sys.stderr)
    sys.exit(1)

TAIPEI_TZ  = ZoneInfo("Asia/Taipei")
SENDER     = os.getenv("BRIEF_SENDER", "luhueijen@shsh.ylc.edu.tw")
RECIPIENTS = ["luhueijen@shsh.ylc.edu.tw", "luhueijen@gmail.com"]
WEEKDAYS   = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
HTTP_HDR   = {"User-Agent": "Mozilla/5.0 (DailyBriefBot/4.0)"}

# WMO 天氣代碼 → 繁體中文
WMO_ZH = {
    0: "晴天", 1: "大致晴朗", 2: "局部多雲", 3: "陰天",
    45: "起霧", 48: "濃霧",
    51: "輕毛毛雨", 53: "毛毛雨", 55: "重毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    71: "小雪", 73: "中雪", 75: "大雪",
    80: "陣雨（輕）", 81: "陣雨", 82: "大陣雨",
    95: "雷雨", 96: "雷雨帶冰雹", 99: "強雷雨",
}


# ── 福音讀經（繁體中文）───────────────────────────────────────────────────────
def fetch_gospel(date: datetime) -> dict:
    """依序嘗試台灣/香港天主教繁體中文讀經網站。"""
    sources = [
        ("https://www.catholic-tc.org.tw/haitheres/mass/", "catholic-tc.org.tw"),
        ("https://www.ccreadbible.org/ccdaily",             "ccreadbible.org"),
        ("https://www.hkccf.org/liturgy.php",               "hkccf.org"),
        ("https://www.cbcrew.org/",                         "cbcrew.org"),
    ]
    for url, name in sources:
        try:
            r = requests.get(url, headers=HTTP_HDR, timeout=15)
            if r.status_code != 200:
                print(f"[WARN] {name} HTTP {r.status_code}", file=sys.stderr)
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            for tag in soup(["nav", "header", "footer", "script", "style", "aside", "iframe"]):
                tag.decompose()
            # 優先取主要內容區塊
            main = (
                soup.find("main") or
                soup.find("article") or
                soup.find("div", {"class": lambda c: c and any(
                    k in c for k in ("content", "main", "reading", "gospel", "mass"))}) or
                soup.find("div", {"id": lambda i: i and any(
                    k in i for k in ("content", "main", "reading"))}) or
                soup.body
            )
            text = (main or soup).get_text("\n").strip()
            paras = [p.strip() for p in text.split("\n") if len(p.strip()) > 8]
            if len(paras) >= 5:
                print(f"✝️  福音來源：{name}")
                return {"text": "\n".join(paras[:50]), "source": name}
        except Exception as e:
            print(f"[WARN] 福音抓取失敗 {name}: {e}", file=sys.stderr)

    return {
        "text": "今日讀經無法自動取得，請手動查閱：\nhttps://www.catholic-tc.org.tw/",
        "source": "（請手動查閱）",
    }


# ── 天氣（Open-Meteo，免 API key，斗六座標）──────────────────────────────────
def get_weather() -> dict:
    """Open-Meteo 免費 API，以斗六市座標（23.71°N, 120.54°E）查詢。"""
    try:
        url = (
            "https://api.open-meteo.com/v1/forecast"
            "?latitude=23.71&longitude=120.54"
            "&daily=temperature_2m_max,temperature_2m_min"
            ",precipitation_sum,precipitation_probability_max,weathercode"
            "&timezone=Asia%2FTaipei&forecast_days=1"
        )
        r = requests.get(url, timeout=12)
        d = r.json()["daily"]

        max_c    = round(float(d["temperature_2m_max"][0]))
        min_c    = round(float(d["temperature_2m_min"][0]))
        rain     = float(d["precipitation_sum"][0] or 0)
        rain_pct = int(d["precipitation_probability_max"][0] or 0)
        code     = int(d["weathercode"][0])
        desc     = WMO_ZH.get(code, f"天氣代碼 {code}")

        if max_c < 15:
            clothing = "厚外套 + 長褲"
        elif max_c <= 22:
            clothing = "薄外套或長袖"
        else:
            clothing = "短袖輕薄上衣"
        if rain > 1 or rain_pct >= 50:
            clothing += "，記得帶傘 ☂️"

        return {
            "desc":     desc,
            "min":      min_c,
            "max":      max_c,
            "rain":     f"{rain:.1f}mm",
            "rain_pct": rain_pct,
            "clothing": clothing,
        }
    except Exception as e:
        print(f"[WARN] 天氣查詢失敗: {e}", file=sys.stderr)
        return {"desc": "資料無法取得", "min": "--", "max": "--",
                "rain": "--", "rain_pct": "--", "clothing": "請手動確認"}


# ── 股市行情（yfinance）──────────────────────────────────────────────────────
def get_stock_html() -> str:
    """取得台股與美股主要指數，回傳 HTML 字串；失敗時回傳空字串。"""
    try:
        import yfinance as yf
    except ImportError:
        print("[WARN] yfinance 未安裝，略過股市（workflow 已加入 pip install）", file=sys.stderr)
        return ""

    indices = {
        "^TWII":  "台股加權",
        "0050.TW": "元大台灣50",
        "^DJI":   "道瓊",
        "^GSPC":  "S&P 500",
        "^IXIC":  "那斯達克",
    }
    rows = ""
    for ticker, name in indices.items():
        try:
            df = yf.Ticker(ticker).history(period="2d")
            if len(df) >= 2:
                prev  = df["Close"].iloc[-2]
                close = df["Close"].iloc[-1]
                chg   = close - prev
                pct   = chg / prev * 100
                arrow = "▲" if chg >= 0 else "▼"
                color = "#27ae60" if chg >= 0 else "#e74c3c"
                rows += (
                    f"<tr>"
                    f"<td style='padding:8px;'>{name}</td>"
                    f"<td style='padding:8px;text-align:right;'>{close:,.2f}</td>"
                    f"<td style='padding:8px;text-align:right;color:{color};'>{arrow} {abs(chg):,.2f}</td>"
                    f"<td style='padding:8px;text-align:right;color:{color};font-weight:bold;'>{pct:+.2f}%</td>"
                    f"</tr>"
                )
            else:
                rows += f"<tr><td colspan='4' style='padding:8px;color:#aaa;'>{name}：資料不足</td></tr>"
        except Exception as e:
            print(f"[WARN] {name} 股市資料失敗: {e}", file=sys.stderr)
            rows += f"<tr><td colspan='4' style='padding:8px;color:#aaa;'>{name}：無法取得</td></tr>"

    if not rows:
        return ""

    return f"""
<h2 style="color:#2980b9;">📈 股市行情</h2>
<table border='1' cellpadding='0' cellspacing='0'
  style='border-collapse:collapse;width:100%;font-size:14px;border-color:#ddd;'>
  <tr style='background:#ecf0f1;font-weight:bold;'>
    <td style='padding:8px;'>指數</td>
    <td style='padding:8px;text-align:right;'>最新收盤</td>
    <td style='padding:8px;text-align:right;'>漲跌</td>
    <td style='padding:8px;text-align:right;'>漲跌幅</td>
  </tr>
  {rows}
</table>
<p style='font-size:11px;color:#bbb;margin-top:4px;'>資料來源：Yahoo Finance（前一交易日收盤價）</p>
"""


# ── 行事曆（選用）────────────────────────────────────────────────────────────
def get_calendar_events(date: datetime) -> list:
    creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON", "")
    if not creds_json:
        return []
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        info  = json.loads(creds_json)
        creds = service_account.Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/calendar.readonly"]
        )
        cal_id = os.getenv("CALENDAR_ID", "luhueijen@gmail.com")
        creds  = creds.with_subject(cal_id)
        cal    = build("calendar", "v3", credentials=creds)
        start  = datetime(date.year, date.month, date.day, 0, 0, tzinfo=TAIPEI_TZ).isoformat()
        end    = datetime(date.year, date.month, date.day, 23, 59, 59, tzinfo=TAIPEI_TZ).isoformat()
        res    = cal.events().list(
            calendarId="primary", timeMin=start, timeMax=end,
            timeZone="Asia/Taipei", singleEvents=True, orderBy="startTime",
        ).execute()
        return res.get("items", [])
    except Exception as e:
        print(f"[WARN] 行事曆查詢失敗（跳過）: {e}", file=sys.stderr)
        return []


# ── HTML 組裝 ──────────────────────────────────────────────────────────────────
def build_html(today: datetime, gospel: dict, weather: dict,
               stock_html: str, events: list) -> str:
    wd   = WEEKDAYS[today.weekday()]
    dstr = today.strftime(f"%Y年%m月%d日（{wd}）")

    gospel_html = (
        gospel["text"]
        .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace("\n", "<br>\n")
    )

    if events:
        rows = "".join(
            "<tr>"
            f"<td style='padding:8px;white-space:nowrap;'>"
            f"{ev['start'].get('dateTime','')[11:16] or ev['start'].get('date','')}</td>"
            f"<td style='padding:8px;'>{ev.get('summary','')}</td>"
            f"<td style='padding:8px;font-size:12px;color:#666;'>{ev.get('location','')}</td>"
            "</tr>"
            for ev in events
        )
        cal_html = (
            "<table border='1' cellpadding='0' cellspacing='0'"
            " style='border-collapse:collapse;width:100%;font-size:14px;border-color:#ddd;'>"
            "<tr style='background:#ecf0f1;font-weight:bold;'>"
            "<td style='padding:8px;'>時間</td><td style='padding:8px;'>行程</td>"
            "<td style='padding:8px;'>地點</td></tr>"
            f"{rows}</table>"
        )
    else:
        cal_html = (
            "<p style='color:#aaa;font-size:13px;font-style:italic;'>"
            "行事曆未啟用——如需自動讀取，請在 GitHub Secrets 設定 GOOGLE_CREDENTIALS_JSON"
            "</p>"
        )

    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{{font-family:'Noto Sans TC',Arial,sans-serif;max-width:680px;margin:auto;
     color:#2c3e50;padding:16px;line-height:1.7;}}
h1{{background:#1a252f;color:#fff;padding:18px 22px;border-radius:8px;
    margin:0 0 8px;font-size:20px;}}
h2{{margin-top:28px;padding-bottom:6px;border-bottom:2px solid #eee;font-size:16px;}}
.box{{border-left:4px solid;padding:14px 18px;border-radius:0 8px 8px 0;
      font-size:14px;line-height:2.0;}}
</style>
</head>
<body>
<h1>🌅 每日晨報 — {dstr}</h1>

<h2 style="color:#8e44ad;">✝️ 今日福音讀經</h2>
<div class="box" style="background:#f8f4ff;border-color:#8e44ad;">{gospel_html}</div>
<p style="font-size:11px;color:#bbb;margin-top:4px;">資料來源：{gospel["source"]}</p>

<h2 style="color:#e67e22;">☀️ 今日天氣（斗六）</h2>
<div class="box" style="background:#fff8f0;border-color:#e67e22;">
<p style="margin:4px 0;">🌡️ <b>氣溫</b>：{weather["min"]}–{weather["max"]}°C</p>
<p style="margin:4px 0;">🌦️ <b>天氣</b>：{weather["desc"]}</p>
<p style="margin:4px 0;">☔ <b>降雨</b>：{weather["rain"]}（機率 {weather["rain_pct"]}%）</p>
<p style="margin:4px 0;">👕 <b>穿著建議</b>：{weather["clothing"]}</p>
</div>

{stock_html}

<h2 style="color:#27ae60;">📅 今日行程</h2>
{cal_html}

<hr style="margin:28px 0;border:none;border-top:1px solid #eee;">
<p style="color:#ccc;font-size:11px;text-align:center;">
  GitHub Actions 自動寄送｜{today.strftime('%Y-%m-%d %H:%M')} (Asia/Taipei)
</p>
</body>
</html>"""


# ── 主程式 ────────────────────────────────────────────────────────────────────
def main():
    pw = os.getenv("GMAIL_APP_PASSWORD", "").strip()
    if not pw:
        print("❌ 請設定環境變數 GMAIL_APP_PASSWORD", file=sys.stderr)
        sys.exit(1)

    today = datetime.now(TAIPEI_TZ)
    wd    = WEEKDAYS[today.weekday()]
    print(f"📅 今日：{today.strftime('%Y-%m-%d %H:%M')} (Asia/Taipei) {wd}")

    gospel     = fetch_gospel(today)
    weather    = get_weather()
    print(f"☀️  天氣：{weather['desc']} {weather['min']}–{weather['max']}°C")
    stock_html = get_stock_html()
    print(f"📈 股市：{'取得成功' if stock_html else '略過（yfinance 未安裝）'}")
    events     = get_calendar_events(today)
    print(f"📅 行事曆：{len(events)} 個事件")

    html    = build_html(today, gospel, weather, stock_html, events)
    subject = f"🌅 每日晨報 — {today.strftime('%Y-%m-%d')}（{wd}）"

    msg = MIMEMultipart("alternative")
    msg["From"]    = SENDER
    msg["To"]      = ", ".join(RECIPIENTS)
    msg["Subject"] = subject
    msg.attach(MIMEText(html, "html", "utf-8"))

    print(f"📤 發送中：{subject}")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(SENDER, pw)
        smtp.sendmail(SENDER, RECIPIENTS, msg.as_bytes())
    print(f"✅ 寄出成功！收件人：{', '.join(RECIPIENTS)}")


if __name__ == "__main__":
    main()
