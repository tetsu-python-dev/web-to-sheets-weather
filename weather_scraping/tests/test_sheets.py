"""Googleへの実接続をせず、送信内容と保護条件を検査する。"""
import unittest
from pathlib import Path
from unittest.mock import Mock
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sheets import ensure_sheets, append_rows, setup_dashboard
from weather import HEADERS


def fake_api():
    api = Mock()
    api.get.return_value.execute.return_value = {'sheets': [
        {'properties': {'title': 'Weather', 'sheetId': 1,
         'gridProperties': {'rowCount': 2000, 'columnCount': 26}}},
        {'properties': {'title': 'Summary', 'sheetId': 2,
         'gridProperties': {'rowCount': 1000, 'columnCount': 26}},
         'charts': [{'chartId': 99, 'spec': {'title': 'WebToSheets: 気温の推移'}},
                    {'chartId': 100, 'spec': {'title': '手動グラフ'}}]}]}
    api.values.return_value.get.return_value.execute.return_value = {'values': [HEADERS]}
    return api


class SheetsTests(unittest.TestCase):
    def test_incompatible_header_stops(self):
        api = fake_api()
        api.values.return_value.get.return_value.execute.return_value = {'values': [['別の見出し']]}
        with self.assertRaises(ValueError):
            ensure_sheets(api, 'test')
        api.values.return_value.update.assert_not_called()

    def test_empty_append_does_not_send(self):
        api = fake_api()
        append_rows(api, 'test', [])
        api.values.assert_not_called()

    def test_append_raw_sort_and_expand(self):
        api = fake_api()
        append_rows(api, 'test', [['key', 1, 2]])
        self.assertEqual(api.values.return_value.append.call_args.kwargs['valueInputOption'], 'RAW')
        requests = [r for call in api.batchUpdate.call_args_list for r in call.kwargs['body']['requests']]
        self.assertTrue(any('sortRange' in r for r in requests))
        self.assertTrue(any(r.get('updateSheetProperties', {}).get('properties', {}).get('gridProperties', {}).get('rowCount') == 2002 for r in requests))

    def test_setup_replaces_only_own_chart(self):
        api = fake_api()
        setup_dashboard(api, 'test')
        requests = [r for call in api.batchUpdate.call_args_list for r in call.kwargs['body']['requests']]
        deleted = [r['deleteEmbeddedObject']['objectId'] for r in requests if 'deleteEmbeddedObject' in r]
        self.assertEqual(deleted, [99])
        self.assertEqual(sum('addChart' in r for r in requests), 1)
        data = api.values.return_value.batchUpdate.call_args.kwargs['body']['data']
        self.assertTrue(any('QUERY' in cell for item in data for row in item['values'] for cell in row))


if __name__ == '__main__':
    unittest.main()
