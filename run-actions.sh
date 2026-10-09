#!/usr/bin/env bash
# 🔄 اجرای هر دو ربات (بله + تلگرام اختیاری) داخل یک سیکل GitHub Actions
#     با سوپروایزر داخلی: اگر ربات کرش کند تا پایان سیکل دوباره بالا می‌آید
set -u
cd "$(dirname "$0")"

if [ -z "${BOT_TOKEN:-}" ]; then
    echo "FATAL: BOT_TOKEN secret is missing!"
    exit 42
fi
mkdir -p work work_tg

SUP() {
    while true; do
        python3 main.py
        echo "[sup] bot exited ($?) → restart in 5s"
        sleep 5
    done
}

# --- ربات بله (اصلی) ---
export BOT_TOKEN
SUP & P1=$!
echo "[run-actions] bale bot supervisor started (pid=$P1)"

# --- ربات تلگرام (اختیاری — فقط وقتی با with_tg=true اجرا شود) ---
P2=""
if [ "${WITH_TG:-false}" = "true" ] && [ -n "${TG_TOKEN:-}" ]; then
    (
        export API_BASE="https://api.telegram.org" BOT_TOKEN="$TG_TOKEN"
        export WORK_DIR=./work_tg STATE_FILE=./offset_tg.json
        SUP
    ) & P2=$!
    echo "[run-actions] telegram bot supervisor started (pid=$P2)"
else
    echo "[run-actions] telegram bot skipped (WITH_TG=${WITH_TG:-false})"
fi

cleanup() {
    echo "[run-actions] SIGTERM → stopping bots..."
    kill $P1 $P2 2>/dev/null
    sleep 2
    pkill -9 -f "python3 main.py" 2>/dev/null
    exit 124
}
trap cleanup TERM INT

wait $P1 ${P2:+$P2}
