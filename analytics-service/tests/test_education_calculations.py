import unittest

from pydantic import ValidationError

from app.data_engine import chart, table
from app.generator import ChartConfig
from app.stored_chart_config import adapt_stored_chart


def saved_chart(frame, **options):
    config = ChartConfig.model_validate(adapt_stored_chart({'sheet': 'Datos', **options}))
    return chart(frame, config.model_dump())


class EducationCalculationTests(unittest.TestCase):
    def test_chart_filters_keep_saved_exclusions_and_tables_keep_unique_columns(self):
        frame, _ = table([['Gestión', 'Oferta', 'Detalle'], ['PRIVADA', 'A', 'uno'],
                          ['ESTATAL', 'B', 'dos'], ['ESTATAL', 'B', 'tres'], ['ESTATAL', 'C', 'cuatro']])
        result = saved_chart(frame, chart_type='table', x_col='Gestión', y_col='Oferta', aggregation='count',
                             table_columns=['Oferta'], table_distinct=True,
                             chart_filters={'Gestión': {'exclude': ['PRIVADA']}})
        self.assertEqual(result['columns'], ['Oferta'])
        self.assertEqual(result['records'], [{'Oferta': 'B'}, {'Oferta': 'C'}])
        self.assertEqual(result['filtered_rows'], 3)

    def test_indicator_row_limit_counts_nonempty_rows_and_preserves_hide_zero(self):
        frame, _ = table([['Establecimiento', 'Docentes'], ['A', 5], ['A', 2], ['B', 0], ['C', 3]])
        config = {'chart_type': 'indicator', 'x_col': 'Establecimiento', 'y_col': 'Docentes', 'aggregation': 'sum',
                  'indicator_condition': {'count_field': 'Establecimiento', 'max_count': 1,
                                          'distinct': False, 'hide_zero': True}}

        def value(selection):
            result = saved_chart(frame, **config, filters={'Establecimiento': [selection]})
            return result['datasets'][0]['data'][0]

        self.assertIsNone(value('A'))
        self.assertIsNone(value('B'))
        self.assertEqual(value('C'), 3)

    def test_profile_chart_keeps_summed_counts_and_calculates_the_percent_per_category(self):
        frame, _ = table([['GÉNERO', 'ÁMBITO', 'ALUMNOS', 'BASE DE CÁLCULO', 'PORCENTAJE ALUMNOS'],
                          ['F', 'URBANO', 20, 80, 25], ['F', 'RURAL', 12, 50, 24],
                          ['M', 'URBANO', 10, 100, 10], ['M', 'RURAL', 6, 60, 10]])
        formula = ('IF(COUNT_DISTINCT([ÁMBITO]) > 1, MAX([ALUMNOS]) / MAX([BASE DE CÁLCULO]), '
                   'MAX([PORCENTAJE ALUMNOS]) / 100)')
        config = {'x_col': 'GÉNERO', 'y_col': 'ALUMNOS', 'y_cols': ['Porcentaje'], 'aggregation': 'sum',
                  'calculated_fields': {'Porcentaje': formula}, 'value_formats': {'Porcentaje': 'Percent(2)'}}
        self.assertEqual(adapt_stored_chart(config)['number_formats']['Porcentaje'],
                         {'style': 'percentage', 'digits': 2, 'group_digits': True})
        all_rows = saved_chart(frame, **config)
        rural = saved_chart(frame, **config, filters={'ÁMBITO': ['RURAL']})
        self.assertEqual(all_rows['labels'], ['F', 'M'])
        self.assertEqual([s['data'] for s in all_rows['datasets']], [[32, 16], [.25, .1]])
        self.assertEqual([s['data'] for s in rural['datasets']], [[12, 6], [.24, .1]])

    def test_response_rate_divides_totals_and_handles_zero_denominator(self):
        frame, _ = table([['año', 'ENTEROS DE LOS PORCENTAJES', 'VALOR ENTERO TOTAL'],
                          [2024, 2, 10], [2024, 15, 30], [2025, 3, 0]])
        config = {'chart_type': 'indicator', 'x_col': 'año', 'y_col': 'Tasa de respuesta', 'aggregation': 'sum',
                  'calculated_fields': {'Tasa de respuesta': 'SUM([ENTEROS DE LOS PORCENTAJES]) / SUM([VALOR ENTERO TOTAL])'}}
        self.assertEqual(saved_chart(frame, **config, filters={'año': ['2024']})['datasets'][0]['data'], [.425])
        self.assertEqual(saved_chart(frame, **config, filters={'año': ['2025']})['datasets'][0]['data'], [None])

    def test_unknown_formula_is_rejected(self):
        with self.assertRaises(ValidationError):
            ChartConfig.model_validate({'sheet': 'Datos', 'calculated_fields': {'Resultado': 'MIN([Otra])'}})
