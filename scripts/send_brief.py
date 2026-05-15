#!/usr/bin/env python3
"""
每日晨報自動發送腳本 v7.0
- 聖言：verbumdeitwn.com 完整導讀（含釋經小幫手、品嚐聖言、活出聖言、全心祈禱）
- 天氣：Open-Meteo（斗六）
- 股市：Yahoo Finance（台股 + 美股）+ 財經要聞
- 行事曆：Google Calendar 私密 iCal（今日、明日、未來一週，CALENDAR_ICAL_URL）
- 信箱待辦：IMAP 近 7 天未讀（🔴今日截止 / 🟡本週待辦）
"""

import html as _html_mod
import io, os, sys, re, smtplib, imaplib
import email as email_lib
from email.header import decode_header as _hdec
from datetime import datetime, timedelta
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

TAIPEI_TZ      = ZoneInfo("Asia/Taipei")
SENDER         = os.getenv("BRIEF_SENDER", "luhueijen@shsh.ylc.edu.tw")
RECIPIENTS     = ["luhueijen@shsh.ylc.edu.tw", "luhueijen@gmail.com"]
PERSONAL_GMAIL = "luhueijen@gmail.com"
WEEKDAYS       = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
WEEKDAY_SHORT  = ["一", "二", "三", "四", "五", "六", "日"]

BOT_HDR = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
}

WMO_ZH = {
    0: "晴天", 1: "大致晴朗", 2: "局部多雲", 3: "陰天",
    45: "起霧", 48: "濃霧",
    51: "輕毛毛雨", 53: "毛毛雨", 55: "重毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    71: "小雪", 73: "中雪", 75: "大雪",
    80: "陣雨（輕）", 81: "陣雨", 82: "大陣雨",
    95: "雷雨", 96: "雷雨帶冰雹", 99: "強雷雨",
}

SECTION_MARKERS = ["釋經小幫手", "品嚐聖言", "活出聖言", "全心祈禱", "聆聽聖言"]

_SKIP_FROM = {
    "noreply", "no-reply", "donotreply", "notification",
    "newsletter", "mailer-daemon", "postmaster", "bounce", "automated",
}

_URGENT_KW = ["截止", "今日截止", "緊急", "urgent", "asap"]


# ── 工具 ─────────────────────────────────────────────────────────────────────

def _esc(text: str) -> str:
    return _html_mod.escape(str(text))


def _decode_hdr(value: str) -> str:
    parts = _hdec(value or "")
    result = ""
    for part, enc in parts:
        if isinstance(part, bytes):
            result += part.decode(enc or "utf-8", errors="replace")
        else:
            result += str(part)
    return result.strip()


# ── 聖言（verbumdeitwn.com）──────────────────────────────────────────────────

def _walk_entry(element, parts: list):
    """遞迴走訪 HTML，收集段落（避免重複）"""
    for el in element.children:
        if not hasattr(el, "name") or not el.name:
            continue
        name = el.name
        if name in ["script", "style", "iframe", "noscript"]:
            continue
        if name in ["h1", "h2", "h3", "h4"]:
            text = el.get_text().strip()
            if text:
                parts.append(("heading", text))
        elif name == "blockquote":
            text = el.get_text("\n").strip()
            if text:
                parts.append(("quote", text))
        elif name == "p":
            text = el.get_text().strip()
            if text:
                parts.append(("para", text))
        elif name in ["div", "section", "article"]:
            _walk_entry(el, parts)


def _parts_to_html(parts: list) -> str:
    html_blocks = []
    for kind, text in parts:
        safe = _esc(text)
        if kind == "heading" or any(m in text for m in SECTION_MARKERS):
            html_blocks.append(
                f"<p style='font-weight:bold;color:#6c3483;margin:14px 0 4px;"
                f"font-size:15px;border-bottom:1px solid #e8d5f5;"
                f"padding-bottom:4px;'>{safe}</p>"
            )
        elif kind == "quote":
            html_blocks.append(
                f"<blockquote style='border-left:3px solid #a569bd;margin:8px 0;"
                f"padding:6px 14px;background:#f9f5ff;color:#555;"
                f"line-height:1.8;'>{safe.replace(chr(10), '<br>')}</blockquote>"
            )
        else:
            html_blocks.append(
                f"<p style='margin:6px 0;line-height:1.8;'>{safe}</p>"
            )
    return "\n".join(html_blocks)


def fetch_verbum_dei(today: datetime) -> str:
    """從 verbumdeitwn.com 取得今日聖言全文，回傳 email 用 HTML"""
    yesterday    = today - timedelta(days=1)
    today_prefix = today.strftime("%m-%d")
    article_url  = None

    for attempt in [yesterday, today]:
        listing_url = attempt.strftime("https://verbumdeitwn.com/%Y/%m/%d/")
        try:
            r = requests.get(listing_url, headers=BOT_HDR, timeout=15)
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if today_prefix in href:
                    article_url = href
                    break
        except Exception as e:
            print(f"[WARN] verbumdei listing: {e}", file=sys.stderr)
        if article_url:
            break

    if not article_url:
        return ("<p style='color:#aaa;'>（今日聖言無法自動取得，"
                "請手動查閱 verbumdeitwn.com）</p>")

    try:
        r = requests.get(article_url, headers=BOT_HDR, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        content = (soup.find("div", class_="entry-content") or
                   soup.find("div", class_="post-content") or
                   soup.find("article"))
        if not content:
            return "<p style='color:#aaa;'>（今日聖言解析失敗）</p>"

        for el in content.find_all(class_=["sharedaddy", "jp-relatedposts",
                                            "wpcnt", "sd-block"]):
            el.decompose()

        parts = []
        _walk_entry(content, parts)
        result = _parts_to_html(parts)
        return result if result else "<p style='color:#aaa;'>（內容為空）</p>"

    except Exception as e:
        print(f"[WARN] verbumdei article: {e}", file=sys.stderr)
        return "<p style='color:#aaa;'>（今日聖言無法取得）</p>"


# ── 天氣（Open-Meteo，斗六）─────────────────────────────────────────────────

def get_weather() -> dict:
    try:
        url = (
            "https://api.open-meteo.com/v1/forecast"
            "?latitude=23.71&longitude=120.54"
            "&daily=temperature_2m_max,temperature_2m_min"
            ",precipitation_sum,precipitation_probability_max,weathercode"
            "&timezone=Asia%2FTaipei&forecast_days=1"
        )
        d        = requests.get(url, timeout=12).json()["daily"]
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
        print(f"[WARN] 天氣: {e}", file=sys.stderr)
        return {"desc": "資料無法取得", "min": "--", "max": "--",
                "rain": "--", "rain_pct": "--", "clothing": "請手動確認"}


# ── 股市 + 財經要聞（yfinance）──────────────────────────────────────────────

def get_stock_data() -> tuple:
    """回傳 (stock_rows_html, news_titles_list)"""
    try:
        import yfinance as yf
    except ImportError:
        print("[WARN] yfinance 未安裝", file=sys.stderr)
        return "", []

    indices = {
        "^TWII":   "台股加權",
        "0050.TW": "元大台灣50",
        "^DJI":    "道瓊",
        "^GSPC":   "S&P 500",
        "^IXIC":   "那斯達克",
    }
    rows = ""
    for ticker_sym, name in indices.items():
        try:
            df = yf.Ticker(ticker_sym).history(period="2d")
            if len(df) >= 2:
                prev, close = df["Close"].iloc[-2], df["Close"].iloc[-1]
                chg   = close - prev
                pct   = chg / prev * 100
                arrow = "▲" if chg >= 0 else "▼"
                color = "#27ae60" if chg >= 0 else "#e74c3c"
                rows += (
                    f"<tr><td style='padding:7px 10px;'>{name}</td>"
                    f"<td style='padding:7px 10px;text-align:right;'>{close:,.2f}</td>"
                    f"<td style='padding:7px 10px;text-align:right;color:{color};'>"
                    f"{arrow} {abs(chg):,.2f}</td>"
                    f"<td style='padding:7px 10px;text-align:right;color:{color};"
                    f"font-weight:bold;'>{pct:+.2f}%</td></tr>"
                )
            else:
                rows += (f"<tr><td colspan='4' style='padding:7px 10px;color:#aaa;'>"
                         f"{name}：資料不足</td></tr>")
        except Exception as e:
            print(f"[WARN] 股市 {name}: {e}", file=sys.stderr)

    # 財經要聞
    news = []
    for sym in ["^TWII", "^GSPC", "^DJI"]:
        try:
            raw = yf.Ticker(sym).news or []
            for item in raw:
                title = ""
                if isinstance(item, dict):
                    title = (item.get("title") or
                             (item.get("content") or {}).get("title", "")
                             if isinstance(item.get("content"), dict) else "")
                if title and title not in news:
                    news.append(title)
                if len(news) >= 5:
                    break
        except Exception as e:
            print(f"[WARN] 財經新聞 {sym}: {e}", file=sys.stderr)
        if len(news) >= 3:
            break

    return rows, news[:3]


# ── 行事曆（Google Calendar 私密 iCal）──────────────────────────────────────

def get_calendar_ical(ical_url: str, start: datetime, days: int = 8) -> dict:
    """
    取得 start 起 days 天的行程。
    回傳 {"YYYY-MM-DD": [{time, summary, location}]}
    """
    try:
        import icalendar
        import recurring_ical_events
    except ImportError:
        print("[WARN] 請安裝 icalendar recurring-ical-events", file=sys.stderr)
        return {}
    try:
        r = requests.get(ical_url, timeout=20)
        r.raise_for_status()
        cal    = icalendar.Calendar.from_ical(r.content)
        result = {}
        for i in range(days):
            tgt = start + timedelta(days=i)
            s   = datetime(tgt.year, tgt.month, tgt.day, 0, 0, 0, tzinfo=TAIPEI_TZ)
            e   = datetime(tgt.year, tgt.month, tgt.day, 23, 59, 59, tzinfo=TAIPEI_TZ)
            evs = recurring_ical_events.of(cal).between(s, e)
            day = []
            for ev in evs:
                dtstart = ev.get("DTSTART").dt
                if isinstance(dtstart, datetime):
                    if dtstart.tzinfo:
                        dtstart = dtstart.astimezone(TAIPEI_TZ)
                    tstr = dtstart.strftime("%H:%M")
                else:
                    tstr = "全天"
                day.append({
                    "time":     tstr,
                    "summary":  str(ev.get("SUMMARY", "")),
                    "location": str(ev.get("LOCATION", "")),
                })
            day.sort(key=lambda x: ("" if x["time"] == "全天" else x["time"]))
            result[tgt.strftime("%Y-%m-%d")] = day
        return result
    except Exception as e:
        print(f"[WARN] iCal: {e}", file=sys.stderr)
        return {}


# ── 信箱待辦（IMAP）─────────────────────────────────────────────────────────

def _imap_fetch(account: str, password: str, since: str, limit: int = 30) -> list:
    items = []
    with imaplib.IMAP4_SSL("imap.gmail.com", 993) as imap:
        imap.login(account, password)
        imap.select("INBOX", readonly=True)
        _, ids = imap.search(None, f"UNSEEN SINCE {since}")
        for mid in ids[0].split()[-limit:]:
            try:
                _, data = imap.fetch(mid, "(RFC822.HEADER)")
                if not data or not data[0]:
                    continue
                msg     = email_lib.message_from_bytes(data[0][1])
                subject = _decode_hdr(msg.get("Subject", "(無主旨)"))
                from_s  = _decode_hdr(msg.get("From", ""))
                if any(s in from_s.lower() for s in _SKIP_FROM):
                    continue
                items.append({"subject": subject, "from": from_s, "account": account})
            except Exception as ex:
                print(f"[WARN] 解析郵件: {ex}", file=sys.stderr)
    return items


def get_inbox_todos(sender: str, password: str, today: datetime,
                    personal_pw: str = "") -> list:
    today_mmdd = today.strftime("%m/%d")
    today_iso  = today.strftime("%Y-%m-%d")
    since      = (today - timedelta(days=7)).strftime("%d-%b-%Y")
    raw        = []

    try:
        raw += _imap_fetch(sender, password, since)
        print(f"📬 學校信箱：{len(raw)} 封未讀")
    except Exception as e:
        print(f"[WARN] IMAP 學校: {e}", file=sys.stderr)

    if personal_pw:
        try:
            pi = _imap_fetch(PERSONAL_GMAIL, personal_pw, since)
            raw += pi
            print(f"📬 個人 Gmail：{len(pi)} 封未讀")
        except Exception as e:
            print(f"[WARN] IMAP 個人: {e}", file=sys.stderr)

    urgent_kw = _URGENT_KW + [today_mmdd, today_iso]
    todos = []
    for item in raw:
        combined = (item["subject"] + " " + item["from"]).lower()
        item["priority"] = "🔴" if any(k.lower() in combined for k in urgent_kw) else "🟡"
        todos.append(item)

    todos.sort(key=lambda t: (0 if t["priority"] == "🔴" else 1))
    return todos


# ── HTML 子元件 ──────────────────────────────────────────────────────────────

def _cal_table(events: list) -> str:
    if not events:
        return "<p style='color:#aaa;font-size:13px;margin:4px 0;'>（無行程）</p>"
    rows = ""
    for ev in events:
        rows += (
            "<tr>"
            f"<td style='padding:6px 10px;white-space:nowrap;font-weight:bold;"
            f"color:#2980b9;'>{_esc(ev['time'])}</td>"
            f"<td style='padding:6px 10px;'>{_esc(ev['summary'])}</td>"
            f"<td style='padding:6px 10px;font-size:12px;color:#888;'>"
            f"{_esc(ev.get('location',''))}</td></tr>"
        )
    return (
        "<table border='1' cellpadding='0' cellspacing='0'"
        " style='border-collapse:collapse;width:100%;font-size:14px;"
        "border-color:#ddd;margin-bottom:8px;'>"
        "<tr style='background:#ecf0f1;'>"
        "<td style='padding:6px 10px;font-weight:bold;'>時間</td>"
        "<td style='padding:6px 10px;font-weight:bold;'>行程</td>"
        "<td style='padding:6px 10px;font-weight:bold;'>地點</td></tr>"
        f"{rows}</table>"
    )


def _future_table(calendar: dict, today: datetime) -> str:
    rows = ""
    for i in range(2, 8):
        tgt    = today + timedelta(days=i)
        dkey   = tgt.strftime("%Y-%m-%d")
        mmdd   = tgt.strftime("%m/%d")
        wd_s   = WEEKDAY_SHORT[tgt.weekday()]
        for ev in calendar.get(dkey, []):
            rows += (
                "<tr>"
                f"<td style='padding:6px 10px;white-space:nowrap;'>"
                f"{mmdd}（{wd_s}）</td>"
                f"<td style='padding:6px 10px;white-space:nowrap;"
                f"color:#2980b9;font-weight:bold;'>{_esc(ev['time'])}</td>"
                f"<td style='padding:6px 10px;'>{_esc(ev['summary'])}</td>"
                f"<td style='padding:6px 10px;font-size:12px;color:#888;'>"
                f"{_esc(ev.get('location',''))}</td></tr>"
            )
    if not rows:
        return "<p style='color:#aaa;font-size:13px;'>（未來一週無特別行程）</p>"
    return (
        "<table border='1' cellpadding='0' cellspacing='0'"
        " style='border-collapse:collapse;width:100%;font-size:14px;border-color:#ddd;'>"
        "<tr style='background:#ecf0f1;'>"
        "<td style='padding:6px 10px;font-weight:bold;'>日期</td>"
        "<td style='padding:6px 10px;font-weight:bold;'>時間</td>"
        "<td style='padding:6px 10px;font-weight:bold;'>行程</td>"
        "<td style='padding:6px 10px;font-weight:bold;'>地點</td></tr>"
        f"{rows}</table>"
    )


def _todo_items_html(items: list) -> str:
    if not items:
        return "<p style='color:#aaa;font-size:13px;margin:4px 0;'>（無）</p>"
    li = "".join(
        f"<li style='margin:4px 0;line-height:1.6;'>{_esc(t['subject'])}"
        f"<span style='font-size:12px;color:#aaa;'> — {_esc(t['from'])}</span></li>"
        for t in items
    )
    return f"<ul style='margin:4px 0;padding-left:22px;'>{li}</ul>"


def _inbox_html(todos: list) -> str:
    if not todos:
        return "<p style='color:#aaa;font-size:13px;'>（無待辦郵件）</p>"
    red = [t for t in todos if t["priority"] == "🔴"]
    yel = [t for t in todos if t["priority"] == "🟡"]

    def grp(items, label, color):
        if not items:
            return ""
        s = (f"<p style='font-weight:bold;color:{color};"
             f"margin:12px 0 4px;'>{label}</p>")
        for t in items:
            s += (
                f"<div style='margin:4px 0;padding:8px 12px;background:#fafafa;"
                f"border-left:4px solid {color};border-radius:0 6px 6px 0;'>"
                f"<div style='font-size:12px;color:#888;'>{_esc(t['from'])}</div>"
                f"<div style='font-weight:bold;margin-top:2px;'>"
                f"{_esc(t['subject'])}</div></div>"
            )
        return s

    return grp(red, "🔴 今日截止 / 緊急", "#e74c3c") + grp(yel, "🟡 本週待辦", "#f39c12")


# ── 主 HTML ─────────────────────────────────────────────────────────────────

def build_html(today: datetime, verbum_html: str, weather: dict,
               stock_rows: str, news: list, calendar: dict, todos: list) -> str:
    wd      = WEEKDAYS[today.weekday()]
    wd_s    = WEEKDAY_SHORT[today.weekday()]
    dstr    = today.strftime(f"%Y年%m月%d日（{wd}）")
    tmrw    = today + timedelta(days=1)
    tmrw_s  = WEEKDAY_SHORT[tmrw.weekday()]

    red_todos = [t for t in todos if t["priority"] == "🔴"]
    yel_todos = [t for t in todos if t["priority"] == "🟡"]

    if calendar:
        today_str = today.strftime("%Y-%m-%d")
        tmrw_str  = tmrw.strftime("%Y-%m-%d")
        cal_html  = (
            f"<p style='font-weight:bold;color:#27ae60;margin:10px 0 5px;'>"
            f"今日 {today.strftime('%m/%d')}（{wd_s}）</p>"
            + _cal_table(calendar.get(today_str, []))
            + f"<p style='font-weight:bold;color:#2980b9;margin:14px 0 5px;'>"
            f"明日 {tmrw.strftime('%m/%d')}（{tmrw_s}）</p>"
            + _cal_table(calendar.get(tmrw_str, []))
        )
        future_html = _future_table(calendar, today)
    else:
        cal_html = future_html = (
            "<p style='color:#aaa;font-size:13px;'>"
            "（行事曆未設定，請新增 CALENDAR_ICAL_URL Secret）</p>"
        )

    stock_table = (
        "<table border='1' cellpadding='0' cellspacing='0'"
        " style='border-collapse:collapse;width:100%;font-size:14px;border-color:#ddd;'>"
        "<tr style='background:#ecf0f1;font-weight:bold;'>"
        "<td style='padding:7px 10px;'>指數</td>"
        "<td style='padding:7px 10px;text-align:right;'>最新</td>"
        "<td style='padding:7px 10px;text-align:right;'>漲跌</td>"
        "<td style='padding:7px 10px;text-align:right;'>漲跌幅</td>"
        f"</tr>{stock_rows}</table>"
        "<p style='font-size:11px;color:#bbb;margin-top:4px;'>來源：Yahoo Finance</p>"
    ) if stock_rows else "<p style='color:#aaa;font-size:13px;'>（股市資料無法取得）</p>"

    news_html = (
        "<ol style='margin:6px 0;padding-left:22px;line-height:1.8;'>"
        + "".join(f"<li>{_esc(n)}</li>" for n in news)
        + "</ol>"
    ) if news else "<p style='color:#aaa;font-size:13px;'>（財經要聞無法取得）</p>"

    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{{font-family:'Noto Sans TC',Arial,sans-serif;max-width:680px;margin:auto;
     color:#2c3e50;padding:16px;line-height:1.7;}}
h1{{background:#1a252f;color:#fff;padding:18px 22px;border-radius:8px;
    margin:0 0 20px;font-size:20px;}}
h2{{margin-top:28px;padding-bottom:6px;border-bottom:2px solid #eee;font-size:16px;}}
</style>
</head>
<body>
<h1>🌅 每日晨報 — {dstr}</h1>

<h2 style="color:#7d3c98;">✝️ 今日聖言</h2>
<div style="background:#faf6ff;padding:12px 18px;border-radius:8px;
            border:1px solid #e8d5f5;">
{verbum_html}
<p style="font-size:11px;color:#bbb;margin-top:10px;text-align:right;">
  來源：主言傳教會台灣 verbumdeitwn.com</p>
</div>

<h2 style="color:#e74c3c;">🔴 今日必辦</h2>
{_todo_items_html(red_todos)}

<h2 style="color:#f39c12;">🟡 本週待辦</h2>
{_todo_items_html(yel_todos)}

<h2 style="color:#e67e22;">☀️ 今日天氣（斗六）</h2>
<div style="background:#fff8f0;border-left:4px solid #e67e22;
            padding:14px 18px;border-radius:0 8px 8px 0;">
<p style="margin:4px 0;">🌡️ <b>氣溫</b>：{weather["min"]}–{weather["max"]}°C</p>
<p style="margin:4px 0;">🌦️ <b>天氣</b>：{weather["desc"]}</p>
<p style="margin:4px 0;">☔ <b>降雨</b>：{weather["rain"]}（機率 {weather["rain_pct"]}%）</p>
<p style="margin:4px 0;">👕 <b>穿著建議</b>：{weather["clothing"]}</p>
</div>

<h2 style="color:#27ae60;">📅 今日 &amp; 明日行程</h2>
{cal_html}

<h2 style="color:#2980b9;">📅 未來一週重要行程</h2>
{future_html}

<h2 style="color:#c0392b;">📬 信箱待辦（近 7 天）</h2>
{_inbox_html(todos)}

<h2 style="color:#2980b9;">📈 金融資訊</h2>
{stock_table}
<p style="font-weight:bold;font-size:14px;color:#2980b9;margin:16px 0 4px;">
  本週國際財經要聞</p>
{news_html}

<hr style="margin:28px 0;border:none;border-top:1px solid #eee;">
<p style="color:#ccc;font-size:11px;text-align:center;">
  GitHub Actions 自動寄送｜{today.strftime('%Y-%m-%d %H:%M')} (Asia/Taipei)
</p>
</body>
</html>"""


# ── 主程式 ───────────────────────────────────────────────────────────────────

def main():
    pw = os.getenv("GMAIL_APP_PASSWORD", "").strip()
    if not pw:
        print("❌ 請設定 GMAIL_APP_PASSWORD", file=sys.stderr)
        sys.exit(1)

    today = datetime.now(TAIPEI_TZ)
    wd    = WEEKDAYS[today.weekday()]
    print(f"📅 今日：{today.strftime('%Y-%m-%d %H:%M')} (Asia/Taipei) {wd}")

    verbum_html = fetch_verbum_dei(today)
    print(f"✝️  聖言：{'取得成功' if len(verbum_html) > 100 else '備援/失敗'}")

    weather = get_weather()
    print(f"☀️  天氣：{weather['desc']} {weather['min']}–{weather['max']}°C")

    stock_rows, news = get_stock_data()
    print(f"📈 股市：{'OK' if stock_rows else '略過'}｜財經要聞 {len(news)} 則")

    ical_url = os.getenv("CALENDAR_ICAL_URL", "").strip()
    calendar = {}
    if ical_url:
        calendar  = get_calendar_ical(ical_url, today, days=8)
        today_cnt = len(calendar.get(today.strftime("%Y-%m-%d"), []))
        tmrw_cnt  = len(calendar.get((today + timedelta(1)).strftime("%Y-%m-%d"), []))
        print(f"📅 行事曆：今日 {today_cnt}，明日 {tmrw_cnt}，未來 8 天已取得")
    else:
        print("📅 行事曆：未設定 CALENDAR_ICAL_URL")

    personal_pw = os.getenv("GMAIL_PERSONAL_APP_PASSWORD", "").strip()
    todos       = get_inbox_todos(SENDER, pw, today, personal_pw)
    print(f"📬 信箱待辦：{len(todos)} 封")

    html    = build_html(today, verbum_html, weather, stock_rows, news, calendar, todos)
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
