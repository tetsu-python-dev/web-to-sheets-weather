"""実行入口。python main.py --help で操作一覧を表示する。"""
import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import time
from datetime import date, datetime, timedelta

from weather import JST, fetch_day, new_only, NotPublishedYet
from sheets import connect, ensure_sheets, existing_keys, append_rows, setup_dashboard

BASE = Path(__file__).resolve().parent


def configure_logging():
    (BASE / 'logs').mkdir(exist_ok=True)
    handlers = [logging.StreamHandler(), RotatingFileHandler(
        BASE / 'logs' / 'weather.log', maxBytes=1_000_000, backupCount=3, encoding='utf-8')]
    # 外部ライブラリの認証URL/コールバックをログファイルへ流さない。
    # このツール自身はrootロガーを使う。
    for handler in handlers:
        handler.addFilter(lambda record: record.name == 'root')
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format='%(asctime)s %(levelname)s %(message)s')
    logging.getLogger('googleapiclient.discovery_cache').setLevel(logging.ERROR)


def load_config(path):
    config = json.loads(path.read_text(encoding='utf-8'))
    station = config['station']
    # 簡易版は確認済みの東京のみ。別地点はHTML構造の検証後に拡張する。
    if str(station['prec_no']) != '44' or str(station['block_no']) != '47662':
        raise ValueError('この版は東京(44/47662)専用です')
    interval = config.get('interval_seconds', 600)
    if not isinstance(interval, int) or interval < 600:
        raise ValueError('interval_secondsは600以上の整数にしてください')
    return config


def run_once(config, target_day=None, dry_run=False):
    now = datetime.now(JST)
    # 毎回「昨日＋今日」を確認し、昨日の24:00と、当日分が公開された場合の追加を拾う。
    days = [target_day] if target_day else [now.date() - timedelta(days=1), now.date()]
    rows = []
    for i, day in enumerate(days):
        if i:
            time.sleep(1)  # ページ間のアクセスを少し離す
        try:
            rows.extend(fetch_day(day, config['station'], now))
        except NotPublishedYet:
            logging.info('取得対象日=%s は公開前のためスキップ（このページは昨日まで公開）', day)
    if dry_run:
        print(json.dumps(rows[-3:], ensure_ascii=False, indent=2))
        logging.info('DRY RUN 抽出=%d Sheetsへの書込なし', len(rows))
        return
    spreadsheet_id = config['spreadsheet_id']
    if not spreadsheet_id or spreadsheet_id.startswith('YOUR_'):
        raise ValueError('config.jsonのspreadsheet_idを設定してください')
    api = connect()
    ensure_sheets(api, spreadsheet_id)
    fresh = new_only(rows, existing_keys(api, spreadsheet_id))
    append_rows(api, spreadsheet_id, fresh)
    logging.info('成功 抽出=%d 重複=%d 新規追加=%d', len(rows), len(rows) - len(fresh), len(fresh))


def report_error(error):
    # OAuthレスポンスや秘密情報を含み得る例外本文はログへ出さない。
    if isinstance(error, (ValueError, FileNotFoundError, RuntimeError, KeyError)):
        logging.error('%s: %s', type(error).__name__, error)
    else:
        status = getattr(getattr(error, 'resp', None), 'status', None)
        if status is None:
            status = getattr(getattr(error, 'response', None), 'status_code', None)
        logging.error('処理失敗 種類=%s HTTP=%s（設定・ネット接続を確認）', type(error).__name__, status or '-')


def main():
    parser = argparse.ArgumentParser(description='東京の公開気象HTML → Google Sheets')
    parser.add_argument('--setup', action='store_true', help='対話認証＋集計・グラフを初期設定')
    parser.add_argument('--loop', action='store_true', help='約10分おきに実行（Ctrl+Cで停止）')
    parser.add_argument('--dry-run', action='store_true', help='スクレイピングのみ。Google認証不要')
    parser.add_argument('--date', type=date.fromisoformat, help='特定日を取得 YYYY-MM-DD')
    args = parser.parse_args()
    if args.loop and (args.setup or args.date):
        parser.error('--loop と --setup/--date は併用できません')
    if args.setup and (args.dry_run or args.date):
        parser.error('--setup と --dry-run/--date は併用できません')
    configure_logging()
    try:
        config = load_config(BASE / 'config.json')
        if args.date and args.date > datetime.now(JST).date():
            raise ValueError('未来日は指定できません')
        if args.setup:
            sid = config['spreadsheet_id']
            if not sid or sid.startswith('YOUR_'):
                raise ValueError('config.jsonのspreadsheet_idを設定してください')
            setup_dashboard(connect(interactive=True), sid)
            logging.info('初期設定完了。次は python main.py でデータを追加してください')
            return 0
        while True:
            started = time.monotonic()
            try:
                run_once(config, args.date, args.dry_run)
            except Exception as error:
                report_error(error)
                if not args.loop:
                    return 1
            if not args.loop:
                return 0
            # 処理時間を含めた周期。失敗時も最低600秒周期を維持。
            time.sleep(max(1, config.get('interval_seconds', 600) - (time.monotonic() - started)))
    except KeyboardInterrupt:
        logging.info('停止しました')
        return 0
    except Exception as error:
        report_error(error)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
