"""気象庁の東京・10分値HTMLを取得し、Sheets用の行へ整形する。"""
import json
import logging
import re
from datetime import datetime, time, timedelta, timezone

import requests
from bs4 import BeautifulSoup

JST = timezone(timedelta(hours=9))
URL = 'https://www.data.jma.go.jp/stats/etrn/view/10min_s1.php'
HEADERS = ['重複キー', '取得日時(JST)', '観測日時(JST)', '観測対象日', '地点',
           '気温(℃)', '10分降水量(mm)', '相対湿度(%)', '平均風速(m/s)',
           'チェック結果', '元の値(JSON)', '出典URL']
# 東京の10min_s1.phpの列番号。構造が変わったら先にヘッダー検査で停止する。
FIELDS = {'気温': (4, -90, 60), '降水量': (3, 0, 300),
          '湿度': (5, 0, 100), '風速': (6, 0, 150)}


class NotPublishedYet(ValueError):
    """気象庁が明示的に公開対象外と案内している日。"""


def sheet_datetime(value):
    """JSTの日時をSheetsの日時シリアル値へ変換する。"""
    local = value.astimezone(JST).replace(tzinfo=None)
    return (local - datetime(1899, 12, 30)).total_seconds() / 86400


def number(text, low, high):
    """欠損・品質記号・不正値をゼロにせず空欄にする。"""
    text = text.strip()
    if text in ('', '--', '×', '///', '#'):
        return '', '欠損'
    if re.fullmatch(r'-?\d+(?:\.\d+)?\s*[\)\]]', text):
        return '', '品質記号あり'
    if not re.fullmatch(r'-?\d+(?:\.\d+)?', text):
        return '', '形式異常'
    value = float(text)
    if not low <= value <= high:
        return '', '範囲異常'
    return value, ''


def parse_html(html, day, station, fetched_at, source_url):
    soup = BeautifulSoup(html, 'html.parser')
    table = soup.select_one('#tablefix1')
    if table is None:
        page_text = soup.get_text(' ', strip=True)
        if '閲覧可能な日は、昨日' in page_text and 'までです。' in page_text:
            raise NotPublishedYet('この10分値ページは昨日まで公開されています')
        raise ValueError('観測テーブルが見つかりません（ページ構造/対象日を確認）')
    trs = table.find_all('tr')
    expected = [
        ['時分', '気圧(hPa)', '降水量(mm)', '気温(℃)', '相対湿度(％)',
         '風向・風速(m/s)', '日照時間(分)'],
        ['現地', '海面', '平均', '風向', '最大瞬間', '風向'],
    ]
    for tr, labels in zip(trs[:2], expected):
        actual = [re.sub(r'\s+', '', th.get_text()) for th in tr.find_all('th')]
        if actual != labels:
            raise ValueError('10分値の列構造が想定と異なります。抽出を停止しました')
    if len(trs) < 3:
        raise ValueError('観測行がありません')
    rows, seen = [], set()
    for tr in trs[2:]:
        cells = [td.get_text(strip=True) for td in tr.find_all('td')]
        if not cells:
            continue
        if len(cells) != 11:
            raise ValueError('観測行の列数が変化しました')
        clock = cells[0]
        if clock == '24:00':
            observed = datetime.combine(day + timedelta(days=1), time(), JST)
        elif re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', clock):
            hour, minute = map(int, clock.split(':'))
            if minute % 10 or clock == '00:00':
                raise ValueError('10分値の観測時刻が不正です')
            observed = datetime.combine(day, time(hour, minute), JST)
        else:
            raise ValueError('観測時刻が解釈できません')
        # 今日のページにある未到来の空欄は保存しない。
        if observed > fetched_at:
            continue
        raw = {name: cells[index] for name, (index, _, _) in FIELDS.items()}
        values, issues = [], []
        for name, (_, low, high) in FIELDS.items():
            # 気象庁の -- は現象なし。降水量の場合のみ0へ整形する。
            if name == '降水量' and raw[name] == '--':
                value, issue = 0.0, ''
            else:
                value, issue = number(raw[name], low, high)
            values.append(value)
            if issue:
                issues.append(f'{name}:{issue}')
        if all(value == '' for value in values):
            logging.warning('全項目が欠損/異常のため保留: %s %s', day, clock)
            continue
        key = f"{station['prec_no']}-{station['block_no']}|{observed.isoformat()}"
        if key in seen:
            raise ValueError('ページ内で観測時刻が重複しています')
        seen.add(key)
        if issues:
            logging.warning('観測 %s: %s', observed.isoformat(), '; '.join(issues))
        rows.append([key, sheet_datetime(fetched_at), sheet_datetime(observed),
                     day.isoformat(), station['name'], *values,
                     '; '.join(issues) or 'OK',
                     json.dumps(raw, ensure_ascii=False), source_url])
    return sorted(rows, key=lambda row: row[2])


def fetch_day(day, station, fetched_at):
    # 自動リトライはせず、失敗時は次の10分周期で再取得する。
    response = requests.get(URL, params={
        'prec_no': station['prec_no'], 'block_no': station['block_no'],
        'year': day.year, 'month': day.month, 'day': day.day, 'view': ''},
        headers={'User-Agent': 'WebToSheetsWeather/1.0 (personal learning portfolio)'},
        timeout=(10, 30))
    response.raise_for_status()
    response.encoding = 'utf-8'
    rows = parse_html(response.text, day, station, fetched_at, response.url)
    logging.info('取得対象日=%s 抽出=%d', day, len(rows))
    return rows


def new_only(rows, existing_keys):
    """書込結果が不明な場合も、次回Sheetsを読み直して重複を避ける。"""
    seen = set(existing_keys)
    result = []
    for row in sorted(rows, key=lambda r: r[2]):
        if row[0] not in seen:
            result.append(row)
            seen.add(row[0])
    return result
