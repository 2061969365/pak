#!/usr/bin/env python3
"""
PikPak batch register - GitHub Actions headless runner.

Usage: python github_action_runner.py

Config via env vars (or .env file):
  INVITE_LINK         PikPak invite link (required)
  PROXY_GATEWAY       SOCKS5 proxy (leave empty for WARP)
  MAX_ROUNDS          Accounts per run (default: 2)
  DELAY_MINUTES       Delay between accounts (default: 10)
  MAX_DAILY_TOTAL     Daily account limit (default: 10)
  COUNTER_FILE        Counter file path (default: /tmp/pikpak_counter.json)
"""

import json
import os
import sys
import time
from pathlib import Path

# Load .env file if exists
env_path = Path(__file__).parent / '.env'
if env_path.exists():
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' in line:
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip())

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

import main as worker
from lib.http_client import configure_proxy, get_current_ip


COUNTER_FILE = os.environ.get('COUNTER_FILE', '/tmp/pikpak_counter.json')

def _load_counter():
    today = time.strftime('%Y-%m-%d')
    try:
        if os.path.exists(COUNTER_FILE):
            with open(COUNTER_FILE) as f:
                data = json.load(f)
            if data.get('date') == today:
                return data.get('count', 0)
    except (json.JSONDecodeError, OSError):
        pass
    return 0

def _save_counter(count):
    data = {'date': time.strftime('%Y-%m-%d'), 'count': count}
    with open(COUNTER_FILE, 'w') as f:
        json.dump(data, f)


def _get_current_warp_ip():
    """Get current exit IP through WARP SOCKS5 proxy"""
    import subprocess
    try:
        result = subprocess.run(
            ['curl', '-s', '--connect-timeout', '5',
             '--socks5-hostname', '127.0.0.1:40000',
             'https://ifconfig.me'],
            capture_output=True, text=True, timeout=10)
        ip = result.stdout.strip()
        if ip and '.' in ip:
            return ip
    except Exception:
        pass
    return None

def _rotate_warp_ip():
    """Disconnect and reconnect WARP until IP actually changes"""
    import subprocess
    old_ip = _get_current_warp_ip()
    print('  Current IP: ' + (old_ip or 'unknown'))

    for attempt in range(5):
        print('  Rotating WARP... (' + str(attempt + 1) + '/5)', end=' ')
        subprocess.run(['sudo', 'warp-cli', '--accept-tos', 'disconnect'],
                       capture_output=True, timeout=10)
        time.sleep(2)
        subprocess.run(['sudo', 'warp-cli', '--accept-tos', 'connect'],
                       capture_output=True, timeout=10)
        time.sleep(4)

        new_ip = _get_current_warp_ip()
        if new_ip and new_ip != old_ip:
            print('IP changed: ' + old_ip + ' -> ' + new_ip)
            return True
        print('same IP, retrying...')

    print('  WARNING: Could not change IP after 5 attempts')
    return False


def load_config():
    invite_link = os.environ.get('INVITE_LINK', '').strip()
    if not invite_link:
        print('[ERROR] INVITE_LINK environment variable not set')
        sys.exit(1)
    return {
        'invite_link': invite_link,
        'proxy_gateway': os.environ.get('PROXY_GATEWAY', '').strip(),
        'max_rounds': int(os.environ.get('MAX_ROUNDS', '2')),
        'delay_minutes': int(os.environ.get('DELAY_MINUTES', '10')),
        'max_daily_total': int(os.environ.get('MAX_DAILY_TOTAL', '10')),
    }


def main():
    print('=' * 60)
    print('  PikPak Batch Register - GitHub Actions Runner')
    print('=' * 60)

    cfg = load_config()
    print()
    print('Configuration:')
    print('  Invite link: ' + cfg['invite_link'][:50] + '...')
    proxy_mode = 'WARP (system VPN)' if not cfg['proxy_gateway'] else cfg['proxy_gateway'][:50]
    print('  Proxy: ' + proxy_mode)
    print('  Batch size: ' + str(cfg['max_rounds']) + ' accounts')
    print('  Daily limit: ' + str(cfg['max_daily_total']) + ' accounts')
    print('  Interval: ' + str(cfg['delay_minutes']) + ' min')

    daily_count = _load_counter()
    remaining = cfg['max_daily_total'] - daily_count
    print()
    print('Daily count: ' + str(daily_count) + ' / ' + str(cfg['max_daily_total']))
    if remaining <= 0:
        print('Daily quota reached, skipping this run')
        github_output = os.environ.get('GITHUB_OUTPUT', '')
        if github_output:
            with open(github_output, 'a') as f:
                f.write('skipped=true\n')
                f.write('daily_count=' + str(daily_count) + '\n')
        return

    batch_size = min(cfg['max_rounds'], remaining)
    print('This batch: ' + str(batch_size) + ' accounts')

    if cfg['proxy_gateway']:
        configure_proxy(gateway=cfg['proxy_gateway'])
        print('Proxy mode: SOCKS5')
    else:
        configure_proxy(gateway='')
        print('Proxy mode: Direct (via WARP system VPN)')

    try:
        current_ip = get_current_ip()
        print('Exit IP: ' + current_ip)
    except Exception as e:
        print('IP check failed: ' + str(e))

    print()
    print('Parsing invite link...')
    try:
        invite_info = worker.parse_invite_link(cfg['invite_link'])
        worker.INVITE_SHARE_ID = invite_info['share_id']
        worker.INVITE_PASS_CODE_TOKEN = invite_info['pass_code_token']
        worker.INVITE_TRACE_FILE_IDS = invite_info['trace_file_ids']
        print('  OK share_id: ' + invite_info['share_id'])
        if invite_info.get('warning'):
            print('  WARNING: ' + invite_info['warning'])
    except Exception as e:
        print('  FAILED: ' + str(e))
        sys.exit(1)

    worker.DELAY_MINUTES = cfg['delay_minutes']

    print()
    print('=' * 60)
    print('  Starting registration (' + str(batch_size) + ' accounts)')
    print('=' * 60)

    success = 0
    fail = 0

    for round_num in range(1, batch_size + 1):
        print()
        print('-' * 40)
        print('  Account ' + str(round_num) + ' / ' + str(batch_size))
        print('-' * 40)

        try:
            acct = worker.run_batch_round(round_num)
            if acct:
                success += 1
                daily_count += 1
                _save_counter(daily_count)
                print('  OK! (' + str(daily_count) + '/' + str(cfg['max_daily_total']) + ')')
            else:
                fail += 1
                print('  FAILED')
        except worker.RateLimitError as e:
            fail += 1
            print('  RATE LIMIT: ' + str(e))
            worker.unpin_proxy()
            worker.force_rotate_proxy()
            worker.pin_proxy()
            time.sleep(30)
        except Exception as e:
            fail += 1
            print('  ERROR: ' + str(e))
            time.sleep(60)

        print('  Total: ' + str(success) + ' OK / ' + str(fail) + ' FAIL')

        if daily_count >= cfg['max_daily_total']:
            print()
            print('Daily limit ' + str(cfg['max_daily_total']) + ' reached, stopping')
            break

        if round_num < batch_size:
            print()
            print('Rotating IP for next account...')
            _rotate_warp_ip()

    print()
    print('=' * 60)
    print('  DONE')
    print('  Success: ' + str(success) + '  Fail: ' + str(fail) + '  Daily total: ' + str(daily_count))
    print('=' * 60)

    github_output = os.environ.get('GITHUB_OUTPUT', '')
    if github_output:
        with open(github_output, 'a') as f:
            f.write('success=' + str(success) + '\n')
            f.write('fail=' + str(fail) + '\n')
            f.write('daily_count=' + str(daily_count) + '\n')
            f.write('skipped=false\n')

    if success > 0:
        result_file = getattr(worker, 'RESULT_FILE', 'batch_result_protocol.txt')
        if os.path.exists(result_file):
            print('Results: ' + result_file)


if __name__ == '__main__':
    main()
