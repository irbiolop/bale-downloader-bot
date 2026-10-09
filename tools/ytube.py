#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
📺 ابزار یوتیوب: جستجو + دانلود ویدیو (تا 720p) یا فقط صدا
فایل‌های بزرگ‌تر از ۲۰ مگ مثل دانلودر عادی به پارت تقسیم و ارسال می‌شوند.
"""

import os
import re
import time

import yt_dlp

from botcore import (CHUNK_MB, HOME_KB, MAX_SIZE, WORK_DIR, edit_message,
                     find_ffmpeg, human, kb, sanitize, send_document,
                     send_message, split_file, btn)


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
        "extractor_args": {"youtube": {"player_client": ["ios", "android", "web_safari", "web"]}},
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


def download(chat_id, url, audio=False, prefix=""):
    """دانلود ویدیو/صدا و ارسال (با تقسیم خودکار پارت)."""
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
            raise RuntimeError("یوتیوب این سرور را محدود کرده (Sign-in/Bot-check).\n"
                               "راه‌حل‌ها: لینک دیگری امتحان کن، از IP خانه/شخصی اجرا کن،\n"
                               "یا اپراتور ربات متغیر محیطی YT_COOKIES را ست کند.")
        raise

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
        raise RuntimeError("فایل خروجی پیدا نشد.")

    title = sanitize(info.get("title") or os.path.basename(path))
    size = os.path.getsize(path)
    if size > MAX_SIZE:
        try:
            os.remove(path)
        except OSError:
            pass
        raise RuntimeError(f"حجم فایل ({human(size)}) از حد مجاز بیشتر است.")

    parts = split_file(path)
    try:
        if len(parts) == 1:
            edit_message(chat_id, mid, f"📤 در حال ارسال «{title}» ({human(size)})…")
            send_document(chat_id, path, caption=f"{prefix}🎬 {title}\n💾 {human(size)}")
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


YT_KB = kb([[btn("🎬 حالت ویدیو", "yt:video"), btn("🎵 حالت فقط صدا", "yt:audio")],
            [btn("🏠 منوی اصلی", "menu:home")]])


def handle(chat_id, text, st):
    d = st["data"]
    t = (text or "").strip()
    if re.match(r"https?://(www\.)?(youtube\.com|youtu\.be|m\.youtube\.com)/", t, re.I):
        download(chat_id, t, audio=bool(d.get("audio_only")))
        return

    ma = re.fullmatch(r"(?:صدا|audio)\s*(\d{1,2})", t, re.I)
    mn = re.fullmatch(r"\d{1,2}", t)
    res = d.get("results") or []
    if ma or mn:
        idx = int((ma or mn).group(1)) - 1
        if not (0 <= idx < len(res)):
            send_message(chat_id, "این شماره در نتایج قبلی نیست؛ اول عنوان را بفرست تا جستجو کنم.", kb=YT_KB)
            return
        download(chat_id, res[idx]["url"], audio=bool(ma or d.get("audio_only")))
        return

    send_message(chat_id, f"🔍 در حال جستجوی یوتیوب: «{t}» …")
    res = search_yt(t)
    if not res:
        send_message(chat_id, "چیزی پیدا نشد؛ عبارت دیگری را امتحان کن.", kb=YT_KB)
        return
    d["results"] = res
    lines = [f"📺 نتایج «{t}»:", ""]
    for i, r in enumerate(res, 1):
        dur = ""
        if r.get("dur"):
            dur = f" ({int(r['dur'] // 60)}:{int(r['dur'] % 60):02d})"
        lines.append(f"{i}. {r['title']}{dur}")
    lines.append("")
    lines.append("🎬 شماره → دانلود ویدیو | 🎵 «صدا ۲» → فقط صدا")
    send_message(chat_id, "\n".join(lines), kb=YT_KB)


def callback(chat_id, act, st):
    d = st["data"]
    if act == "audio":
        d["audio_only"] = True
        send_message(chat_id, "🎵 حالت فقط-صدا فعال شد (خروجی mp3/m4a).", kb=YT_KB)
    elif act == "video":
        d["audio_only"] = False
        send_message(chat_id, "🎬 حالت ویدیو فعال شد (تا 720p).", kb=YT_KB)
