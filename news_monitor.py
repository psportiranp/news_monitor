# -*- coding: utf-8 -*-
"""
ربات رصد اخبار سایت‌ها
------------------------
این اسکریپت لیست سایت‌های موجود در urls.json را چک می‌کند، لینک‌های موجود
در هر صفحه را استخراج می‌کند و با اجرای قبلی مقایسه می‌کند. اگر لینک تازه‌ای
پیدا شود (که می‌تواند نشانه‌ی خبر جدید باشد)، یک پیام در تلگرام/ایتا برای
شما ارسال می‌کند.

نکته مهم: در اولین اجرا فقط وضعیت فعلی هر سایت ذخیره می‌شود (مبنا ساخته
می‌شود) و پیامی ارسال نمی‌شود. از اجرای دوم به بعد، خبرهای تازه گزارش
خواهند شد.
"""

import json
import os
import re
import sys
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

if getattr(sys, "frozen", False):
    # وقتی برنامه به exe تبدیل شده، فایل‌های کنار خودِ exe را بخوان
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
URLS_FILE = os.path.join(BASE_DIR, "urls.json")
STATE_FILE = os.path.join(BASE_DIR, "state.json")
LOG_FILE = os.path.join(BASE_DIR, "monitor.log")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

REQUEST_TIMEOUT = 20  # ثانیه
DELAY_BETWEEN_REQUESTS = 1.5  # ثانیه - برای اینکه سایت‌ها فشار زیادی نبینند
MAX_LINKS_PER_NOTIFICATION = 6
MIN_TITLE_LENGTH = 20  # حداقل طول متن لینک تا «تیتر خبر» در نظر گرفته شود

# لینک‌هایی که مسیرشان شامل این کلمات باشد، خبر واقعی نیستند (منو/دسته‌بندی/و...)
NOISE_PATH_KEYWORDS = (
    "/tag/", "/tags/", "/category/", "/categories/", "/author/", "/page/",
    "/search", "/login", "/signin", "/register", "/contact", "/about",
    "/privacy", "/rss", "/feed", "/print/", "/share", "/comment",
    "/wp-admin", "/wp-login", "/cdn-cgi", "/ads/", "/advert", "/sitemap",
)

NOISE_EXTENSIONS = (
    ".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
    ".pdf", ".zip", ".rar", ".mp4", ".mp3", ".ico", ".woff", ".woff2", ".ttf",
)

SOCIAL_DOMAINS = (
    "facebook.com", "twitter.com", "x.com", "t.me", "telegram.me",
    "instagram.com", "linkedin.com", "wa.me", "whatsapp.com",
    "youtube.com", "aparat.com", "eitaa.com",
)

PERSIAN_MONTHS = (
    "فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
    "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند",
)

RELATIVE_TIME_PHRASES = (
    "دقیقه پیش", "ساعت پیش", "لحظاتی پیش", "هم‌اکنون", "امروز", "دیروز",
)

DATE_REGEXES = [
    re.compile(r"\d{1,2}\s+(?:" + "|".join(PERSIAN_MONTHS) + r")\s+\d{2,4}"),
    re.compile(r"\d{3,4}/\d{1,2}/\d{1,2}"),
    re.compile(r"\d{1,2}/\d{1,2}/\d{3,4}"),
]


def log(message: str) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_json(path: str, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log(f"خطا در خواندن فایل {path}: {e}")
    return default


def save_json(path: str, data) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def send_telegram(token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = requests.post(
            url,
            data={"chat_id": chat_id, "text": text, "disable_web_page_preview": False},
            timeout=15,
        )
        if resp.status_code != 200:
            log(f"تلگرام پاسخ غیرمنتظره داد: {resp.status_code} - {resp.text[:200]}")
    except Exception as e:
        log(f"خطا در ارسال پیام تلگرام: {e}")


def send_eitaa(token: str, chat_id: str, text: str) -> None:
    """ارسال پیام به کانال ایتا از طریق سامانه eitaayar.ir"""
    url = f"https://eitaayar.ir/api/{token}/sendMessage"
    try:
        resp = requests.post(url, data={"chat_id": chat_id, "text": text}, timeout=15)
        try:
            data = resp.json()
        except Exception:
            data = None
        if resp.status_code != 200 or not (data and data.get("ok")):
            log(f"ایتا پاسخ غیرمنتظره داد: {resp.status_code} - {resp.text[:200]}")
    except Exception as e:
        log(f"خطا در ارسال پیام به ایتا: {e}")


def send_notification(config: dict, text: str) -> None:
    platform = config.get("notify_platform", "telegram")
    if platform == "eitaa":
        send_eitaa(config.get("eitaa_token"), config.get("eitaa_chat_id"), text)
    else:
        send_telegram(config.get("telegram_bot_token"), config.get("telegram_chat_id"), text)


def find_date_near(a_tag):
    """تلاش می‌کند تاریخ یا عبارت زمانی نزدیک به لینک را پیدا کند (تشخیص
    قطعی نیست، فقط بهترین تلاش است تا در پیام نشان داده شود)."""
    try:
        text = ""
        node = a_tag
        for _ in range(3):
            if node is None:
                break
            text += " " + node.get_text(" ", strip=True)
            node = node.parent
        text = text[:400]

        for phrase in RELATIVE_TIME_PHRASES:
            if phrase in text:
                return phrase
        for regex in DATE_REGEXES:
            m = regex.search(text)
            if m:
                return m.group(0)
    except Exception:
        pass
    return None


def extract_links(html: str, base_url: str) -> dict:
    """لینک‌های صفحه را استخراج می‌کند: {url کامل: {"text":..., "date":...}}"""
    soup = BeautifulSoup(html, "html.parser")
    links = {}
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        full_url = urljoin(base_url, href)
        if not full_url.startswith(("http://", "https://")):
            continue
        if full_url in links:
            continue
        text = " ".join(a.get_text(strip=True).split())[:140]
        date = find_date_near(a)
        links[full_url] = {"text": text, "date": date}
    return links


def _strip_www(netloc: str) -> str:
    netloc = netloc.lower()
    return netloc[4:] if netloc.startswith("www.") else netloc


def is_meaningful_link(url: str, base_netloc: str, title: str = "") -> bool:
    """فقط لینک‌هایی را قبول می‌کند که واقعاً شبیه یک خبر/مطلب باشند، نه
    صفحه‌ی اصلی، منو، دسته‌بندی، شبکه‌ی اجتماعی، فایل، یا لینک‌های خارجی."""
    parsed = urlparse(url)
    if parsed.path in ("", "/"):
        return False

    if _strip_www(parsed.netloc) != _strip_www(base_netloc):
        return False

    path_lower = parsed.path.lower()
    if any(keyword in path_lower for keyword in NOISE_PATH_KEYWORDS):
        return False
    if any(path_lower.endswith(ext) for ext in NOISE_EXTENSIONS):
        return False
    if any(domain in parsed.netloc.lower() for domain in SOCIAL_DOMAINS):
        return False

    if not title or len(title) < MIN_TITLE_LENGTH:
        return False

    return True


def check_site(category: str, url: str, state: dict, config: dict) -> bool:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except Exception as e:
        log(f"[خطا در دریافت] {category} | {url} -> {e}")
        return False

    try:
        base_netloc = urlparse(url).netloc
        links_now = extract_links(resp.text, url)
        links_now_urls = set(links_now.keys())

        prev_urls = set(state.get(url, []))
        is_first_run = url not in state

        # وضعیت همیشه به‌روزرسانی می‌شود تا همان لینک دوباره «تازه» دیده نشود
        state[url] = list(links_now_urls)

        if is_first_run:
            log(f"[مبنا ساخته شد] {category} | {url} ({len(links_now_urls)} لینک)")
            return False

        new_candidates = links_now_urls - prev_urls
        new_urls = [
            u for u in new_candidates
            if is_meaningful_link(u, base_netloc, links_now.get(u, {}).get("text", ""))
        ]

        if not new_urls:
            return False

        lines = [f"🔔 خبر تازه - {category}", url, ""]
        for u in new_urls[:MAX_LINKS_PER_NOTIFICATION]:
            info = links_now.get(u, {})
            title = info.get("text") or u
            date = info.get("date")
            line = f"• {title}"
            if date:
                line += f"  ({date})"
            line += f"\n  {u}"
            lines.append(line)

        if len(new_urls) > MAX_LINKS_PER_NOTIFICATION:
            lines.append(f"... و {len(new_urls) - MAX_LINKS_PER_NOTIFICATION} مورد دیگر")

        message = "\n".join(lines)
        send_notification(config, message)
        log(f"[خبر جدید] {category} | {url} | {len(new_urls)} مورد")
        return True

    except Exception as e:
        log(f"[خطا در پردازش] {category} | {url} -> {e}")
        return False


def load_config() -> dict:
    """تنظیمات را از config.json می‌خواند؛ اگر متغیرهای محیطی (مثلاً در
    گیت‌هاب اکشن‌ها) موجود باشند، آن‌ها را جایگزین می‌کند."""
    config = load_json(CONFIG_FILE, {})
    env_map = {
        "notify_platform": "NOTIFY_PLATFORM",
        "telegram_bot_token": "TELEGRAM_BOT_TOKEN",
        "telegram_chat_id": "TELEGRAM_CHAT_ID",
        "eitaa_token": "EITAA_TOKEN",
        "eitaa_chat_id": "EITAA_CHAT_ID",
    }
    for config_key, env_key in env_map.items():
        value = os.environ.get(env_key)
        if value:
            config[config_key] = value
    return config


def main():
    config = load_config()
    platform = config.get("notify_platform", "telegram")

    if platform == "eitaa":
        if not config.get("eitaa_token") or not config.get("eitaa_chat_id"):
            log("توکن یا chat_id ایتا تنظیم نشده است.")
            return
    else:
        if not config.get("telegram_bot_token") or not config.get("telegram_chat_id"):
            log("توکن یا chat_id تلگرام تنظیم نشده است.")
            return

    urls_by_category = load_json(URLS_FILE, {})
    if not urls_by_category:
        log("فایل urls.json خالی است یا پیدا نشد.")
        return

    state = load_json(STATE_FILE, {})

    total_sites = sum(len(v) for v in urls_by_category.values())
    log(f"شروع بررسی {total_sites} سایت در {len(urls_by_category)} دسته...")

    found = 0
    checked = 0
    try:
        for category, urls in urls_by_category.items():
            for url in urls:
                checked += 1
                try:
                    if check_site(category, url, state, config):
                        found += 1
                except Exception as e:
                    log(f"[خطای غیرمنتظره] {category} | {url} -> {e}")
                time.sleep(DELAY_BETWEEN_REQUESTS)
    finally:
        # حتی اگر برنامه وسط کار با خطا متوقف شود، وضعیتِ تا همین‌جا را
        # ذخیره می‌کنیم تا در اجرای بعدی دوباره همان خبرها تکراری گزارش نشوند
        save_json(STATE_FILE, state)

    log(f"پایان بررسی. {checked} سایت چک شد، {found} خبر تازه پیدا شد.")


if __name__ == "__main__":
    main()
