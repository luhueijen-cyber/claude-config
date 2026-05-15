#!/usr/bin/env python3
"""
每日晨報自動發送腳本 v5.0
- 福音：API 取讀經章節 → BibleGateway 繁體中文經文
- 天氣：Open-Meteo（斗六精確座標）
- 股市：Yahoo Finance（台股 + 美股）
- 行事曆：選用（設定 GOOGLE_CREDENTIALS_JSON）
"""

import io, os, sys, json, re, smtplib
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

BOT_HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/124.0.0.0 Safari/537.36",
           "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8"}

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

# 禮儀季節英文 → 繁體中文
SEASON_ZH = {
    "Advent": "將臨期", "Christmas": "聖誕期",
    "Ordinary": "常年期", "Lent": "四旬期",
    "Eastertide": "復活期", "Easter": "復活期",
    "Holy Week": "聖週", "Triduum": "逾越節三日慶典",
}

# 聖經書名英文 → 思高繁體中文
BOOK_ZH = {
    "Gen": "創世紀", "Ex": "出谷紀", "Exod": "出谷紀",
    "Lev": "肋未紀", "Num": "戶籍紀", "Deut": "申命紀",
    "Josh": "若蘇厄書", "Judg": "民長紀", "Ruth": "盧德傳",
    "1 Sam": "撒慕爾紀上", "2 Sam": "撒慕爾紀下",
    "1 Kgs": "列王紀上", "2 Kgs": "列王紀下",
    "1 Chr": "編年紀上", "2 Chr": "編年紀下",
    "Job": "約伯傳", "Ps": "聖詠", "Psalm": "聖詠",
    "Prov": "箴言", "Eccl": "訓道篇", "Song": "雅歌",
    "Wis": "智慧篇", "Sir": "德訓篇",
    "Isa": "依撒意亞", "Jer": "耶肋米亞",
    "Lam": "哀歌", "Bar": "巴路克", "Ezek": "厄則克耳",
    "Dan": "達尼爾", "Hos": "歐瑟亞", "Joel": "岳厄爾",
    "Amos": "亞毛斯", "Jonah": "約納", "Mic": "米該亞",
    "Nah": "納鴻", "Hab": "哈巴谷", "Zeph": "索福尼亞",
    "Hag": "哈蓋", "Zech": "匝加利亞", "Mal": "瑪拉基亞",
    "Matt": "瑪竇福音", "Mt": "瑪竇福音",
    "Mark": "馬爾谷福音", "Mk": "馬爾谷福音",
    "Luke": "路加福音", "Lk": "路加福音",
    "John": "若望福音", "Jn": "若望福音",
    "Acts": "宗徒大事錄",
    "Rom": "羅馬書",
    "1 Cor": "格林多前書", "2 Cor": "格林多後書",
    "Gal": "迦拉達書", "Eph": "厄弗所書", "Phil": "斐理伯書",
    "Col": "哥羅森書",
    "1 Thess": "得撒洛尼前書", "2 Thess": "得撒洛尼後書",
    "1 Tim": "弟茂德前書", "2 Tim": "弟茂德後書",
    "Titus": "弟鐸書", "Phlm": "費肋孟書", "Heb": "希伯來書",
    "Jas": "雅各伯書",
    "1 Pet": "伯多祿前書", "2 Pet": "伯多祿後書",
    "1 John": "若望一書", "1 Jn": "若望一書",
    "2 John": "若望二書", "2 Jn": "若望二書",
    "3 John": "若望三書", "3 Jn": "若望三書",
    "Jude": "猶達書", "Rev": "默示錄",
}


def ref_to_zh(ref: str) -> str:
    """英文書名轉繁體中文，例如 John 16:20-23 → 若望福音 16:20-23"""
    for en, zh in sorted(BOOK_ZH.items(), key=lambda x: -len(x[0])):
        if ref.startswith(en + " ") or ref.startswith(en + ":"):
            return zh + " " + ref[len(en):].strip()
    return ref


def fetch_bible_text(reference: str) -> str:
    """從 BibleGateway 取繁體中文聖經段落（CUVMPT 和合本現代標點繁體）"""
    try:
        encoded_ref = requests.utils.quote(reference)
        url = f"https://www.biblegateway.com/passage/?search={encoded_ref}&version=CUVMPT"
        r = requests.get(url, headers=BOT_HDR, timeout=15)
        if r.status_code != 200:
            return f"（{reference}，無法取得）"
        soup = BeautifulSoup(r.text, "html.parser")
        # 移除頁尾、腳注、交叉引用
        for tag in soup(["footer", "header", "nav", "script", "style"]):
            tag.decompose()
        for tag in soup.find_all(class_=["footnotes", "crossrefs", "footnote",
                                          "crossreference", "copyright-table"]):
            tag.decompose()
        for tag in soup.find_all("sup"):
            tag.decompose()
        # 找主要段落
        passage = (soup.find("div", class_="passage-text") or
                   soup.find("div", class_="text-html") or
                   soup.find("div", id="passage-content"))
        if not passage:
            return f"（{reference}，解析失敗）"
        # 移除版本資訊行
        for tag in passage.find_all("h3"):
            tag.decompose()
        # 取文字，保留段落分行
        lines = []
        for elem in passage.find_all(["p", "div"]):
            t = elem.get_text(" ").strip()
            # 移除版權行
            if t and len(t) > 5 and "版權" not in t and "Copyright" not in t:
                lines.append(t)
        text = "\n".join(lines)
        # 清除多餘空白
        text = re.sub(r" {2,}", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip() or f"（{reference}，內容為空）"
    except Exception as e:
        print(f"[WARN] BibleGateway 失敗 {reference}: {e}", file=sys.stderr)
        return f"（{reference}，請手動查閱）"


# ── 福音讀經 ──────────────────────────────────────────────────────────────────
def fetch_gospel(date: datetime) -> dict:
    """
    步驟1：從 cpbjr API 取今日天主教讀經章節
    步驟2：從 BibleGateway 取繁體中文經文
    """
    year = date.year
    mmdd = date.strftime("%m-%d")
    api_url = f"https://cpbjr.github.io/catholic-readings-api/readings/{year}/{mmdd}.json"
    try:
        r = requests.get(api_url, headers=BOT_HDR, timeout=10)
        r.raise_for_status()
        data = r.json()

        readings      = data.get("readings", {})
        reading1_ref  = readings.get("firstReading", "")
        psalm_ref     = readings.get("psalm", "")
        gospel_ref    = readings.get("gospel", "")
        season_en     = data.get("season", "")
        season        = SEASON_ZH.get(season_en, season_en)

        reading1_zh   = ref_to_zh(reading1_ref)
        gospel_zh     = ref_to_zh(gospel_ref)

        print(f"✝️  讀經章節：{reading1_ref} ｜ 福音：{gospel_ref}")
        reading1_text = fetch_bible_text(reading1_ref) if reading1_ref else ""
        gospel_text   = fetch_bible_text(gospel_ref)   if gospel_ref   else ""

        return {
            "season":        season,
            "reading1_ref":  reading1_zh,
            "psalm_ref":     ref_to_zh(psalm_ref),
            "gospel_ref":    gospel_zh,
            "reading1_text": reading1_text,
            "gospel_text":   gospel_text,
            "ok": True,
        }
    except Exception as e:
        print(f"[WARN] 讀經 API 失敗: {e}", file=sys.stderr)
        return {
            "season": "", "reading1_ref": "", "psalm_ref": "", "gospel_ref": "",
            "reading1_text": "", "ok": False,
            "gospel_text": "今日讀經無法自動取得，請手動查閱：\nhttps://www.catholic-tc.org.tw/",
        }


# ── 天氣（Open-Meteo，斗六精確座標）─────────────────────────────────────────
def get_weather() -> dict:
    try:
        url = (
            "https://api.open-meteo.com/v1/forecast"
            "?latitude=23.71&longitude=120.54"
            "&daily=temperature_2m_max,temperature_2m_min"
            ",precipitation_sum,precipitation_probability_max,weathercode"
            "&timezone=Asia%2FTaipei&forecast_days=1"
        )
        d = requests.get(url, timeout=12).json()["daily"]
        max_c    = round(float(d["temperature_2m_max"][0]))
        min_c    = round(float(d["temperature_2m_min"][0]))
        rain     = float(d["precipitation_sum"][0] or 0)
        rain_pct = int(d["precipitation_probability_max"][0] or 0)
        desc     = WMO_ZH.get(int(d["weathercode"][0]), "未知")

        clothing = (
            "厚外套 + 長褲" if max_c < 15 else
            "薄外套或長袖"  if max_c <= 22 else
            "短袖輕薄上衣"
        )
        if rain > 1 or rain_pct >= 50:
            clothing += "，記得帶傘 ☂️"

        return {"desc": desc, "min": min_c, "max": max_c,
                "rain": f"{rain:.1f}mm", "rain_pct": rain_pct, "clothing": clothing}
    except Exception as e:
        print(f"[WARN] 天氣查詢失敗: {e}", file=sys.stderr)
        return {"desc": "資料無法取得", "min": "--", "max": "--",
                "rain": "--", "rain_pct": "--", "clothing": "請手動確認"}


# ── 股市（yfinance）──────────────────────────────────────────────────────────
def get_stock_html() -> str:
    try:
        import yfinance as yf
    except ImportError:
        print("[WARN] yfinance 未安裝", file=sys.stderr)
        return ""

    indices = {
        "^TWII":   "台股加權",
        "0050.TW": "元大台灣50",
        "^DJI":    "道瓊",
        "^GSPC":   "S&P 500",
        "^IXIC":   "那斯達克",
    }
    rows = ""
    for ticker, name in indices.items():
        try:
            df = yf.Ticker(ticker).history(period="2d")
            if len(df) >= 2:
                prev, close = df["Close"].iloc[-2], df["Close"].iloc[-1]
                chg   = close - prev
                pct   = chg / prev * 100
                arrow = "▲" if chg >= 0 else "▼"
                color = "#27ae60" if chg >= 0 else "#e74c3c"
                rows += (
                    f"<tr><td style='padding:7px 10px;'>{name}</td>"
                    f"<td style='padding:7px 10px;text-align:right;'>{close:,.2f}</td>"
                    f"<td style='padding:7px 10px;text-align:right;color:{color};'>{arrow} {abs(chg):,.2f}</td>"
                    f"<td style='padding:7px 10px;text-align:right;color:{color};font-weight:bold;'>{pct:+.2f}%</td>"
                    "</tr>"
                )
            else:
                rows += f"<tr><td colspan='4' style='padding:7px 10px;color:#aaa;'>{name}：資料不足</td></tr>"
        except Exception as e:
            print(f"[WARN] 股市 {name}: {e}", file=sys.stderr)
            rows += f"<tr><td colspan='4' style='padding:7px 10px;color:#aaa;'>{name}：無法取得</td></tr>"

    return f"""
<h2 style="color:#2980b9;">📈 股市行情</h2>
<table border='1' cellpadding='0' cellspacing='0'
  style='border-collapse:collapse;width:100%;font-size:14px;border-color:#ddd;'>
  <tr style='background:#ecf0f1;font-weight:bold;'>
    <td style='padding:7px 10px;'>指數</td>
    <td style='padding:7px 10px;text-align:right;'>最新收盤</td>
    <td style='padding:7px 10px;text-align:right;'>漲跌</td>
    <td style='padding:7px 10px;text-align:right;'>漲跌幅</td>
  </tr>{rows}
</table>
<p style='font-size:11px;color:#bbb;margin-top:4px;'>資料來源：Yahoo Finance（前一交易日）</p>
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
        ).with_subject(os.getenv("CALENDAR_ID", "luhueijen@gmail.com"))
        cal   = build("calendar", "v3", credentials=creds)
        start = datetime(date.year, date.month, date.day, 0, 0, tzinfo=TAIPEI_TZ).isoformat()
        end   = datetime(date.year, date.month, date.day, 23, 59, 59, tzinfo=TAIPEI_TZ).isoformat()
        res   = cal.events().list(
            calendarId="primary", timeMin=start, timeMax=end,
            timeZone="Asia/Taipei", singleEvents=True, orderBy="startTime",
        ).execute()
        return res.get("items", [])
    except Exception as e:
        print(f"[WARN] 行事曆查詢失敗: {e}", file=sys.stderr)
        return []


# ── HTML 組裝 ──────────────────────────────────────────────────────────────────
def gospel_block(g: dict) -> str:
    def reading_html(ref: str, text: str, label: str) -> str:
        if not ref:
            return ""
        safe = (text
                .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .replace("\n", "<br>\n"))
        return (
            f"<p style='margin:12px 0 4px;font-weight:bold;color:#6c3483;'>{label}：{ref}</p>"
            f"<div style='font-size:14px;line-height:2.0;padding:10px 16px;"
            f"background:#faf6ff;border-left:3px solid #a569bd;border-radius:0 6px 6px 0;'>"
            f"{safe}</div>"
        )

    season_line = (
        f"<p style='color:#7d3c98;font-size:13px;margin:0 0 8px;'>⛪ 禮儀季節：{g['season']}</p>"
        if g.get("season") else ""
    )
    psalm_line = (
        f"<p style='margin:10px 0 4px;font-size:13px;color:#888;'>🎵 答唱詠：{g['psalm_ref']}</p>"
        if g.get("psalm_ref") else ""
    )
    return (
        season_line
        + reading_html(g.get("reading1_ref", ""), g.get("reading1_text", ""), "第一讀經")
        + psalm_line
        + reading_html(g.get("gospel_ref", ""), g.get("gospel_text", ""), "✝️ 福音")
    )


def build_html(today: datetime, gospel: dict, weather: dict,
               stock_html: str, events: list) -> str:
    wd   = WEEKDAYS[today.weekday()]
    dstr = today.strftime(f"%Y年%m月%d日（{wd}）")

    if events:
        rows = "".join(
            "<tr>"
            f"<td style='padding:7px 10px;white-space:nowrap;'>"
            f"{ev['start'].get('dateTime','')[11:16] or ev['start'].get('date','')}</td>"
            f"<td style='padding:7px 10px;'>{ev.get('summary','')}</td>"
            f"<td style='padding:7px 10px;font-size:12px;color:#666;'>{ev.get('location','')}</td>"
            "</tr>"
            for ev in events
        )
        cal_html = (
            "<table border='1' cellpadding='0' cellspacing='0'"
            " style='border-collapse:collapse;width:100%;font-size:14px;border-color:#ddd;'>"
            "<tr style='background:#ecf0f1;font-weight:bold;'>"
            "<td style='padding:7px 10px;'>時間</td>"
            "<td style='padding:7px 10px;'>行程</td>"
            "<td style='padding:7px 10px;'>地點</td></tr>"
            f"{rows}</table>"
        )
    else:
        cal_html = "<p style='color:#aaa;font-size:13px;'>（行事曆未啟用）</p>"

    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{{font-family:'Noto Sans TC',Arial,sans-serif;max-width:680px;margin:auto;
     color:#2c3e50;padding:16px;line-height:1.7;}}
h1{{background:#1a252f;color:#fff;padding:18px 22px;border-radius:8px;margin:0;font-size:20px;}}
h2{{margin-top:28px;padding-bottom:6px;border-bottom:2px solid #eee;font-size:16px;}}
</style>
</head>
<body>
<h1>🌅 每日晨報 — {dstr}</h1>

<h2 style="color:#7d3c98;">✝️ 今日彌撒讀經</h2>
{gospel_block(gospel)}

<h2 style="color:#e67e22;">☀️ 今日天氣（斗六）</h2>
<div style="background:#fff8f0;border-left:4px solid #e67e22;padding:14px 18px;border-radius:0 8px 8px 0;">
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
        print("❌ 請設定 GMAIL_APP_PASSWORD", file=sys.stderr)
        sys.exit(1)

    today = datetime.now(TAIPEI_TZ)
    wd    = WEEKDAYS[today.weekday()]
    print(f"📅 今日：{today.strftime('%Y-%m-%d %H:%M')} (Asia/Taipei) {wd}")

    gospel     = fetch_gospel(today)
    weather    = get_weather()
    print(f"☀️  天氣：{weather['desc']} {weather['min']}–{weather['max']}°C")
    stock_html = get_stock_html()
    print(f"📈 股市：{'取得成功' if stock_html else '略過'}")
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
