# Polymarket Trader Telegram Alert Bot (single-file simple version)
# Save as poll_polymarket_alert.py

import requests
import time
import json
import threading
from pathlib import Path
from datetime import datetime

CONFIG_FILE = Path('config.json')
SEEN_FILE = Path('seen_tx.json')
SUBS_FILE = Path('subscriptions.json')

# Load config
if not CONFIG_FILE.exists():
    print("config.json not found. Create one from config.json.example or set env and create it.")
    raise SystemExit(1)

with open(CONFIG_FILE, 'r') as f:
    cfg = json.load(f)

API_KEY = cfg.get('polygonscan_api_key')
TELEGRAM_BOT_TOKEN = cfg.get('telegram_bot_token')
POLL_INTERVAL = int(cfg.get('poll_interval', 20))
POLYDATA_API = cfg.get('polymarket_data_api', 'https://data-api.polymarket.com')

if not API_KEY or not TELEGRAM_BOT_TOKEN:
    print("Missing polygonscan_api_key or telegram_bot_token in config.json")
    raise SystemExit(1)

BASE_TELEGRAM_URL = f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}'

# load or init storage
if SEEN_FILE.exists():
    try:
        with open(SEEN_FILE, 'r') as f:
            seen = json.load(f)
    except:
        seen = {}
else:
    seen = {}

if SUBS_FILE.exists():
    try:
        with open(SUBS_FILE, 'r') as f:
            subs = json.load(f)
    except:
        subs = {}
else:
    subs = {}

def norm_addr(a):
    return a.strip().lower()

def polygonscan_txs_for_address(address, api_key, startblock=0, endblock=99999999):
    url = 'https://api.polygonscan.com/api'
    params = {
        'module': 'account',
        'action': 'txlist',
        'address': address,
        'startblock': startblock,
        'endblock': endblock,
        'page': 1,
        'offset': 100,
        'sort': 'asc',
        'apikey': api_key
    }
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    if data.get('status') != '1' or 'result' not in data:
        return []
    return data['result']

def polygonscan_balance(address, api_key):
    url = 'https://api.polygonscan.com/api'
    params = {'module':'account','action':'balance','address':address,'tag':'latest','apikey':api_key}
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    data = r.json()
    if data.get('status') != '1' or 'result' not in data:
        return None
    bal = int(data['result']) / (10**18)
    return bal

def polymarket_get_recent_trades_for_wallet(wallet_address, limit=5):
    url = f"{POLYDATA_API}/trades"
    params = {'proxyWallet': wallet_address, 'limit': limit}
    try:
        r = requests.get(url, params=params, timeout=10)
        if r.status_code != 200:
            return []
        return r.json() if r.text else []
    except Exception:
        return []

def match_tx_with_polymarket_trade(tx_hash, wallet_address):
    trades = polymarket_get_recent_trades_for_wallet(wallet_address, limit=10)
    for t in trades:
        if not t:
            continue
        txh = t.get('txHash') or t.get('transactionHash') or t.get('transaction_hash')
        if txh and txh.lower() == tx_hash.lower():
            return t
    return None

def send_telegram(chat_id, text, parse_mode='Markdown'):
    url = f'{BASE_TELEGRAM_URL}/sendMessage'
    payload = {'chat_id': str(chat_id), 'text': text, 'parse_mode': parse_mode, 'disable_web_page_preview': True}
    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
    except Exception as e:
        print('Failed to send telegram to', chat_id, e)

def get_updates(offset=None, timeout=30):
    url = f'{BASE_TELEGRAM_URL}/getUpdates'
    params = {'timeout': timeout}
    if offset:
        params['offset'] = offset
    r = requests.get(url, params=params, timeout=timeout+10)
    r.raise_for_status()
    return r.json()

def fmt_wallet_info(address):
    addr = norm_addr(address)
    bal = None
    try:
        bal = polygonscan_balance(addr, API_KEY)
    except Exception:
        bal = None
    try:
        txs = polygonscan_txs_for_address(addr, API_KEY)
    except Exception:
        txs = []
    msg = [f'*Wallet:* `{addr}`']
    if bal is not None:
        msg.append(f'*Balance (MATIC):* {bal}')
    msg.append('*Recent transactions:*')
    if not txs:
        msg.append('_No recent transactions found or API returned empty._')
    else:
        for tx in txs[-5:][::-1]:
            ts = datetime.utcfromtimestamp(int(tx.get('timeStamp','0'))).strftime('%Y-%m-%d %H:%M:%S UTC')
            h = tx.get('hash')
            frm = tx.get('from')
            to = tx.get('to')
            val = int(tx.get('value','0'))/(10**18)
            link = f'https://polygonscan.com/tx/{h}'
            msg.append(f'- {ts} | from `{frm}` → `{to}` | {val} MATIC | [tx]({link})')
    msg.append('\n_Send /follow <address> to subscribe to alerts for this address._')
    return '\n'.join(msg)

def save_subs():
    with open(SUBS_FILE, 'w') as f:
        json.dump(subs, f)

def save_seen():
    with open(SEEN_FILE, 'w') as f:
        json.dump(seen, f)

def add_subscription(chat_id, address):
    cid = str(chat_id)
    a = norm_addr(address)
    lst = subs.get(cid, [])
    if a in lst:
        return False
    lst.append(a)
    subs[cid] = lst
    save_subs()
    seen.setdefault(a, None)
    save_seen()
    return True

def remove_subscription(chat_id, address):
    cid = str(chat_id)
    a = norm_addr(address)
    lst = subs.get(cid, [])
    if a not in lst:
        return False
    lst.remove(a)
    subs[cid] = lst
    save_subs()
    return True

def process_update(u):
    if 'message' not in u:
        return
    m = u['message']
    chat = m.get('chat', {})
    chat_id = chat.get('id')
    text = m.get('text','')
    if not text:
        return
    text = text.strip()
    if text.startswith('/'):
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts)>1 else None
        if cmd == '/help':
            help_text = ('Usage:\n/follow <address> - follow an address and receive alerts\n/unfollow <address> - stop following\n/list - list addresses you follow\n/info <address> - get wallet details now\nOr simply paste a Polygon wallet address to get details and follow prompt.')
            send_telegram(chat_id, help_text)
            return
        if cmd == '/follow' and arg:
            added = add_subscription(chat_id, arg)
            if added:
                send_telegram(chat_id, f'✅ Now following `{norm_addr(arg)}` for alerts.')
            else:
                send_telegram(chat_id, f'ℹ️ `{norm_addr(arg)}` was already in your list.')
            return
        if cmd == '/unfollow' and arg:
            removed = remove_subscription(chat_id, arg)
            if removed:
                send_telegram(chat_id, f'🗑️ Unfollowed `{norm_addr(arg)}`')
            else:
                send_telegram(chat_id, f'⚠️ `{norm_addr(arg)}` not found in your subscriptions.')
            return
        if cmd == '/list':
            lst = subs.get(str(chat_id), [])
            if not lst:
                send_telegram(chat_id, 'You are not following any addresses.')
            else:
                send_telegram(chat_id, '*Your subscriptions:*\n' + '\n'.join([f'- `{a}`' for a in lst]))
            return
        if cmd == '/info' and arg:
            info = fmt_wallet_info(arg)
            send_telegram(chat_id, info)
            return
        send_telegram(chat_id, 'Unknown command. Send /help for usage.')
        return
    txt = text.strip()
    if txt.startswith('0x') and len(txt) >= 10:
        info = fmt_wallet_info(txt)
        send_telegram(chat_id, info)
        send_telegram(chat_id, f'To receive ongoing alerts for this address, reply with `/follow {norm_addr(txt)}`')
        return
    send_telegram(chat_id, 'Send a Polygon wallet address (0x...) or /help for commands.')

def telegram_listener():
    print('Starting Telegram listener (long-poll getUpdates)...')
    offset = None
    while True:
        try:
            res = get_updates(offset=offset, timeout=30)
            if not res.get('ok'):
                time.sleep(1)
                continue
            for u in res.get('result', []):
                offset = u['update_id'] + 1
                try:
                    process_update(u)
                except Exception as e:
                    print('Error processing update', e)
        except Exception as e:
            print('getUpdates error', e)
            time.sleep(5)

def fmt_tx_message_for_subscribers(address, tx):
    ts = datetime.utcfromtimestamp(int(tx.get('timeStamp','0'))).strftime('%Y-%m-%d %H:%M:%S UTC')
    hash_ = tx.get('hash')
    to = tx.get('to')
    frm = tx.get('from')
    value = int(tx.get('value','0'))/(10**18)
    input_data = tx.get('input')
    link = f'https://polygonscan.com/tx/{hash_}'
    trade = None
    try:
        trade = match_tx_with_polymarket_trade(hash_, address)
    except Exception:
        trade = None
    if trade:
        side = trade.get('side')
        price = trade.get('price')
        size = trade.get('size')
        title = trade.get('title') or trade.get('name') or trade.get('market')
        outcome = trade.get('outcome')
        msg = (f'*Polymarket Trade by* `{address}`\\n'
               f'*Market:* {title}\\n'
               f'*Outcome:* {outcome}\\n'
               f'*Side:* {side} | *Price:* {price} | *Size:* {size}\\n'
               f'*Time:* {ts}\\n'
               f'*Tx:* {link}\\n')
        return msg
    msg = (f'*New transaction by* `{address}`\\n'
           f'*Time:* {ts}\\n'
           f'*From:* `{frm}`\\n'
           f'*To:* `{to}`\\n'
           f'*Value (MATIC):* {value}\\n'
           f'*Tx:* {link}\\n\\n'
           f'_Raw input:_ `{input_data[:200]}`')
    return msg

def poll_subscriptions():
    print('Starting subscription poll loop...')
    while True:
        all_addresses = set()
        for lst in subs.values():
            for a in lst:
                all_addresses.add(a)
        for addr in list(all_addresses):
            try:
                txs = polygonscan_txs_for_address(addr, API_KEY)
            except Exception as e:
                print('Error fetching txs for', addr, e)
                continue
            if not txs:
                continue
            last_seen = seen.get(addr)
            if last_seen is None:
                seen[addr] = txs[-1]['hash']
                save_seen()
                print(f'Initial sync for {addr}, last tx {seen[addr]}')
                continue
            seen_flag = False
            new_list = []
            for tx in txs:
                if tx['hash'] == last_seen:
                    seen_flag = True
                    continue
                if seen_flag:
                    new_list.append(tx)
            if not seen_flag:
                new_list = txs[-2:]
            for tx in new_list:
                msg = fmt_tx_message_for_subscribers(addr, tx)
                for chat_id, lst in subs.items():
                    if addr in lst:
                        try:
                            send_telegram(chat_id, msg)
                        except Exception as e:
                            print('Failed send to', chat_id, e)
                print('Alert sent for', addr, tx.get('hash'))
                seen[addr] = tx.get('hash')
                save_seen()
        time.sleep(POLL_INTERVAL)

if __name__ == '__main__':
    t = threading.Thread(target=telegram_listener, daemon=True)
    t.start()
    try:
        poll_subscriptions()
    except KeyboardInterrupt:
        print('Stopping...')
