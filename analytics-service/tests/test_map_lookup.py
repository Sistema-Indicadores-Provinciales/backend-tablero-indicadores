import unittest

import pandas as pd
from pydantic import ValidationError

from app.data_engine import chart, DataError
from app.generator import ChartConfig


class VerifiedMapTests(unittest.TestCase):
    def setUp(self):
        self.frame = pd.DataFrame({
            'Localidad':['A','A','B','Pendiente',None],
            'Tipo':['Hospital','Hospital','Centro','Centro','Centro'],
            'Mes':['Enero','Febrero','Enero','Enero','Enero'],
            'Casos':[2.,3.,0.,7.,None],
        })
        self.config = {'sheet':'Datos','chart_type':'map','aggregation':'sum','y_col':'Casos',
            'map_lookup':{'column':'Localidad','locations':{
                'A':{'lat':-26.,'lng':-58.,'name':'A oficial','reference':'GeoRef A'},
                'B':{'lat':-25.,'lng':-59.,'name':'B oficial','reference':'GeoRef B'},
            }}}

    def test_all_values_survive_including_unlocated_zero_and_null(self):
        out = chart(self.frame, self.config)
        self.assertEqual({p['label']:p['value'] for p in out['map_points']}, {'A':5.,'B':0.})
        self.assertEqual(out['filtered_rows'],5)
        self.assertEqual({r['Localidad']:r['Casos'] for r in out['map_unlocated']}, {'Pendiente':7.,None:None})
        self.assertEqual(sum(r['Casos'] or 0 for r in out['records']),12)
        self.assertEqual(len(out['warnings']),1)

    def test_filters_and_chart_specific_rules_apply_before_aggregation(self):
        cfg={**self.config,'filters':{'Mes':['Enero']},'filter_rules':[{'column':'Casos','operator':'not_equal','value':'0'}]}
        out=chart(self.frame,cfg)
        self.assertEqual([(p['label'],p['value']) for p in out['map_points']],[('A',2.)])
        self.assertEqual(sum(r['Casos'] or 0 for r in out['records']),9)

    def test_category_colors_do_not_turn_record_counts_into_size_metrics(self):
        cfg={**self.config,'aggregation':'count','y_col':'','map_lookup':{**self.config['map_lookup'],'category_col':'Tipo','sized':False}}
        out=chart(self.frame,cfg)
        a=next(p for p in out['map_points'] if p['label']=='A')
        self.assertIsNone(a['value'])
        self.assertEqual(a['category'],'Hospital')
        self.assertEqual(a['metrics'],[{'field':'Registros','value':2}])
        self.assertEqual(sum(r['Registros'] for r in out['records']),len(self.frame))

    def test_same_coordinates_do_not_merge_different_source_categories(self):
        cfg={**self.config,'map_lookup':{**self.config['map_lookup'],'locations':{
            k:self.config['map_lookup']['locations']['A'] for k in ['A','B']}}}
        out=chart(self.frame,cfg)
        self.assertEqual(len(out['map_points']),2)
        self.assertNotEqual(out['map_points'][0]['id'],out['map_points'][1]['id'])

    def test_does_not_guess_a_similar_name(self):
        f=pd.DataFrame({'Localidad':['a','A '],'Casos':[1,2]})
        out=chart(f,self.config)
        self.assertEqual(out['map_points'],[])
        self.assertEqual(len(out['map_unlocated']),2)

    def test_coordinate_catalogue_is_validated_on_save(self):
        for lat in [float('nan'),float('inf'),91]:
            cfg={**self.config,'map_lookup':{**self.config['map_lookup'],'locations':{'A':{'lat':lat,'lng':0,'name':'A','reference':'test'}}}}
            with self.assertRaises(ValidationError):ChartConfig.model_validate(cfg)
        validated=ChartConfig.model_validate(self.config).model_dump()
        self.assertEqual(chart(self.frame,validated)['map_points'][0]['value'],5.)

    def test_invalid_measure_does_not_silently_become_zero(self):
        with self.assertRaises(DataError):chart(pd.DataFrame({'Localidad':['A'],'Casos':['texto']}),self.config)

    def test_reference_note_survives_validation_and_filters(self):
        self.config['map_lookup']['locations']['A']['note']='Centro de salud usado como referencia del área.'
        cfg=ChartConfig.model_validate({**self.config,'filters':{'Mes':['Enero']}}).model_dump()
        point=next(p for p in chart(self.frame,cfg)['map_points'] if p['label']=='A')
        self.assertEqual(point['location_note'],'Centro de salud usado como referencia del área.')
        self.assertEqual(point['value'],2.)

    def test_empty_selection_returns_no_points(self):
        out=chart(self.frame,{**self.config,'filters':{'Mes':['Marzo']}})
        self.assertEqual(out['map_points'],[])
        self.assertEqual(out['records'],[])
        self.assertEqual(out['filtered_rows'],0)


if __name__=='__main__':unittest.main()
