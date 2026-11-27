import json
import unittest

import pandas as pd

from app.data_engine import chart, DataError
from app.expression_engine import CalculationPlan, compile_expression, evaluate, ExpressionError
from app.editor_storage import revision, update_operations


class ExpressionExtensions(unittest.TestCase):
    def setUp(self):
        self.frame = pd.DataFrame({'AÑO': [2024, 2025, 2025], 'Delegación': ['A', 'A', 'B'],
                                   'NUNCA ASISTIERON': [1, 6, 1], 'MATRICULA': [10, 10, 20],
                                   'Gestión': ['ESTATAL', 'PRIVADA', 'OTRA'], 'Institución': ['X', 'X', 'Y']})
        self.config = {'chart_type': 'bar', 'x_col': 'Delegación', 'y_col': 'Tasa', 'aggregation': 'max',
                       'calculated_fields': [{'name': 'Tasa', 'expression': '[NUNCA ASISTIERON] / [MATRICULA]', 'mode': 'row'}]}

    def test_row_ratio_before_max_differs_from_ratio_of_sums(self):
        per_row = chart(self.frame, self.config)
        self.assertEqual(per_row['datasets'][0]['data'], [.6, .05])
        cfg = {**self.config, 'calculated_fields': [{'name': 'Tasa', 'expression': 'SUM([NUNCA ASISTIERON])/SUM([MATRICULA])', 'mode': 'aggregate'}]}
        self.assertEqual(chart(self.frame, cfg)['datasets'][0]['data'], [.35, .05])

    def test_case_text_and_numeric_keep_order(self):
        for expression, expected in [('CASE WHEN [AÑO] = 2024 THEN "$ 61 M" WHEN [AÑO] = 2025 THEN "$ 891 M" ELSE NULL END', ['$ 61 M', '$ 891 M', '$ 891 M']),
                                     ('CASE WHEN [AÑO] >= 2024 THEN 1 WHEN [AÑO] = 2025 THEN 2 ELSE 0 END', [1, 1, 1])]:
            self.assertEqual(evaluate(compile_expression(expression), self.frame).tolist(), expected)

    def test_null_zero_boolean_and_functions(self):
        frame = pd.DataFrame({'V': [0, None, 2], 'T': ['a', 'a', None]})
        cases = {'SUM([V])': 2, 'AVG([V])': 1, 'MIN([V])': 0, 'MAX([V])': 2, 'MEDIAN([V])': 1,
                 'COUNT([T])': 2, 'COUNT_DISTINCT([T])': 1, 'COALESCE(1/0, 7)': 7,
                 'NULLIF(2,2)': None, 'ROUND(-1.234,2)': -1.23,
                 'SELECTED_COUNT([AÑO])': 1, 'NOT (COUNT([V]) > 2) AND COUNT([V]) != 0': True}
        for expression, expected in cases.items():
            with self.subTest(expression=expression):
                self.assertEqual(evaluate(compile_expression(expression), frame, {'AÑO': {'include': ['2025']}}), expected)
        self.assertEqual(evaluate(compile_expression('IF([V] IS NULL, 3, [V])'), frame).tolist(), [0, 3, 2])
        self.assertEqual(evaluate(compile_expression('[V] > NULL'), frame).tolist(), [False, False, False])
        self.assertEqual(evaluate(compile_expression('CONCAT([T], "!")'), frame).tolist(), ['a!', 'a!', '!'])
        json.dumps(chart(pd.DataFrame({'X': ['a'], 'V': [0]}), {'chart_type': 'indicator', 'x_col': 'X', 'y_col': 'Z', 'aggregation': 'sum',
                       'calculated_fields': [{'name': 'Z', 'expression': '1/[V]', 'mode': 'row'}]}), allow_nan=False)

    def test_invalid_expressions_and_dependencies(self):
        for expression in ['__import__("os")', '[V].value', '2 ** 100', 'SUM(SUM([V]))', '[Falta]+1', '"a"+1', '"1" = 1']:
            with self.subTest(expression=expression), self.assertRaises((ExpressionError, DataError)):
                plan = CalculationPlan([{'name': 'Prueba', 'expression': expression}], self.frame.columns)
                plan.value('Prueba', self.frame)
        for fields in [[{'name': 'AÑO', 'expression': '1'}], [{'name': 'A', 'expression': '[B]'}, {'name': 'B', 'expression': '[A]'}],
                       [{'name': 'A', 'expression': '1'}, {'name': 'a', 'expression': '2'}]]:
            with self.assertRaises(ExpressionError):
                CalculationPlan(fields, self.frame.columns)
        explosive = [{'name': 'F0', 'expression': '[MATRICULA] + [MATRICULA]', 'mode': 'row'}]
        explosive.extend({'name': f'F{index}', 'expression': f'[F{index-1}] + [F{index-1}]', 'mode': 'row'} for index in range(1, 20))
        with self.assertRaises(ExpressionError):
            CalculationPlan(explosive, self.frame.columns)

    def test_counts_and_selection_have_separate_meanings(self):
        cfg = {**self.config, 'chart_type': 'indicator', 'y_col': 'NUNCA ASISTIERON', 'aggregation': 'sum',
               'display_condition': {'expression': 'COUNT([AÑO]) <= 1', 'alternative': '-'}}
        self.assertTrue(chart(self.frame, cfg)['display_hidden'])
        cfg['display_condition']['expression'] = 'COUNT_DISTINCT([AÑO]) = 1 AND SELECTED_COUNT([AÑO]) = 1'
        cfg['selected_filters'] = {'AÑO': ['2025']}
        self.assertEqual(chart(self.frame[self.frame['AÑO'] == 2025], cfg)['datasets'][0]['data'], [7])

    def test_chart_exclusions_keep_third_category(self):
        cfg = {'chart_type': 'indicator', 'x_col': 'Delegación', 'aggregation': 'count',
               'chart_filters': {'Gestión': {'exclude': ['PRIVADA']}}}
        self.assertEqual(chart(self.frame, cfg)['datasets'][0]['data'], [2])

    def test_table_distinct_selected_combinations(self):
        cfg = {'chart_type': 'table', 'table_columns': ['Institución'], 'table_unique': True,
               'chart_filters': {'Gestión': {'exclude': ['OTRA']}}}
        self.assertEqual(chart(self.frame, cfg)['records'], [{'Institución': 'X'}])
        cfg['table_columns'] = ['Institución', 'Gestión']
        self.assertEqual(len(chart(self.frame, cfg)['records']), 2)

    def test_none_accepts_identical_repetitions_and_rejects_different_values(self):
        cfg = {'chart_type': 'bar', 'x_col': 'Institución', 'y_col': 'AÑO', 'aggregation': 'none'}
        with self.assertRaises(DataError):
            chart(self.frame, cfg)
        self.assertEqual(chart(self.frame.iloc[1:], cfg)['datasets'][0]['data'], [2025, 2025])

    def test_others_recalculates_union_and_mean(self):
        frame = pd.DataFrame({'X': ['A', 'B', 'C', 'C'], 'V': [10, 4, 2, 4], 'ID': ['a', 'b', 'b', 'c']})
        cfg = {'chart_type': 'bar', 'x_col': 'X', 'y_col': 'ID', 'aggregation': 'distinct', 'category_limit': 2, 'include_other': True}
        self.assertEqual(chart(frame, cfg)['datasets'][0]['data'], [1, 2])
        cfg.update(y_col='V', aggregation='mean')
        self.assertAlmostEqual(chart(frame, cfg)['datasets'][0]['data'][1], 10 / 3)

    def test_other_recalculates_aggregate_formula_and_multiple_grouped_metrics(self):
        frame = pd.DataFrame({'X': ['A', 'B', 'C', 'C'], 'G': ['g', 'g', 'g', 'g'], 'V': [10, 4, 2, 4], 'D': [2, 1, 2, 2]})
        cfg = {'chart_type': 'bar', 'x_col': 'X', 'group_col': 'G', 'y_col': 'Tasa', 'aggregation': 'sum',
               'calculated_fields': [{'name': 'Tasa', 'expression': 'SUM([V])/SUM([D])', 'mode': 'aggregate'}], 'category_limit': 2, 'include_other': True}
        self.assertEqual(chart(frame, cfg)['datasets'][0]['data'], [5, 2])
        cfg.update(y_col='V', y_cols=['D'], aggregation='mean', calculated_fields=[])
        result = chart(frame, cfg)
        self.assertAlmostEqual(result['datasets'][0]['data'][1], 10 / 3)
        self.assertAlmostEqual(result['datasets'][1]['data'][1], 5 / 3)

    def test_calculated_text_dimension_and_escaped_column_names(self):
        frame = pd.DataFrame({'A]B': [1, 2], 'V': [10, 20]})
        cfg = {'chart_type': 'bar', 'x_col': 'Nombre', 'y_col': 'V', 'aggregation': 'sum',
               'calculated_fields': [{'name': 'Nombre', 'expression': 'CASE WHEN [A]]B] = 1 THEN "Primero" ELSE "Otro" END', 'mode': 'row'}]}
        result = chart(frame, cfg)
        self.assertEqual(result['labels'], ['Otro', 'Primero'])
        self.assertEqual(result['datasets'][0]['data'], [20, 10])

    def test_raw_leaf_updates_and_ordered_preparation_revision(self):
        original = {'name': 'Sección', 'widgets': [{'id': 'a', 'title': 'Antes', 'config': {'unrecognized': 1, 'sheet': 'Datos'}}]}
        self.assertEqual(update_operations(original, original), {})
        edited = json.loads(json.dumps(original))
        edited['widgets'][0]['title'] = 'Después'
        self.assertEqual(update_operations(original, edited), {'$set': {'widgets.0.title': 'Después'}})
        edited['widgets'][0]['config']['computed_columns'] = {'A': [], 'B': []}
        first = revision(edited)
        edited['widgets'][0]['config']['computed_columns'] = {'B': [], 'A': []}
        self.assertNotEqual(first, revision(edited))

    def test_reusable_forward_references_and_selection_context_by_row(self):
        definitions = [{'name': 'Doble', 'expression': '[Tasa] * 2', 'mode': 'row'}, self.config['calculated_fields'][0]]
        cfg = {**self.config, 'calculated_fields': definitions, 'y_col': 'Doble'}
        self.assertEqual(chart(self.frame, cfg)['datasets'][0]['data'], [1.2, .1])
        cfg['calculated_fields'] = [{'name': 'Visible', 'expression': 'IF(SELECTED_COUNT([AÑO]) = 1, [MATRICULA], NULL)', 'mode': 'row'}]
        cfg.update(y_col='Visible', selected_filters={'AÑO': ['2025']})
        self.assertEqual(chart(self.frame, cfg)['datasets'][0]['data'], [10, 20])

    def test_summary_calculations_sort_using_an_unselected_source_column(self):
        frame = pd.DataFrame({'MES': ['Febrero', 'Enero', 'Enero'], 'ORDEN': [2, 1, 1], 'A': [20, 2, 4], 'B': [2, 2, 2]})
        cfg = {'chart_type': 'table', 'table_mode': 'summary', 'x_col': 'MES', 'table_dimensions': ['MES'], 'y_col': 'Tasa', 'aggregation': 'sum',
               'calculated_fields': [{'name': 'Tasa', 'expression': 'SUM([A])/SUM([B])', 'mode': 'aggregate'}],
               'table_sorts': [{'field': 'ORDEN', 'aggregation': 'min', 'direction': 'asc'}]}
        result = chart(frame, cfg)
        self.assertEqual(result['records'], [{'MES': 'Enero', 'Tasa': 1.5}, {'MES': 'Febrero', 'Tasa': 10}])
        cfg.update(calculated_fields=[], y_col='ORDEN', aggregation='none')
        self.assertEqual(chart(frame, cfg)['records'], [{'MES': 'Enero', 'ORDEN': 1}, {'MES': 'Febrero', 'ORDEN': 2}])


if __name__ == '__main__':
    unittest.main()
