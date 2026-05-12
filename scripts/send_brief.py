#!/usr/bin/env python3
"""
寄送每日晨報至指定信箱
用法：
  $env:GMAIL_APP_PASSWORD="xxxx xxxx xxxx xxxx"
  python scripts/send_brief.py
"""

import io
import os
import smtplib
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

# 強制 stdout 使用 UTF-8，避免 Windows CP950 編碼錯誤
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

SENDER    = "luhueijen@gmail.com"
RECIPIENT = "luhueijen@gmail.com"
TODO_PATH = Path("~/todo.md").expanduser()

WEEKDAYS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]

def build_html() -> str:
    today = datetime.now(ZoneInfo("Asia/Taipei"))
    wd    = WEEKDAYS[today.weekday()]
    dstr  = today.strftime(f"%Y年%m月%d日（{wd}）")

    todo_content = ""
    if TODO_PATH.exists():
        raw = TODO_PATH.read_text(encoding="utf-8")
        # 將 Markdown 轉成簡單 HTML（保留換行與區塊）
        todo_content = (
            raw.replace("&", "&amp;")
               .replace("<", "&lt;")
               .replace(">", "&gt;")
               .replace("\n", "<br>")
        )

    return f"""<html>
<head><meta charset="utf-8"></head>
<body style="font-family:'Noto Sans TC',sans-serif;max-width:700px;margin:auto;color:#2c3e50;">
<h1 style="background:#2c3e50;color:#fff;padding:16px;border-radius:6px;">
  🌅 每日晨報 — {dstr}
</h1>
<div style="background:#f9f9f9;border-left:4px solid #3498db;padding:16px;
            border-radius:0 6px 6px 0;font-size:15px;line-height:1.8;">
{todo_content}
</div>
<hr style="margin-top:24px;">
<p style="color:#bdc3c7;font-size:11px;text-align:center;">
  由 Claude Code /daily-brief 自動寄送｜{today.strftime("%Y-%m-%d %H:%M")}
</p>
</body></html>"""


def send(password: str):
    today   = datetime.now(ZoneInfo("Asia/Taipei"))
    wd      = WEEKDAYS[today.weekday()]
    subject = f"🌅 每日晨報 — {today.strftime('%Y-%m-%d')}（{wd}）"

    msg = MIMEMultipart("alternative")
    msg["From"]    = SENDER
    msg["To"]      = RECIPIENT
    msg["Subject"] = subject
    msg.attach(MIMEText(build_html(), "html", "utf-8"))

    print(f"正在連接 Gmail SMTP…")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(SENDER, password)
        smtp.sendmail(SENDER, RECIPIENT, msg.as_bytes())
    print(f"✅ 已寄出：{subject}")
    print(f"   收件人：{RECIPIENT}")


if __name__ == "__main__":
    pw = os.getenv("GMAIL_APP_PASSWORD", "").strip()
    if not pw:
        print("❌ 請先設定環境變數 GMAIL_APP_PASSWORD")
        print()
        print("如何取得 Gmail 應用程式密碼：")
        print("  1. 前往 https://myaccount.google.com/apppasswords")
        print("  2. 選擇「其他」，名稱輸入 daily-brief")
        print("  3. 複製產生的 16 碼密碼（格式：xxxx xxxx xxxx xxxx）")
        print()
        print("設定後執行：")
        print('  $env:GMAIL_APP_PASSWORD="xxxx xxxx xxxx xxxx"')
        print("  python scripts/send_brief.py")
        sys.exit(1)

    send(pw)
