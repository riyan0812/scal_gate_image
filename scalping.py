import ccxt
import pandas as pd
import numpy as np
import time
from datetime import datetime, timezone, timedelta
import logging
from collections import defaultdict
import requests
import mplfinance as mpf
import matplotlib.pyplot as plt
import os

# Konfigurasi
TIMEFRAMES = ['15m']
EMA_10 = 10
EMA_30 = 30
EMA_100 = 100
TAKE_PROFIT_PERCENT = 0.5
STOP_LOSS_PERCENT = 0.3
CHART_CANDLE_COUNT = 50  # Jumlah candle pada chart

# Discord Config
DISCORD_WEBHOOK_URL = 'https://discord.com/api/webhooks/1372589339040546836/lA_oauH6tmlAEwAVdBY3obj1e3vD9N2MkI5dd8EmJSF43C8NWd1sWzWUPcvVgUsXjdbZ'

# Setup Exchange - Gate.io
exchange = ccxt.gate({
    'enableRateLimit': True,
    'options': {
        'defaultType': 'spot'
    }
})

# Zona waktu WIB
WIB = timezone(timedelta(hours=7))

# Sistem Anti-Duplikat dan Pelacakan Sinyal
signal_history = defaultdict(dict)
daily_stats = {
    'valid_signals': 0,
    'invalid_signals': 0,
    'signals_by_pair': defaultdict(int),
    'last_report_date': None
}

def send_discord_message(content, file_path=None):
    try:
        payload = {
            "content": content,
            "username": "EMA Scalping Bot (Gate.io)"
        }
        files = None
        if file_path:
            files = {'file': open(file_path, 'rb')}
        response = requests.post(DISCORD_WEBHOOK_URL, data={'payload_json': json.dumps(payload)}, files=files)
        response.raise_for_status()
        if file_path:
            os.remove(file_path)  # Hapus file setelah dikirim
    except Exception as e:
        logging.error(f"Discord send error: {str(e)}")

def create_candlestick_chart(data, pair, tf, signal_data):
    """Buat chart candlestick dengan EMA dan simpan sebagai PNG."""
    # Konversi data OHLCV ke DataFrame
    df = pd.DataFrame(data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df.set_index('timestamp', inplace=True)

    # Hitung EMA
    df['ema10'] = df['close'].ewm(span=EMA_10, adjust=False).mean()
    df['ema30'] = df['close'].ewm(span=EMA_30, adjust=False).mean()
    df['ema100'] = df['close'].ewm(span=EMA_100, adjust=False).mean()

    # Ambil data terakhir untuk chart
    df = df[-CHART_CANDLE_COUNT:]

    # Konfigurasi style chart
    mc = mpf.make_marketcolors(up='green', down='red', wick='black', volume='blue')
    s = mpf.make_mpf_style(marketcolors=mc, gridstyle='--')

    # Tambahkan EMA ke plot
    apds = [
        mpf.make_addplot(df['ema10'], color='blue', label='EMA10', width=1),
        mpf.make_addplot(df['ema30'], color='orange', label='EMA30', width=1),
        mpf.make_addplot(df['ema100'], color='purple', label='EMA100', width=1),
    ]

    # Tambahkan penanda sinyal pada candle terakhir
    signal_color = 'green' if signal_data['signal'] == 'BUY' else 'red' if signal_data['signal'] == 'SELL' else 'gray'
    signal_marker = '^' if signal_data['signal'] == 'BUY' else 'v' if signal_data['signal'] == 'SELL' else 'o'
    signal_y = df['high'].iloc[-1] * 1.01 if signal_data['signal'] == 'BUY' else df['low'].iloc[-1] * 0.99
    signal_plot = mpf.make_addplot(
        [np.nan] * (len(df)-1) + [signal_y],
        scatter=True, markersize=100, marker=signal_marker, color=signal_color
    )
    apds.append(signal_plot)

    # Simpan chart
    file_path = f"chart_{pair.replace('/', '_')}_{tf}_{int(time.time())}.png"
    mpf.plot(
        df, type='candle', style=s, title=f"{pair} {tf} Signal",
        addplot=apds, volume=True, savefig=file_path
    )
    return file_path

def get_top_100_coins():
    """Fetch top 100 coins by market cap from CoinGecko."""
    try:
        url = "https://api.coingecko.com/api/v3/coins/markets"
        params = {
            'vs_currency': 'usd',
            'order': 'market_cap_desc',
            'per_page': 100,
            'page': 1,
            'sparkline': False
        }
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        coins = response.json()
        return [coin['symbol'].upper() for coin in coins]
    except Exception as e:
        logging.error(f"Error fetching top 100 coins: {str(e)}")
        return ['BTC', 'ETH', 'ADA', 'TRX', 'NEAR', 'SHIB', 'ETC']

def get_all_spot_pairs():
    try:
        top_coins = set(get_top_100_coins())
        logging.info(f"Fetched {len(top_coins)} top coins by market cap")
        markets = exchange.load_markets()
        usdt_pairs = []
        for symbol, market in markets.items():
            if (market['spot'] and
                market['active'] and
                market['quote'] == 'USDT'):
                base_coin = symbol.split('/')[0]
                if base_coin in top_coins:
                    volume = float(market.get('quoteVolume', market.get('baseVolume', 0)))
                    usdt_pairs.append((symbol, volume))
        usdt_pairs.sort(key=lambda x: x[1], reverse=True)
        selected_pairs = [pair[0] for pair in usdt_pairs]
        logging.info(f"Found {len(selected_pairs)} USDT pairs for top 100 coins")
        if not selected_pairs:
            logging.warning("No USDT pairs found, using fallback")
            return ['ADA/USDT', 'TRX/USDT', 'NEAR/USDT', 'SHIB/USDT', 'BTC/USDT', 'ETH/USDT', 'ETC/USDT']
        return selected_pairs[:50]
    except Exception as e:
        logging.error(f"Error loading markets: {str(e)}")
        return ['ADA/USDT', 'TRX/USDT', 'NEAR/USDT', 'SHIB/USDT', 'BTC/USDT', 'ETH/USDT', 'ETC/USDT']

PAIRS = get_all_spot_pairs()

def calculate_emas(closes):
    df = pd.DataFrame({'close': closes})
    return (
        df['close'].ewm(span=EMA_10, adjust=False).mean().iloc[-1],
        df['close'].ewm(span=EMA_30, adjust=False).mean().iloc[-1],
        df['close'].ewm(span=EMA_100, adjust=False).mean().iloc[-1]
    )

def generate_signal_message(pair, tf, data):
    arrow = "🟢" if data['signal'] == 'BUY' else "🔴" if data['signal'] == 'SELL' else "⚪"
    validity = "Valid" if data['is_valid'] else "Invalid"
    return (
        f"{arrow} **{pair} {tf} Signal**\n"
        f"Validity: {validity}\n"
        f"Price: {data['price']:.6f}\n"
        f"Take Profit: {data['take_profit']:.6f}\n"
        f"Stop Loss: {data['stop_loss']:.6f}\n"
        f"Time: {data['time'].strftime('%Y-%m-%d %H:%M:%S')} WIB\n"
        f"Exchange: Gate.io\n"
        f"Chart: See attached"
    )

def send_daily_report():
    total_signals = daily_stats['valid_signals'] + daily_stats['invalid_signals']
    valid_percent = (daily_stats['valid_signals'] / total_signals * 100) if total_signals > 0 else 0
    signals_by_pair = "\n".join([f"{pair}: {count}" for pair, count in daily_stats['signals_by_pair'].items()])
    report = (
        f"📊 **Daily Trading Report ({datetime.now(WIB).strftime('%Y-%m-%d')})**\n"
        f"```\n"
        f"Valid Signals:   {daily_stats['valid_signals']}\n"
        f"Invalid Signals: {daily_stats['invalid_signals']}\n"
        f"Valid Percent:   {valid_percent:.2f}%\n"
        f"Total Signals:   {total_signals}\n"
        f"\nSignals by Pair:\n{signals_by_pair}\n"
        f"```\n"
        f"Time: {datetime.now(WIB).strftime('%Y-%m-%d %H:%M:%S')} WIB"
    )
    send_discord_message(report)
    logging.info("Daily report sent")
    daily_stats['valid_signals'] = 0
    daily_stats['invalid_signals'] = 0
    daily_stats['signals_by_pair'] = defaultdict(int)
    daily_stats['last_report_date'] = datetime.now(WIB).date()

def monitor():
    while True:
        try:
            current_time = datetime.now(WIB)
            if (current_time.hour == 0 and current_time.minute == 0 and
                (daily_stats['last_report_date'] != current_time.date())):
                send_daily_report()

            for pair in PAIRS:
                for tf in TIMEFRAMES:
                    try:
                        data = None
                        for _ in range(3):
                            try:
                                data = exchange.fetch_ohlcv(pair, tf, limit=EMA_100+CHART_CANDLE_COUNT)
                                break
                            except Exception as e:
                                logging.warning(f"Retry fetching {pair} {tf}: {str(e)}")
                                time.sleep(5)

                        if not data or len(data) < EMA_100:
                            continue

                        closes = np.array([x[4] for x in data])
                        current_price = closes[-1]
                        ema10, ema30, ema100 = calculate_emas(closes)

                        if ema10 > ema30 > ema100:
                            current_signal = 'BUY'
                            is_valid = True
                        elif ema10 < ema30 < ema100:
                            current_signal = 'SELL'
                            is_valid = True
                        else:
                            current_signal = 'NEUTRAL'
                            is_valid = False

                        signal_key = f"{pair}|{tf}"

                        if (signal_key not in signal_history or
                            signal_history[signal_key]['signal'] != current_signal):

                            if current_signal in ['BUY', 'SELL']:
                                tp = current_price * (1 + TAKE_PROFIT_PERCENT/100) if current_signal == 'BUY' else current_price * (1 - TAKE_PROFIT_PERCENT/100)
                                sl = current_price * (1 - STOP_LOSS_PERCENT/100) if current_signal == 'BUY' else current_price * (1 + STOP_LOSS_PERCENT/100)
                            else:
                                tp = sl = current_price

                            signal_data = {
                                'signal': current_signal,
                                'is_valid': is_valid,
                                'price': current_price,
                                'ema10': ema10,
                                'ema30': ema30,
                                'ema100': ema100,
                                'take_profit': tp,
                                'stop_loss': sl,
                                'time': datetime.now(WIB)
                            }

                            signal_history[signal_key] = signal_data
                            daily_stats['signals_by_pair'][pair] += 1
                            if is_valid:
                                daily_stats['valid_signals'] += 1
                            else:
                                daily_stats['invalid_signals'] += 1

                            # Buat dan kirim chart
                            chart_file = create_candlestick_chart(data, pair, tf, signal_data)
                            message = generate_signal_message(pair, tf, signal_data)
                            send_discord_message(message, chart_file)
                            logging.info(f"Signal sent with chart: {pair} {tf} {current_signal} (Valid: {is_valid})")
                            time.sleep(1)

                    except Exception as e:
                        logging.error(f"Error processing {pair} {tf}: {str(e)}")
                        time.sleep(10)

            time.sleep(30)

        except KeyboardInterrupt:
            break
        except Exception as e:
            logging.error(f"Monitor error: {str(e)}")
            time.sleep(30)

if __name__ == "__main__":
    import json
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler('gateio_scalping.log'),
            logging.StreamHandler()
        ]
    )

    logging.info("Starting Gate.io EMA Scalping Bot with Candlestick Charts")
    monitor()
