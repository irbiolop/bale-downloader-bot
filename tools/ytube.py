#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
📺 ابزار یوتیوب: جستجو + نتایج دکمه‌ای + کارت انتخاب (🎬 ویدیو / 🎵 صدا)
  • نتایج جستجو به‌صورت دکمه‌ی شیشه‌ای می‌آید → کاربر فقط دکمه می‌زند
  • دانلودها در پس‌زمینه اجرا می‌شوند تا ربات به پیام‌های دیگر جواب بدهد
  • فایل‌های بزرگ‌تر از ۲۰ مگ مثل دانلودر عادی پارت‌بندی و ارسال می‌شوند
"""

import os
import re
import time

import yt_dlp

from botcore import (CHUNK_MB, HOME_KB, MAX_SIZE, WORK_DIR, bg, btn, edit_message,
                     find_ffmpeg, human, kb, sanitize, send_document, send_message,
                     split_file)


def _opts(extra=None):
    ff = find_ffmpeg()
    o = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "retries": 5,
        "socket_timeout": 30,
        "outtmpl": {"default": os.path.join(WORK_DIR, "%(title).80B-%(id)s.%(ext)s")},
        # کلاینت‌های جایگزین → عبور از محدودیت ضدربات برخی IPها
        # (android_vr معمولاً بدون کوکی هم جواب می‌دهد؛ ترتیب = اولویت)
        "extractor_args": {"youtube": {"player_client": ["android_vr", "ios", "tv", "web_safari", "web"]}},
    }
    if ff:
        o["ffmpeg_location"] = str(ff)
    ck = os.getenv("YT_COOKIES", "").strip()  # محتوای فایل کوکی Netscape (اختیاری)
    if ck:
        ckf = WORK_DIR / "yt_cookies.txt"
        try:
            ckf.write_text(ck, encoding="utf-8")
            o["cookiefile"] = str(ckf)
        except OSError:
            pass
    if extra:
        o.update(extra)
    return o


def search_yt(q, n=8):
    with yt_dlp.YoutubeDL(_opts({"skip_download": True, "extract_flat": "in_playlist"})) as y:
        info = y.extract_info(f"ytsearch{n}:{q}", download=False)
    out = []
    for e in (info or {}).get("entries") or []:
        url = e.get("url") or e.get("webpage_url") or ""
        if url and not url.startswith("http"):
            url = "https://www.youtube.com/watch?v=" + url
        out.append({"title": e.get("title") or "?", "url": url, "dur": e.get("duration") or 0})
    return out


def _dur(d):
    try:
        d = int(d or 0)
        return f"{d // 60}:{d % 60:02d}" if d else "؟"
    except Exception:  # noqa: BLE001
        return "؟"


def _results_kb(res):
    """هر نتیجه = یک دکمه؛ کاربر لازم نیست شماره تایپ کند."""
    rows = []
    for i, r in enumerate(res):
        title = (r.get("title") or "?")[:48]
        rows.append([btn(f"{i + 1}. {title}", f"yt:pick:{i}")])
    rows.append([btn("🏠 منوی اصلی", "menu:home")])
    return kb(rows)


def _card_kb(i):
    return kb([
        [btn("🎬 دانلود ویدیو", f"yt:dl:{i}"), btn("🎵 فقط صدا", f"yt:aud:{i}")],
        [btn("🔙 نتایج جستجو", "yt:back"), btn("🏠 منوی اصلی", "menu:home")],
    ])


def _hook(chat_id, mid, t0):
    def h(d):
        try:
            if d.get("status") == "downloading":
                if time.time() - t0[0] < 4:
                    return
                t0[0] = time.time()
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                done = d.get("downloaded_bytes") or 0
                pct = f"{int(done * 100 / total)}%" if total else human(done)
                speed = (d.get("_speed_str") or "").strip()
                edit_message(chat_id, mid, f"⬇️ یوتیوب: {pct} — {speed}")
            elif d.get("status") == "finished":
                edit_message(chat_id, mid, "🔄 در حال پردازش/ادغام فایل…")
        except Exception:  # noqa: BLE001
            pass
    return h


def download(chat_id, url, audio=False):
    """دانلود ویدیو/صدا و ارسال (با تقسیم خودکار پارت). در ترد bg اجرا می‌شود."""
    ff = find_ffmpeg()
    if audio:
        extra = {"format": "bestaudio[ext=m4a]/bestaudio/best"}
        if ff:
            extra["postprocessors"] = [{"key": "FFmpegExtractAudio",
                                        "preferredcodec": "mp3", "preferredquality": "192"}]
    elif ff:
        extra = {"format": "bv*[height<=720][ext=mp4]+ba[ext=m4a]/b[height<=720]/bv*+ba/b",
                 "merge_output_format": "mp4"}
    else:
        extra = {"format": "b[ext=mp4]/b[height<=720]/b"}

    prog = send_message(chat_id, "⬇️ آماده‌سازی دانلود…")
    mid = prog.get("message_id") if isinstance(prog, dict) else None
    t0 = [time.time()]
    try:
        with yt_dlp.YoutubeDL(_opts({**extra, "progress_hooks": [_hook(chat_id, mid, t0)]})) as y:
            info = y.extract_info(url, download=True)
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "sign in" in msg.lower() or "bot" in msg.lower() or "cookies" in msg.lower():
            edit_message(chat_id, mid,
                         "⚠️ یوتیوب دانلود از این سرور را محدود کرده (Sign-in/Bot-check).\n\n"
                         "راه‌حل‌ها:\n"
                         "• اگر ربات روی سرور/گیت‌هاب اجرا می‌شود، این محدودیت طبیعی است — "
                         "روی کامپیوتر شخصی (IP خانگی) جواب می‌دهد\n"
                         "• یا اپراتور ربات کوکی اکانت را در متغیر YT_COOKIES بگذارد\n"
                         "• ویدیوی دیگری هم می‌توانی امتحان کنی")
            return
        edit_message(chat_id, mid, f"❌ خطا در دانلود یوتیوب: {msg[:300]}")
        return

    path = None
    try:
        rd = info.get("requested_downloads") or []
        if rd:
            path = rd[0].get("filepath")
    except Exception:  # noqa: BLE001
        pass
    if not path or not os.path.exists(path):
        cands = [os.path.join(WORK_DIR, f) for f in os.listdir(WORK_DIR)]
        cands = [c for c in cands if os.path.isfile(c) and time.time() - os.path.getmtime(c) < 900]
        path = max(cands, key=os.path.getmtime) if cands else None
    if not path:
        edit_message(chat_id, mid, "❌ فایل خروجی پیدا نشد.")
        return

    title = sanitize(info.get("title") or os.path.basename(path))
    size = os.path.getsize(path)
    if size > MAX_SIZE:
        try:
            os.remove(path)
        except OSError:
            pass
        edit_message(chat_id, mid, f"❌ حجم فایل ({human(size)}) از حد مجاز بیشتر است.")
        return

    parts = split_file(path)
    try:
        if len(parts) == 1:
            edit_message(chat_id, mid, f"📤 در حال ارسال «{title}» ({human(size)})…")
            send_document(chat_id, path, caption=f"{'🎵' if audio else '🎬'} {title}\n💾 {human(size)}")
            edit_message(chat_id, mid, "✅ تمام شد!")
        else:
            edit_message(chat_id, mid,
                         f"✂️ حجم {human(size)} → تقسیم به {len(parts)} پارتِ {CHUNK_MB:.0f} مگی")
            for i, p in enumerate(parts, 1):
                send_document(chat_id, p, caption=f"🎬 {title}\nپارت {i}/{len(parts)}")
                time.sleep(1.2)
            edit_message(chat_id, mid, f"✅ تمام شد! {len(parts)} پارت ارسال شد.")
    finally:
        for p in parts:
            try:
                os.remove(p)
            except OSError:
                pass


YT_KB = kb([
    [btn("🎵 حالت فقط‌صدا برای لینک‌ها", "yt:audio")],
    [btn("🏠 منوی اصلی", "menu:home")],
])


def handle(chat_id, text, st):
    d = st["data"]
    t = (text or "").strip()
    res = d.get("results") or []

    # ۱) لینک مستقیم یوتیوب
    if re.match(r"https?://(www\.)?(youtube\.com|youtu\.be|m\.youtube\.com)/", t, re.I):
        send_message(chat_id, "⬇️ دانلود در پس‌زمینه شروع شد؛ پیشرفت را همین‌جا می‌فرستم.")
        bg(download, chat_id, t, audio=bool(d.get("audio_only")))
        return

    # ۲) میان‌بر «صدا 2» → دانلود مستقیم صدای نتیجه
    ma = re.fullmatch(r"(?:صدا|audio)\s*(\d{1,2})", t, re.I)
    # ۳) شماره → کارت انتخاب (دکمه‌ها)
    mn = re.fullmatch(r"\d{1,2}", t)
    if ma or mn:
        num = ma.group(1) if ma else mn.group(0)   # ⚠️ mn گروه ۱ ندارد
        i = int(num) - 1
        if not (0 <= i < len(res)):
            send_message(chat_id, "این شماره در نتایج فعلی نیست؛ اول عنوان بفرست تا جستجو کنم.", kb=YT_KB)
            return
        if ma:  # میان‌بر صدا → مستقیم دانلود
            send_message(chat_id, f"⬇️ 🎵 «{res[i]['title'][:60]}» → دانلود صدا شروع شد.")
            bg(download, chat_id, res[i]["url"], audio=True)
        else:
            send_message(chat_id, _card_text(res[i]), kb=_card_kb(i))
        return

    # ۴) هر چیز دیگری = عبارت جستجو
    send_message(chat_id, f"🔍 در حال جستجوی یوتیوب: «{t}» …")
    try:
        res = search_yt(t)
    except Exception as e:  # noqa: BLE001
        send_message(chat_id, f"❌ جستجو ناموفق بود: {str(e)[:200]}\nچند لحظه بعد دوباره امتحان کن.", kb=YT_KB)
        return
    if not res:
        send_message(chat_id, "چیزی پیدا نشد؛ عبارت دیگری را امتحان کن.", kb=YT_KB)
        return
    d["results"] = res
    lines = [f"📺 نتایج «{t}»:", "👇 روی هر نتیجه بزن تا ویدیو یا صدایش را بفرستم:", ""]
    for i, r in enumerate(res, 1):
        lines.append(f"{i}. {r['title']}  ({_dur(r.get('dur'))})")
    lines.append("")
    lines.append("💡 یا «صدا 2» بفرست = نتیجه‌ی ۲ فقط صدا")
    send_message(chat_id, "\n".join(lines), kb=_results_kb(res))


def _card_text(r):
    return (f"🎬 انتخاب شد:\n«{r.get('title', '?')}»\n"
            f"⏱ مدت: {_dur(r.get('dur'))}\n\n"
            "کدام را برایت بفرستم؟ 👇")


def callback(chat_id, act, st):
    d = st["data"]
    res = d.get("results") or []

    if act == "audio":
        d["audio_only"] = True
        send_message(chat_id, "🎵 حالت فقط-صدا فعال شد (لینک یوتیوب بفرست یا جستجو کن).", kb=YT_KB)
    elif act == "video":
        d["audio_only"] = False
        send_message(chat_id, "🎬 حالت ویدیو فعال شد (تا 720p).", kb=YT_KB)
    elif act == "back":
        if res:
            lines = ["📺 نتایج قبلی — روی نتیجه بزن:", ""]
            for i, r in enumerate(res, 1):
                lines.append(f"{i}. {r['title']}  ({_dur(r.get('dur'))})")
            send_message(chat_id, "\n".join(lines), kb=_results_kb(res))
        else:
            send_message(chat_id, "اول عنوان بفرست تا جستجو کنم (مثال: آهنگ جدید).", kb=YT_KB)
    elif act.startswith("pick:"):
        i = _idx(act)
        if i is None or not (0 <= i < len(res)):
            send_message(chat_id, "این نتیجه دیگر در لیست نیست؛ دوباره جستجو کن.", kb=YT_KB)
            return
        send_message(chat_id, _card_text(res[i]), kb=_card_kb(i))
    elif act.startswith("dl:") or act.startswith("aud:"):
        i = _idx(act)
        if i is None or not (0 <= i < len(res)):
            send_message(chat_id, "این نتیجه دیگر در لیست نیست؛ دوباره جستجو کن.", kb=YT_KB)
            return
        r = res[i]
        audio = act.startswith("aud:")
        send_message(chat_id,
                     f"⬇️ {'🎵 صدا' if audio else '🎬 ویدیو'}: «{r['title'][:60]}»\n"
                     "دانلود در پس‌زمینه شروع شد؛ پیشرفت را همین‌جا می‌فرستم.")
        bg(download, chat_id, r["url"], audio=audio)
    else:
        send_message(chat_id, "؟", kb=YT_KB)


def _idx(act):
    try:
        return int(act.split(":")[1])
    except Exception:  # noqa: BLE001
        return None
