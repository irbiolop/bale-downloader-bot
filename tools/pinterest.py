#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
📌 ابزار پینترست: جستجوی پین + دانلود تصویر/ویدیوی اصلی با بالاترین کیفیت
"""

import html as H
import json
import os
import re
import time

import requests

from botcore import (BROWSER_UA, CHUNK, HOME_KB, WORK_DIR, human, sanitize,
                     send_document, send_message, send_photo, split_file)

_s = requests.Session()
_s.headers.update({
    "User-Agent": BROWSER_UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://www.pinterest.com/",
})


def _collect_urls(obj, out):
    if isinstance(obj, dict):
        for v in obj.values():
            _collect_urls(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _collect_urls(v, out)
    elif isinstance(obj, str) and "pinimg.com" in obj:
        out.append(obj.replace("\\/", "/"))


def _title(txt):
    m = re.search(r"<title[^>]*>(.*?)</title>", txt, re.S)
    return H.unescape(m.group(1)).strip()[:120] if m else ""


def parse_pin(url):
    """پارس پین → بهترین کیفیت تصویر/ویدیو."""
    r = _s.get(url, timeout=30, allow_redirects=True)
    r.raise_for_status()
    txt = r.text
    images, videos = [], []
    m = re.search(r'<script[^>]*id="__PWS_DATA__"[^>]*>(.*?)</script>', txt, re.S)
    if m:
        try:
            found = []
            _collect_urls(json.loads(H.unescape(m.group(1))), found)
            for u in found:
                if re.match(r"https://v\d*\.pinimg\.com", u):
                    videos.append(u)
                elif "i.pinimg.com" in u:
                    images.append(u)
        except Exception:  # noqa: BLE001
            pass
    if not images:
        for u in re.findall(r"https://i\.pinimg\.com/[^\"'\\\s]+", txt):
            images.append(u.replace("\\/", "/"))
    for u in re.findall(r"https://v\d*\.pinimg\.com/[^\"'\\\s]+?\.mp4[^\"'\\\s]*", txt):
        videos.append(u)

    img = next((u for u in images if "/originals/" in u), None)
    if not img and images:
        img = re.sub(r"/(?:\d+x\d*|\d+x|originals)/", "/originals/", sorted(set(images), key=len)[-1])
    vid = None
    for u in videos:
        if u.endswith(".mp4") and (vid is None or "720" in u or len(u) > len(vid)):
            vid = u
    if vid is None and videos:
        vid = videos[0]
    if not img and not vid:
        raise RuntimeError("مدیایی در این پین پیدا نکردم (پین خصوصی است یا ساختار صفحه عوض شده).")
    return {"image": img, "video": vid, "title": _title(txt) or "پین پینترست"}


def _download_media(url, ext):
    path = WORK_DIR / f"pin_{int(time.time())}{ext}"
    with _s.get(url, stream=True, timeout=(15, 900)) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for ch in r.iter_content(512 * 1024):
                if ch:
                    f.write(ch)
    return path


def download_pin(chat_id, url):
    info = parse_pin(url)
    title = info["title"]
    if info["video"]:
        edit = send_message(chat_id, "⬇️ در حال دانلود ویدیوی پین…")
        path = _download_media(info["video"], ".mp4")
        size = os.path.getsize(path)
        if size > CHUNK:
            parts = split_file(path)
            for i, p in enumerate(parts, 1):
                send_document(chat_id, p, caption=f"📌 {title}\nپارت {i}/{len(parts)}")
                time.sleep(1.2)
            for p in parts:
                try:
                    os.remove(p)
                except OSError:
                    pass
        else:
            send_document(chat_id, path, caption=f"📌 {title}\n💾 {human(size)}")
        try:
            os.remove(path)
        except OSError:
            pass
        from botcore import edit_message
        edit_message(chat_id, edit.get("message_id") if isinstance(edit, dict) else None, "✅ تمام شد!")
    else:
        path = None
        try:
            path = _download_media(info["image"], ".jpg")
        except Exception:  # noqa: BLE001  (originals گاهی 403 می‌دهد → نسخه‌ی ساده‌تر)
            info2 = parse_pin(url)
            imgs = [u for u in re.findall(r"https://i\.pinimg\.com/[^\"'\\\s]+",
                                          requests.get(url, headers=_s.headers, timeout=30).text)
                    if u.replace("\\/", "/") not in (info["image"],)]
            if not imgs:
                raise
            path = _download_media(imgs[0].replace("\\/", "/"), ".jpg")
        if os.path.getsize(path) <= 8 * 1024 * 1024:
            send_photo(chat_id, path, caption=f"📌 {title}")
        else:
            send_document(chat_id, path, caption=f"📌 {title}")
        try:
            os.remove(path)
        except OSError:
            pass


def search_pins(q, n=10):
    """جستجوی رسمی-داخلی پینترست (Resource API).
    توجه: پینترست برای IPهای دیتاسنتر معمولاً نتایج خالی برمی‌گرداند؛
    دانلود مستقیم لینک پین اما همه‌جا کار می‌کند."""
    data = {"options": {"article": None, "appliedProductFilters": "---",
                        "query": q, "page_size": n, "bookmarks": []},
            "context": {}}
    headers = dict(_s.headers)
    headers.update({
        "X-Requested-With": "XMLHttpRequest",
        "X-Pinterest-Appstate": "active",
        "X-Pinterest-PWS-Handler": "www/search/[scope].js",
        "X-Pinterest-Source-Url": f"/search/pins/?q={q}",
        "Accept": "application/json, text/javascript, */*, q=0.01",
    })
    params = {"source_url": f"/search/pins/?q={q}",
              "data": json.dumps(data, separators=(",", ":"))}
    r = _s.get("https://www.pinterest.com/resource/BaseSearchResource/get/",
               params=params, timeout=25, headers=headers)
    r.raise_for_status()
    j = r.json()
    rr = (j.get("resource_response") or {})
    results = ((rr.get("data") or {}).get("results")) or []
    out, seen = [], set()

    def _thumb(p):
        imgs = p.get("images") or {}
        for k in ("474x", "236x", "orig"):
            if k in imgs and imgs[k].get("url"):
                return imgs[k]["url"]
        first = next(iter(imgs.values()), {})
        return first.get("url")

    def walk(o):
        if isinstance(o, dict):
            if "images" in o and o.get("id") and str(o["id"]) not in seen:
                seen.add(str(o["id"]))
                out.append({"id": str(o["id"]),
                            "title": (o.get("title") or o.get("grid_title") or o.get("description") or "").strip()[:80],
                            "url": f"https://www.pinterest.com/pin/{o['id']}/",
                            "thumb": _thumb(o)})
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(results)
    if not out and rr.get("status") == "success" and not results:
        raise RuntimeError("پینترست برای این سرور نتیجه‌ی خالی برگرداند (محدودیت ضدربات).")
    return out[:n]


def handle(chat_id, text, st):
    d = st["data"]
    t = (text or "").strip()
    if re.match(r"https?://", t, re.I):
        send_message(chat_id, "📌 در حال دریافت پین…")
        download_pin(chat_id, t)
        return
    if re.fullmatch(r"\d{1,2}", t):
        res = d.get("results") or []
        i = int(t) - 1
        if not (0 <= i < len(res)):
            send_message(chat_id, "این شماره در نتایج نیست؛ اول جستجو کن.", kb=HOME_KB)
            return
        send_message(chat_id, f"📌 دانلود نتیجه {i + 1}…\n{res[i]['url']}")
        download_pin(chat_id, res[i]["url"])
        return
    send_message(chat_id, f"🔍 جستجوی پینترست: «{t}» …")
    try:
        res = search_pins(t)
    except Exception as e:  # noqa: BLE001
        send_message(chat_id, f"❌ جستجو ناموفق بود ({e}).\n"
                              "احتمالاً پینترست این سرور را محدود کرده؛ لینک پین را مستقیم بفرست.", kb=HOME_KB)
        return
    if not res:
        send_message(chat_id, "چیزی پیدا نشد؛ عبارت دیگری امتحان کن.", kb=HOME_KB)
        return
    d["results"] = res
    lines = [f"📌 نتایج «{t}»:", ""]
    for i, p in enumerate(res, 1):
        lines.append(f"{i}. {p['title'] or '(بدون عنوان)'}")
        lines.append(f"    {p['url']}")
    lines.append("")
    lines.append("🔢 شماره بفرست تا تصویر/ویدیوی اصلی با بالاترین کیفیت دانلود شود.")
    send_message(chat_id, "\n".join(lines), kb=HOME_KB)
