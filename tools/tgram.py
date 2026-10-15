#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
✈️ ابزار اکانت تلگرام با پایروگرام:
  • ورود تعاملی از داخل چت بله (شماره → کد → رمز دوم‌مرحله‌ای) یا Session String
  • 📥 دانلود مدیا از لینک پیام تلگرام (t.me/...) — در پس‌زمینه
  • 📡 دریافت پیام‌های جدید کانال‌ها به‌صورت زنده → به چت بله (مدیا هم دانلود و ارسال می‌شود)
  • 🔍 جستجوی پیام در یک چت/کانال
سشن و api_id/api_hash از state.json یا متغیر محیطی TG_SESSION / TG_API_ID / TG_API_HASH خوانده می‌شود.

⚠️ نکته‌ی مهم پایروگرام ۲: Client.export_session_string() متد «همگام» است و نباید
به‌عنوان کوروتین صدا زده شود (باگ نسخه‌ی قبل: لاگین همیشه در قدم آخر شکست می‌خورد).
"""

import asyncio
import os
import re
import threading
import time

from botcore import (CHUNK, HOME_KB, OWNER_ID, WORK_DIR, bg, edit_message, human,
                     kb, sanitize, send_document, send_message, send_photo,
                     split_file, save_state, state)

BR = {"loop": None, "client": None, "lock": threading.Lock(), "err": None}
LOGIN = None  # {"app","loop","phone","hash"} حین ورود تعاملی
STATUS = {"running": False}
CONNECTED = threading.Event()

# --- نگاشت خطاهای تلگرام به پیام فارسی ---
_ERR_MAP = [
    ("PHONE_CODE_INVALID", "کد اشتباه است؛ کد را دوباره بفرست (فقط رقم‌ها کافی است)."),
    ("PHONE_CODE_EXPIRED", "کد منقضی شده است؛ دوباره «ورود» را بزن و با کد جدید ادامه بده."),
    ("PHONE_NUMBER_UNOCCUPIED", "این شماره اکانت تلگرام ندارد."),
    ("PHONE_NUMBER_INVALID", "شماره معتبر نیست؛ با کد کشور بنویس: +98912…"),
    ("PHONE_NUMBER_BANNED", "این شماره توسط تلگرام مسدود شده است."),
    ("FLOOD_WAIT", "تلاش‌های زیاد؛ تلگرام موقتاً محدود کرده. چند دقیقه بعد دوباره امتحان کن."),
    ("SESSION_PASSWORD_NEEDED", "این اکانت رمز دوم‌مرحله‌ای (2FA) دارد؛ رمزش را بفرست."),
    ("PASSWORD_HASH_INVALID", "رمز دوم‌مرحله‌ای اشتباه است؛ دوباره بفرست."),
    ("API_ID_INVALID", "api_id / api_hash نامعتبر است؛ از my.telegram.org دوباره بگیر."),
    ("API_ID_PUBLISHED_FLOOD", "این api_id توسط تلگرام محدود شده؛ یک اپ دیگر در my.telegram.org بساز."),
    ("AUTH_KEY_UNREGISTERED", "سشن باطل شده؛ باید دوباره وارد شوی."),
    ("AUTH_KEY_DUPLICATED", "این سشن جای دیگر باز شده؛ دوباره وارد شو."),
]


def _fa_err(e):
    s = str(e).upper()
    for k, v in _ERR_MAP:
        if k in s:
            return v
    return None


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

TG_HELP_LOGIN = (
    "✈️ اتصال اکانت تلگرام (پایروگرام)\n\n"
    "بعد از ورود، این کارها را می‌کنم:\n"
    "• 📥 لینک پیام تلگرام بدهی → مدیایش را دانلود و همین‌جا می‌فرستم\n"
    "• 📡 هر کانالی را معرفی کنی → پیام‌های جدیدش زنده به این چت می‌آید\n"
    "• 🔍 در چت‌ها جستجو می‌کنم\n\n"
    "مراحل ورود (۳ قدم):\n"
    "۱️⃣ اگر api_id/api_hash نداشته باشم، یک‌بار ازت می‌پرسم\n"
    "۲️⃣ شماره تلفن اکانت (با +98)\n"
    "۳️⃣ کدی که در خود تلگرام به دستت می‌رسد (+ رمز 2FA اگر داری)\n\n"
    "برای شروع، دکمه‌ی «ورود» را بزن 👇"
)


# ---------------- پل asyncio/پایروگرام ----------------
def _ensure_client(session, api_id, api_hash, wait=45):
    """کلاینت دائمی تلگرام در یک ترد جدا با event loop خودش؛ تا اتصال واقعی صبر می‌کند."""
    with BR["lock"]:
        if BR["client"]:
            return BR["client"]
        from pyrogram import Client
        loop = asyncio.new_event_loop()
        app = Client("tgacc", api_id=api_id, api_hash=api_hash,
                     session_string=session, in_memory=True)
        CONNECTED.clear()
        res = {"err": None}

        def _run():
            asyncio.set_event_loop(loop)
            from pyrogram import filters  # noqa: F401
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
            try:
                loop.run_until_complete(app.start())
                STATUS["running"] = True
                print("✅ pyrogram client started")
            except Exception as e:  # noqa: BLE001
                res["err"] = e
                return
            CONNECTED.set()
            loop.run_forever()

        threading.Thread(target=_run, daemon=True).start()
        BR.update(loop=loop, client=app, err=None)
        if wait and not CONNECTED.wait(wait):
            err = res.get("err")
            BR.update(loop=None, client=None)
            STATUS["running"] = False
            fa = _fa_err(err) if err else "زمان اتصال تمام شد"
            raise RuntimeError(f"اتصال تلگرام برقرار نشد: {fa or err}")
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
    app = Client("login_tmp", api_id=api_id, api_hash=api_hash, in_memory=True)

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
        # ⚠️ export_session_string در پایروگرام ۲ متد همگام است (نه کوروتین)
        s = LOGIN["app"].export_session_string()
        state()["tg"]["session"] = s
        save_state()
        try:
            lr(LOGIN["app"].disconnect(), 30)
        except Exception:  # noqa: BLE001
            pass
        LOGIN = None
        api_id, api_hash = _creds()
        _ensure_client(s, api_id, api_hash)
        me = run_coro(BR["client"].get_me(), 60)
        st["data"].pop("action", None)
        save_state()
        un = f"@{me.username}" if getattr(me, "username", None) else "(بدون یوزرنیم)"
        send_message(chat_id,
                     f"✅ وارد شدی! اکانت: {getattr(me, 'first_name', '')} {un}\n"
                     "سشن ذخیره شد و از این به بعد خودکار وصل می‌شود.\n\n"
                     "حالا یکی از ابزارها را انتخاب کن 👇",
                     kb=_kb_conn())
    except Exception as e:  # noqa: BLE001
        send_message(chat_id,
                     f"❌ خطا در پایان ورود: {e}\n"
                     "لطفاً دوباره از منو «🔗 ورود» را بزن و مراحل را تکرار کن.",
                     kb=TG_KB_LOGIN)


def login_start(chat_id, st):
    api_id, api_hash = _creds()
    if not (api_id and api_hash):
        st["data"]["action"] = "creds"
        send_message(chat_id,
                     "برای ورود به تلگرام، یک‌بار به api_id و api_hash نیاز داریم:\n\n"
                     "۱) در مرورگر برو به: my.telegram.org\n"
                     "۲) «API development tools» را بزن و یک اپ بساز\n"
                     "۳) دو مقدار App api_id و App api_hash را به این شکل بفرست:\n\n"
                     "<code>api_id api_hash</code>\n\n"
                     "مثال: <code>123456 0abc123def456…</code>", kb=HOME_KB)
        return
    st["data"]["action"] = "phone"
    send_message(chat_id,
                 "📱 شماره تلفن اکانت تلگرام را با کد کشور بفرست:\n"
                 "مثال: <code>+989121234567</code>", kb=HOME_KB)


def show_menu(chat_id, st):
    sess = _session()
    if sess and BR["client"]:
        send_message(chat_id, "✈️ اکانت تلگرام وصل است ✅\n\nابزارها را انتخاب کن:", kb=_kb_conn())
    elif sess:
        send_message(chat_id, "⏳ سشن ذخیره‌شده دارم؛ در حال وصل شدن…")
        try:
            init()
            if BR["client"]:
                send_message(chat_id, "✈️ اکانت وصل شد ✅", kb=_kb_conn())
                return
        except Exception as e:  # noqa: BLE001
            send_message(chat_id, f"⚠️ وصل شدن با سشن قبلی ممکن نشد: {e}", kb=TG_KB_LOGIN)
            return
        send_message(chat_id, "اتصال برقرار نشد؛ دوباره تلاش کن.", kb=TG_KB_LOGIN)
    else:
        send_message(chat_id, TG_HELP_LOGIN, kb=TG_KB_LOGIN)


def callback(chat_id, act, st):
    d = st["data"]
    if act == "login":
        d.pop("action", None)
        login_start(chat_id, st)
        return
    if not BR["client"]:
        send_message(chat_id, "اول وارد اکانت شو؛ دکمه‌ی «ورود» را بزن 👇", kb=TG_KB_LOGIN)
        return
    if act == "dl":
        d["action"] = "dl"
        send_message(chat_id,
                     "لینک پیام تلگرام را بفرست:\n"
                     "• عمومی: <code>https://t.me/channel/123</code>\n"
                     "• خصوصی: <code>https://t.me/c/1234567/89</code>", kb=HOME_KB)
    elif act == "sub":
        d["action"] = "sub"
        send_message(chat_id,
                     "آیدی کانال را بفرست (مثال: <code>@durov</code>)\n"
                     "از این به بعد پیام‌های جدید همان کانال، زنده به همین چت می‌آید:", kb=HOME_KB)
    elif act == "search":
        d["action"] = "search"
        send_message(chat_id,
                     "به شکل زیر بفرست:\n<code>@channel عبارت جستجو</code>\n\n"
                     "مثال: <code>@durov telegram</code>", kb=HOME_KB)
    elif act == "status":
        try:
            me = run_coro(BR["client"].get_me(), 60)
            chans = state()["tg"]["channels"]
            txt = (f"ℹ️ اکانت: {getattr(me, 'first_name', '—')} (@{getattr(me, 'username', '—')})\n"
                   f"📡 کانال‌های فعال: {len(chans)}")
            for k in chans:
                txt += f"\n• {k}"
            send_message(chat_id, txt, kb=_kb_conn())
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

    # ---- قدم ۱: api_id/api_hash ----
    if act == "creds":
        parts = text.split()
        if len(parts) < 2 or not parts[0].isdigit():
            send_message(chat_id, "فرمت درست: <code>api_id api_hash</code>\nمثال: <code>123456 0abc123…</code>", kb=HOME_KB)
            return
        s = state()["tg"]
        s["api_id"], s["api_hash"] = parts[0].strip(), parts[1].strip()
        save_state()
        d.pop("action", None)
        login_start(chat_id, st)
        return

    # ---- قدم ۲: شماره تلفن ----
    if act == "phone":
        global LOGIN
        phone = re.sub(r"[\s\-()]", "", text.strip())
        if phone.isdigit():
            phone = "+" + phone
        if not re.match(r"^\+\d{8,15}$", phone):
            send_message(chat_id, "شماره معتبر نیست؛ با کد کشور بفرست: +98912…", kb=HOME_KB)
            return
        try:
            LOGIN = {"app": None, "loop": None}
            app, loop = _login_client()
            LOGIN.update(app=app, loop=loop)
            sent = lr(app.send_code(phone), 90)
        except Exception as e:  # noqa: BLE001
            LOGIN = None
            d.pop("action", None)
            fa = _fa_err(e)
            send_message(chat_id, f"❌ {fa or ('ارسال کد ناموفق بود: ' + str(e))}", kb=TG_KB_LOGIN)
            return
        LOGIN.update(phone=phone, hash=sent.phone_code_hash)
        d["action"] = "code"
        send_message(chat_id,
                     "🔑 کد ورود را بفرست (کدی که در خود تلگرام به دستت رسیده؛ فقط رقم‌ها کافی است).\n"
                     "مثال: <code>53921</code>", kb=HOME_KB)
        return

    # ---- قدم ۳: کد ورود ----
    if act == "code":
        if not LOGIN:
            send_message(chat_id, "ابتدا شماره را بفرست؛ از منو «ورود» را بزن.", kb=TG_KB_LOGIN)
            return
        code = re.sub(r"[\s\-–—]", "", text.strip())
        try:
            lr(LOGIN["app"].sign_in(LOGIN["phone"], LOGIN["hash"], code), 90)
            _finish_login(chat_id, st)
        except Exception as e:  # noqa: BLE001
            su = str(e).upper()
            if "SESSION_PASSWORD_NEEDED" in su or ("PASSWORD" in su and "NEEDED" in su):
                d["action"] = "pass"
                send_message(chat_id,
                             "🔐 این اکانت رمز دوم‌مرحله‌ای (2FA) دارد؛ رمزش را بفرست:", kb=HOME_KB)
                return
            fa = _fa_err(e)
            if fa:
                if "EXPIRED" in su:
                    d["action"] = "phone"
                elif "INVALID" in su and "CODE" in su:
                    d["action"] = "code"  # بمان روی همان قدم تا دوباره بفرستد
                send_message(chat_id, f"❌ {fa}", kb=HOME_KB)
            else:
                send_message(chat_id, f"❌ کد پذیرفته نشد: {e}\nدوباره بفرست یا /menu بزن.", kb=HOME_KB)
        return

    # ---- قدم ۴: رمز 2FA ----
    if act == "pass":
        if not LOGIN:
            send_message(chat_id, "ابتدا از منو شروع کن.", kb=TG_KB_LOGIN)
            return
        try:
            lr(LOGIN["app"].check_password(text.strip()), 90)
            _finish_login(chat_id, st)
        except Exception as e:  # noqa: BLE001
            fa = _fa_err(e)
            send_message(chat_id, f"❌ {fa or ('رمز پذیرفته نشد: ' + str(e))}", kb=HOME_KB)
        return

    # ---- ابزارها (بعد از ورود) ----
    if act == "dl":
        m = re.search(r"https?://t\.me/\S+", text)
        if not m:
            send_message(chat_id, "لینک پیام تلگرام را بفرست. مثال: <code>https://t.me/channel/123</code>", kb=HOME_KB)
            return
        d.pop("action", None)
        send_message(chat_id, "⏳ دانلود از تلگرام در پس‌زمینه شروع شد؛ پیشرفت را همین‌جا می‌فرستم.")
        bg(_download_link, chat_id, m.group(0))
        return
    if act == "sub":
        ch = text.strip()
        if not ch.startswith("@") and re.match(r"^[A-Za-z0-9_]{4,64}$", ch):
            ch = "@" + ch
        try:
            info = run_coro(BR["client"].get_chat(ch), 90)
        except Exception as e:  # noqa: BLE001
            fa = _fa_err(e)
            send_message(chat_id, f"❌ {fa or ('کانال پیدا نشد: ' + str(e))}", kb=HOME_KB)
            return
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
        try:
            msgs = run_coro(BR["client"].search_messages(mm.group(1), mm.group(2).strip(), limit=10), 120)
        except Exception as e:  # noqa: BLE001
            fa = _fa_err(e)
            send_message(chat_id, f"❌ {fa or str(e)}", kb=HOME_KB)
            return
        lines = [f"🔍 نتایج جستجو در {mm.group(1)}:"]
        for msg in msgs:
            who = getattr(msg.from_user, "first_name", "") or ""
            body = (msg.text or msg.caption or "[مدیا]")[:120].replace("\n", " ")
            lines.append(f"• {who}: {body}")
        d.pop("action", None)
        send_message(chat_id, "\n".join(lines) if len(lines) > 1 else "چیزی پیدا نشد.", kb=_kb_conn())
        return

    send_message(chat_id, "از دکمه‌ها استفاده کن 👇", kb=_kb_conn() if BR["client"] else TG_KB_LOGIN)


def _download_link(chat_id, url):
    m = re.match(r"https?://t\.me/(?:c/(\d+)/(\d+)|([^/\s]+)/(\d+))", url)
    if not m:
        send_message(chat_id, "لینک نامعتبر است.", kb=HOME_KB)
        return
    if m.group(1):
        chat, mid = int("-100" + m.group(1)), int(m.group(2))
    else:
        chat, mid = m.group(3), int(m.group(4))
    try:
        msg = run_coro(BR["client"].get_messages(chat, mid), 120)
        if not msg or not msg.media:
            send_message(chat_id, "این پیام مدیا ندارد یا در دسترس نیست.", kb=_kb_conn())
            return
    except Exception as e:  # noqa: BLE001
        fa = _fa_err(e)
        send_message(chat_id, f"❌ {fa or ('دسترسی به پیام ممکن نشد: ' + str(e))}\n"
                              "نکته: برای کانال‌های خصوصی، اول اکانت باید عضو آن کانال باشد.", kb=_kb_conn())
        return
    prog = send_message(chat_id, "⬇️ در حال دانلود مدیا از تلگرام…")
    mid_id = prog.get("message_id") if isinstance(prog, dict) else None
    try:
        path = run_coro(BR["client"].download_media(msg), 3600)
    except Exception as e:  # noqa: BLE001
        send_message(chat_id, f"❌ دانلود ناموفق بود: {e}", kb=_kb_conn())
        return
    if not path:
        send_message(chat_id, "❌ دانلود ناموفق بود.", kb=_kb_conn())
        return
    size = os.path.getsize(path)
    name = os.path.basename(path)
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
