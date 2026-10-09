#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🐙 ابزار گیت‌هاب: ساخت ریپازیتوری، آپلود فایل (کامیت)، ساخت ورک‌فلو اکشنز، لیست ریپوها
توکن: از متغیر محیطی GITHUB_TOKEN / GH_PAT یا با دکمه «🔑 تنظیم توکن» در چت
      (توکن در state.json روی همین سرور ذخیره می‌شود و در کد/گیت هاردکد نیست.)
"""

import base64
import os
import re
import time

import requests

from botcore import (HOME_KB, WORK_DIR, get_bale_file, kb, sanitize,
                     send_message, state)

GAPI = "https://api.github.com"

GH_KB = kb([
    [{"text": "🆕 ساخت ریپازیتوری", "callback_data": "gh:new"},
     {"text": "📤 آپلود فایل", "callback_data": "gh:up"}],
    [{"text": "⚙️ ساخت ورک‌فلو اکشنز", "callback_data": "gh:act"},
     {"text": "📋 ریپوهای من", "callback_data": "gh:list"}],
    [{"text": "🔑 تنظیم توکن", "callback_data": "gh:tok"},
     {"text": "🏠 منوی اصلی", "callback_data": "menu:home"}],
])

RUN_BOT_YML = """name: Run Bot

on:
  workflow_dispatch:
  schedule:
    - cron: "*/30 * * * *"

concurrency:
  group: bot
  cancel-in-progress: false

jobs:
  run:
    runs-on: ubuntu-latest
    timeout-minutes: 300
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Install deps
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt || true
      - name: Run
        run: python main.py
        env:
          BOT_TOKEN: ${{ secrets.BOT_TOKEN }}
"""


def _tok():
    s = state()
    return (s.get("gh_token") or os.getenv("GITHUB_TOKEN") or os.getenv("GH_PAT") or "").strip()


def _api(method, path, tok=None, **kw):
    tok = tok or _tok()
    r = requests.request(method, GAPI + path,
                         headers={"Authorization": f"Bearer {tok}",
                                  "Accept": "application/vnd.github+json"},
                         timeout=30, **kw)
    try:
        j = r.json()
    except ValueError:
        j = {"message": r.text[:200]}
    if r.status_code >= 300:
        raise RuntimeError(f"GitHub {r.status_code}: {j.get('message', j)}")
    return j


def me():
    return _api("GET", "/user")


def show_menu(chat_id, st):
    tok = _tok()
    who = ""
    if tok:
        try:
            u = me()
            who = f"✅ متصل به حساب: {u.get('login')}"
            if not state().get("gh_token"):
                state()["gh_token"] = tok  # از متغیر محیطی به state هم ببر (برای سرور شخصی)
        except Exception as e:  # noqa: BLE001
            who = f"⚠️ توکن فعلی کار نکرد: {e}"
    else:
        who = "⚠️ هنوز توکن گیت‌هاب تنظیم نشده.\nدکمه «🔑 تنظیم توکن» را بزن (PAT با دسترسی repo)."
    send_message(chat_id, f"🐙 ابزار گیت‌هاب\n{who}\n\nیکی از عملیات‌ها را انتخاب کن:", kb=GH_KB)


def _resolve_repo(chat_id, text):
    t = text.strip().replace(".git", "").replace("https://github.com/", "").strip("/")
    if "/" in t:
        return t
    try:
        login = me()["login"]
        return f"{login}/{t}"
    except Exception as e:  # noqa: BLE001
        send_message(chat_id, f"❌ {e}", kb=GH_KB)
        return None


def callback(chat_id, act, st):
    d = st["data"]
    tok = _tok()
    if act != "tok" and not tok:
        d["action"] = "tok"
        send_message(chat_id, "اول توکن گیت‌هاب (PAT با دسترسی repo) را بفرست:", kb=HOME_KB)
        return
    if act == "tok":
        d["action"] = "tok"
        send_message(chat_id, "توکن PAT گیت‌هاب را بفرست (فقط بین من و تو می‌ماند و در کد ذخیره نمی‌شود):", kb=HOME_KB)
    elif act == "new":
        d["action"] = "new_name"
        send_message(chat_id, "نام ریپوی جدید را بفرست (مثال: my-project):", kb=HOME_KB)
    elif act == "up":
        d["action"] = "up_repo"
        send_message(chat_id, "ریپوی مقصد را بفرست (owner/repo یا فقط نام):", kb=HOME_KB)
    elif act == "act":
        d["action"] = "act_repo"
        send_message(chat_id, "نام ریپویی که ورک‌فلو به آن اضافه شود را بفرست:", kb=HOME_KB)
    elif act == "list":
        try:
            repos = _api("GET", "/user/repos?per_page=15&sort=updated", tok=tok)
            lines = ["📋 ریپوهای اخیر:"]
            for r in repos:
                lines.append(f"• {r['full_name']} {'🔒' if r.get('private') else '🌐'}")
            send_message(chat_id, "\n".join(lines), kb=GH_KB)
        except Exception as e:  # noqa: BLE001
            send_message(chat_id, f"❌ {e}", kb=GH_KB)
    else:
        send_message(chat_id, "؟", kb=GH_KB)


def handle(chat_id, text, st):
    d = st["data"]
    tok = _tok()
    act = d.get("action")
    if act == "tok":
        t = text.strip()
        if not re.match(r"^(ghp_|github_pat_|gho_|ghu_|ghs_)", t):
            send_message(chat_id, "این شکل یک توکن گیت‌هاب نیست (باید با ghp_ یا github_pat_ شروع شود).", kb=GH_KB)
            return
        try:
            u = _api("GET", "/user", tok=t)
        except Exception as e:  # noqa: BLE001
            send_message(chat_id, f"❌ توکن کار نکرد: {e}", kb=GH_KB)
            return
        state()["gh_token"] = t
        from botcore import save_state
        save_state()
        d.pop("action", None)
        send_message(chat_id, f"✅ وصل شد به حساب {u.get('login')}؛ توکن ذخیره شد.", kb=GH_KB)
    elif act == "new_name":
        name = sanitize(text.strip().replace(" ", "-"))
        try:
            r = _api("POST", "/user/repos", tok=tok,
                     json={"name": name, "private": True, "auto_init": True})
            d.pop("action", None)
            send_message(chat_id, f"✅ ریپوی خصوصی ساخته شد:\n{r['html_url']}", kb=GH_KB)
        except Exception as e:  # noqa: BLE001
            send_message(chat_id, f"❌ {e}", kb=GH_KB)
    elif act == "up_repo":
        repo = _resolve_repo(chat_id, text)
        if repo:
            d["action"] = "up_files"
            d["repo"] = repo
            send_message(chat_id,
                         f"📤 حالا فایل‌ها (کد/عکس/سند) را بفرست تا در {repo} کامیت شوند.\n"
                         "پایان آپلود: /done", kb=HOME_KB)
    elif act == "act_repo":
        repo = _resolve_repo(chat_id, text)
        if repo:
            try:
                content = base64.b64encode(RUN_BOT_YML.encode()).decode()
                _api("PUT", f"/repos/{repo}/contents/.github/workflows/run-bot.yml", tok=tok,
                     json={"message": "add Run Bot workflow via bale bot", "content": content})
                d.pop("action", None)
                send_message(chat_id,
                             f"✅ ورک‌فلو «Run Bot» به {repo} اضافه شد:\n"
                             f"https://github.com/{repo}/blob/main/.github/workflows/run-bot.yml\n\n"
                             "فقط یادت باشد BOT_TOKEN را در Settings→Secrets همان ریپو ست کنی.", kb=GH_KB)
            except Exception as e:  # noqa: BLE001
                send_message(chat_id, f"❌ {e}", kb=GH_KB)
    else:
        send_message(chat_id, "از دکمه‌های منوی گیت‌هاب استفاده کن:", kb=GH_KB)


def handle_file(chat_id, msg, st):
    """دریافت سند از چت بله و کامیت آن در ریپوی مقصد."""
    d = st["data"]
    if d.get("action") != "up_files":
        send_message(chat_id, "اول از منوی گیت‌هاب «📤 آپلود فایل» را انتخاب کن.", kb=GH_KB)
        return
    tok = _tok()
    doc = msg.get("document") or {}
    fname = sanitize(doc.get("file_name") or f"file_{int(time.time())}")
    if int(doc.get("file_size") or 0) > 45 * 1024 * 1024:
        send_message(chat_id, "❌ فایل بزرگ‌تر از 45MB قابل کامیت مستقیم نیست.", kb=GH_KB)
        return
    send_message(chat_id, f"⏳ در حال دریافت و کامیت {fname} …")
    path = None
    try:
        path = get_bale_file(doc["file_id"])
        content = base64.b64encode(open(path, "rb").read()).decode()
        _api("PUT", f"/repos/{d['repo']}/contents/{fname}", tok=tok,
             json={"message": f"upload {fname} via bale bot", "content": content})
        send_message(chat_id, f"✅ {fname} در {d['repo']} کامیت شد.\n"
                              f"https://github.com/{d['repo']}/blob/main/{fname}", kb=GH_KB)
    except Exception as e:  # noqa: BLE001
        send_message(chat_id, f"❌ {e}", kb=GH_KB)
    finally:
        if path:
            try:
                os.remove(path)
            except OSError:
                pass
