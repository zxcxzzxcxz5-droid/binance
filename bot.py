
import os
import json
import time
import requests

BASE = "https://fapi.binance.com"
TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
STATE_FILE = "state.json"

session = requests.Session()
session.headers.update({"User-Agent": "BinanceSignalBot/1.0"})


def get_json(path, params=None):
    response = session.get(
        BASE + path, params=params, timeout=20
    )
    response.raise_for_status()
    return response.json()


def get_rsi(closes, period=14):
    changes = [
        closes[i] - closes[i - 1]
        for i in range(1, len(closes))
    ]
    if len(changes) < period:
        return None

    gains = [max(x, 0) for x in changes]
    losses = [max(-x, 0) for x in changes]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(changes)):
        avg_gain = (
            avg_gain * (period - 1) + gains[i]
        ) / period
        avg_loss = (
            avg_loss * (period - 1) + losses[i]
        ) / period

    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0

    return 100 - 100 / (1 + avg_gain / avg_loss)


def send_telegram(message):
    response = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data={
            "chat_id": CHAT_ID,
            "text": message,
            "disable_web_page_preview": True,
        },
        timeout=20,
    )
    response.raise_for_status()


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"sent": []}


def save_state(state):
    # Keep only the latest 2000 signal IDs.
    state["sent"] = state["sent"][-2000:]
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def main():
    info = get_json("/fapi/v1/exchangeInfo")

    symbols = [
        item["symbol"]
        for item in info["symbols"]
        if item["status"] == "TRADING"
        and item["contractType"] == "PERPETUAL"
        and item["quoteAsset"] == "USDT"
    ]

    state = load_state()
    sent = set(state["sent"])
    new_signals = []

    for number, symbol in enumerate(symbols, 1):
        try:
            candles = get_json(
                "/fapi/v1/klines",
                {
                    "symbol": symbol,
                    "interval": "4h",
                    "limit": 100,
                },
            )

            # Exclude the still-forming candle.
            closed = candles[:-1]
            if len(closed) < 20:
                continue

            closes = [float(c[4]) for c in closed]
            rsi = get_rsi(closes)

            if rsi is None or rsi < 90:
                continue

            # Last confirmed volume must be lower
            # than the preceding confirmed candle.
            if float(closed[-1][5]) >= float(closed[-2][5]):
                continue

            candle_time = int(closed[-1][0])
            signal_id = f"{symbol}:{candle_time}"

            if signal_id in sent:
                continue

            # Current candle's open is the reference entry price.
            next_open = float(candles[-1][1])

            message = (
                "Binance 4H 숏 조건 감지\n\n"
                f"종목: {symbol}\n"
                f"확정봉 RSI(14): {rsi:.2f}\n"
                f"거래량 감소: 충족\n"
                f"다음 봉 시가 참고가: {next_open:.10g} USDT\n"
                f"확정봉 시작 시각(ms): {candle_time}\n\n"
                "자동 주문 없음. 진입 추천이 아닌 조건 감지 알림."
            )

            # Record only after Telegram accepts the message.
            send_telegram(message)
            sent.add(signal_id)
            new_signals.append(signal_id)

            # Save after every successful notification.
            state["sent"] = list(sent)
            save_state(state)

            time.sleep(0.1)

        except Exception as exc:
            print(f"ERROR {symbol}: {exc}")

        if number % 50 == 0:
            print(f"Scanned {number}/{len(symbols)} symbols")

    print(f"New signals sent: {len(new_signals)}")


if __name__ == "__main__":
    main()
