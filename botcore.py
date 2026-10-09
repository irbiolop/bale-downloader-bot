#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🧰 هسته‌ی مشترک ربات جعبه‌ابزار بله
  • تنظیمات از متغیر محیطی (هیچ توکنی در کد ذخیره نمی‌شود)
  • لایه‌ی Bot API بله با مدیریت 429 و خطای شبکه
  • وضعیت کاربران (حالت فعال هر کاربر) در state.json
  • کیبوردهای شیشه‌ای (دکمه‌های inline) و ابزارهای مشترک فایل
"""

import json
import math
import os
import re
import shutil
import threading
import time
from pathlib import Path
from urllib.parse import urlparse, unquote

import requests

# ---------------- تنظیمات ----------------
BOT_TOKEN  = os.getenv("BOT_TOKEN", "").strip()
API_BASE   = os.getenv("API_BASE", "https://tapi.bale.ai").rstrip("/")
API        = f"{API_BASE}/bot{BOT_TOKEN}"
CHUNK_MB   = float(os.getenv("CHUNK_MB", "20"))     # اندازه‌ی هر پارت (مگابایت)
MAX_MB     = float(os.getenv("MAX_MB", "2048"))     # حداکثر حجم قابل دانلود (مگابایت)
WORKERS    = int(os.getenv("WORKERS", "2"))         # تعداد دانلود همزمان
WORK_DIR   = Path(os.getenv("WORK_DIR", "./work"))
STATE_FILE = Path(os.getenv("STATE_FILE", "./state.json"))
OWNER_ID   = os.getenv("OWNER_ID", "").strip()      # اگر ست شود، فقط همین کاربر اجازه دارد

CHUNK    = CHUNK_MB * 1024 * 1024
MAX_SIZE = MAX_MB * 1024 * 1024

WORK_DIR.mkdir(parents=True, exist_ok=True)

http = requests.Session()
http.headers.update({"User-Agent": "Mozilla/5.0 (compatible; BaleToolboxBot/2.0)"})

URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)

EXT_BY_CTYPE = {
    "video/mp4": ".mp4", "video/x-matroska": ".mkv", "video/x-msvideo": ".avi", "video/webm": ".webm",
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

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


class TooBig(Exception):
    """حجم فایل از سقف مجاز بیشتر است."""


# ---------------- وضعیت مشترک ----------------
_state_lock = threading.Lock()
_state = {
    "offset": 0,
    "users": {},      # chat_id -> {"mode": ..., "data": {...}}
    "tg": {"session": "", "api_id": "", "api_hash": "", "channels": {}},  # channels: tg_chat_id -> bale_chat_id
    "gh_token": "",
}


def load_state():
    global _state
    try:
        d = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(d, dict):
            d.setdefault("offset", 0)
            d.setdefault("users", {})
            tg = d.setdefault("tg", {})
            tg.setdefault("session", "")
            tg.setdefault("api_id", "")
            tg.setdefault("api_hash", "")
            ch = tg.setdefault("channels", {})
            if isinstance(ch, list):        # سازگاری نسخه‌های قبل
                tg["channels"] = {}
            d.setdefault("gh_token", "")
            _state = d
    except FileNotFoundError:
        pass
    except Exception as e:  # noqa: BLE001
        print("state load error:", e)


def save_state():
    with _state_lock:
        try:
            tmp = STATE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(_state, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, STATE_FILE)
        except Exception as e:  # noqa: BLE001
            print("state save error:", e)


def state():
    return _state


def ustate(chat_id):
    """دیکشنری وضعیت کاربر (mode + data) — همیشه موجود."""
    with _state_lock:
        u = _state["users"].setdefault(str(chat_id), {})
        u.setdefault("mode", "menu")
        u.setdefault("data", {})
        return u


def drop_user(chat_id):
    with _state_lock:
        _state["users"].pop(str(chat_id), None)


# ---------------- لایه‌ی Bot API ----------------
def api(method, retries=4, timeout=(15, 70), **params):
    """فراخوانی امن متدهای Bot API با مدیریت ریت‌لیمیت و خطای شبکه."""
    url = f"{API}/{method}"
    for i in range(retries):
        try:
            r = http.post(url, json=params, timeout=timeout)
            try:
                j = r.json()
            except ValueError:
                j = {}
            if r.status_code == 429:
                wait = int(j.get("parameters", {}).get("retry_after", 5))
                time.sleep(wait + 1)
                continue
            if j.get("ok"):
                return j.get("result")
            # خطای منطقی API را به بالا پرتاب کن تا هندلر پیام مناسب بدهد
            raise RuntimeError(f"{method} → HTTP {r.status_code}: {j.get('description', j)}")
        except requests.RequestException as e:
            if i == retries - 1:
                raise
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"{method} failed after {retries} retries")


def chunk_text(text, size=3500):
    s = str(text)
    parts = []
    while s:
        parts.append(s[:size])
        s = s[size:]
    return parts or [""]


def send_message(chat_id, text, kb=None, reply_to=None):
    """ارسال پیام (خودکار تقسیم پیام‌های طولانی). kb = reply_markup شیشه‌ای."""
    res = None
    parts = chunk_text(text)
    for i, part in enumerate(parts):
        p = {"chat_id": chat_id, "text": part}
        if reply_to:
            p["reply_to_message_id"] = reply_to
        if kb and i == len(parts) - 1:
            p["reply_markup"] = kb
        try:
            res = api("sendMessage", **p)
        except RuntimeError:
            if "reply_markup" in p:  # اگر دکمه‌ها پذیرفته نشد، بدون دکمه بفرست
                p.pop("reply_markup")
                res = api("sendMessage", **p)
            else:
                raise
    return res


def edit_message(chat_id, message_id, text, kb=None):
    if not message_id:
        return None
    try:
        p = {"chat_id": chat_id, "message_id": message_id, "text": chunk_text(text)[0]}
        if kb:
            p["reply_markup"] = kb
        return api("editMessageText", **p)
    except RuntimeError:
        return None  # ویرایش تکراری/خطای بله → بی‌اهمیت


def answer_callback(cb_id, text=None):
    try:
        p = {"callback_query_id": cb_id}
        if text:
            p["text"] = str(text)[:190]
        api("answerCallbackQuery", **p)
    except RuntimeError:
        pass  # حیاتی نیست


def send_document(chat_id, path, caption="", kb=None):
    """آپلود multipart فایل (سقف بله ~۵۰ مگ) با تلاش مجدد."""
    url = f"{API}/sendDocument"
    path = Path(path)
    data = {"chat_id": str(chat_id)}
    if caption:
        data["caption"] = str(caption)[:1000]
    if kb:
        data["reply_markup"] = json.dumps(kb)
    for i in range(4):
        try:
            with open(path, "rb") as f:
                r = http.post(url, data=data, files={"document": (path.name, f)}, timeout=(20, 1800))
            try:
                j = r.json()
            except ValueError:
                j = {}
            if r.status_code == 429:
                time.sleep(int(j.get("parameters", {}).get("retry_after", 5)) + 1)
                continue
            if j.get("ok"):
                return True
            if kb and ("markup" in str(j).lower() or "button" in str(j).lower()):
                data.pop("reply_markup", None)  # بدون دکمه دوباره
                continue
            if r.status_code in (400, 413) and "too large" in str(j).lower():
                return False
            raise RuntimeError(f"sendDocument → HTTP {r.status_code}: {j.get('description', j)}")
        except requests.RequestException as e:
            if i == 3:
                raise
            time.sleep(3 * (i + 1))
    return False


def send_photo(chat_id, path, caption=""):
    url = f"{API}/sendPhoto"
    path = Path(path)
    data = {"chat_id": str(chat_id)}
    if caption:
        data["caption"] = str(caption)[:1000]
    for i in range(3):
        try:
            with open(path, "rb") as f:
                r = http.post(url, data=data, files={"photo": (path.name, f)}, timeout=(20, 600))
            if r.json().get("ok"):
                return True
            raise RuntimeError(f"sendPhoto: {r.json().get('description')}")
        except (requests.RequestException, ValueError) as e:
            if i == 2:
                raise RuntimeError(f"sendPhoto failed: {e}")
            time.sleep(2)
    return False


def get_bale_file(file_id, dest_dir=None):
    """دانلود فایلی که کاربر در چت فرستاده (برای آپلود به گیت‌هاب و...)."""
    info = api("getFile", file_id=file_id)
    fpath = info.get("file_path") or f"{file_id}"
    name = os.path.basename(fpath) or f"{file_id}.bin"
    dest = Path(dest_dir or WORK_DIR) / f"{int(time.time())}_{name}"
    r = http.get(f"{API_BASE}/file/bot{BOT_TOKEN}/{fpath}", stream=True, timeout=(20, 600))
    r.raise_for_status()
    with open(dest, "wb") as f:
        for ch in r.iter_content(512 * 1024):
            if ch:
                f.write(ch)
    return dest


# ---------------- ابزارهای مشترک ----------------
def human(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{int(n)} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0


def sanitize(name):
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", str(name)).strip(" .")
    return name[:120] or "file"


def pick_filename(url, headers):
    """استخراج نام فایل از Content-Disposition یا URL ("" اگر هیچ‌کدام نبود)."""
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
    if len(name) > 80:
        root, ext = os.path.splitext(name)
        name = root[:76] + ext
    return name


def split_file(src, out_dir=None):
    """تقسیم فایل به پارت‌های CHUNK مگابایتی؛ خروجی: لیست Path پارت‌ها (اگر کوچک باشد خودش)."""
    src = Path(src)
    if src.stat().st_size <= CHUNK:
        return [src]
    out_dir = Path(out_dir or src.parent)
    n = math.ceil(src.stat().st_size / CHUNK)
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


def find_ffmpeg():
    """مسیر ffmpeg (سیستمی یا از پکیج imageio-ffmpeg) یا None."""
    p = shutil.which("ffmpeg")
    if p:
        return p
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return None


# ---------------- کیبوردها ----------------
def btn(text, data):
    return {"text": text, "callback_data": data}


def kb(rows):
    return {"inline_keyboard": rows}


HOME_KB = kb([[btn("🏠 منوی اصلی", "menu:home")]])

MENU_KB = kb([
    [btn("📥 دانلود فایل از لینک", "menu:dl"), btn("🔍 جستجوی گوگل", "menu:gsearch")],
    [btn("🌐 دریافت HTML صفحه", "menu:html"), btn("📺 یوتیوب", "menu:yt")],
    [btn("📌 پینترست", "menu:pin"), btn("✈️ تلگرام (اکانت)", "menu:tg")],
    [btn("🐙 گیت‌هاب", "menu:gh"), btn("📊 وضعیت", "menu:status")],
    [btn("🆘 راهنما", "menu:help")],
])

WELCOME = (
    "سلام 👋 به جعبه‌ابزار من!\n\n"
    "یکی از ابزارها رو از دکمه‌های شیشه‌ای زیر انتخاب کن 👇\n"
    "هر وقت خواستی برگردی، «🏠 منوی اصلی» رو بزن.\n\n"
    "💡 میان‌بر: اگر لینک مستقیم فایل بفرستی، مستقیم می‌فرستمش."
)

TOOL_TEXT = {
    "dl": "📥 دانلودر فایل\n\n🔗 لینک مستقیم فایل را بفرست:\n"
          f"• تا {CHUNK_MB:.0f} مگ → همان‌جا ارسال می‌شود\n"
          f"• بزرگ‌تر از {CHUNK_MB:.0f} مگ → خودکار به پارت‌های {CHUNK_MB:.0f} مگی تقسیم می‌شود\n"
          f"• حداکثر حجم: {MAX_MB:.0f} مگابایت",
    "gsearch": "🔍 جستجوی گوگل\n\nعبارت موردنظرت را بفرست.\nبعد از نمایش نتایج، «شماره» هر نتیجه را بفرست "
               "تا سورس کامل HTML همان صفحه را برایت بفرستم.",
    "html": "🌐 دریافت HTML صفحه\n\nلینک صفحه را بفرست (با http/https):\n"
            "• سورس کامل + خلاصه‌ی محتوا + لیست تصاویر/فونت‌ها/اسکریپت‌ها را می‌فرستم",
    "yt": "📺 یوتیوب\n\n• عنوان بنویس → جستجو می‌کنم\n• شماره نتیجه → دانلود ویدیو (تا 720p)\n"
          "• «صدا ۲» → فقط فایل صوتی (mp3/m4a)\n• لینک مستقیم یوتیوب هم می‌پذیرم",
    "pin": "📌 پینترست\n\n• لینک پین را بفرست → تصویر/ویدیوی اصلی را می‌فرستم\n"
           "• یا عبارت بنویس → در پینترست جستجو می‌کنم؛ بعد شماره نتیجه را بفرست",
}
