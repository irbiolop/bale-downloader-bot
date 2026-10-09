#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
✈️ ابزار اکانت تلگرام با پایروگرام:
  • ورود تعاملی از داخل چت بله (شماره → کد → رمز دوم‌مرحله‌ای) یا Session String
  • 📥 دانلود مدیا از لینک پیام تلگرام (t.me/...)
  • 📡 دریافت پیام‌های جدید کانال‌ها به‌صورت زنده → فورward به چت بله (مدیا هم دانلود و ارسال می‌شود)
  • 🔍 جستجوی پیام در یک چت/کانال
سشن و api_id/api_hash از state.json یا متغیر محیطی TG_SESSION / TG_API_ID / TG_API_HASH خوانده می‌شود.
"""

import asyncio
import os
import re
import threading
import time

from botcore import (CHUNK, HOME_KB, OWNER_ID, WORK_DIR, human, kb, sanitize,
                     send_document, send_message, send_photo, split_file,
                     save_state, state)

BR = {"loop": None, "client": None, "lock": threading.Lock()}
LOGIN = None  # {"app","loop","phone","hash"} حین ورود تعاملی
STATUS = {"running": False}


def _creds():
    s = state().get("tg", {})
    try:
        api_id = int(os.getenv("TG_API_ID") or s.get("api_id") or 0)
    except ValueError:
        api_id = 0
    api_hash = os.getenv("TG_API_HASH") or s.get("api_hash") or ""
    return api_id, api_hash


def _session():
    return state().get("tg", {}).get("session") or os.getenv("TG_SESSION") or ""


def _kb_conn():
    return kb([
        [{"text": "📥 دانلود از لینک تلگرام", "callback_data": "tg:dl"},
         {"text": "📡 دریافت پیام‌های کانال", "callback_data": "tg:sub"}],
        [{"text": "🔍 جستجو در چت", "callback_data": "tg:search"},
         {"text": "ℹ️ وضعیت اتصال", "callback_data": "tg:status"}],
        [{"text": "🔌 خروج از اکانت", "callback_data": "tg:logout"},
         {"text": "🏠 منوی اصلی", "callback_data": "menu:home"}],
    ])


TG_KB_LOGIN = kb([
    [{"text": "🔗 ورود به اکانت تلگرام", "callback_data": "tg:login"}],
    [{"text": "🏠 منوی اصلی", "callback_data": "menu:home"}],
])


# ---------------- پل asyncio/پایروگرام ----------------
def _ensure_client(session, api_id, api_hash):
    """کلاینت دائمی تلگرام در یک ترد جدا با event loop خودش."""
    with BR["lock"]:
        if BR["client"]:
            return BR["client"]
        from pyrogram import Client
        loop = asyncio.new_event_loop()
        app = Client("tgacc", api_id=api_id, api_hash=api_hash,
                     session_string=session, in_memory=True)

        def _run():
            asyncio.set_event_loop(loop)
            from pyrogram import filters
            from pyrogram.handlers import MessageHandler

            async def _relay(cli, msg):
                target = state()["tg"]["channels"].get(str(msg.chat.id))
                if not target:
                    return
                try:
                    who = msg.chat.title or msg.chat.username or "تلگرام"
                    if msg.media:
                        path = await cli.download_media(msg)
                        if path:
                            if os.path.getsize(path) > CHUNK:
                                parts = split_file(path)
                                for i, p in enumerate(parts, 1):
                                    send_document(target, p, caption=f"📨 {who}\nپارت {i}/{len(parts)}")
                                    time.sleep(1.0)
                                for p in parts:
                                    try:
                                        os.remove(p)
                                    except OSError:
                                        pass
                            elif msg.photo:
                                send_photo(target, path, caption=f"📨 {who}")
                            else:
                                send_document(target, path, caption=f"📨 {who}")
                            try:
                                os.remove(path)
                            except OSError:
                                pass
                    elif msg.text:
                        send_message(target, f"📨 از «{who}»:\n{msg.text[:3000]}")
                except Exception as e:  # noqa: BLE001
                    print("tg relay error:", e)

            def _on_msg(cli, msg):
                try:
                    if msg.chat and str(msg.chat.id) in state()["tg"]["channels"]:
                        asyncio.ensure_future(_relay(cli, msg))
                except Exception as e:  # noqa: BLE001
                    print("tg handler error:", e)

            app.add_handler(MessageHandler(_on_msg), group=0)
            loop.run_until_complete(app.start())
            STATUS["running"] = True
            print("✅ pyrogram client started")
            loop.run_forever()

        threading.Thread(target=_run, daemon=True).start()
        BR.update(loop=loop, client=app)
    return app


def run_coro(coro, timeout=900):
    if not BR["loop"] or not BR["client"]:
        raise RuntimeError("کلاینت تلگرام روشن نیست؛ اول وارد اکانت شو.")
    return asyncio.run_coroutine_threadsafe(coro, BR["loop"]).result(timeout)


def init():
    """در استارت ربات: اگر سشن داریم، خودکار وصل شو."""
    sess = _session()
    api_id, api_hash = _creds()
    if sess and api_id and api_hash:
        try:
            _ensure_client(sess, api_id, api_hash)
        except Exception as e:  # noqa: BLE001
            print("tg init error:", e)


# ---------------- ورود تعاملی ----------------
def _login_client():
    from pyrogram import Client
    api_id, api_hash = _creds()
    loop = asyncio.new_event_loop()
    app = Client(":memory:", api_id=api_id, api_hash=api_hash, in_memory=True)

    def _run():
        asyncio.set_event_loop(loop)
        loop.run_forever()

    loop.run_until_complete(app.connect())
    threading.Thread(target=_run, daemon=True).start()
    return app, loop


def lr(coro, timeout=120):
    return asyncio.run_coroutine_threadsafe(coro, LOGIN["loop"]).result(timeout)


def _finish_login(chat_id, st):
    global LOGIN
    try:
        s = lr(LOGIN["app"].export_session_string())
        state()["tg"]["session"] = s
        save_state()
        try:
            lr(LOGIN["app"].disconnect(), 30)
        except Exception:  # noqa: BLE001
            pass
        api_id, api_hash = _creds()
        _ensure_client(s, api_id, api_hash)
        me = run_coro(BR["client"].get_me(), 60)
        LOGIN = None
        st["data"].pop("action", None)
        save_state()
        send_message(chat_id,
                     f"✅ وارد شدی! اکانت: {getattr(me, 'first_name', '')} (@{getattr(me, 'username', '')})\n"
                     "سشن ذخیره شد و از این به بعد خودکار وصل می‌شود.",
                     kb=_kb_conn())
    except Exception as e:  # noqa: BLE001
        send_message(chat_id, f"❌ خطا در پایان ورود: {e}", kb=TG_KB_LOGIN)


def login_start(chat_id, st):
    api_id, api_hash = _creds()
    if not (api_id and api_hash):
        st["data"]["action"] = "creds"
        send_message(chat_id,
                     "برای ورود به تلگرام به api_id و api_hash نیاز است.\n"
                     "۱) برو به my.telegram.org → API development tools\n"
                     "۲) یک اپ بساز و مقادیرش را به شکل زیر بفرست:\n\n"
                     "<code>api_id api_hash</code>\n\n"
                     "مثال: <code>123456 abcd1234abcd...</code>", kb=HOME_KB)
        return
    st["data"]["action"] = "phone"
    send_message(chat_id, "📱 شماره تلفن اکانت تلگرام را با کد کشور بفرست:\nمثال: <code>+989121234567</code>", kb=HOME_KB)


def show_menu(chat_id, st):
    sess = _session()
    if sess and BR["client"]:
        send_message(chat_id, "✈️ اکانت تلگرام وصل است ✅\n\nابزارها را انتخاب کن:", kb=_kb_conn())
    elif sess:
        init()
        if BR["client"]:
            send_message(chat_id, "✈️ اکانت وصل شد ✅", kb=_kb_conn())
        else:
            send_message(chat_id, "سشن ذخیره شده ولی اتصال برقرار نشد؛ دوباره تلاش می‌کنم.", kb=TG_KB_LOGIN)
    else:
        send_message(chat_id,
                     "✈️ اتصال اکانت تلگرام (پایروگرام)\n\n"
                     "امکانات:\n"
                     "• 📥 دانلود مدیا از لینک پیام‌های تلگرام\n"
                     "• 📡 دریافت زنده‌ی پیام‌ها و مدیای کانال‌ها → همین‌جا در بله\n"
                     "• 🔍 جستجوی پیام در چت‌ها\n\n"
                     "برای شروع، دکمه‌ی ورود را بزن.", kb=TG_KB_LOGIN)


def callback(chat_id, act, st):
    d = st["data"]
    if act == "login" or act == "creds":
        d["action"] = "creds" if act == "creds" else ""
        login_start(chat_id, st)
        return
    if not BR["client"]:
        send_message(chat_id, "اول وارد اکانت شو.", kb=TG_KB_LOGIN)
        return
    if act == "dl":
        d["action"] = "dl"
        send_message(chat_id, "لینک پیام تلگرام را بفرست:\n• عمومی: https://t.me/channel/123\n• خصوصی: https://t.me/c/1234567/89", kb=HOME_KB)
    elif act == "sub":
        d["action"] = "sub"
        send_message(chat_id, "آیدی کانال را بفرست (مثال: @durov)\nپیام‌های جدید همان لحظه به این چت می‌آید:", kb=HOME_KB)
    elif act == "search":
        d["action"] = "search"
        send_message(chat_id, "به شکل زیر بفرست:\n<code>@channel عبارت جستجو</code>", kb=HOME_KB)
    elif act == "status":
        try:
            me = run_coro(BR["client"].get_me(), 60)
            chans = state()["tg"]["channels"]
            send_message(chat_id,
                         f"ℹ️ اکانت: {getattr(me, 'first_name', '—')} (@{getattr(me, 'username', '—')})\n"
                         f"📡 کانال‌های فعال: {len(chans)}\n"
                         + ("\n".join(f"• @{v}" if False else f"• {k}" for k, v in chans.items()) if chans else ""),
                         kb=_kb_conn())
        except Exception as e:  # noqa: BLE001
            send_message(chat_id, f"❌ {e}", kb=_kb_conn())
    elif act == "logout":
        try:
            run_coro(BR["client"].disconnect(), 30)
        except Exception:  # noqa: BLE001
            pass
        BR["client"] = None
        BR["loop"] = None
        STATUS["running"] = False
        state()["tg"]["session"] = ""
        state()["tg"]["channels"] = {}
        save_state()
        send_message(chat_id, "🔌 از اکانت خارج شدی و سشن پاک شد.", kb=TG_KB_LOGIN)
    else:
        send_message(chat_id, "؟", kb=_kb_conn())


def handle(chat_id, text, st):
    d = st["data"]
    act = d.get("action")
    if act == "creds":
        parts = text.split()
        if len(parts) < 2:
            send_message(chat_id, "فرمت درست: <code>api_id api_hash</code>", kb=HOME_KB)
            return
        s = state()["tg"]
        s["api_id"], s["api_hash"] = parts[0].strip(), parts[1].strip()
        save_state()
        d.pop("action", None)
        login_start(chat_id, st)
        return
    if act == "phone":
        phone = text.strip().replace(" ", "").replace("-", "")
        if not re.match(r"^\+?\d{8,15}$", phone):
            send_message(chat_id, "شماره معتبر نیست؛ با کد کشور بفرست: +98912…", kb=HOME_KB)
            return
        global LOGIN
        LOGIN = {"app": None, "loop": None}
        app, loop = _login_client()
        LOGIN.update(app=app, loop=loop)
        sent = lr(app.send_code(phone), 90)
        LOGIN.update(phone=phone, hash=sent.phone_code_hash)
        d["action"] = "code"
        send_message(chat_id, "🔑 کد ورود را بفرست (کدی که در تلگرام دریافت کردی):", kb=HOME_KB)
        return
    if act == "code":
        if not LOGIN:
            send_message(chat_id, "ابتدا شماره را بفرست (دوباره از منو شروع کن).", kb=HOME_KB)
            return
        try:
            lr(LOGIN["app"].sign_in(LOGIN["phone"], LOGIN["hash"], text.strip()), 90)
            _finish_login(chat_id, st)
        except Exception as e:  # noqa: BLE001
            if "PASSWORD" in str(e).upper() or "SessionPassword" in str(e):
                d["action"] = "pass"
                send_message(chat_id, "🔐 رمز دوم‌مرحله‌ای (2FA) را بفرست:", kb=HOME_KB)
            else:
                send_message(chat_id, f"❌ کد پذیرفته نشد: {e}\nدوباره بفرست یا /menu بزن.", kb=HOME_KB)
        return
    if act == "pass":
        if not LOGIN:
            send_message(chat_id, "ابتدا از منو شروع کن.", kb=HOME_KB)
            return
        try:
            lr(LOGIN["app"].check_password(text.strip()), 90)
            _finish_login(chat_id, st)
        except Exception as e:  # noqa: BLE001
            send_message(chat_id, f"❌ رمز اشتباه: {e}", kb=HOME_KB)
        return
    if act == "dl":
        m = re.search(r"https?://t\.me/\S+", text)
        if not m:
            send_message(chat_id, "لینک پیام تلگرام را بفرست.", kb=HOME_KB)
            return
        d.pop("action", None)
        _download_link(chat_id, m.group(0))
        return
    if act == "sub":
        ch = text.strip()
        if not ch.startswith("@") and re.match(r"^[A-Za-z0-9_]{4,64}$", ch):
            ch = "@" + ch
        info = run_coro(BR["client"].get_chat(ch), 90)
        state()["tg"]["channels"][str(info.id)] = chat_id
        save_state()
        d.pop("action", None)
        send_message(chat_id, f"📡 اشتراک فعال شد: {info.title or info.username}\n"
                              "از این به بعد پیام‌های جدید (با مدیا) به این چت ارسال می‌شود.", kb=_kb_conn())
        return
    if act == "search":
        mm = re.match(r"(@\w+)\s+(.+)", text.strip(), re.S)
        if not mm:
            send_message(chat_id, "فرمت: <code>@channel عبارت</code>", kb=HOME_KB)
            return
        msgs = run_coro(BR["client"].search_messages(mm.group(1), mm.group(2).strip(), limit=10), 120)
        lines = [f"🔍 نتایج جستجو در {mm.group(1)}:"]
        for msg in msgs:
            who = getattr(msg.from_user, "first_name", "") or ""
            body = (msg.text or msg.caption or "[مدیا]")[:120].replace("\n", " ")
            lines.append(f"• {who}: {body}")
        d.pop("action", None)
        send_message(chat_id, "\n".join(lines) if len(lines) > 1 else "چیزی پیدا نشد.", kb=_kb_conn())
        return
    send_message(chat_id, "از دکمه‌ها استفاده کن.", kb=_kb_conn() if BR["client"] else TG_KB_LOGIN)


def _download_link(chat_id, url):
    m = re.match(r"https?://t\.me/(?:c/(\d+)/(\d+)|([^/\s]+)/(\d+))", url)
    if not m:
        send_message(chat_id, "لینک نامعتبر است.", kb=HOME_KB)
        return
    if m.group(1):
        chat, mid = int("-100" + m.group(1)), int(m.group(2))
    else:
        chat, mid = m.group(3), int(m.group(4))
    msg = run_coro(BR["client"].get_messages(chat, mid), 120)
    if not msg or not msg.media:
        send_message(chat_id, "این پیام مدیا ندارد یا در دسترس نیست.", kb=HOME_KB)
        return
    prog = send_message(chat_id, "⬇️ در حال دانلود مدیا از تلگرام…")
    mid_id = prog.get("message_id") if isinstance(prog, dict) else None
    path = run_coro(BR["client"].download_media(msg), 3600)
    if not path:
        send_message(chat_id, "❌ دانلود ناموفق بود.", kb=HOME_KB)
        return
    size = os.path.getsize(path)
    name = os.path.basename(path)
    from botcore import edit_message
    if size > CHUNK:
        parts = split_file(path)
        for i, p in enumerate(parts, 1):
            send_document(chat_id, p, caption=f"✈️ {sanitize(name)}\nپارت {i}/{len(parts)}")
            time.sleep(1.2)
        for p in parts:
            try:
                os.remove(p)
            except OSError:
                pass
    else:
        if name.lower().endswith((".jpg", ".jpeg", ".png", ".webp")) and size <= 8 * 1024 * 1024:
            send_photo(chat_id, path, caption=f"✈️ {name} — {human(size)}")
        else:
            send_document(chat_id, path, caption=f"✈️ {name} — {human(size)}")
    try:
        os.remove(path)
    except OSError:
        pass
    edit_message(chat_id, mid_id, "✅ تمام شد!")
