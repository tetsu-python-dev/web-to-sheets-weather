import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch, Mock
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from weather import parse_html, number, new_only, fetch_day, JST, NotPublishedYet

STATION = {'name': '東京', 'prec_no': '44', 'block_no': '47662'}
DAY = date(2026, 9, 30)
NOW = datetime(2026, 10, 1, 1, tzinfo=JST)
# 自作の検証用HTML。観測値は架空。
HEADER = '''<table id="tablefix1"><tr><th>時分</th><th>気圧(hPa)</th>
<th>降水量(mm)</th><th>気温(℃)</th><th>相対湿度(％)</th>
<th>風向・風速(m/s)</th><th>日照時間(分)</th></tr>
<tr><th>現地</th><th>海面</th><th>平均</th><th>風向</th><th>最大瞬間</th><th>風向</th></tr>'''


def html(clock='00:10', temp='20.0', rain='0.0', humidity='65', wind='1.5'):
    cells = [clock, '1010', '1013', rain, temp, humidity, wind, '南', '3.0', '南', '0']
    return HEADER + '<tr>' + ''.join(f'<td>{v}</td>' for v in cells) + '</tr></table>'


class WeatherTests(unittest.TestCase):
    def parse(self, text, now=NOW):
        return parse_html(text, DAY, STATION, now, 'https://example.test')

    def test_regular_and_midnight(self):
        row = self.parse(html('24:00'))[0]
        self.assertIn('2026-10-01T00:00:00+09:00', row[0])
        self.assertEqual(row[3], '2026-09-30')
        self.assertEqual(row[5:9], [20.0, 0.0, 65.0, 1.5])

    def test_missing_and_abnormal_do_not_become_zero(self):
        row = self.parse(html(temp='20.0 )', rain='×', humidity='101', wind='abc'))
        self.assertEqual(row, [])  # 全項目不確実なら次回まで保留
        row = self.parse(html(temp='20', rain='×', humidity='101'))[0]
        self.assertEqual(row[6:8], ['', ''])
        self.assertIn('降水量:欠損', row[9])
        self.assertIn('湿度:範囲異常', row[9])
        self.assertEqual(number('0.0', 0, 300), (0.0, ''))

    def test_no_precipitation_symbol_is_zero(self):
        row = self.parse(html(rain='--'))[0]
        self.assertEqual(row[6], 0.0)
        self.assertEqual(row[9], 'OK')
        self.assertIn('--', row[10])

    def test_future_is_skipped(self):
        self.assertEqual(self.parse(html('10:00'), datetime(2026, 9, 30, 9, tzinfo=JST)), [])

    def test_layout_change_stops(self):
        with self.assertRaises(ValueError):
            self.parse(html().replace('気温(℃)', '別の列'))
        with self.assertRaises(ValueError):
            self.parse('<html>アクセスエラー</html>')

    def test_explicit_unpublished_notice(self):
        with self.assertRaises(NotPublishedYet):
            self.parse('<p>閲覧可能な日は、昨日2026年9月30日までです。</p>')

    def test_bad_clock_stops(self):
        for clock in ['25:00', '01:13', '00:00', 'invalid']:
            with self.assertRaises(ValueError):
                self.parse(html(clock))

    def test_duplicate_and_order(self):
        first = self.parse(html('00:10'))[0]
        second = self.parse(html('00:20'))[0]
        self.assertEqual(new_only([second, first, second], {first[0]}), [second])

    @patch('weather.requests.get')
    def test_http_failure_is_not_parsed(self, get):
        import requests
        response = Mock()
        response.raise_for_status.side_effect = requests.HTTPError('503')
        get.return_value = response
        with self.assertRaises(requests.HTTPError):
            fetch_day(DAY, STATION, NOW)
        get.assert_called_once()

    @patch('weather.requests.get')
    def test_timeout_propagates(self, get):
        import requests
        get.side_effect = requests.Timeout()
        with self.assertRaises(requests.Timeout):
            fetch_day(DAY, STATION, NOW)


if __name__ == '__main__':
    unittest.main()
