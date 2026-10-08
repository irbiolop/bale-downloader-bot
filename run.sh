#!/usr/bin/env bash
# ♾️ اجرای همیشگی ربات — اگر ربات به هر دلیلی خاموش شود، خودش ۵ ثانیه بعد دوباره بالا می‌آید
cd "$(dirname "$0")"
mkdir -p work
while true; do
    python3 main.py
    echo "[run.sh] bot exited (code=$?) → restart in 5s..."
    sleep 5
done
