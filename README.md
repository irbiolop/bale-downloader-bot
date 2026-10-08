# 🤖 ربات دانلودر فایل — پیام‌رسان بله

رباتی که **لینک مستقیم فایل** می‌گیرد، آن را دانلود می‌کند و در چت برای شما می‌فرستد.
اگر حجم فایل بیشتر از **۲۰ مگابایت** باشد، آن را به‌صورت خودکار به **پارت‌های ۲۰ مگی** تقسیم کرده و پشت‌سرهم ارسال می‌کند.

- نام ربات بله: **@arixbot**

## ✨ قابلیت‌ها

- دانلود از لینک مستقیم HTTP/HTTPS با نمایش **درصد پیشرفت زنده**
- تقسیم خودکار فایل‌های بزرگ به پارت‌های ۲۰ مگابایتی (طبق مستندات بله، سقف آپلود هر فایل ۵۰ مگ است؛ پارت‌های ۲۰ مگی کاملاً امن هستند)
- ارسال خودکار همه‌ی پارت‌ها + پیام راهنمای بازسازی فایل اصلی
- تشخیص نام واقعی فایل از هدر `Content-Disposition` یا URL + حدس پسوند از `Content-Type`
- تشخیص لینک‌های صفحه‌ی وب (HTML) و هشدار به کاربر
- صف پردازش + دانلودهای همزمان (thread pool) + مدیریت ریت‌لیمیت (HTTP 429)
- **تمدید خودکار / ضدخاموشی**: سوپروایزر داخلی + `run.sh` + سرویس systemd → اگر ربات کرش کند یا اینترنت قطع شود، خودش دوباره بالا می‌آید و از همان‌جا ادامه می‌دهد (offset ذخیره می‌شود تا پیام‌ها دوباره پردازش نشوند)
- سقف حجم دانلود (پیش‌فرض ۲ گیگابایت) برای جلوگیری از سوءاستفاده

## 🚀 نصب و اجرا

```bash
pip install -r requirements.txt
python3 main.py        # اجرای ساده
# یا
./run.sh               # اجرای خودترمیم‌شونده (پیشنهادی)
```

## ⚙️ تنظیمات (متغیرهای محیطی)

| متغیر | پیش‌فرض | توضیح |
|---|---|---|
| `BOT_TOKEN` | توکن ربات بله | توکن ربات (از BotFather بله) |
| `API_BASE` | `https://tapi.bale.ai` | برای اجرا روی تلگرام: `https://api.telegram.org` |
| `CHUNK_MB` | `20` | اندازه‌ی هر پارت به مگابایت |
| `MAX_MB` | `2048` | حداکثر حجم قابل دانلود به مگابایت |
| `WORKERS` | `2` | تعداد پردازش همزمان |
| `WORK_DIR` | `./work` | پوشه‌ی موقت فایل‌ها |

مثال اجرا روی تلگرام با همین کد:

```bash
API_BASE=https://api.telegram.org BOT_TOKEN=<توکن‌بات‌تلگرام> python3 main.py
```

## ♾️ اجرای دائمی (۲۴/۷)

### روش ۱ — اسکریپت run.sh (ساده‌ترین)

```bash
chmod +x run.sh
nohup ./run.sh > bot.log 2>&1 &
```

### روش ۲ — سرویس systemd (لینوکس/VPS — پیشنهادی برای همیشه‌روشن بودن)

فایل `/etc/systemd/system/bale-bot.service` را بسازید:

```ini
[Unit]
Description=Bale File Downloader Bot
After=network-online.target

[Service]
WorkingDirectory=/path/to/bale-downloader-bot
ExecStart=/usr/bin/python3 /path/to/bale-downloader-bot/main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

سپس:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now bale-bot
sudo journalctl -u bale-bot -f     # مشاهده‌ی لاگ زنده
```

با `Restart=always` هر وقت ربات خطا بدهد، systemd ظرف ۵ ثانیه دوباره اجرایش می‌کند.

## 🧩 بازسازی فایل اصلی از پارت‌ها

همه‌ی پارت‌ها را در یک پوشه بریزید و یکی از این دستورها را اجرا کنید:

```bash
# لینوکس / مک
cat myfile.zip.part* > myfile.zip
```

```bat
:: ویندوز (CMD)
copy /b myfile.zip.part1+myfile.zip.part2+myfile.zip.part3 myfile.zip
```

## ⚠️ نکات امنیتی

- توکن ربات را محرمانه نگه دارید و در کد‌های عمومی/ریپوی پابلیک قرار ندهید.
- اگر توکن جایی به‌اشتراک گذاشته شد (مثلاً در چت)، از BotFather بله آن را ریست (Revoke) کنید.
- سقف `MAX_MB` را متناسب با منابع سرور تنظیم کنید؛ دانلود و آپلود فایل‌های خیلی بزرگ زمان‌بر است.
