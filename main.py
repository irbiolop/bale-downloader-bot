#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🤖 ربات دانلودر فایل برای پیام‌رسان بله (سازگار با Bot API تلگرام)

قابلیت‌ها:
  • دانلود فایل از لینک مستقیم (HTTP/HTTPS) و ارسال خودکار در چت
  • فایل‌های بزرگ‌تر از ۲۰ مگابایت → تقسیم خودکار به پارت‌های ۲۰ مگی و ارسال پشت‌سرهم
  • نمایش درصد پیشرفت دانلود به‌صورت زنده
  • اجرای همیشگی: سوپروایزر داخلی + run.sh + سرویس systemd  →  هیچ‌وقت نمی‌میرد (تمدید خودکار)
  • مدیریت ریت‌لیمیت (429) و خطاهای شبکه با تلاش مجدد

اجرا:  python3 main.py      (یا  ./run.sh  برای اجرای خودترمیم‌شونده)
"""

import json
import logging
import math
import os
import queue
import re
import secrets
import shutil
import sys
import threading
import time
import traceback
from pathlib import Path
from urllib.parse import urlparse, unquote

import requests

# ---------------- تنظیمات (همه با متغیر محیطی قابل تغییر هستند) ----------------
BOT_TOKEN  = os.getenv("BOT_TOKEN", "1236650446:viLT-1kk41c8vFwkVbHgmnpQZp0yBo6y_nQ")
API_BASE   = os.getenv("API_BASE", "https://tapi.bale.ai").rstrip("/")
CHUNK_MB   = int(os.getenv("CHUNK_MB", "20"))      # اندازه‌ی هر پارت (مگابایت)
MAX_MB     = int(os.getenv("MAX_MB", "2048"))      # حداکثر حجم قابل دانلود (مگابایت)
WORKERS    = int(os.getenv("WORKERS", "2"))        # تعداد دانلود همزمان
WORK_DIR   = Path(os.getenv("WORK_DIR", "./work"))
STATE_FILE = Path(os.getenv("STATE_FILE", "./offset.json"))

CHUNK    = CHUNK_MB * 1024 * 1024
MAX_SIZE = MAX_MB * 1024 * 1024

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("bale-bot")

http = requests.Session()
http.headers.update({"User-Agent": "Mozilla/5.0 (compatible; BaleDownloaderBot/1.0)"})

URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)

# پسوند پیشنهادی بر اساس نوع محتوا (وقتی نام فایل از سرور نیامده باشد)
EXT_BY_CTYPE = {
    "video/mp4": ".mp4", "video/x-matroska": ".mkv", "video/x-msvideo": ".avi",
    "video/webm": ".webm",
    "audio/mpeg": ".mp3", "audio/mp4": ".m4a", "audio/ogg": ".ogg", "audio/wav": ".wav",
    "application/pdf": ".pdf", "application/zip": ".zip",
    "application/x-rar-compressed": ".rar", "application/x-7z-compressed": ".7z",
    "image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp",
    "application/vnd.android.package-archive": ".apk",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/octet-stream": ".bin", "text/plain": ".txt",
}

# ---------------- متن‌های ربات (فارسی) ----------------
WELCOME = (
    "سلام 👋\n"
    "من ربات دانلودر فایل هستم!\n\n"
    "🔗 کافیه لینک مستقیم فایل رو بفرستی:\n"
    f"• فایل تا {CHUNK_MB} مگابایت → همون‌جا برات ارسال می‌شه\n"
    f"• فایل بزرگ‌تر از {CHUNK_MB} مگ → خودکار به پارت‌های {CHUNK_MB} مگی تقسیم و پشت‌سرهم ارسال می‌شه\n"
    f"• حداکثر حجم مجاز: {MAX_MB} مگابایت\n\n"
    "دستورها:\n"
    "/help — همین راهنما\n"
    "/status — وضعیت ربات\n\n"
    "⚠️ نکته: لینک باید «مستقیم» باشه (آدرس خودِ فایل)، نه لینک صفحه‌ی دانلود سایت‌ها."
)
MSG_CHECKING   = "🔗 در حال بررسی لینک…"
MSG_HTML       = "❌ این لینک یک صفحه‌ی وب (HTML) هست، نه فایل مستقیم.\nلطفاً آدرس مستقیم خود فایل رو بفرست."
MSG_BAD_URL    = "🤔 لینکی پیدا نکردم!\nیه لینک مستقیم (http/https) فایل برام بفرست."
MSG_QUEUED     = "⏳ درخواستت به صف پردازش اضافه شد؛ به‌محض شروع، درصد پیشرفت رو همین‌جا می‌فرستم."
MSG_QUEUE_FULL = "😕 الان صف پر است؛ چند لحظه‌ی دیگر دوباره امتحان کن."
MSG_SEND_FAIL  = "❌ ارسال فایل ناموفق بود؛ لطفاً لینک رو دوباره بفرست."


class TooBig(Exception):
    """حجم فایل از سقف مجاز بیشتر است."""


# ---------------- لایه‌ی Bot API ----------------
def api(method, retries=4, timeout=(15, 70), **params):
    """فراخوانی امن متدهای Bot API با مدیریت ریت‌لیمیت و خطای شبکه."""
    url = f"{API_BASE}/bot{BOT_TOKEN}/{method}"
    for i in range(retries):
        try:
            r = http.post(url, json=params, timeout=timeout)
            try:
                j = r.json()
            except ValueError:
                j = {}
            if r.status_code == 429:
                wait = int(j.get("parameters", {}).get("retry_after", 5))
                log.warning("rate-limit on %s → sleep %ss", method, wait)
                time.sleep(wait + 1)
                continue
            if j.get("ok"):
                return j.get("result")
            log.error("API %s → HTTP %s: %s", method, r.status_code, j)
            return None
        except requests.RequestException as e:
            log.warning("API %s network error (%d/%d): %s", method, i + 1, retries, e)
            time.sleep(2 * (i + 1))
    return None


def send_message(chat_id, text):
    return api("sendMessage", chat_id=chat_id, text=text)


def edit_message(chat_id, message_id, text):
    if not message_id:
        return
    api("editMessageText", chat_id=chat_id, message_id=message_id, text=text)


def send_document(chat_id, path: Path, caption=None):
    """آپلود multipart فایل (حداکثر ۵۰ مگ طبق مستندات بله) با تلاش مجدد."""
    api("sendChatAction", chat_id=chat_id, action="upload_document")
    url = f"{API_BASE}/bot{BOT_TOKEN}/sendDocument"
    data = {"chat_id": str(chat_id)}
    if caption:
        data["caption"] = caption
    for i in range(4):
        try:
            with open(path, "rb") as f:
                r = http.post(url, data=data,
                              files={"document": (path.name, f)},
                              timeout=(20, 1800))
            try:
                j = r.json()
            except ValueError:
                j = {}
            if r.status_code == 429:
                wait = int(j.get("parameters", {}).get("retry_after", 5))
                log.warning("sendDocument rate-limit → sleep %ss", wait)
                time.sleep(wait + 1)
                continue
            if j.get("ok"):
                return True
            log.error("sendDocument → HTTP %s: %s", r.status_code, j)
            if r.status_code in (400, 413) and "too large" in str(j).lower():
                return False
            time.sleep(3 * (i + 1))
        except requests.RequestException as e:
            log.warning("sendDocument network error (%d/4): %s", i + 1, e)
            time.sleep(3 * (i + 1))
    return False


def get_updates(offset):
    """long-polling آپدیت‌ها؛ در خطای شبکه آرام و پیوسته تلاش می‌کند."""
    try:
        r = http.post(f"{API_BASE}/bot{BOT_TOKEN}/getUpdates",
                      json={"offset": offset, "timeout": 50},
                      timeout=(15, 70))
        r.raise_for_status()
        j = r.json()
        if j.get("ok"):
            return j.get("result", [])
        log.error("getUpdates error: %s", j)
        if "webhook" in str(j).lower():
            api("deleteWebhook")
        time.sleep(3)
    except requests.RequestException as e:
        log.warning("getUpdates network error: %s", e)
        time.sleep(3)
    return []


# ---------------- ابزارهای کمکی ----------------
def human(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{int(n)} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0


def sanitize(name):
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", name).strip(" .")
    return name or "file"


def pick_filename(url, headers):
    """استخراج نام فایل از هدر Content-Disposition یا خود URL."""
    cd = headers.get("content-disposition") or ""
    name = ""
    if cd:
        m = re.search(r"filename\*\s*=\s*(?:UTF-8|utf-8)''([^;]+)", cd)
        if m:
            name = unquote(m.group(1).strip())
        else:
            m = re.search(r'filename\s*=\s*"([^"]+)"', cd) or re.search(r"filename\s*=\s*([^;]+)", cd)
            if m:
                name = m.group(1).strip().strip('"')
    if not name:
        name = os.path.basename(unquote(urlparse(url).path)) or ""
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", name).strip(" .")
    if len(name) > 80:  # حفظ پسوند در نام‌های طولانی
        root, ext = os.path.splitext(name)
        name = root[:76] + ext
    return name


# ---------------- دانلود و تقسیم ----------------
def download_file(url, dest: Path, progress_cb=None):
    """دانلود استریمی (بدون مصرف رم اضافه). خروجی: dict(html/ctype/filename/size)"""
    with http.get(url, stream=True, allow_redirects=True, timeout=(15, 180)) as r:
        r.raise_for_status()
        ctype = (r.headers.get("Content-Type") or "").strip().lower()
        fname = pick_filename(url, r.headers)
        total = int(r.headers.get("Content-Length") or 0)
        if total and total > MAX_SIZE:
            raise TooBig(human(total))
        if "text/html" in ctype:  # لینک صفحه‌ی وب است نه فایل مستقیم
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


def split_file(src: Path, out_dir: Path):
    """تقسیم فایل به پارت‌های CHUNK مگابایتی؛ خروجی: لیست مسیر پارت‌ها (مرتب)."""
    size = src.stat().st_size
    n = math.ceil(size / CHUNK)
    parts = []
    with open(src, "rb") as f:
        for i in range(1, n + 1):
            part = out_dir / f"{src.name}.part{i}"
            remaining = CHUNK
            with open(part, "wb") as out:
                while remaining > 0:
                    buf = f.read(min(1024 * 1024, remaining))
                    if not buf:
                        break
                    out.write(buf)
                    remaining -= len(buf)
            parts.append(part)
    return parts


# ---------------- پردازش هر درخواست ----------------
def process_job(chat_id, url):
    job_dir = WORK_DIR / f"job_{time.strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3)}"
    job_dir.mkdir(parents=True, exist_ok=True)
    prog_id = None
    try:
        prog = send_message(chat_id, MSG_CHECKING)
        prog_id = prog.get("message_id") if isinstance(prog, dict) else None

        dest = job_dir / "download.bin"
        state = {"t": 0.0, "pct": -1}

        def cb(done, total):
            now = time.time()
            if now - state["t"] < 4:  # هر ۴ ثانیه یک‌بار آپدیت کن
                return
            state["t"] = now
            if total:
                pct = int(done * 100 / total)
                if pct != state["pct"]:
                    state["pct"] = pct
                    edit_message(chat_id, prog_id, f"⬇️ {pct}% — {human(done)} از {human(total)}")
            else:
                edit_message(chat_id, prog_id, f"⬇️ {human(done)} دانلود شد…")

        meta = download_file(url, dest, cb)
        if meta.get("html"):
            edit_message(chat_id, prog_id, MSG_HTML)
            return

        size = dest.stat().st_size
        name = sanitize(meta.get("filename") or f"file_{time.strftime('%Y%m%d_%H%M%S')}")
        if not os.path.splitext(name)[1] and meta.get("ctype"):
            name += EXT_BY_CTYPE.get(meta["ctype"].split(";")[0].strip(), ".bin")
        final = job_dir / name
        os.replace(dest, final)
        log.info("downloaded: %s (%s) from %s", name, human(size), url[:120])

        if size <= CHUNK:
            edit_message(chat_id, prog_id, f"📤 در حال ارسال «{name}» ({human(size)})…")
            if send_document(chat_id, final, caption=f"📄 {name} — {human(size)}"):
                edit_message(chat_id, prog_id, "✅ فایل کامل ارسال شد.")
            else:
                send_message(chat_id, MSG_SEND_FAIL)
            return

        # فایل بزرگ → تقسیم به پارت‌های ۲۰ مگی و ارسال پشت‌سرهم
        n = math.ceil(size / CHUNK)
        edit_message(chat_id, prog_id,
                     f"✂️ حجم فایل {human(size)} است (بیشتر از {CHUNK_MB} مگ) → به {n} پارت تقسیم می‌شود…")
        sent = 0
        parts = split_file(final, job_dir)
        for i, part in enumerate(parts, start=1):
            edit_message(chat_id, prog_id, f"📤 ارسال پارت {i} از {n}…")
            cap = f"📦 {name}\nپارت {i} از {n} — {human(part.stat().st_size)}"
            if not send_document(chat_id, part, caption=cap):
                send_message(chat_id, f"⚠️ ارسال پارت {i} ناموفق بود. لطفاً لینک را دوباره بفرست.")
                return
            sent += 1
            part.unlink(missing_ok=True)
            time.sleep(1.2)  # فاصله بین پارت‌ها برای جلوگیری از ریت‌لیمیت

        edit_message(chat_id, prog_id, f"✅ تمام شد! {sent} پارت ارسال شد.")
        send_message(
            chat_id,
            "💡 برای بازسازی فایل اصلی، همه‌ی پارت‌ها را در یک پوشه بگذار و یکی از این دستورها را اجرا کن:\n"
            f"• ویندوز (CMD): copy /b {name}.part1+{name}.part2+… {name}\n"
            f"• لینوکس/مک: cat {name}.part* > {name}",
        )
    except TooBig as e:
        send_message(chat_id, f"❌ حجم فایل ({e}) از حد مجاز ({MAX_MB} مگابایت) بیشتر است.")
    except requests.RequestException as e:
        log.warning("download error for %s: %s", url[:120], e)
        send_message(chat_id, f"❌ خطا در دانلود: {e}\nلینک را دوباره بفرست.")
    except Exception as e:  # noqa: BLE001
        log.error(traceback.format_exc())
        send_message(chat_id, f"❌ خطای غیرمنتظره: {e}")
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)  # همیشه پاک‌سازی


# ---------------- صف و ورکرها ----------------
job_q = queue.Queue(maxsize=20)


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


def handle_message(msg):
    chat_id = (msg.get("chat") or {}).get("id")
    text = (msg.get("text") or "").strip()
    if not chat_id or not text:
        return
    low = text.lower()
    if low.startswith(("/start", "/help")):
        send_message(chat_id, WELCOME)
        return
    if low.startswith("/status"):
        send_message(
            chat_id,
            "🟢 ربات فعال است\n"
            f"اندازه‌ی هر پارت: {CHUNK_MB} مگابایت\n"
            f"حداکثر حجم: {MAX_MB} مگابایت\n"
            f"پردازش همزمان: {WORKERS}\n"
            f"در صف: {job_q.qsize()} درخواست",
        )
        return
    m = URL_RE.search(text)
    if m:
        try:
            job_q.put_nowait((chat_id, m.group(0)))
            send_message(chat_id, MSG_QUEUED)
        except queue.Full:
            send_message(chat_id, MSG_QUEUE_FULL)
        return
    send_message(chat_id, MSG_BAD_URL)


# ---------------- ذخیره‌ی offset (پس از ری‌استارت پیام‌ها دوباره پردازش نمی‌شوند) ----------------
def load_state():
    try:
        return int(json.loads(STATE_FILE.read_text()).get("offset", 0))
    except Exception:  # noqa: BLE001
        return 0


def save_state(offset):
    try:
        STATE_FILE.write_text(json.dumps({"offset": offset}))
    except Exception:  # noqa: BLE001
        pass


def poll_loop():
    offset = load_state()
    log.info("… polling started")
    while True:
        for u in get_updates(offset):
            offset = u["update_id"] + 1
            save_state(offset)
            msg = u.get("message") or u.get("edited_message") or u.get("channel_post")
            if msg:
                try:
                    handle_message(msg)
                except Exception:  # noqa: BLE001
                    log.error(traceback.format_exc())


# ---------------- اجرای اصلی با سوپروایزر همیشگی ----------------
def main():
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    me = api("getMe")
    if not me:
        log.error("اتصال به API برقرار نشد یا توکن نامعتبر است.")
        sys.exit(1)
    log.info("✅ ورود موفق | id=%s | username=@%s | name=%s",
             me.get("id"), me.get("username"), me.get("first_name"))

    for i in range(WORKERS):
        threading.Thread(target=worker, daemon=True, name=f"worker-{i}").start()

    while True:  # سوپروایزر: هر خطایی → توقف ۵ ثانیه → ری‌استارت کامل حلقه
        try:
            poll_loop()
        except Exception:  # noqa: BLE001
            log.error("SUPERVISOR: restarting loop\n%s", traceback.format_exc())
            time.sleep(5)


if __name__ == "__main__":
    main()
