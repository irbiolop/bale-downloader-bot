#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🧰 ربات جعبه‌ابزار بله — نسخه ۲

ابزارها (منوی شیشه‌ای بعد از /start):
  📥 دانلود فایل از لینک (تقسیم خودکار ۲۰ مگی)
  🔍 جستجوی گوگل (→ دریافت HTML هر نتیجه)
  🌐 دریافت HTML کامل صفحه
  📺 یوتیوب (سرچ + دانلود ویدیو/صدا)
  📌 پینترست (سرچ + دانلود پین)
  ✈️ اکانت تلگرام با پایروگرام (دریافت پیام کانال، دانلود، سرچ)
  🐙 گیت‌هاب (ساخت ریپو، آپلود فایل، ساخت اکشن)

امنیت: توکن فقط از متغیر محیطی BOT_TOKEN — هیچ توکنی در کد ذخیره نیست.
اجرای همیشگی: سوپروایزر داخلی + run.sh + GitHub Actions (bot.yml)
"""

import math
import os
import queue
import secrets
import shutil
import sys
import threading
import time
import traceback
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests

from botcore import (API, BOT_TOKEN, CHUNK, CHUNK_MB, EXT_BY_CTYPE, HOME_KB, MAX_MB,
                     MAX_SIZE, MENU_KB, OWNER_ID, STATE_FILE, TOOL_TEXT, TooBig, WELCOME,
                     WORK_DIR, WORKERS, api, answer_callback, chunk_text, drop_user,
                     edit_message, find_ffmpeg, human, kb, load_state, pick_filename,
                     sanitize, save_state, send_document, send_message, split_file,
                     state, ustate, btn)
import tools.gsearch as gsearch
import tools.htmlfetch as htmlfetch
import tools.gh as gh
import tools.pinterest as pinterest
import tools.tgram as tgram
import tools.ytube as ytube

T0 = time.time()
HELP = (
    "🧰 جعبه‌ابزار — راهنما\n\n"
    "📥 دانلود فایل: لینک مستقیم → ارسال (+ تقسیم خودکار ۲۰ مگی)\n"
    "🔍 گوگل: جستجو + شماره نتیجه → HTML همان صفحه\n"
    "🌐 HTML صفحه: لینک → سورس کامل + خلاصه + آمار مخلفات\n"
    "📺 یوتیوب: جستجو، دانلود ویدیو تا 720p یا فقط صدا\n"
    "📌 پینترست: جستجو، دانلود تصویر/ویدیوی اصلی پین\n"
    "✈️ تلگرام: ورود با پایروگرام → دریافت پیام کانال‌ها، دانلود مدیا، جستجو\n"
    "🐙 گیت‌هاب: ساخت ریپو، آپلود فایل، ساخت ورک‌فلو اکشنز\n\n"
    "دستورها: /menu منو | /status وضعیت | /cancel لغو حالت فعلی"
)


class _Log:
    @staticmethod
    def info(fmt, *a):
        print(time.strftime("%Y-%m-%d %H:%M:%S"), "| INFO |", fmt % a)

    @staticmethod
    def error(fmt, *a):
        print(time.strftime("%Y-%m-%d %H:%M:%S"), "| ERROR |", fmt % a)


log = _Log()

MSG_HTML        = "❌ این لینک یک صفحه‌ی وب (HTML) هست، نه فایل مستقیم.\nلطفاً آدرس مستقیم خود فایل را بفرست."
MSG_QUEUED      = "⏳ درخواستت به صف پردازش اضافه شد؛ درصد پیشرفت را همین‌جا می‌فرستم."
MSG_QUEUE_FULL  = "😕 الان صف پر است؛ چند لحظه‌ی دیگر دوباره امتحان کن."
MSG_SEND_FAIL   = "❌ ارسال فایل ناموفق بود؛ لطفاً لینک را دوباره بفرست."


# ---------------- ابزار دانلود (منطق تست‌شده‌ی نسخه ۱) ----------------
job_q = queue.Queue(maxsize=20)


def download_file(url, dest: Path, progress_cb=None):
    """دانلود استریمی؛ خروجی: dict(html/ctype/filename/size)"""
    with requests.get(url, stream=True, allow_redirects=True, timeout=(15, 180),
                      headers={"User-Agent": "Mozilla/5.0 (compatible; BaleToolboxBot/2.0)"}) as r:
        r.raise_for_status()
        ctype = (r.headers.get("Content-Type") or "").strip().lower()
        fname = pick_filename(url, r.headers)
        total = int(r.headers.get("Content-Length") or 0)
        if total and total > MAX_SIZE:
            raise TooBig(human(total))
        if "text/html" in ctype:
            return {"html": True, "ctype": ctype, "filename": fname}
        done = 0
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=512 * 1024):
                if not chunk:
                    continue
                f.write(chunk)
                done += len(chunk)
                if done > MAX_SIZE:
                    raise TooBig(human(MAX_SIZE) + "+")
                if progress_cb:
                    progress_cb(done, total)
        return {"html": False, "ctype": ctype, "filename": fname, "size": done}


def process_job(chat_id, url):
    job_dir = WORK_DIR / f"job_{time.strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3)}"
    job_dir.mkdir(parents=True, exist_ok=True)
    try:
        prog = send_message(chat_id, "🔗 در حال بررسی لینک…")
        prog_id = prog.get("message_id") if isinstance(prog, dict) else None
        dest = job_dir / "download.bin"
        st_ = {"t": 0.0, "pct": -1}

        def cb(done, total):
            now = time.time()
            if now - st_["t"] < 4:
                return
            st_["t"] = now
            if total:
                pct = int(done * 100 / total)
                if pct != st_["pct"]:
                    st_["pct"] = pct
                    edit_message(chat_id, prog_id, f"⬇️ {pct}% — {human(done)} از {human(total)}")
            else:
                edit_message(chat_id, prog_id, f"⬇️ {human(done)} دانلود شد…")

        meta, last_err = None, None
        for attempt in range(1, 4):  # تلاش مجدد خودکار (قطع‌شدن وسط کار سرورها)
            try:
                st_.update(t=0.0, pct=-1)
                meta = download_file(url, dest, cb)
                break
            except TooBig:
                raise
            except (requests.RequestException, OSError) as e:
                last_err = e
                log.info("download attempt %d/3 failed: %s", attempt, e)
                dest.unlink(missing_ok=True)
                if attempt < 3:
                    edit_message(chat_id, prog_id, f"⚠️ اتصال قطع شد؛ تلاش مجدد {attempt + 1} از 3…")
                    time.sleep(3)
        if meta is None:
            raise last_err
        if meta.get("html"):
            edit_message(chat_id, prog_id, MSG_HTML)
            return

        size = dest.stat().st_size
        name = sanitize(meta.get("filename") or f"file_{time.strftime('%Y%m%d_%H%M%S')}")
        if not os.path.splitext(name)[1] and meta.get("ctype"):
            name += EXT_BY_CTYPE.get(meta["ctype"].split(";")[0].strip(), ".bin")
        final = job_dir / name
        os.replace(dest, final)
        log.info("downloaded: %s (%s)", name, human(size))

        if size <= CHUNK:
            edit_message(chat_id, prog_id, f"📤 در حال ارسال «{name}» ({human(size)})…")
            if send_document(chat_id, final, caption=f"📄 {name} — {human(size)}"):
                edit_message(chat_id, prog_id, "✅ فایل کامل ارسال شد.")
            else:
                send_message(chat_id, MSG_SEND_FAIL, kb=HOME_KB)
            return

        n = math.ceil(size / CHUNK)
        edit_message(chat_id, prog_id,
                     f"✂️ حجم {human(size)} (بیشتر از {CHUNK_MB:.0f} مگ) → به {n} پارت تقسیم می‌شود…")
        parts = split_file(final, job_dir)
        sent = 0
        for i, part in enumerate(parts, start=1):
            edit_message(chat_id, prog_id, f"📤 ارسال پارت {i} از {n}…")
            cap = f"📦 {name}\nپارت {i} از {n} — {human(part.stat().st_size)}"
            if not send_document(chat_id, part, caption=cap):
                send_message(chat_id, f"⚠️ ارسال پارت {i} ناموفق بود. لطفاً لینک را دوباره بفرست.", kb=HOME_KB)
                return
            sent += 1
            part.unlink(missing_ok=True)
            time.sleep(1.2)

        edit_message(chat_id, prog_id, f"✅ تمام شد! {sent} پارت ارسال شد.")
        send_message(
            chat_id,
            "💡 برای بازسازی فایل اصلی، همه‌ی پارت‌ها را در یک پوشه بگذار و:\n"
            f"• ویندوز (CMD): copy /b {name}.part1+{name}.part2+… {name}\n"
            f"• لینوکس/مک: cat {name}.part* > {name}",
            kb=HOME_KB,
        )
    except TooBig as e:
        send_message(chat_id, f"❌ حجم فایل ({e}) از حد مجاز ({MAX_MB:.0f} مگابایت) بیشتر است.", kb=HOME_KB)
    except (requests.RequestException, RuntimeError) as e:
        log.error("download error for %s: %s", url[:120], e)
        send_message(chat_id, f"❌ خطا در دانلود: {e}", kb=HOME_KB)
    except Exception as e:  # noqa: BLE001
        log.error(traceback.format_exc())
        send_message(chat_id, f"❌ خطای غیرمنتظره: {e}", kb=HOME_KB)
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)


def worker():
    while True:
        chat_id, url = job_q.get()
        try:
            log.info("JOB start | chat=%s | url=%s", chat_id, url[:150])
            process_job(chat_id, url)
        except Exception:  # noqa: BLE001
            log.error(traceback.format_exc())
        finally:
            job_q.task_done()


# ---------------- منو و روتر ----------------
def send_menu(chat_id, intro=None):
    send_message(chat_id, (intro or WELCOME), kb=MENU_KB)


def handle_message(msg):
    chat_id = (msg.get("chat") or {}).get("id")
    if not chat_id:
        return
    st = ustate(chat_id)
    text = (msg.get("text") or "").strip()
    mode = st.get("mode", "menu")
    d = st["data"]

    # فایل/سند فرستاده شده؟
    if msg.get("document"):
        if mode == "gh":
            gh.handle_file(chat_id, msg, st)
            save_state()
        else:
            send_message(chat_id, "📄 اگر می‌خواهی فایل را به گیت‌هاب آپلود کنم، از منو «🐙 گیت‌هاب → آپلود فایل» را انتخاب کن.", kb=MENU_KB)
        return

    if not text:
        return

    # دستورها
    if text.startswith("/"):
        cmd = text.split()[0].lower().split("@")[0]
        if cmd in ("/start",):
            drop_user(chat_id)
            save_state()
            send_menu(chat_id)
            return
        if cmd in ("/menu", "/cancel"):
            drop_user(chat_id)
            save_state()
            send_menu(chat_id, "منوی اصلی 👇")
            return
        if cmd == "/help":
            send_message(chat_id, HELP, kb=MENU_KB)
            return
        if cmd == "/status":
            mins = int((time.time() - T0) // 60)
            tg_on = bool(tgram.BR["client"])
            ff = find_ffmpeg()
            send_message(
                chat_id,
                "🟢 ربات فعال است\n"
                f"⏱ آپ‌تایم این اجرا: {mins} دقیقه\n"
                f"📥 پارت: {CHUNK_MB:.0f} مگ | حداکثر: {MAX_MB:.0f} مگ | همزمان: {WORKERS}\n"
                f"📥 در صف دانلود: {job_q.qsize()}\n"
                f"✈️ تلگرام: {'وصل ✅' if tg_on else 'قطع ❌'}\n"
                f"🐙 گیت‌هاب: {'توکن تنظیم ✅' if gh._tok() else 'بدون توکن ❌'}\n"
                f"🎵 ffmpeg: {'موجود ✅' if ff else 'نصب نیست (mp3 نمی‌سازم) ⚠️'}",
                kb=HOME_KB,
            )
            return
        if cmd == "/done" and mode == "gh":
            st["data"].pop("action", None)
            send_message(chat_id, "✅ آپلود پایان یافت.", kb=gh.GH_KB)
            save_state()
            return
        send_message(chat_id, "دستور ناشناخته؛ /help را ببین.", kb=MENU_KB)
        return

    # مسیریابی بر اساس حالت کاربر
    try:
        if mode == "menu":
            m = __import__("botcore").URL_RE.search(text)
            if m:  # میان‌بر: لینک مستقیم → دانلودر
                st["mode"] = "dl"
                job_q.put_nowait((chat_id, m.group(0)))
                send_message(chat_id, MSG_QUEUED, kb=HOME_KB)
                save_state()
            else:
                send_message(chat_id, "یکی از ابزارها را از منو انتخاب کن 👇 یا لینک مستقیم فایل بفرست.", kb=MENU_KB)
        elif mode == "dl":
            m = __import__("botcore").URL_RE.search(text)
            if not m:
                send_message(chat_id, "لینک مستقیم فایل را بفرست (یا /menu).", kb=HOME_KB)
                return
            job_q.put_nowait((chat_id, m.group(0)))
            send_message(chat_id, MSG_QUEUED, kb=HOME_KB)
        elif mode == "gsearch":
            gsearch.handle(chat_id, text, st)
        elif mode == "html":
            htmlfetch.handle(chat_id, text, st)
        elif mode == "yt":
            ytube.handle(chat_id, text, st)
        elif mode == "pin":
            pinterest.handle(chat_id, text, st)
        elif mode == "gh":
            gh.handle(chat_id, text, st)
        elif mode == "tg":
            tgram.handle(chat_id, text, st)
        else:
            send_menu(chat_id)
    except Exception as e:  # noqa: BLE001
        log.error(traceback.format_exc())
        send_message(chat_id, f"❌ خطا: {e}", kb=HOME_KB)
    finally:
        save_state()


def handle_callback(cb):
    chat_id = ((cb.get("message") or {}).get("chat") or {}).get("id")
    data = cb.get("data") or ""
    if not chat_id or not data:
        return
    answer_callback(cb.get("id"))
    st = ustate(chat_id)
    try:
        if data == "menu:home":
            drop_user(chat_id)
            save_state()
            send_menu(chat_id, "منوی اصلی 👇")
            return
        if data == "menu:status":
            handle_message({"chat": {"id": chat_id}, "text": "/status"})
            return
        if data == "menu:help":
            send_message(chat_id, HELP, kb=MENU_KB)
            return
        if data.startswith("menu:"):
            tool = data[5:]
            if tool not in ("dl", "gsearch", "html", "yt", "pin", "tg", "gh"):
                return
            st["mode"] = tool
            st["data"] = {}
            if tool == "gh":
                gh.show_menu(chat_id, st)
            elif tool == "tg":
                tgram.show_menu(chat_id, st)
            else:
                kbs = {"yt": ytube.YT_KB, "dl": HOME_KB, "gsearch": HOME_KB, "html": HOME_KB, "pin": HOME_KB}
                send_message(chat_id, TOOL_TEXT[tool], kb=kbs.get(tool, HOME_KB))
            save_state()
            return
        if data.startswith("gh:"):
            gh.callback(chat_id, data[3:], st)
        elif data.startswith("tg:"):
            tgram.callback(chat_id, data[3:], st)
        elif data.startswith("yt:"):
            ytube.callback(chat_id, data[3:], st)
        else:
            send_message(chat_id, "دکمه‌ی ناشناخته؛ از منو استفاده کن 👇", kb=MENU_KB)
    except Exception as e:  # noqa: BLE001
        log.error(traceback.format_exc())
        send_message(chat_id, f"❌ خطا: {e}", kb=HOME_KB)
    finally:
        save_state()


def handle_update(u):
    # محدودسازی به مالک (اختیاری، با OWNER_ID)
    if OWNER_ID:
        who = None
        if u.get("message"):
            who = (u["message"].get("chat") or {}).get("id")
        elif u.get("callback_query"):
            who = ((u["callback_query"].get("message") or {}).get("chat") or {}).get("id")
        if who and str(who) != OWNER_ID:
            try:
                send_message(who, "⛔️ این ربات خصوصی است.")
            except Exception:  # noqa: BLE001
                pass
            return
    if u.get("message"):
        handle_message(u["message"])
    elif u.get("callback_query"):
        handle_callback(u["callback_query"])


# ---------------- polling ----------------
def get_updates(offset):
    try:
        r = requests.post(f"{API}/getUpdates",
                          json={"offset": offset, "timeout": 50},
                          timeout=(15, 70))
        r.raise_for_status()
        j = r.json()
        if j.get("ok"):
            return j.get("result", [])
        log.error("getUpdates error: %s", j)
        if "webhook" in str(j).lower():
            try:
                api("deleteWebhook")
            except Exception:  # noqa: BLE001
                pass
        time.sleep(3)
    except requests.RequestException as e:
        log.error("getUpdates network error: %s", e)
        time.sleep(3)
    return []


def poll_loop():
    st = state()
    offset = st.get("offset", 0)
    log.info("… polling started")
    while True:
        for u in get_updates(offset):
            offset = u["update_id"] + 1
            st["offset"] = offset
            save_state()
            try:
                handle_update(u)
            except Exception:  # noqa: BLE001
                log.error(traceback.format_exc())


def main():
    if not BOT_TOKEN:
        print("BOT_TOKEN تنظیم نشده است! مثال:  export BOT_TOKEN='123456:ABCDEF'")
        sys.exit(1)
    load_state()

    me = None
    for attempt in range(6):
        try:
            me = api("getMe")
            break
        except Exception as e:  # noqa: BLE001
            log.error("getMe failed (%d/6): %s", attempt + 1, e)
            time.sleep(10)
    if not me:
        log.error("اتصال به API برقرار نشد یا توکن نامعتبر است.")
        sys.exit(1)
    log.info("✅ ورود موفق | id=%s | username=@%s", me.get("id"), me.get("username"))

    # حذف وبهوک قبلی تا در polling دخالت نکند
    try:
        api("deleteWebhook")
        log.info("✅ وبهوک حذف شد (اگر بوده)")
    except Exception as e:  # noqa: BLE001
        log.error("deleteWebhook: %s", e)

    # کلاینت تلگرام (اگر سشن ذخیره شده)
    try:
        tgram.init()
    except Exception as e:  # noqa: BLE001
        log.error("tg init: %s", e)

    for i in range(WORKERS):
        threading.Thread(target=worker, daemon=True, name=f"worker-{i}").start()

    while True:  # سوپروایزر همیشگی
        try:
            poll_loop()
        except KeyboardInterrupt:
            print("exit")
            break
        except Exception:  # noqa: BLE001
            log.error("SUPERVISOR: restarting loop\n%s", traceback.format_exc())
            time.sleep(5)


if __name__ == "__main__":
    main()
