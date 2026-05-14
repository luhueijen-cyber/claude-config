#!/usr/bin/env python3
"""
每日晨報自動發送腳本 v3.0
- 完全自動化：直接生成並發送，不依賴 todo.md
- 使用 Asia/Taipei 時區，確保日期永遠正確
- 包含：福音讀經、天氣（斗六）、行事曆（選用）
- 必要環境變數：GMAIL_APP_PASSWORD
- 選用環境變數：BRIEF_SENDER、GOOGLE_CREDENTIALS_JSON
"""

import io, os, sys, json, smtplib
from datetime import datetime
from zoneinfo import ZoneInfo
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# 強制 UTF-8，避免 Windows / GitHub Actions 編碼問題
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("❌ 缺少套件：pip install requests beautifulsoup4", file=sys.stderr)
    sys.exit(1)

# ── 設定 ──────────────────────────────────────────────────────────────────────
TAIPEI_TZ  = ZoneInfo("Asia/Taipei")
SENDER     = os.getenv("BRIEF_SENDER", "luhueijen@shsh.ylc.edu.tw")
RECIPIENTS = ["luhueijen@shsh.ylc.edu.tw", "luhueijen@gmail.com"]
WEEKDAYS   = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
HTTP_HDR   = {"User-Agent": "Mozilla/5.0 (DailyBriefBot/3.0; +https://github.com)"}


# ── 福音讀經 ──────────────────────────────────────────────────────────────────
def fetch_gospel(date: datetime) -> dict:
    """抓取天主教每日彌撒讀經，依序嘗試多個來源。"""
    sources = [
        "https://www.ccreadbible.org/ccdaily",
        "https://www.catholic-tc.org.tw/haitheres/mass/",
        "https://bible.usccb.org/bible/readings/",
    ]
    for url in sources:
        try:
            r = requests.get(url, headers=HTTP_HDR, timeout=15)
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            # 移除不需要的導覽元素
            for tag in soup(["nav", "header", "footer", "script", "style", "aside", "iframe"]):
                tag.decompose()
            text = soup.get_text("\n").strip()
            paras = [p.strip() for p in text.split("\n") if len(p.strip()) > 20]
            if len(paras) >= 4:
                domain = url.split("/")[2]
                print(f"✝️  福音來源：{domain}")
                return {
                    "text": "\n".join(paras[:40]),
                    "source": domain,
                }
        except Exception as e:
            print(f"[WARN] 福音抓取失敗 {url}: {e}", file=sys.stderr)

    return {
        "text": (
            "今日福音資料無法自動取得。\n"
            "請手動查閱：https://www.catholic-tc.org.tw/"
        ),
        "source": "（請手動查閱）",
    }


# ── 天氣 ──────────────────────────────────────────────────────────────────────
def get_weather() -> dict:
    """從 wttr.in 取得斗六天氣（完全免費，不需要 API key）。"""
    try:
        r = requests.get(
            "https://wttr.in/Douliu,Taiwan?format=j1",
            headers={"User-Agent": "curl/7.0"},
            timeout=12,
        )
        d = r.json()
        cur = d["current_condition"][0]
        day = d["weather"][0]

        max_c = int(day["maxtempC"])
        min_c = int(day["mintempC"])

        lang_zh = cur.get("lang_zh")
        desc = (lang_zh[0]["value"] if lang_zh else None) or cur["weatherDesc"][0]["value"]

        rain = sum(float(h.get("precipMM", 0)) for h in day.get("hourly", []))
        rain_pct = int(cur.get("precipMM", 0))

        if max_c < 15:
            clothing = "厚外套 + 長褲"
        elif max_c <= 22:
            clothing = "薄外套或長袖"
        else:
            clothing = "短袖輕薄上衣"
        if rain > 1:
            clothing += "，記得帶傘 ☂️"

        return {
            "desc": desc,
            "min": min_c,
            "max": max_c,
            "rain": f"{rain:.1f}mm",
            "clothing": clothing,
        }
    except Exception as e:
        print(f"[WARN] 天氣查詢失敗: {e}", file=sys.stderr)
        return {
            "desc": "資料無法取得",
            "min": "--", "max": "--",
            "rain": "--",
            "clothing": "請手動確認",
        }


# ── 行事曆（選用）────────────────────────────────────────────────────────────
def get_calendar_events(date: datetime) -> list:
    """
    取得今日 Google 行事曆事件。
    需要在 GitHub Secrets 設定 GOOGLE_CREDENTIALS_JSON（服務帳號 JSON）。
    若未設定則跳過，不影響其他功能。
    """
    creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON", "")
    if not creds_json:
        return []
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        info = json.loads(creds_json)
        creds = service_account.Credentials.from_service_account_info(
            info,
            scopes=["https://www.googleapis.com/auth/calendar.readonly"],
        )
        cal_id = os.getenv("CALENDAR_ID", "luhueijen@gmail.com")
        # 若服務帳號有 domain-wide delegation，嘗試以使用者身份存取
        if hasattr(creds, "with_subject"):
            creds = creds.with_subject(cal_id)

        cal = build("calendar", "v3", credentials=creds)
        start = datetime(date.year, date.month, date.day, 0, 0, tzinfo=TAIPEI_TZ).isoformat()
        end   = datetime(date.year, date.month, date.day, 23, 59, 59, tzinfo=TAIPEI_TZ).isoformat()
        res = cal.events().list(
            calendarId="primary",
            timeMin=start, timeMax=end,
            timeZone="Asia/Taipei",
            singleEvents=True, orderBy="startTime",
        ).execute()
        return res.get("items", [])
    except Exception as e:
        print(f"[WARN] 行事曆查詢失敗（跳過）: {e}", file=sys.stderr)
        return []


# ── HTML 生成 ──────────────────────────────────────────────────────────────────
def build_html(today: datetime, gospel: dict, weather: dict, events: list) -> str:
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
            f"<td style='white-space:nowrap;padding:8px;'>"
            f"{(ev['start'].get('dateTime','')[11:16] or ev['start'].get('date',''))}"
            "</td>"
            f"<td style='padding:8px;'>{ev.get('summary','')}</td>"
            f"<td style='padding:8px;font-size:12px;color:#666;'>{ev.get('location','')}</td>"
            "</tr>"
            for ev in events
        )
        cal_section = (
            "<table border='1' cellpadding='0' cellspacing='0' "
            "style='border-collapse:collapse;width:100%;font-size:14px;border-color:#ddd;'>"
            "<tr style='background:#ecf0f1;font-weight:bold;'>"
            "<td style='padding:8px;'>時間</td>"
            "<td style='padding:8px;'>行程</td>"
            "<td style='padding:8px;'>地點</td></tr>"
            f"{rows}</table>"
        )
    else:
        cal_section = (
            "<p style='color:#aaa;font-size:13px;font-style:italic;'>"
            "行事曆未啟用（如需自動讀取請在 GitHub Secrets 設定 GOOGLE_CREDENTIALS_JSON）"
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
    margin:0 0 8px;font-size:20px;letter-spacing:.5px;}}
h2{{margin-top:28px;padding-bottom:6px;border-bottom:2px solid #eee;font-size:16px;}}
.box{{border-left:4px solid;padding:14px 18px;border-radius:0 8px 8px 0;
      margin-bottom:6px;font-size:14px;line-height:2.0;}}
.gospel{{background:#f8f4ff;border-color:#8e44ad;}}
.weather{{background:#fff8f0;border-color:#e67e22;}}
p.src{{font-size:11px;color:#bbb;margin-top:4px;}}
footer{{color:#ccc;font-size:11px;text-align:center;margin-top:28px;padding-top:12px;
        border-top:1px solid #eee;}}
</style>
</head>
<body>
<h1>🌅 每日晨報 — {dstr}</h1>

<h2 style="color:#8e44ad;">✝️ 今日福音讀經</h2>
<div class="box gospel">{gospel_html}</div>
<p class="src">資料來源：{gospel["source"]}</p>

<h2 style="color:#e67e22;">☀️ 今日天氣（斗六）</h2>
<div class="box weather">
<p style="margin:4px 0;">🌡️ <b>氣溫</b>：{weather["min"]}–{weather["max"]}°C</p>
<p style="margin:4px 0;">🌦️ <b>天氣</b>：{weather["desc"]}</p>
<p style="margin:4px 0;">☔ <b>降雨</b>：{weather["rain"]}</p>
<p style="margin:4px 0;">👕 <b>穿著建議</b>：{weather["clothing"]}</p>
</div>

<h2 style="color:#27ae60;">📅 今日行程</h2>
{cal_section}

<footer>
GitHub Actions 自動寄送｜{today.strftime('%Y-%m-%d %H:%M')} (Asia/Taipei)
</footer>
</body>
</html>"""


# ── 主程式 ────────────────────────────────────────────────────────────────────
def main():
    pw = os.getenv("GMAIL_APP_PASSWORD", "").strip()
    if not pw:
        print("❌ 請設定環境變數 GMAIL_APP_PASSWORD", file=sys.stderr)
        print()
        print("取得方式：")
        print("  1. 前往 https://myaccount.google.com/apppasswords")
        print("  2. 新增應用程式密碼，名稱輸入 daily-brief")
        print("  3. 將 16 碼密碼存入 GitHub Secrets: GMAIL_APP_PASSWORD")
        sys.exit(1)

    # 使用台北時區取得今日日期（核心修正：不依賴 UTC）
    today = datetime.now(TAIPEI_TZ)
    wd    = WEEKDAYS[today.weekday()]
    print(f"📅 今日：{today.strftime('%Y-%m-%d %H:%M')} (Asia/Taipei) {wd}")

    gospel  = fetch_gospel(today)
    weather = get_weather()
    events  = get_calendar_events(today)
    print(f"📅 行事曆：{len(events)} 個事件")

    html    = build_html(today, gospel, weather, events)
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
