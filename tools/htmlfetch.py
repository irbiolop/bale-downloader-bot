#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🌐 ابزار دریافت HTML کامل صفحه + خلاصه محتوا + آمار مخلفات (تصویر/CSS/JS/مدیا)
فایل HTML با تگ <base> ذخیره می‌شود تا وقتی بازش می‌کنی، تصاویر و استایل هم لود شوند.
"""

import os
import re
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from botcore import BROWSER_UA, WORK_DIR, HOME_KB, sanitize, send_document, send_message

_s = requests.Session()
_s.headers.update({"User-Agent": BROWSER_UA,
                   "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                   "Accept-Language": "fa,en;q=0.8"})
MAX_HTML_MB = 30


def fetch(url):
    """دریافت و پارس صفحه. خروجی: (مسیر فایل HTML، دیکشنری اطلاعات)"""
    r = _s.get(url, timeout=(15, 90), allow_redirects=True)
    r.raise_for_status()
    if len(r.content) > MAX_HTML_MB * 1024 * 1024:
        raise RuntimeError(f"صفحه خیلی بزرگ است ({len(r.content) // 1048576} مگ)")
    r.encoding = r.apparent_encoding or r.encoding or "utf-8"
    soup = BeautifulSoup(r.text, "html.parser")

    title = (soup.title.get_text(strip=True) if soup.title else "")[:200]
    desc = ""
    md = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", attrs={"property": "og:description"})
    if md and md.get("content"):
        desc = md["content"].strip()[:400]

    # تزریق <base> برای نمایش درست تصاویر/استایل در فایل ذخیره‌شده
    if soup.head is None:
        soup.html.wrap(soup.new_tag("head")) if soup.html else None
    if soup.head and not soup.head.find("base"):
        soup.head.insert(0, soup.new_tag("base", href=r.url))

    assets = {"img": [], "css": [], "js": [], "media": []}
    for im in soup.find_all("img", src=True)[:300]:
        assets["img"].append(urljoin(r.url, im["src"]))
    for lk in soup.find_all("link", href=True):
        rel = lk.get("rel") or []
        if "stylesheet" in rel or (isinstance(rel, list) and "stylesheet" in " ".join(rel)):
            assets["css"].append(urljoin(r.url, lk["href"]))
    for sc in soup.find_all("script", src=True):
        assets["js"].append(urljoin(r.url, sc["src"]))
    for vd in soup.find_all(["video", "audio", "source"], src=True):
        assets["media"].append(urljoin(r.url, vd["src"]))

    text = re.sub(r"\n{3,}", "\n\n", soup.get_text("\n", strip=True))

    fname = f"page_{int(time.time())}_{sanitize(title or 'page')[:40]}.html"
    path = WORK_DIR / fname
    path.write_text(str(soup), encoding="utf-8")
    return path, {"url": r.url, "title": title, "desc": desc, "text": text,
                  "assets": assets, "size": len(r.content)}


def fetch_and_send(chat_id, url):
    try:
        path, info = fetch(url)
    except Exception as e:  # noqa: BLE001
        send_message(chat_id, f"❌ دریافت صفحه ناموفق بود: {e}", kb=HOME_KB)
        return
    a = info["assets"]
    rep = (
        f"📄 عنوان: {info['title'] or '—'}\n"
        f"🔗 آدرس نهایی: {info['url']}\n"
        f"💾 حجم سورس: {info['size'] / 1024:.1f} KB\n"
        f"🖼 تصاویر: {len(a['img'])} | 🎨 CSS: {len(a['css'])} | ⚙️ JS: {len(a['js'])} | 🎬 مدیا: {len(a['media'])}\n"
    )
    if info["desc"]:
        rep += f"\n📝 توضیحات: {info['desc']}\n"
    rep += "\n📝 متن صفحه:\n" + (info["text"][:1800] or "—")
    if a["img"]:
        rep += "\n\n🖼 چند نمونه تصویر:\n" + "\n".join(a["img"][:8])
    send_message(chat_id, rep, kb=HOME_KB)
    try:
        send_document(chat_id, path, caption="🌐 سورس کامل HTML (با تگ base)")
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def handle(chat_id, text, st):
    t = (text or "").strip()
    if not re.match(r"https?://", t, re.I):
        send_message(chat_id, "لینک کامل صفحه را بفرست (شروع‌شده با http یا https).", kb=HOME_KB)
        return
    send_message(chat_id, f"🌐 در حال دریافت و پارس صفحه…\n{t}")
    fetch_and_send(chat_id, t)
