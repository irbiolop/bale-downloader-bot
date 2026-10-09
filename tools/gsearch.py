#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🔍 ابزار جستجو — زنجیره‌ی چندموتوره با فالبک خودکار:
  Google → DuckDuckGo → Bing → Google News RSS (تقریباً همیشه در دسترس)
عدد نتیجه را بفرست → ابزار HTML همان صفحه فعال می‌شود.
"""

import re
import html as H
from urllib.parse import unquote

import requests

from botcore import BROWSER_UA, HOME_KB, send_message

UA = {"User-Agent": BROWSER_UA, "Accept-Language": "fa,en;q=0.8"}
_s = requests.Session()
_s.headers.update(UA)


def _clean(s):
    return H.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def _google(q, n=8):
    """اسکرپ نتایج وب گوگل (روی IP های مسکونی جواب می‌دهد)."""
    r = _s.get("https://www.google.com/search",
               params={"q": q, "num": n + 4, "hl": "fa", "safe": "off"}, timeout=15)
    r.raise_for_status()
    out, seen = [], set()
    for m in re.finditer(r'<a href="(https?://[^"]+)"[^>]*>.{0,400}?<h3[^>]*>(.*?)</h3>', r.text, re.S):
        url = H.unescape(m.group(1))
        title = _clean(m.group(2))
        host = url.split("/")[2] if "://" in url else ""
        if "google." in host or url in seen or not title:
            continue
        seen.add(url)
        out.append({"title": title, "url": url})
        if len(out) >= n:
            break
    return out


def _ddg(q, n=8):
    """اسکرپ DuckDuckGo HTML."""
    r = _s.post("https://html.duckduckgo.com/html/", data={"q": q}, timeout=15)
    r.raise_for_status()
    out, seen = [], set()
    for m in re.finditer(
            r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.{0,600}?'
            r'(?:class="result__snippet"[^>]*>(.*?)</a>)?',
            r.text, re.S):
        url = m.group(1)
        if url.startswith("//"):
            url = "https:" + url
        mm = re.search(r"uddg=([^&]+)", url)
        if mm:
            url = unquote(mm.group(1))
        if url in seen:
            continue
        seen.add(url)
        out.append({"title": _clean(m.group(2)), "url": url,
                    "snippet": _clean(m.group(3) or "")[:160]})
        if len(out) >= n:
            break
    return out


def _bing(q, n=8):
    """اسکرپ Bing (b_algo)."""
    r = _s.get("https://www.bing.com/search", params={"q": q, "setlang": "fa"}, timeout=15)
    r.raise_for_status()
    out, seen = [], set()
    for m in re.finditer(r'<li class="b_algo".{0,600}?<a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>', r.text, re.S):
        url, title = m.group(1), _clean(m.group(2))
        if url in seen or not title:
            continue
        seen.add(url)
        out.append({"title": title, "url": url})
        if len(out) >= n:
            break
    return out


def _gnews(q, n=10):
    """گوگل‌نیوز RSS — تقریباً همیشه در دسترس (حتی از سرورها)."""
    r = _s.get("https://news.google.com/rss/search",
               params={"q": q, "hl": "fa", "gl": "IR", "ceid": "IR:fa"}, timeout=20)
    r.raise_for_status()
    out = []
    for m in re.finditer(r"<item><title>(.*?)</title><link>(.*?)</link>", r.text):
        title = _clean(m.group(1))
        link = m.group(2).strip()
        title = re.sub(r"\s+-\s+[^-]{2,40}$", "", title)  # حذف پسوند نام منبع خبری
        out.append({"title": title[:100], "url": link})
        if len(out) >= n:
            break
    return out


def search(q, n=8):
    """زنجیره‌ی موتورها تا اولین نتیجه‌ی غیرخالی. خروجی: (نتایج, نام موتور)"""
    for fn, name in ((_google, "Google"), (_ddg, "DuckDuckGo"), (_bing, "Bing"), (_gnews, "Google News")):
        try:
            rs = fn(q, n)
            if rs:
                return rs, name
        except Exception:  # noqa: BLE001
            continue
    return [], None


def handle(chat_id, text, st):
    """حالت gsearch: عبارت جستجو یا شماره نتیجه (→ HTML)."""
    d = st["data"]
    t = (text or "").strip()
    if re.fullmatch(r"\d{1,2}", t):
        res = d.get("results") or []
        i = int(t) - 1
        if not (0 <= i < len(res)):
            send_message(chat_id, "این شماره در نتایج قبلی نیست؛ اول جستجو کن.", kb=HOME_KB)
            return
        url = res[i]["url"]
        send_message(chat_id, f"🌐 گرفتن HTML نتیجه‌ی {i + 1}…\n{url}")
        import tools.htmlfetch as htmlfetch
        htmlfetch.fetch_and_send(chat_id, url)
        return
    send_message(chat_id, f"🔍 در حال جستجوی «{t}» …")
    rs, engine = search(t)
    if not rs:
        send_message(chat_id, "نتیجه‌ای پیدا نشد یا همه‌ی موتورهای جستجو پاسخ ندادند.\n"
                              "عبارت دیگری را امتحان کن یا چند لحظه بعد دوباره بفرست.", kb=HOME_KB)
        return
    d["results"] = rs
    lines = [f"🔍 نتایج «{t}» ({engine}):", ""]
    for i, r in enumerate(rs, 1):
        lines.append(f"{i}. {r['title']}")
        lines.append(f"    {r['url']}")
        if r.get("snippet"):
            lines.append(f"    └ {r['snippet']}")
    lines.append("")
    lines.append("🔢 شماره نتیجه را بفرست تا HTML کامل آن صفحه را بگیری.")
    send_message(chat_id, "\n".join(lines), kb=HOME_KB)
