import unittest

from app.data_engine import chart, table, restrict_frame, contextual_filter_options, sort_group_values, DataError
from app.calculated_columns import calculated_columns


class MigrationSemanticsTests(unittest.TestCase):
    def test_indicator_requires_unambiguous_dimensions_after_all_filters(self):
        frame, _ = table([['Vacuna','Dosis','Población'],['A','1ra',100],['A','1ra',20],
                          ['A','2da',100],['B','1ra',200],['A',None,5]])
        cfg = {'chart_type':'indicator','x_col':'Vacuna','y_col':'Población',
               'indicator_single_value_columns':['Vacuna','Dosis']}
        self.assertEqual(chart(frame,cfg)['datasets'][0]['data'],[None])
        self.assertEqual(chart(frame,{**cfg,'filters':{'Vacuna':['A']}})['datasets'][0]['data'],[None])
        self.assertEqual(chart(frame,{**cfg,'filters':{'Vacuna':['A'],'Dosis':['1ra']}})['datasets'][0]['data'],[120])
        restricted = {**cfg,'filters':{'Vacuna':['A']},'filter_rules':[{'column':'Dosis','operator':'not_equal','value':'2da'}]}
        self.assertEqual(chart(frame,restricted)['datasets'][0]['data'],[125])
        self.assertEqual(chart(frame,{**cfg,'filters':{'Vacuna':['No existe']}})['filtered_rows'],0)
        self.assertEqual(chart(frame,{**cfg,'indicator_single_value_columns':[]})['datasets'][0]['data'],[425])
        for invalid in [{'chart_type':'bar'},{'indicator_single_value_columns':['Inexistente']}]:
            with self.assertRaises(DataError):chart(frame,{**cfg,**invalid})

    def test_date_extrema_sort_filter_labels_chronologically(self):
        frame, _ = table([['Etiqueta','Fecha'],['Octubre','30/10/2026'],['Enero','01/01/2026'],
                          ['Octubre','29/10/2026'],['Vacío',None]],types={'Fecha':'date'})
        config = {'field':'Fecha','aggregation':'max','direction':'desc'}
        self.assertEqual(sort_group_values(frame,'Etiqueta',config),['Octubre','Enero','Vacío'])
        self.assertEqual(sort_group_values(frame,'Etiqueta',{**config,'aggregation':'min','direction':'asc'}),['Enero','Octubre','Vacío'])
        options = contextual_filter_options(frame,['Etiqueta'],{}, {'Etiqueta':config})
        self.assertEqual(options['Etiqueta']['values'][:2],['Octubre','Enero'])
        with self.assertRaises(DataError):
            sort_group_values(frame,'Etiqueta',{**config,'aggregation':'sum'})
        with self.assertRaises(DataError):
            sort_group_values(frame,'Etiqueta',{**config,'field':'Etiqueta'})
        zoned, _ = table([['Etiqueta','Fecha'],['A','2026-10-30T00:30:00+02:00'],
                           ['B','2026-10-29T23:30:00+00:00']],types={'Fecha':'date'})
        self.assertEqual(sort_group_values(zoned,'Etiqueta',config),['B','A'])

    def test_indicator_adds_columns_per_row_preserving_null_arithmetic(self):
        frame, _ = table([['Fecha','Activa','Contacto','Síntomas'],['A',1,2,3],['A',4,None,5],['B',0,0,0]])
        cfg = {'chart_type':'indicator','x_col':'Fecha','y_col':'Activa','indicator_addends':['Contacto','Síntomas']}
        self.assertEqual(chart(frame,cfg)['datasets'][0]['data'],[6])
        self.assertEqual(chart(frame,{**cfg,'filters':{'Fecha':['B']}})['datasets'][0]['data'],[0])
        self.assertEqual(frame['Activa'].iloc[0],1)
        with self.assertRaises(DataError):
            chart(frame,{**cfg,'indicator_addends':['No existe']})

    def test_published_text_rate_is_not_recomputed_or_added_across_dates(self):
        frame, _ = table([['Fecha','Tasa'],['A','7,30%'],['B','0,00%']],types={'Tasa':'text'})
        cfg={'chart_type':'indicator','indicator_text_column':'Tasa','filters':{'Fecha':['A']}}
        self.assertEqual(chart(frame,cfg)['indicator_text'],'7,30%')
        result=chart(frame,{**cfg,'filters':{}})
        self.assertIsNone(result['indicator_text'])
        self.assertTrue(result['warnings'])
        self.assertIsNone(chart(frame,{**cfg,'filters':{'Fecha':['Sin datos']}})['indicator_text'])
        with self.assertRaises(DataError):
            chart(frame,{**cfg,'chart_type':'pie'})

    def test_conditional_sum_keeps_zero_for_other_zones_and_numeric_year_cutoff(self):
        frame, _ = table([['Año','Zona','Total'],[2020,'CAPITAL',900],[2021,'CAPITAL',2],
                          [2025,'CAPITAL',3],[2025,'INTERIOR',7],[2026,'OTROS',None]])
        cfg = {'chart_type':'indicator','aggregation':'sum','x_col':'Año','y_col':'Total',
               'filter_rules':[{'column':'Año','operator':'greater_equal','value':'2021'}],
               'indicator_sum_rules':[{'column':'Zona','operator':'equal','value':'CAPITAL'}]}
        self.assertEqual(chart(frame,cfg)['datasets'][0]['data'],[5])
        selected = chart(frame,{**cfg,'filters':{'Zona':['INTERIOR']}})
        self.assertEqual(selected['datasets'][0]['data'],[0])
        self.assertEqual(selected['filtered_rows'],1)
        self.assertEqual(chart(frame,{**cfg,'filters':{'Año':['2026']}})['datasets'][0]['data'],[0])
        self.assertEqual(chart(frame,{**cfg,'filters':{'Año':['2020']}})['filtered_rows'],0)
        self.assertEqual(frame['Total'].iloc[3],7)
        with self.assertRaises(DataError):
            chart(frame,{**cfg,'chart_type':'bar'})
        with self.assertRaises(DataError):
            restrict_frame(frame,{'filter_rules':[{'column':'Año','operator':'greater_equal','value':'NaN'}]})
        options = contextual_filter_options(restrict_frame(frame,cfg),['Año'],{})
        self.assertNotIn('2020',options['Año']['values'])

    def test_ratio_divides_sums_after_filters_without_dropping_missing_numerators(self):
        frame, _ = table([['Año','Cubiertos','Total'],[2026,10,20],[2026,30,40],[2026,None,40],[2025,0,0]])
        cfg = {'chart_type':'indicator','y_col':'Cubiertos','indicator_ratio':{'denominator':'Total','scale':100},'filters':{'Año':['2026']}}
        self.assertEqual(chart(frame,cfg)['datasets'][0]['data'],[40])
        self.assertEqual(chart(frame,{**cfg,'filters':{'Año':['2025']}})['datasets'][0]['data'],[None])
        with self.assertRaises(DataError):
            chart(frame,{**cfg,'indicator_ratio':{'denominator':'No existe'}})

    def test_empty_months_remain_null_without_fabricating_zeroes(self):
        frame, _ = table([['Mes','Monto','Cantidad'],['Enero',10,2],['Febrero',None,None]])
        cfg = {'chart_type':'bar','x_col':'Mes','y_col':'Monto','preserve_empty_categories':True}
        result = chart(frame,cfg)
        self.assertEqual(result['labels'],['Enero','Febrero'])
        self.assertEqual(result['datasets'][0]['data'],[10,None])
        self.assertEqual(chart(frame,{**cfg,'y_cols':['Cantidad']})['datasets'][1]['data'],[2,None])
        self.assertEqual(chart(frame,{**cfg,'filters':{'Mes':['Febrero']}})['datasets'][0]['data'],[None])

    def test_indicator_lookup_uses_category_after_filters_and_does_not_sum_rows(self):
        frame, _ = table([['Año','Organismo'],[2026,'A'],[2026,'B'],[2025,'A'],[2027,'A']])
        cfg = {'chart_type':'indicator','indicator_lookup':{'column':'Año','values':{'2025':891,'2026':581}}}
        self.assertEqual(chart(frame,cfg)['datasets'][0]['data'],[891])
        self.assertEqual(chart(frame,{**cfg,'filters':{'Año':['2026']}})['datasets'][0]['data'],[581])
        self.assertEqual(chart(frame,{**cfg,'filters':{'Año':['2026'],'Organismo':['B']}})['datasets'][0]['data'],[581])
        self.assertEqual(chart(frame,{**cfg,'filters':{'Año':['2027']}})['datasets'][0]['data'],[None])
        self.assertEqual(chart(frame,{**cfg,'filters':{'Año':['2024']}})['datasets'][0]['data'],[None])
        with self.assertRaises(DataError):
            chart(frame,{**cfg,'chart_type':'bar'})

    def test_summary_dimensions_keep_empty_metrics_and_use_secondary_sort(self):
        frame, _ = table([['Año','Mes','Org','Orden','Importe','Cantidad','Privado'],
                          [2026,'Enero','A',1,10,None,'oculto'],[2026,'Enero','A',1,20,None,'oculto'],
                          [2026,'Enero','B',2,40,2,'oculto'],[2026,'Febrero','A',3,None,None,'oculto']])
        cfg = {'chart_type':'table','table_mode':'summary','x_col':'Año','y_col':'Importe','y_cols':['Cantidad'],
               'table_dimensions':['Año','Mes','Org'],'table_sorts':[{'field':'Orden','aggregation':'sum','direction':'asc'},
                                                                 {'field':'Importe','aggregation':'sum','direction':'desc'}]}
        result = chart(frame,cfg)
        self.assertEqual(result['columns'],['Año','Mes','Org','Importe','Cantidad'])
        self.assertEqual(result['records'],[
            {'Año':2026,'Mes':'Enero','Org':'B','Importe':40,'Cantidad':2},
            {'Año':2026,'Mes':'Enero','Org':'A','Importe':30,'Cantidad':None},
            {'Año':2026,'Mes':'Febrero','Org':'A','Importe':None,'Cantidad':None}])
        self.assertNotIn('oculto',str(result))
        self.assertEqual(len(chart(frame,{**cfg,'filters':{'Org':['B']}})['records']),1)
        with self.assertRaises(DataError):
            chart(frame,{**cfg,'table_dimensions':['No existe']})

    def test_conditional_sum_is_evaluated_per_group_and_after_reader_filters(self):
        frame, _ = table([['Mes','Tipo','Personas'],['Enero','GUARDIAS',10],['Enero','ASISTENCIAS',20],['Febrero','ASISTENCIAS',30]])
        cfg = {'chart_type':'bar','x_col':'Mes','y_col':'Personas','aggregation':'sum',
               'metric_condition':{'column':'Tipo','excluded_value':'ASISTENCIAS'}}
        self.assertEqual(chart(frame,cfg)['datasets'][0]['data'],[10,30])
        self.assertEqual(chart(frame,{**cfg,'chart_type':'indicator'})['datasets'][0]['data'],[10])
        self.assertEqual(chart(frame,{**cfg,'chart_type':'indicator','filters':{'Tipo':['ASISTENCIAS']}})['datasets'][0]['data'],[50])
        self.assertEqual(frame['Personas'].tolist(),[10,20,30])

    def test_other_distinct_recounts_union_instead_of_summing_categories(self):
        frame, _ = table([['Area','Nombre'],['A','Uno'],['A','Dos'],['B','Repetido'],['C','Repetido']])
        result = chart(frame, {'chart_type':'pie','x_col':'Area','y_col':'Nombre','aggregation':'distinct',
                               'category_limit':2,'include_other':True})
        self.assertEqual(result['labels'], ['A','Otros'])
        self.assertEqual(result['datasets'][0]['data'], [2,1])

    def test_millions_are_rounded_after_aggregation(self):
        frame, _ = table([['Año', 'Importe'], [2025, 900000], [2025, 900000]])
        cfg = {'chart_type': 'indicator', 'x_col': 'Año', 'y_col': 'Importe',
               'indicator_divisor': 1000000, 'indicator_rounding': 'floor'}
        self.assertEqual(chart(frame, cfg)['datasets'][0]['data'], [1])
        self.assertEqual(chart(frame, {**cfg, 'indicator_rounding': 'none'})['datasets'][0]['data'], [1.8])

    def test_conditional_counter_checks_year_before_counting_matching_rows(self):
        frame, _ = table([['Año', 'Informa'], [2026, 'MDH'], [2026, 'CF'], [2025, 'CF']])
        cfg = {'chart_type': 'indicator', 'aggregation': 'count', 'x_col': 'Año',
               'indicator_condition': {'column': 'Año', 'minimum': 2026},
               'indicator_rules': [{'column': 'Informa', 'operator': 'equal', 'value': 'MDH'}]}
        self.assertEqual(chart(frame, cfg)['datasets'][0]['data'], [None])
        self.assertEqual(chart(frame, {**cfg, 'filters': {'Año': ['2026']}})['datasets'][0]['data'], [1])
        self.assertEqual(chart(frame, {**cfg, 'filters': {'Año': ['2025']}})['datasets'][0]['data'], [None])
        self.assertEqual(chart(frame, {**cfg, 'filters': {'Año': ['2026'], 'Informa': ['CF']}})['datasets'][0]['data'], [0])
        with self.assertRaises(DataError):
            chart(frame, {**cfg, 'chart_type': 'bar'})

    def test_count_column_excludes_nulls_without_removing_empty_groups(self):
        frame, _ = table([['Grupo', 'Paciente'], ['A', 'uno'], ['A', None], ['B', None]])
        cfg = {'chart_type': 'bar', 'aggregation': 'count', 'x_col': 'Grupo', 'count_col': 'Paciente'}
        self.assertEqual(chart(frame, cfg)['datasets'][0]['data'], [1, 0])
        self.assertEqual(chart(frame, {**cfg, 'chart_type': 'indicator'})['datasets'][0]['data'], [1])
        self.assertEqual(chart(frame, {**cfg, 'count_col': ''})['datasets'][0]['data'], [2, 1])

    def test_text_formula_merges_case_variants_before_filters_and_totals(self):
        frame, meta = table([['Distrito', 'Total'], ['DISTRITO I', 2], ['Distrito I', 3], [None, 7]])
        definitions = {'Distrito normalizado': [{'column': 'Distrito', 'start': 1, 'length': 1, 'case': 'upper'},
                                               {'column': 'Distrito', 'start': 2, 'case': 'lower'}]}
        result, metadata = calculated_columns(frame, meta, definitions)
        self.assertEqual(result['Distrito normalizado'].tolist(), ['Distrito i', 'Distrito i', None])
        self.assertEqual(metadata['column_meta']['Distrito normalizado']['unique_count'], 1)
        data = chart(result, {'chart_type': 'bar', 'x_col': 'Distrito normalizado', 'y_col': 'Total',
                              'filters': {'Distrito normalizado': ['Distrito i']}})
        self.assertEqual(data['datasets'][0]['data'], [5])
        self.assertNotIn('Distrito normalizado', frame)
        with self.assertRaises(DataError):
            calculated_columns(frame, meta, {'Distrito': [{'literal': 'no sobrescribir'}]})
        with self.assertRaises(DataError):
            calculated_columns(frame, meta, {'Nueva': [{'column': 'No existe'}]})

    def test_series_limit_uses_explicit_sort_without_changing_counter(self):
        frame, _ = table([['Mes', 'Año', 'Total'], ['Enero', 2023, 1], ['Enero', 2024, 2], ['Enero', 2025, 3]])
        cfg = {'chart_type': 'bar', 'x_col': 'Mes', 'y_col': 'Total', 'group_col': 'Año', 'series_limit': 2,
               'series_sort': {'field': 'Año', 'aggregation': 'max', 'direction': 'desc'}}
        result = chart(frame, cfg)
        self.assertEqual([d['data'] for d in result['datasets']], [[3], [2]])
        self.assertEqual(chart(frame, {'chart_type': 'indicator', 'x_col': 'Mes', 'y_col': 'Total'})['datasets'][0]['data'], [6])

    def test_top_categories_preserve_total_and_order(self):
        frame, _ = table([['Hospital', 'Cantidad'], ['A', 10], ['B', 3], ['C', 7], ['D', 2]])
        cfg = {'x_col': 'Hospital', 'y_col': 'Cantidad', 'chart_type': 'pie', 'aggregation': 'sum',
               'category_sort': {'field': 'Cantidad', 'aggregation': 'sum', 'direction': 'desc'},
               'category_limit': 3, 'include_other': True}
        result = chart(frame, cfg)
        self.assertEqual(result['labels'], ['A', 'C', 'Otros'])
        self.assertEqual(result['datasets'][0]['data'], [10, 7, 5])
        self.assertEqual(sum(result['datasets'][0]['data']), 22)
        with self.assertRaises(DataError):
            chart(frame, {**cfg, 'aggregation': 'mean'})

    def test_summary_table_aggregates_without_exposing_detail(self):
        frame, _ = table([['Origen', 'Destino', 'Documento'], ['Interior', 'Capital', 'privado-1'],
                          ['Interior', 'Capital', 'privado-2'], ['Capital', 'Capital', 'privado-3']])
        cfg = {'chart_type': 'table', 'table_mode': 'summary', 'x_col': 'Origen', 'group_col': 'Destino', 'aggregation': 'count'}
        result = chart(frame, cfg)
        self.assertEqual(result['columns'], ['Origen', 'Destino', 'Registros'])
        self.assertEqual(result['records'], [{'Origen': 'Capital', 'Destino': 'Capital', 'Registros': 1},
                                             {'Origen': 'Interior', 'Destino': 'Capital', 'Registros': 2}])
        self.assertNotIn('privado', str(result))

    def test_summary_multiple_metrics_and_missing_values(self):
        frame, _ = table([['Año', 'Gasto', 'Cantidad'], [2025, 10, 2], [2025, None, 3], [2024, 5, 1]])
        result = chart(frame, {'chart_type': 'table', 'table_mode': 'summary', 'x_col': 'Año', 'y_col': 'Gasto', 'y_cols': ['Cantidad'], 'aggregation': 'sum'})
        self.assertEqual(result['records'], [{'Año': 2024, 'Gasto': 5, 'Cantidad': 1}, {'Año': 2025, 'Gasto': 10, 'Cantidad': 5}])

    def test_chart_restrictions_precede_facet_options_and_never_expand(self):
        frame, _ = table([['Indicador', 'Valor', 'Año'], ['NACIDOS VIVOS', 8, 2025], ['TASA MORTALIDAD', 6.4, 2025], ['NACIDOS VIVOS', 0, 2024]])
        cfg = {'x_col': 'Año', 'y_col': 'Valor', 'filter_rules': [
            {'column': 'Indicador', 'operator': 'not_contains', 'value': 'TASA'},
            {'column': 'Valor', 'operator': 'not_equal', 'value': '0'}]}
        restricted = restrict_frame(frame, cfg)
        self.assertEqual(contextual_filter_options(restricted, ['Año'], {})['Año']['values'], ['2025'])
        self.assertEqual(chart(frame, cfg)['datasets'][0]['data'], [8])
        cfg['filters'] = {'Año': ['2024']}
        self.assertEqual(chart(frame, cfg)['filtered_rows'], 0)
        cfg['filter_rules'] = [{'column': 'Año', 'operator': 'in', 'values': []}]
        self.assertEqual(chart(frame, cfg)['filtered_rows'], 0)


class CategoryTieTests(unittest.TestCase):
    def test_original_category_order_only_resolves_ties_and_recalculates_others(self):
        from app.data_engine import chart
        import pandas as pd
        frame=pd.DataFrame({'Causa':['A','B','C','D'], 'Distrito':[12,12,12,13], 'Total':[10,20,30,40]})
        cfg={'chart_type':'pie','x_col':'Causa','y_col':'Total','aggregation':'sum',
             'category_sort':{'field':'Distrito','aggregation':'max','direction':'desc','tie_order':['C','B','A']},
             'category_limit':3,'include_other':True}
        result=chart(frame,cfg)
        self.assertEqual(result['labels'],['D','C','Otros'])
        self.assertEqual(result['datasets'][0]['data'],[40,30,30])
        filtered=chart(frame,{**cfg,'filters':{'Causa':['A','B','C']}})
        self.assertEqual(filtered['labels'],['C','B','A'])
        self.assertEqual(filtered['datasets'][0]['data'],[30,20,10])


class CompleteSummaryTests(unittest.TestCase):
    def test_summary_retains_every_aggregated_category_beyond_500(self):
        from app.data_engine import chart
        import pandas as pd
        frame=pd.DataFrame({'Diagnóstico':[f'Causa {i}' for i in range(1046)],'Privado':['no publicar']*1046})
        result=chart(frame,{'chart_type':'table','table_mode':'summary','x_col':'Diagnóstico','aggregation':'count'})
        self.assertEqual(len(result['records']),1046)
        self.assertEqual(sum(r['Registros'] for r in result['records']),1046)
        self.assertEqual(result['columns'],['Diagnóstico','Registros'])
        self.assertNotIn('Privado',str(result))
