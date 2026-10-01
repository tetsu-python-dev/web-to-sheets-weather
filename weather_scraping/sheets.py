"""OAuth認証、データ追記、集計とグラフの初期設定。"""
import json
import os
from pathlib import Path

import httplib2
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from google_auth_httplib2 import AuthorizedHttp

from weather import HEADERS

BASE = Path(__file__).resolve().parent
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
DATA = 'Weather'
SUMMARY = 'Summary'


def connect(interactive=False):
    token = BASE / 'token.json'
    creds = Credentials.from_authorized_user_file(str(token), SCOPES) if token.exists() else None
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    if not creds or not creds.valid:
        if not interactive:
            raise RuntimeError('認証が必要です。先に python main.py --setup を実行してください')
        flow = InstalledAppFlow.from_client_secrets_file(str(BASE / 'credentials.json'), SCOPES)
        creds = flow.run_local_server(port=0)
    # 更新後のトークンも保存する。秘密情報をログへ出さない。
    temporary = BASE / 'token.json.tmp'
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(creds.to_json())
    temporary.replace(token)
    http = AuthorizedHttp(creds, http=httplib2.Http(timeout=30))
    return build('sheets', 'v4', http=http, cache_discovery=False).spreadsheets()


def ensure_sheets(api, spreadsheet_id):
    meta = api.get(spreadsheetId=spreadsheet_id).execute()
    sheets = {s['properties']['title']: s for s in meta['sheets']}
    missing = [title for title in (DATA, SUMMARY) if title not in sheets]
    if missing:
        api.batchUpdate(spreadsheetId=spreadsheet_id, body={'requests': [
            {'addSheet': {'properties': {'title': title,
             'gridProperties': {'rowCount': 1000, 'columnCount': 26}}}} for title in missing]}).execute()
        return ensure_sheets(api, spreadsheet_id)
    for title in (DATA, SUMMARY):
        if sheets[title]['properties']['gridProperties']['columnCount'] < 26:
            api.batchUpdate(spreadsheetId=spreadsheet_id, body={'requests': [{
                'updateSheetProperties': {'properties': {
                    'sheetId': sheets[title]['properties']['sheetId'],
                    'gridProperties': {'columnCount': 26}},
                    'fields': 'gridProperties.columnCount'}}]}).execute()
    needed = sheets[DATA]['properties']['gridProperties']['rowCount'] + 2
    if sheets[SUMMARY]['properties']['gridProperties']['rowCount'] < needed:
        api.batchUpdate(spreadsheetId=spreadsheet_id, body={'requests': [{
            'updateSheetProperties': {'properties': {
                'sheetId': sheets[SUMMARY]['properties']['sheetId'],
                'gridProperties': {'rowCount': needed}}, 'fields': 'gridProperties.rowCount'}}]}).execute()
    header = api.values().get(spreadsheetId=spreadsheet_id, range='Weather!A1:L1').execute().get('values', [])
    if header and header[0] != HEADERS:
        raise ValueError('Weatherの見出しが一致しません。新しい専用スプレッドシートを指定してください')
    if not header:
        used = api.values().get(spreadsheetId=spreadsheet_id, range='Weather!A:L').execute().get('values', [])
        if used:
            raise ValueError('Weatherに見出しのない既存データがあります。書込を停止しました')
        api.values().update(spreadsheetId=spreadsheet_id, range='Weather!A1:L1',
                            valueInputOption='RAW', body={'values': [HEADERS]}).execute()
    return sheets


def existing_keys(api, spreadsheet_id):
    rows = api.values().get(spreadsheetId=spreadsheet_id, range='Weather!A2:A').execute().get('values', [])
    return {row[0] for row in rows if row}


def append_rows(api, spreadsheet_id, rows):
    if not rows:
        return
    # appendの再送はしない。タイムアウト時は次周期でキーを読み直す。
    api.values().append(spreadsheetId=spreadsheet_id, range='Weather!A:L',
                        valueInputOption='RAW', insertDataOption='INSERT_ROWS',
                        body={'values': rows}).execute()
    # 過去日の手動取得を行っても時系列順に並べる。
    meta = api.get(spreadsheetId=spreadsheet_id).execute()
    sid = next(s['properties']['sheetId'] for s in meta['sheets'] if s['properties']['title'] == DATA)
    data_sheet = next(s for s in meta['sheets'] if s['properties']['title'] == DATA)
    summary_sheet = next(s for s in meta['sheets'] if s['properties']['title'] == SUMMARY)
    capacity = data_sheet['properties']['gridProperties']['rowCount'] + 2
    if summary_sheet['properties']['gridProperties']['rowCount'] < capacity:
        api.batchUpdate(spreadsheetId=spreadsheet_id, body={'requests': [{
            'updateSheetProperties': {'properties': {
                'sheetId': summary_sheet['properties']['sheetId'],
                'gridProperties': {'rowCount': capacity}}, 'fields': 'gridProperties.rowCount'}}]}).execute()
    # 追加で拡張された行にも日時表示を適用する。
    formats = []
    for sheet_id, start_col, end_col in [(sid, 1, 3), (summary_sheet['properties']['sheetId'], 9, 10)]:
        formats.append({'repeatCell': {'range': {'sheetId': sheet_id, 'startRowIndex': 1,
            'startColumnIndex': start_col, 'endColumnIndex': end_col},
            'cell': {'userEnteredFormat': {'numberFormat': {'type': 'DATE_TIME',
                'pattern': 'yyyy-mm-dd hh:mm:ss'}}}, 'fields': 'userEnteredFormat.numberFormat'}})
    api.batchUpdate(spreadsheetId=spreadsheet_id, body={'requests': formats + [{
        'sortRange': {'range': {'sheetId': sid, 'startRowIndex': 1},
                      'sortSpecs': [{'dimensionIndex': 2, 'sortOrder': 'ASCENDING'}]}}]}).execute()


def setup_dashboard(api, spreadsheet_id):
    sheets = ensure_sheets(api, spreadsheet_id)
    data_id = sheets[DATA]['properties']['sheetId']
    summary_id = sheets[SUMMARY]['properties']['sheetId']
    requests = [
        {'updateSpreadsheetProperties': {'properties': {'timeZone': 'Asia/Tokyo', 'locale': 'ja_JP'},
                                         'fields': 'timeZone,locale'}},
        {'updateSheetProperties': {'properties': {'sheetId': data_id,
          'gridProperties': {'frozenRowCount': 1}}, 'fields': 'gridProperties.frozenRowCount'}},
        {'repeatCell': {'range': {'sheetId': data_id, 'startRowIndex': 1,
          'startColumnIndex': 1, 'endColumnIndex': 3}, 'cell': {
          'userEnteredFormat': {'numberFormat': {'type': 'DATE_TIME', 'pattern': 'yyyy-mm-dd hh:mm:ss'}}},
          'fields': 'userEnteredFormat.numberFormat'}},
        {'repeatCell': {'range': {'sheetId': summary_id, 'startRowIndex': 3,
          'startColumnIndex': 9, 'endColumnIndex': 10}, 'cell': {
          'userEnteredFormat': {'numberFormat': {'type': 'DATE_TIME', 'pattern': 'mm-dd hh:mm'}}},
          'fields': 'userEnteredFormat.numberFormat'}},
        {'repeatCell': {'range': {'sheetId': data_id, 'endRowIndex': 1}, 'cell': {
          'userEnteredFormat': {'backgroundColor': {'red': 0.12, 'green': 0.3, 'blue': 0.45},
                               'textFormat': {'bold': True, 'foregroundColor': {'red': 1, 'green': 1, 'blue': 1}}}},
          'fields': 'userEnteredFormat'}},
    ]
    # 自分が作成したタイトルのグラフだけ置換し、setupを何度実行しても増やさない。
    for chart in sheets[SUMMARY].get('charts', []):
        if chart.get('spec', {}).get('title') == 'WebToSheets: 気温の推移':
            requests.append({'deleteEmbeddedObject': {'objectId': chart['chartId']}})
    domain = {'sheetId': summary_id, 'startRowIndex': 2, 'startColumnIndex': 9, 'endColumnIndex': 10}
    series = {'sheetId': summary_id, 'startRowIndex': 2, 'startColumnIndex': 10, 'endColumnIndex': 11}
    requests.append({'addChart': {'chart': {'spec': {
        'title': 'WebToSheets: 気温の推移', 'basicChart': {
        'chartType': 'LINE', 'legendPosition': 'NO_LEGEND', 'headerCount': 1,
        'axis': [{'position': 'BOTTOM_AXIS', 'title': '観測日時 (JST)'},
                 {'position': 'LEFT_AXIS', 'title': '気温 (℃)'}],
        'domains': [{'domain': {'sourceRange': {'sources': [domain]}}}],
        'series': [{'series': {'sourceRange': {'sources': [series]}}, 'targetAxis': 'LEFT_AXIS'}]}},
        'position': {'overlayPosition': {'anchorCell': {'sheetId': summary_id, 'rowIndex': 1, 'columnIndex': 12},
                                        'widthPixels': 700, 'heightPixels': 400}}}}})
    api.batchUpdate(spreadsheetId=spreadsheet_id, body={'requests': requests}).execute()
    daily = '''=IFERROR(QUERY(Weather!D1:I,"select D,count(D),avg(F),min(F),max(F),sum(G),avg(H),avg(I) where D is not null group by D order by D label D '観測対象日',count(D) '保存行数',avg(F) '平均気温',min(F) '最低気温',max(F) '最高気温',sum(G) '保存分の降水量',avg(H) '平均湿度',avg(I) '平均風速'",1),"データ待ち")'''
    chart_data = '''=IFERROR(QUERY(Weather!C1:F,"select C,F where C is not null order by C label C '観測日時',F '気温(℃)'",1),"データ待ち")'''
    api.values().batchUpdate(spreadsheetId=spreadsheet_id, body={
        'valueInputOption': 'USER_ENTERED', 'data': [
        {'range': 'Summary!A1', 'values': [['日別集計（欠損は平均から除外、降水量は保存分のみ）']]},
        {'range': 'Summary!A3', 'values': [[daily]]},
        {'range': 'Summary!J3', 'values': [[chart_data]]}]}).execute()
