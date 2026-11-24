import unittest
import random

import pandas as pd
from app.data_engine import chart, sort_group_values
from app.generator import SortConfig


class SelectionOrderTests(unittest.TestCase):
    def test_better_candidates_remove_then_insert_before_resolving_ties(self):
        frame = pd.DataFrame({'Category': range(10), 'Rank': [1, 3, 1, 2, 3, 3, 1, 3, 2, 3]})
        config = {'field': 'Rank', 'aggregation': 'max', 'direction': 'desc', 'tie_breaker': 'source_heap'}
        self.assertEqual(sort_group_values(frame, 'Category', config, category_limit=4),
                         [4, 9, 7, 5, 1, 3, 8, 0, 2, 6])

    def test_equal_scores_follow_source_heap_without_losing_other_values(self):
        frame = pd.DataFrame({'Category': [f'C{i}' for i in range(62)], 'Rank': [9] * 62, 'Value': range(1, 63)})
        cfg = {'chart_type': 'pie', 'x_col': 'Category', 'y_col': 'Value', 'aggregation': 'sum',
               'category_limit': 20, 'include_other': True,
               'category_sort': {'field': 'Rank', 'aggregation': 'max', 'direction': 'desc', 'tie_breaker': 'source_heap'}}
        result = chart(frame, cfg)
        expected = [2, 4, 5, 6, 8, 9, 10, 11, 12, 13, 14, 16, 17, 18, 19, 20, 15, 7, 3]
        self.assertEqual(result['labels'], [f'C{i}' for i in expected] + ['Otros'])
        self.assertEqual(result['datasets'][0]['data'][:-1], [i + 1 for i in expected])
        self.assertEqual(sum(result['datasets'][0]['data']), frame.Value.sum())
        filtered = chart(frame, {**cfg, 'filters': {'Category': ['C0', 'C1', 'C2']}})
        self.assertEqual(filtered['labels'], ['C2', 'C1', 'C0'])
        self.assertEqual(filtered['datasets'][0]['data'], [3, 2, 1])

    def test_mixed_scores_choose_best_values_both_directions_with_nulls(self):
        rng = random.Random(29)
        scores = [rng.randint(0, 7) for _ in range(80)] + [None, None]
        frame = pd.DataFrame({'Category': range(len(scores)), 'Rank': scores})
        for direction in ['asc', 'desc']:
            config = {'field': 'Rank', 'aggregation': 'max', 'direction': direction, 'tie_breaker': 'source_heap'}
            values = sort_group_values(frame, 'Category', config, category_limit=12)
            self.assertEqual(set(values), set(range(len(scores))))
            self.assertEqual(len(values), len(scores))
            expected = sorted(scores[:-2], reverse=direction == 'desc')[:13]
            self.assertEqual([scores[i] for i in values[:13]], expected)
            self.assertEqual(set(values[-2:]), {80, 81})
        self.assertEqual(sort_group_values(frame.iloc[:0], 'Category', config), [])

    def test_remainder_distinct_count_does_not_sum_overlapping_categories(self):
        frame = pd.DataFrame({'Category': ['A', 'B', 'C', 'D', 'E'], 'Person': ['p1', 'p1', 'p1', 'p2', 'p2'], 'Rank': [1]*5})
        cfg = {'chart_type': 'pie', 'x_col': 'Category', 'y_col': 'Person', 'aggregation': 'distinct',
               'category_limit': 2, 'include_other': True,
               'category_sort': {'field': 'Rank', 'aggregation': 'max', 'direction': 'desc', 'tie_breaker': 'source_heap'}}
        result = chart(frame, cfg)
        self.assertEqual(result['labels'], ['C', 'Otros'])
        self.assertEqual(result['datasets'][0]['data'], [1, 2])

    def test_default_sort_and_legacy_tie_order_remain_unchanged(self):
        frame = pd.DataFrame({'Category': ['C', 'A', 'B'], 'Rank': [1, 1, 1]})
        config = SortConfig(field='Rank', aggregation='max', direction='desc').model_dump()
        self.assertEqual(sort_group_values(frame, 'Category', config), ['A', 'B', 'C'])
        self.assertEqual(sort_group_values(frame, 'Category', {**config, 'tie_order': ['B', 'A', 'C']}), ['B', 'A', 'C'])
