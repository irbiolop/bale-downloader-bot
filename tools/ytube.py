#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
📺 ابزار یوتیوب — نسخه‌ی پروکسی‌محور
  • سرچ: Worker کلادفلر (/ytsearch) → فالبک: yt-dlp محلی
  • دانلود: Worker (/ytinfo + استریم از /proxy) — حتی از IP دیتاسنتر جواب می‌دهد
      - صدا: بهترین ترک صوتی → (در صورت وجود ffmpeg) تبدیل به MP3
      - ویدیو: اول فرمت muxed (تا 720p)؛ اگر نبود، بهترین ویدیوی mp4 ≤1080p + صدا → ادغام با ffmpeg
  • فالبک دانلود: yt-dlp با کلاینت‌های جایگزین (کد قدیمی‌ی تست‌شده)
  • فایل‌های بزرگ‌تر از ۲۰ مگ مثل دانلودر عادی پارت‌بندی و ارسال می‌شوند
"""

import os
import re
import secrets
import subprocess
import time

import requests
import yt_dlp

from botcore import (BROWSER_UA, CHUNK_MB, HOME_KB, MAX_SIZE, PROXY_ON, WORK_DIR,
                     bg, btn, edit_message, find_ffmpeg, human, kb, proxy_get,
                     proxy_url, sanitize, send_document, send_message, split_file)

VID_RE = re.compile(r"(?:v=|youtu\.be/|shorts/|embed/)([\w-]{6,})")


def _vid(url):
    m = VID_RE.search(url or "")
    return m.group(1) if m else ""


# ============================================================
# ۱) مسیر پروکسی (Worker)
# ============================================================

def search_yt_proxy(q, n=8):
    """سرچ از طریق Worker؛ خروجی: لیست dict یا None (پروکسی نیست/خطا)."""
    d = proxy_get("/ytsearch", q=q, n=n, timeout=45)
    if not d or not d.get("results"):
        return None
    out = []
    for r in d["results"][:n]:
        if not r.get("url"):
            continue
        out.append({
            "title": r.get("title") or "?",
            "url": r["url"],
            "videoId": r.get("videoId") or _vid(r["url"]),
            "dur": int(r.get("durationSec") or 0),
            "channel": r.get("channel") or "",
            "views": r.get("views") or "",
        })
    return out or None


def _info(vid):
    return proxy_get("/ytinfo", id=vid, timeout=60)


def _stream_dl(url, dest, progress_cb=None):
    """دانلود استریمی (از مسیر پروکسی) با گزارش پیشرفت هر ~۴ ثانیه."""
    done, total = 0, 0
    last = [0.0]
    with requests.get(url, stream=True, timeout=(25, 300),
                      headers={"User-Agent": BROWSER_UA}) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length") or 0)
        with open(dest, "wb") as f:
            for chunk in r.iter_content(512 * 1024):
                if not chunk:
                    continue
                f.write(chunk)
                done += len(chunk)
                if progress_cb and time.time() - last[0] >= 4:
                    last[0] = time.time()
                    progress_cb(done, total)
    if progress_cb:
        progress_cb(done, total)
    return done


def _prog(chat_id, mid, label):
    def cb(done, total):
        if total:
            pct = int(done * 100 / total)
            edit_message(chat_id, mid, f"⬇️ {label}: {pct}% — {human(done)} از {human(total)}")
        else:
            edit_message(chat_id, mid, f"⬇️ {label}: {human(done)} …")
    return cb


def _pick_audio(fmts):
    auds = [f for f in fmts if f.get("type") == "audio"]
    auds.sort(key=lambda f: f.get("bitrate") or 0, reverse=True)
    return auds[0] if auds else None


def _pick_video(muxed, adaptive, want_hd=True):
    """ویدیو: اول muxed تا 720p؛ بعد muxed کم‌کیفیت‌تر؛ بعد مسیر ادغام adaptive."""
    for f in sorted(muxed, key=lambda f: f.get("height") or 0, reverse=True):
        if (f.get("height") or 0) <= 720:
            return f, None
    if muxed:
        return sorted(muxed, key=lambda f: f.get("height") or 0)[0], None
    vids = [f for f in adaptive if f.get("type") == "video" and "mp4" in (f.get("mime") or "")]
    vids = [f for f in vids if (f.get("height") or 0) <= (1080 if want_hd else 480)]
    vids.sort(key=lambda f: f.get("height") or 0, reverse=True)
    if vids:
        return None, vids[0]
    return None, None


def _ffmpeg_run(ff, args, timeout=1800):
    r = subprocess.run([ff, "-y", "-loglevel", "error", *args],
                       capture_output=True, text=True, timeout=timeout)
    return r.returncode == 0, (r.stderr or "")[-300:]


def download_proxy(chat_id, vid, audio=False):
    """دانلود کامل یوتیوب از مسیر Worker + ارسال. None برمی‌گرداند اگر پروکسی ممکن نبود."""
    if not PROXY_ON or not vid:
        return None
    info = _info(vid)
    if not info or info.get("error"):
        return None  # → فالبک yt-dlp

    title = sanitize(info.get("title") or vid)
    muxed, adaptive = info.get("muxed") or [], info.get("adaptive") or []
    ff = find_ffmpeg()
    job_dir = WORK_DIR / f"yt_{time.strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3)}"
    job_dir.mkdir(parents=True, exist_ok=True)
    final = None

    prog = send_message(chat_id, "🛰 در حال دریافت از پروکسی…")
    mid = prog.get("message_id") if isinstance(prog, dict) else None

    try:
        if audio:
            a = _pick_audio(adaptive) or _pick_audio(muxed)
            if not a:
                edit_message(chat_id, mid, "❌ ترک صوتی پیدا نشد.")
                return True
            ext = ".m4a" if "mp4" in a["mime"] else (".opus" if "opus" in a["mime"] else ".weba")
            raw = job_dir / ("audio" + ext)
            edit_message(chat_id, mid, f"⬇️ صدا ({a.get('quality') or 'audio'})…")
            _stream_dl(proxy_url(a["url"]), raw, _prog(chat_id, mid, "صدا"))
            if ff:
                edit_message(chat_id, mid, "🎵 تبدیل به MP3…")
                final = job_dir / (title + ".mp3")
                ok, err = _ffmpeg_run(ff, ["-i", str(raw), "-vn", "-b:a", "192k", str(final)])
                if not ok or not final.exists() or final.stat().st_size == 0:
                    final = job_dir / (title + ext)
                    os.replace(raw, final)
            else:
                final = job_dir / (title + ext)
                os.replace(raw, final)
        else:
            muxed_f, adapt_f = _pick_video(muxed, adaptive)
            if muxed_f:
                final = job_dir / (title + ".mp4")
                q = muxed_f.get("quality") or f"{muxed_f.get('height') or '?'}p"
                edit_message(chat_id, mid, f"⬇️ ویدیو ({q})…")
                _stream_dl(proxy_url(muxed_f["url"]), final, _prog(chat_id, mid, "ویدیو"))
            elif adapt_f:
                if not ff:
                    edit_message(chat_id, mid,
                                 "⚠️ این ویدیو فقط فرمت جدا ویدیو/صدا دارد و ffmpeg در سرور نیست.\n"
                                 "فقط صدا را امتحان کن (🎵) یا روی سیستم شخصی اجرا کن.")
                    return True
                a = _pick_audio(adaptive)
                if not a:
                    edit_message(chat_id, mid, "❌ ترک صوتی پیدا نشد.")
                    return True
                vfile = job_dir / "video.mp4"
                afile = job_dir / "audio.m4a"
                q = adapt_f.get("quality") or f"{adapt_f.get('height') or '?'}p"
                edit_message(chat_id, mid, f"⬇️ ویدیو {q} + صدا (جدا)…")
                _stream_dl(proxy_url(adapt_f["url"]), vfile, _prog(chat_id, mid, "ویدیو"))
                edit_message(chat_id, mid, f"⬇️ ترک صدا…")
                _stream_dl(proxy_url(a["url"]), afile, _prog(chat_id, mid, "صدا"))
                edit_message(chat_id, mid, f"🔄 ادغام ویدیو+صدا ({q})…")
                final = job_dir / (title + ".mp4")
                ok, err = _ffmpeg_run(
                    ff, ["-i", str(vfile), "-i", str(afile), "-c", "copy",
                         "-movflags", "+faststart", str(final)])
                if not ok or not final.exists() or final.stat().st_size == 0:
                    raise RuntimeError("ffmpeg merge failed: " + err)
            else:
                edit_message(chat_id, mid, "❌ هیچ فرمت قابل دانلودی پیدا نشد.")
                return True

        if not final or not final.exists() or final.stat().st_size == 0:
            edit_message(chat_id, mid, "❌ فایل خروجی ساخته نشد.")
            return True

        size = final.stat().st_size
        if size > MAX_SIZE:
            edit_message(chat_id, mid, f"❌ حجم فایل ({human(size)}) از حد مجاز بیشتر است.")
            return True

        parts = split_file(final)
        try:
            if len(parts) == 1:
                edit_message(chat_id, mid, f"📤 در حال ارسال «{title}» ({human(size)})…")
                send_document(chat_id, final, caption=f"{'🎵' if audio else '🎬'} {title}\n💾 {human(size)}")
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
        return True
    finally:
        import shutil
        shutil.rmtree(job_dir, ignore_errors=True)


def download(chat_id, url, audio=False):
    """دانلود: اول پروکسی، اگر نشد yt-dlp. در ترد bg اجرا می‌شود."""
    vid = _vid(url)
    if PROXY_ON and vid:
        try:
            done = download_proxy(chat_id, vid, audio=audio)
            if done:
                return
            send_message(chat_id, "⚠️ دانلود از پروکسی ممکن نشد؛ روش جایگزین (yt-dlp) را امتحان می‌کنم…")
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            send_message(chat_id, f"⚠️ پروکسی خطا داد ({str(e)[:120]})؛ روش جایگزین را امتحان می‌کنم…")
    _download_ytdlp(chat_id, url, audio=audio)


# ============================================================
# ۲) مسیر فالبک: yt-dlp محلی (کد قبلی‌ی تست‌شده)
# ============================================================

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


def search_yt_fallback(q, n=8):
    with yt_dlp.YoutubeDL(_opts({"skip_download": True, "extract_flat": "in_playlist"})) as y:
        info = y.extract_info(f"ytsearch{n}:{q}", download=False)
    out = []
    for e in (info or {}).get("entries") or []:
        url = e.get("url") or e.get("webpage_url") or ""
        if url and not url.startswith("http"):
            url = "https://www.youtube.com/watch?v=" + url
        out.append({"title": e.get("title") or "?", "url": url,
                    "videoId": e.get("id") or "", "dur": e.get("duration") or 0,
                    "channel": "", "views": ""})
    return out


def _dur(d):
    try:
        d = int(d or 0)
        return f"{d // 3600}:{d % 3600 // 60:02d}:{d % 60:02d}" if d >= 3600 else (
            f"{d // 60}:{d % 60:02d}" if d else "؟")
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


def _download_ytdlp(chat_id, url, audio=False):
    """دانلود با yt-dlp (مسیر فالبک). در ترد bg اجرا می‌شود."""
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


# ============================================================
# ۳) هندلرها (سرچ/دکمه‌ها)
# ============================================================

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
        num = ma.group(1) if ma else mn.group(0)
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
    res = search_yt_proxy(t) if PROXY_ON else None
    engine = "پروکسی"
    if res is None:
        try:
            res = search_yt_fallback(t)
            engine = "yt-dlp"
        except Exception as e:  # noqa: BLE001
            send_message(chat_id, f"❌ جستجو ناموفق بود: {str(e)[:200]}\nچند لحظه بعد دوباره امتحان کن.", kb=YT_KB)
            return
    if not res:
        send_message(chat_id, "چیزی پیدا نشد؛ عبارت دیگری را امتحان کن.", kb=YT_KB)
        return
    d["results"] = res
    lines = [f"📺 نتایج «{t}» ({engine}):", "👇 روی هر نتیجه بزن تا ویدیو یا صدایش را بفرستم:", ""]
    for i, r in enumerate(res, 1):
        ch = f" — {r['channel'][:24]}" if r.get("channel") else ""
        lines.append(f"{i}. {r['title']}  ({_dur(r.get('dur'))}){ch}")
    lines.append("")
    lines.append("💡 یا «صدا 2» بفرست = نتیجه‌ی ۲ فقط صدا")
    send_message(chat_id, "\n".join(lines), kb=_results_kb(res))


def _card_text(r):
    ch = f"\n📺 کانال: {r['channel']}" if r.get("channel") else ""
    vw = f"\n👁 {r['views']}" if r.get("views") else ""
    return (f"🎬 انتخاب شد:\n«{r.get('title', '?')}»\n"
            f"⏱ مدت: {_dur(r.get('dur'))}{ch}{vw}\n\n"
            "کدام را برایت بفرستم؟ 👇")


def callback(chat_id, act, st):
    d = st["data"]
    res = d.get("results") or []

    if act == "audio":
        d["audio_only"] = True
        send_message(chat_id, "🎵 حالت فقط-صدا فعال شد (لینک یوتیوب بفرست یا جستجو کن).", kb=YT_KB)
    elif act == "video":
        d["audio_only"] = False
        send_message(chat_id, "🎬 حالت ویدیو فعال شد (تا 1080p).", kb=YT_KB)
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
