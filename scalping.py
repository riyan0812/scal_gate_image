import ccxt
import pandas as pd
import numpy as np
import time
from datetime import datetime, timezone, timedelta
import logging
from collections import defaultdict
import requests
import mplfinance as mpf
import os
import json

# Konfigurasi
TIMEFRAMES = ['1m']
EMA_10 = 10
EMA_30 = 30
EMA_100 = 100
TAKE_PROFIT_PERCENT = 0.5
STOP_LOSS_PERCENT = 0.3
CHART_CANDLE_COUNT = 100  # Jumlah candle pada chart

# Discord Config
DISCORD_WEBHOOK_URL = 'https://discord.com/api/webhooks/xxxxxxxx'

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
    'signals_by_pair': defaultdict(int),
    'last_report_date': None
}

def send_discord_message(content, file_path=None):
    logging.debug(f"Sending Discord message: {content[:100]}... (file: {file_path})")
    try:
        payload = {
            "content": content,
            "username": "EMA Scalping Bot (Gate.io)"
        }
        files = None
        if file_path:
            files = {'file': open(file_path, 'rb')}
            logging.debug(f"Attaching file: {file_path}")
        response = requests.post(DISCORD_WEBHOOK_URL, data={'payload_json': json.dumps(payload)}, files=files)
        response.raise_for_status()
        logging.debug(f"Discord message sent successfully, status code: {response.status_code}")
        if file_path:
            os.remove(file_path)
            logging.debug(f"Removed temporary chart file: {file_path}")
    except Exception as e:
        logging.error(f"Discord send error: {str(e)}")

def create_candlestick_chart(data, pair, tf, signal_data):
    """Buat chart candlestick dengan EMA dan simpan sebagai PNG."""
    logging.debug(f"Creating candlestick chart for {pair} {tf}, signal: {signal_data['signal']}")
    df = pd.DataFrame(data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df.set_index('timestamp', inplace=True)

    df['ema10'] = df['close'].ewm(span=EMA_10, adjust=False).mean()
    df['ema30'] = df['close'].ewm(span=EMA_30, adjust=False).mean()
    df['ema100'] = df['close'].ewm(span=EMA_100, adjust=False).mean()
    logging.debug(f"EMA values for {pair} {tf}: ema10={df['ema10'].iloc[-1]:.6f}, ema30={df['ema30'].iloc[-1]:.6f}, ema100={df['ema100'].iloc[-1]:.6f}")

    df = df[-CHART_CANDLE_COUNT:]

    mc = mpf.make_marketcolors(up='green', down='red', wick='black', volume='blue')
    s = mpf.make_mpf_style(marketcolors=mc, gridstyle='--')

    apds = [
        mpf.make_addplot(df['ema10'], color='blue', label='EMA10', width=1),
        mpf.make_addplot(df['ema30'], color='orange', label='EMA30', width=1),
        mpf.make_addplot(df['ema100'], color='purple', label='EMA100', width=1),
    ]

    signal_color = 'green' if signal_data['signal'] == 'BUY' else 'red' if signal_data['signal'] == 'SELL' else 'gray'
    signal_marker = '^' if signal_data['signal'] == 'BUY' else 'v' if signal_data['signal'] == 'SELL' else 'o'
    signal_y = df['high'].iloc[-1] * 1.01 if signal_data['signal'] == 'BUY' else df['low'].iloc[-1] * 0.99
    signal_plot = mpf.make_addplot(
        [np.nan] * (len(df)-1) + [signal_y],
        scatter=True, markersize=100, marker=signal_marker, color=signal_color
    )
    apds.append(signal_plot)

    file_path = f"chart_{pair.replace('/', '_')}_{tf}_{int(time.time())}.png"
    mpf.plot(
        df, type='candle', style=s, title=f"{pair} {tf} Signal",
        addplot=apds, volume=True, savefig=file_path
    )
    logging.debug(f"Candlestick chart saved: {file_path}")
    return file_path

def get_top_100_coins():
    """Fetch top 100 coins by market cap from CoinGecko."""
    logging.debug("Fetching top 100 coins from CoinGecko")
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
        coin_symbols = [coin['symbol'].upper() for coin in coins]
        logging.debug(f"Retrieved {len(coin_symbols)} coins: {coin_symbols[:10]}...")
        return coin_symbols
    except Exception as e:
        logging.error(f"Error fetching top 100 coins: {str(e)}")
        logging.debug("Falling back to default coins")
        return ['BTC', 'ETH', 'ADA', 'TRX', 'NEAR', 'SHIB', 'ETC']

def get_all_spot_pairs():
    logging.debug("Fetching all spot pairs from Gate.io")
    try:
        top_coins = set(get_top_100_coins())
        logging.info(f"Fetched {len(top_coins)} top coins by market cap")
        markets = exchange.load_markets()
        logging.debug(f"Loaded {len(markets)} markets from Gate.io")
        usdt_pairs = []
        for symbol, market in markets.items():
            if (market['spot'] and
                market['active'] and
                market['quote'] == 'USDT'):
                base_coin = symbol.split('/')[0]
                if base_coin in top_coins:
                    volume = float(market.get('quoteVolume', market.get('baseVolume', 0)))
                    usdt_pairs.append((symbol, volume))
                    logging.debug(f"Added pair {symbol} with volume {volume}")
        usdt_pairs.sort(key=lambda x: x[1], reverse=True)
        selected_pairs = [pair[0] for pair in usdt_pairs]
        logging.info(f"Found {len(selected_pairs)} USDT pairs for top 100 coins")
        logging.debug(f"Top 5 pairs: {selected_pairs[:5]}")
        if not selected_pairs:
            logging.warning("No USDT pairs found, using fallback")
            return ['ADA/USDT', 'TRX/USDT', 'NEAR/USDT', 'SHIB/USDT', 'BTC/USDT', 'ETH/USDT', 'ETC/USDT']
        return selected_pairs[:50]
    except Exception as e:
        logging.error(f"Error loading markets: {str(e)}")
        logging.debug("Falling back to default pairs")
        return ['ADA/USDT', 'TRX/USDT', 'NEAR/USDT', 'SHIB/USDT', 'BTC/USDT', 'ETH/USDT', 'ETC/USDT']

PAIRS = get_all_spot_pairs()

def calculate_emas(closes):
    logging.debug(f"Calculating EMAs for {len(closes)} closing prices")
    df = pd.DataFrame({'close': closes})
    df['ema10'] = df['close'].ewm(span=EMA_10, adjust=False).mean()
    df['ema30'] = df['close'].ewm(span=EMA_30, adjust=False).mean()
    df['ema100'] = df['close'].ewm(span=EMA_100, adjust=False).mean()
    logging.debug(f"EMA results: ema10={df['ema10'].iloc[-1]:.6f}, ema30={df['ema30'].iloc[-1]:.6f}, ema100={df['ema100'].iloc[-1]:.6f}")
    return df[['ema10', 'ema30', 'ema100']]

def generate_signal_message(pair, tf, data):
    arrow = "🟢" if data['signal'] == 'BUY' else "🔴" if data['signal'] == 'SELL' else "⚪"
    message = (
        f"{arrow} **{pair} {tf} Signal**\n"
        f"Price: {data['price']:.6f}\n"
        f"Take Profit: {data['take_profit']:.6f}\n"
        f"Stop Loss: {data['stop_loss']:.6f}\n"
        f"Time: {data['time'].strftime('%Y-%m-%d %H:%M:%S')} WIB\n"
        f"Exchange: Gate.io\n"
        f"Chart: See attached"
    )
    logging.debug(f"Generated signal message for {pair} {tf}: {message[:100]}...")
    return message

def send_daily_report():
    logging.debug("Generating daily trading report")
    total_signals = daily_stats['valid_signals']
    signals_by_pair = "\n".join([f"{pair}: {count}" for pair, count in daily_stats['signals_by_pair'].items()])
    report = (
        f"📊 **Daily Trading Report ({datetime.now(WIB).strftime('%Y-%m-%d')})**\n"
        f"```\n"
        f"Valid Signals:   {daily_stats['valid_signals']}\n"
        f"Total Signals:   {total_signals}\n"
        f"\nSignals by Pair:\n{signals_by_pair}\n"
        f"```\n"
        f"Time: {datetime.now(WIB).strftime('%Y-%m-%d %H:%M:%S')} WIB"
    )
    logging.debug(f"Daily report content: {report[:100]}...")
    send_discord_message(report)
    logging.info("Daily report sent")
    daily_stats['valid_signals'] = 0
    daily_stats['signals_by_pair'] = defaultdict(int)
    daily_stats['last_report_date'] = datetime.now(WIB).date()

def monitor():
    logging.debug("Starting monitor loop")
    while True:
        try:
            current_time = datetime.now(WIB)
            logging.debug(f"Current time: {current_time}")
            if (current_time.hour == 0 and current_time.minute == 0 and
                (daily_stats['last_report_date'] != current_time.date())):
                logging.debug("Triggering daily report")
                send_daily_report()

            for pair in PAIRS:
                for tf in TIMEFRAMES:
                    logging.debug(f"Processing {pair} {tf}")
                    try:
                        data = None
                        for attempt in range(3):
                            try:
                                data = exchange.fetch_ohlcv(pair, tf, limit=EMA_100+CHART_CANDLE_COUNT)
                                logging.debug(f"Fetched OHLCV data for {pair} {tf}, length: {len(data)}")
                                break
                            except Exception as e:
                                logging.warning(f"Retry {attempt+1}/3 fetching {pair} {tf}: {str(e)}")
                                time.sleep(5)

                        if not data or len(data) < EMA_100:
                            logging.debug(f"Insufficient data for {pair} {tf}, skipping")
                            continue

                        closes = np.array([x[4] for x in data])
                        current_price = closes[-1]
                        logging.debug(f"Current price for {pair} {tf}: {current_price:.6f}")
                        ema_df = calculate_emas(closes)

                        # Ambil EMA untuk dua candle terakhir
                        ema10_curr = ema_df['ema10'].iloc[-1]
                        ema10_prev = ema_df['ema10'].iloc[-2]
                        ema30_curr = ema_df['ema30'].iloc[-1]
                        ema30_prev = ema_df['ema30'].iloc[-2]
                        ema100_curr = ema_df['ema100'].iloc[-1]
                        ema100_prev = ema_df['ema100'].iloc[-2]
                        logging.debug(
                            f"EMA values for {pair} {tf}: "
                            f"ema10_prev={ema10_prev:.6f}, ema10_curr={ema10_curr:.6f}, "
                            f"ema30_prev={ema30_prev:.6f}, ema30_curr={ema30_curr:.6f}, "
                            f"ema100_prev={ema100_prev:.6f}, ema100_curr={ema100_curr:.6f}"
                        )

                        current_signal = 'NEUTRAL'

                        # Deteksi crossover EMA10 dan EMA30 terhadap EMA100
                        # BUY: EMA10 dan EMA30 melewati EMA100 dari bawah ke atas
                        if (ema10_prev <= ema100_prev and ema10_curr > ema100_curr and
                            ema30_prev <= ema100_prev and ema30_curr > ema100_curr):
                            current_signal = 'BUY'
                            logging.debug(f"BUY signal detected for {pair} {tf} (Golden Cross)")
                        # SELL: EMA10 dan EMA30 melewati EMA100 dari atas ke bawah
                        elif (ema10_prev >= ema100_prev and ema10_curr < ema100_curr and
                              ema30_prev >= ema100_prev and ema30_curr < ema100_curr):
                            current_signal = 'SELL'
                            logging.debug(f"SELL signal detected for {pair} {tf} (Death Cross)")

                        signal_key = f"{pair}|{tf}"
                        logging.debug(f"Signal key: {signal_key}, Current signal: {current_signal}")

                        # Hanya kirim sinyal jika berbeda dari sinyal sebelumnya dan bukan NEUTRAL
                        if (current_signal != 'NEUTRAL' and
                            (signal_key not in signal_history or
                             signal_history[signal_key]['signal'] != current_signal)):

                            tp = current_price * (1 + TAKE_PROFIT_PERCENT/100) if current_signal == 'BUY' else current_price * (1 - TAKE_PROFIT_PERCENT/100)
                            sl = current_price * (1 - STOP_LOSS_PERCENT/100) if current_signal == 'BUY' else current_price * (1 + STOP_LOSS_PERCENT/100)

                            signal_data = {
                                'signal': current_signal,
                                'price': current_price,
                                'ema10': ema10_curr,
                                'ema30': ema30_curr,
                                'ema100': ema100_curr,
                                'take_profit': tp,
                                'stop_loss': sl,
                                'time': datetime.now(WIB)
                            }
                            logging.debug(f"Signal data for {pair} {tf}: {signal_data}")

                            signal_history[signal_key] = signal_data
                            daily_stats['signals_by_pair'][pair] += 1
                            daily_stats['valid_signals'] += 1
                            logging.debug(f"Updated stats: valid_signals={daily_stats['valid_signals']}, signals_by_pair={dict(daily_stats['signals_by_pair'])}")

                            chart_file = create_candlestick_chart(data, pair, tf, signal_data)
                            message = generate_signal_message(pair, tf, signal_data)
                            send_discord_message(message, chart_file)
                            logging.info(f"Signal sent with chart: {pair} {tf} {current_signal}")
                            time.sleep(1)

                    except Exception as e:
                        logging.error(f"Error processing {pair} {tf}: {str(e)}")
                        time.sleep(10)

            time.sleep(30)

        except KeyboardInterrupt:
            logging.info("Script terminated by user")
            break
        except Exception as e:
            logging.error(f"Monitor error: {str(e)}")
            time.sleep(30)

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG,  # Changed to DEBUG for detailed logging
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler('gateio_scalping.log'),
            logging.StreamHandler()
        ]
    )

    logging.info("Starting Gate.io EMA Scalping Bot with Candlestick Charts")
    monitor()
