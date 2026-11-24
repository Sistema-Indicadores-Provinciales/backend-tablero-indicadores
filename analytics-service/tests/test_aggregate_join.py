import unittest
from unittest.mock import patch

from app import generator as api
from app.aggregate_join import aggregate_join
from app.data_engine import DataError, chart, table


class AggregateJoinTests(unittest.TestCase):
    def inputs(self):
        left = table([['Year', 'Month', 'Org', 'Hours', 'Order'],
                      [2026, 'Jan', 'A', 10, 1], [2026, 'Jan', 'A', 30, 1],
                      [2026, 'Feb', 'A', 60, 2], [2026, 'Jan', 'B', 100, 1],
                      [2026, None, 'A', 4, 1]])
        right = table([['Year', 'Month', 'Org', 'Previous', 'Order'],
                       [2026, 'Jan', 'A', 5, 1], [2026, 'Jan', 'A', 15, 1],
                       [2026, 'Feb', 'A', 40, 2], [2026, 'Jan', 'B', 100, 1],
                       [2027, 'Jan', 'C', 200, 1], [2026, None, 'A', 2, 1]])
        cfg = dict(keys=['Year', 'Month', 'Org'], left_sums=['Hours', 'Order'],
                   right_dimensions=['Order'], right_sums={'Previous': 'Previous', 'Right order': 'Order'})
        return left, right, cfg

    def test_aggregate_before_outer_join_and_preserve_unmatched_left_keys(self):
        left, right, cfg = self.inputs()
        frame, meta = aggregate_join(*left, *right, cfg)
        january = frame[(frame.Month == 'Jan') & (frame.Org == 'A')].iloc[0]
        self.assertEqual((january.Hours, january.Previous, january.Order), (40, 20, 2))
        unmatched = frame[frame['Hoja combinada · Year'] == 2027].iloc[0]
        self.assertIsNone(unmatched.Year)
        self.assertIsNone(unmatched.Month)
        self.assertIsNone(unmatched.Hours)
        # Each null key row is retained independently, never paired with the other.
        self.assertEqual(len(frame), 6)
        self.assertEqual(len(frame[frame.Month.isna()]), 3)
        self.assertEqual(meta['row_count'], 6)
        self.assertEqual(meta['column_meta']['Previous']['type'], 'number')

    def test_right_extra_dimension_preserves_original_multiplicity(self):
        left, right, cfg = self.inputs()
        right[0].loc[len(right[0])] = [2026, 'Jan', 'A', 9, 99]
        frame, _ = aggregate_join(*left, *right, cfg)
        rows = frame[(frame.Month == 'Jan') & (frame.Org == 'A')]
        self.assertEqual(rows.Hours.tolist(), [40, 40])
        self.assertEqual(rows.Previous.tolist(), [20, 9])

    def test_bounds_and_invalid_columns_reject_instead_of_truncating(self):
        left, right, cfg = self.inputs()
        with patch('app.aggregate_join.MAX_ROWS', 2), self.assertRaises(DataError):
            aggregate_join(*left, *right, cfg)
        with self.assertRaises(DataError):
            aggregate_join(*left, *right, {**cfg, 'left_sums': ['Missing']})
        with self.assertRaises(DataError):
            aggregate_join(*left, *right, {**cfg, 'left_sums': ['Org']})

    def test_reads_both_tabs_with_same_authorized_source_and_token(self):
        left, right, cfg = self.inputs()
        config = api.ChartConfig(sheet='Current', aggregate_join={**cfg, 'sheet': 'Previous'})
        def read(doc, token, sheet):
            self.assertEqual(doc['_id'], 'authorized')
            self.assertEqual(token, 'viewer-token')
            data = left[0] if sheet == 'Current' else right[0]
            return {'values': [data.columns.tolist(), *data.values.tolist()]}
        with patch.object(api, 'google_get', side_effect=read) as get:
            result, _ = api.table_from_source({'kind': 'google', '_id': 'authorized'}, config, 'viewer-token')
        self.assertEqual(get.call_count, 2)
        self.assertEqual(len(result), 6)
        self.assertNotEqual(api.read_config_key(config), api.read_config_key(api.ReadConfig(sheet='Current')))
        with self.assertRaises(DataError):
            api.table_from_source({'kind': 'google', 'access_mode': 'public'}, config, '')


class MetricRatioTests(unittest.TestCase):
    def test_ratio_of_sums_after_filtering_and_per_series_order(self):
        frame, _ = table([['Month', 'Org', 'Actual', 'Previous', 'Order'],
                          ['Jan', 'A', 30, 10, 1], ['Jan', 'A', 30, 30, 1],
                          ['Feb', 'A', 90, 100, 2], ['Jan', 'B', 22, 20, 1]])
        cfg = dict(chart_type='line', x_col='Month', y_col='Actual', group_col='Org', aggregation='sum',
                   metric_ratio={'denominator': 'Previous', 'subtract_denominator': True, 'scale': 100, 'series_order': 'desc'},
                   category_sort={'field': 'Order', 'aggregation': 'max', 'direction': 'asc'})
        result = chart(frame, cfg)
        self.assertEqual(result['labels'], ['Jan', 'Feb'])
        self.assertEqual(result['datasets'], [{'label': 'B', 'data': [10, None]}, {'label': 'A', 'data': [50, -10]}])
        filtered = chart(frame, {**cfg, 'filters': {'Month': ['Jan']}})
        self.assertEqual(filtered['datasets'][0], {'label': 'A', 'data': [50]})
        restricted = chart(frame, {**cfg, 'filter_rules': [{'column': 'Org', 'operator': 'equal', 'value': 'B'}]})
        self.assertEqual(restricted['datasets'], [{'label': 'B', 'data': [10]}])

    def test_zero_or_missing_denominator_remains_missing(self):
        frame, _ = table([['Month', 'Actual', 'Previous'], ['Jan', 10, 0], ['Feb', 10, None], ['Mar', None, 20]])
        cfg = dict(chart_type='line', x_col='Month', y_col='Actual', aggregation='sum',
                   metric_ratio={'denominator': 'Previous', 'subtract_denominator': True})
        result = chart(frame, cfg)
        self.assertEqual(result['datasets'][0]['data'], [None, None, None])
        for extra in [{'chart_type': 'pie'}, {'include_other': True}, {'y_cols': ['Previous']}, {'aggregation': 'mean'}]:
            with self.assertRaises(DataError):
                chart(frame, {**cfg, **extra})
