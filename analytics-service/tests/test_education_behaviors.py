"""Education acceptance examples expressed as configurations, never section rules."""
import unittest
import pandas as pd
from app.data_engine import chart


class EducationBehaviors(unittest.TestCase):
    def test_detailed_maximum_by_year_and_course(self):
        frame = pd.DataFrame({'AÑO': [2024, 2024, 2025, 2025], 'CURSO': ['A', 'A', 'A', 'B'], 'VALOR': [2, 8, 5, 3]})
        result = chart(frame, {'chart_type': 'line', 'x_col': 'AÑO', 'group_col': 'CURSO', 'y_col': 'VALOR', 'aggregation': 'max'})
        self.assertEqual(result['labels'], [2024, 2025])
        self.assertEqual([dataset['data'] for dataset in result['datasets']], [[8, 5], [None, 3]])

    def test_other_indicators_keep_source_percentage_separate(self):
        frame = pd.DataFrame({'DELEGACION': ['A', 'A'], 'NUNCA ASISTIERON': [2, 1], 'MATRICULA': [10, 10], 'PORCENTAJE': [99, 88]})
        cfg = {'chart_type': 'bar', 'x_col': 'DELEGACION', 'y_col': 'Cociente', 'aggregation': 'max',
               'calculated_fields': [{'name': 'Cociente', 'expression': '[NUNCA ASISTIERON]/[MATRICULA]', 'mode': 'row'}]}
        self.assertEqual(chart(frame, cfg)['datasets'][0]['data'], [.2])
        records = chart(frame, {**cfg, 'chart_type': 'table', 'table_columns': ['DELEGACION', 'PORCENTAJE']})['records']
        self.assertEqual([row['PORCENTAJE'] for row in records], [99, 88])

    def test_establishments_counts_distinct_and_teacher_condition(self):
        frame = pd.DataFrame({'ESTABLECIMIENTO': ['A', 'A', 'B'], 'DOCENTES': [1, 2, 0]})
        cfg = {'chart_type': 'indicator', 'x_col': 'ESTABLECIMIENTO', 'y_col': 'ESTABLECIMIENTO', 'aggregation': 'count'}
        self.assertEqual(chart(frame, cfg)['datasets'][0]['data'], [3])
        self.assertEqual(chart(frame, {**cfg, 'aggregation': 'distinct'})['datasets'][0]['data'], [2])
        teachers = {**cfg, 'aggregation': 'sum', 'y_col': 'DOCENTES', 'display_condition': {'expression': 'COUNT([ESTABLECIMIENTO]) <= 1 AND SUM([DOCENTES]) != 0', 'alternative': '-'}}
        self.assertTrue(chart(frame, teachers)['display_hidden'])
        self.assertTrue(chart(frame.iloc[2:], teachers)['display_hidden'])
        self.assertEqual(chart(frame.iloc[:1], teachers)['datasets'][0]['data'], [1])

    def test_educational_units_sum_real_field_and_unique_pairs(self):
        frame = pd.DataFrame({'ESTABLECIMIENTO': ['A', 'A', 'A'], 'MODALIDAD': ['X', 'X', 'Y'], 'TOTAL HISTORICO': [2, 3, 4]})
        cfg = {'chart_type': 'indicator', 'x_col': 'ESTABLECIMIENTO', 'y_col': 'TOTAL HISTORICO', 'aggregation': 'sum'}
        self.assertEqual(chart(frame, cfg)['datasets'][0]['data'], [9])
        pairs = chart(frame, {**cfg, 'chart_type': 'table', 'table_columns': ['ESTABLECIMIENTO', 'MODALIDAD'], 'table_unique': True})['records']
        self.assertEqual(pairs, [{'ESTABLECIMIENTO': 'A', 'MODALIDAD': 'X'}, {'ESTABLECIMIENTO': 'A', 'MODALIDAD': 'Y'}])

    def test_labor_costs_sum_multiple_metrics_and_count_repeated_month(self):
        frame = pd.DataFrame({'MES': ['Febrero', 'Enero', 'Enero'], 'ORDEN': [2, 1, 1], 'SALARIO': [30, 10, 20], 'HORAS': [3, 1, 2]})
        cfg = {'chart_type': 'bar', 'x_col': 'MES', 'y_col': 'SALARIO', 'y_cols': ['HORAS'], 'aggregation': 'sum',
               'category_sort': {'field': 'ORDEN', 'aggregation': 'min', 'direction': 'asc'}}
        result = chart(frame, cfg)
        self.assertEqual(result['labels'], ['Enero', 'Febrero'])
        self.assertEqual([item['data'] for item in result['datasets']], [[30, 30], [3, 3]])
        counter = {**cfg, 'chart_type': 'indicator', 'y_cols': [], 'display_condition': {'expression': 'COUNT([MES]) <= 1'}}
        self.assertTrue(chart(frame[frame['MES'] == 'Enero'], counter)['display_hidden'])

    def test_titles_allow_multiple_delegations_of_same_month(self):
        frame = pd.DataFrame({'MES': ['Enero', 'Enero'], 'DELEGACION': ['A', 'B'], 'TOTAL': [10, 20]})
        cfg = {'chart_type': 'indicator', 'x_col': 'MES', 'y_col': 'TOTAL', 'aggregation': 'sum',
               'display_condition': {'expression': 'COUNT_DISTINCT([MES]) <= 1'}}
        self.assertEqual(chart(frame, cfg)['datasets'][0]['data'], [30])

    def test_actions_multiple_metrics_sort_by_one_metric(self):
        frame = pd.DataFrame({'ACCION': ['A', 'B', 'A'], 'TOTAL': [10, 40, 20], 'PERSONAS': [1, 4, 2]})
        cfg = {'chart_type': 'bar', 'x_col': 'ACCION', 'y_col': 'TOTAL', 'y_cols': ['PERSONAS'], 'aggregation': 'sum',
               'category_sort': {'field': 'TOTAL', 'aggregation': 'sum', 'direction': 'desc'}}
        result = chart(frame, cfg)
        self.assertEqual(result['labels'], ['B', 'A'])
        self.assertEqual([item['data'] for item in result['datasets']], [[40, 30], [4, 3]])

    def test_offers_exclusions_intersect_section_filters_and_keep_unique_pairs(self):
        frame = pd.DataFrame({'GESTION': ['PRIVADA', 'ESTATAL', 'OTRA', 'ESTATAL'], 'AÑO': [2025, 2025, 2025, 2024],
                              'TIPO': ['X', 'X', 'X', 'Y'], 'OFERTA': ['A', 'A', 'B', 'C']})
        cfg = {'chart_type': 'indicator', 'x_col': 'TIPO', 'aggregation': 'count', 'filters': {'AÑO': ['2025']},
               'chart_filters': {'GESTION': {'exclude': ['PRIVADA']}}}
        self.assertEqual(chart(frame, cfg)['datasets'][0]['data'], [2])
        cfg['chart_filters'] = {'GESTION': {'exclude': ['ESTATAL']}}
        self.assertEqual(chart(frame, cfg)['datasets'][0]['data'], [2])
        table = chart(frame, {**cfg, 'chart_type': 'table', 'table_columns': ['TIPO', 'OFERTA'], 'table_unique': True})
        self.assertEqual(table['records'], [{'TIPO': 'X', 'OFERTA': 'A'}, {'TIPO': 'X', 'OFERTA': 'B'}])
