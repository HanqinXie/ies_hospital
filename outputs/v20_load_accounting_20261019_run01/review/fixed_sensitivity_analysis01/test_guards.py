"""Small synthetic checks for this external read-only analysis, not model tests."""
from pathlib import Path
import copy
import importlib.util
import unittest

HERE = Path(__file__).resolve().parent

class Guards(unittest.TestCase):
    def setUp(self):
        path=HERE/'guards.py'
        self.assertTrue(path.is_file(), 'External evidence guards not implemented yet')
        spec=importlib.util.spec_from_file_location('fixed_sensitivity_guards',path)
        self.g=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.g)
        self.scene=dict(id='case',enable_propagation=True,start_hour=1,exogenous_events=[dict(start_minute=1,end_minute=3)],propagation_pair_id='pair',shock={'e':.15})
        self.facts={k:'a'*64 for k in ('actual_initial_state_sha256','actual_exogenous_sha256','source_binding_sha256','model_config_sha256','runtime_inputs_sha256')}
        self.cfg={'execution_recovery':{'grid_open_transfer_minutes':.25,'local_contact_restore_delay_minutes':0.}}

    def test_missing_is_not_zero(self):
        with self.assertRaises(ValueError):self.g.finite(None,'missing')

    def test_nonfinite_rejected(self):
        with self.assertRaises(ValueError):self.g.finite(float('nan'),'nan')

    def test_bool_rejected(self):
        with self.assertRaises(ValueError):self.g.finite(True,'bool')

    def test_zero_is_observed(self):self.assertEqual(self.g.finite('0','observed'),0.)

    def test_duplicate_rows_rejected(self):
        with self.assertRaises(ValueError):self.g.unique_index([{'id':'a'},{'id':'a'}],lambda r:r['id'],{'a'},'duplicate')

    def test_unknown_row_rejected(self):
        with self.assertRaises(ValueError):self.g.unique_index([{'id':'b'}],lambda r:r['id'],{'a'},'unknown')

    def clock(self,**changes):
        cfg=copy.deepcopy(self.cfg);cfg['execution_recovery']['grid_open_transfer_minutes']=1.
        kw=dict(left_scene=self.scene,right_scene=copy.deepcopy(self.scene),left_config=self.cfg,right_config=cfg,variant='open1',left_facts=self.facts,right_facts=copy.deepcopy(self.facts),left_policies=[{'id':'E'}],right_policies=[{'id':'E'}],left_injection='b'*64,right_injection='b'*64)
        kw.update(changes);return self.g.validate_clock_pair(**kw)

    def test_exact_open_clock_allowed(self):self.assertTrue(self.clock()['validated'])

    def test_wrong_clock_rejected(self):
        cfg=copy.deepcopy(self.cfg);cfg['execution_recovery']['grid_open_transfer_minutes']=2.
        with self.assertRaises(ValueError):self.clock(right_config=cfg)

    def test_extra_config_change_rejected(self):
        cfg=copy.deepcopy(self.cfg);cfg['execution_recovery'].update(grid_open_transfer_minutes=1.,local_contact_restore_delay_minutes=.25)
        with self.assertRaises(ValueError):self.clock(right_config=cfg)

    def test_changed_scene_rejected(self):
        s=copy.deepcopy(self.scene);s['shock']={'e':.6}
        with self.assertRaises(ValueError):self.clock(right_scene=s)

    def test_changed_actual_reset_rejected(self):
        f=copy.deepcopy(self.facts);f['actual_initial_state_sha256']='c'*64
        with self.assertRaises(ValueError):self.clock(right_facts=f)

    def test_changed_injection_rejected(self):
        with self.assertRaises(ValueError):self.clock(right_injection='c'*64)

    def test_changed_policy_rejected(self):
        with self.assertRaises(ValueError):self.clock(right_policies=[{'id':'K1'}])

    def test_propagation_flag_is_intended_stream_difference(self):
        off=copy.deepcopy(self.scene);off.update(id='off',enable_propagation=False)
        facts=copy.deepcopy(self.facts);facts['actual_exogenous_sha256']='b'*64
        self.assertTrue(self.g.validate_propagation_pair(self.scene,off,self.facts,facts)['validated'])

    def test_propagation_changed_shock_rejected(self):
        off=copy.deepcopy(self.scene);off.update(id='off',enable_propagation=False,shock={'e':.6})
        facts=copy.deepcopy(self.facts);facts['actual_exogenous_sha256']='b'*64
        with self.assertRaises(ValueError):self.g.validate_propagation_pair(self.scene,off,self.facts,facts)

    def test_half_open_events_and_dedup(self):
        e=dict(event_id='trip',kind='protection_trip',time_minute=1,cause_class='residual_stress_protection')
        last=dict(e,event_id='endpoint',time_minute=600)
        v=self.g.event_counts([e,e,last],[0,600])
        self.assertEqual(v['residual_stress_protection'],1)

    def test_class_label_is_not_trip(self):
        v=self.g.event_counts([dict(event_id='label',kind='L2',time_minute=1)],[0,600])
        self.assertEqual(v['actual_protection_trip'],0)

    def test_missing_event_evidence_is_unknown(self):
        self.assertIsNone(self.g.event_counts(None,[0,600])['actual_protection_trip'])

    def test_unknown_trip_cause_not_residual(self):
        v=self.g.event_counts([dict(event_id='trip',kind='protection_trip',time_minute=1)],[0,600])
        self.assertEqual(v['unknown_protection_cause'],1);self.assertEqual(v['residual_stress_protection'],0)

    def test_actual_no_export_kind_counted_separately(self):
        v=self.g.event_counts([dict(event_id='isolate',kind='no_export_interlock',time_minute=1,cause_class='no_export_isolation')],[0,600])
        self.assertEqual(v['no_export_isolation'],1);self.assertEqual(v['actual_protection_trip'],0)

    def test_actual_minimum_output_kind_counted_separately(self):
        v=self.g.event_counts([dict(event_id='minimum',kind='minimum_output_interlock',time_minute=1,cause_class='minimum_output_interlock')],[0,600])
        self.assertEqual(v['minimum_output_interlock'],1);self.assertEqual(v['actual_protection_trip'],0)

    def test_runs_union_not_carrier_sum(self):
        v=self.g.merge_runs([dict(start_minute=1,end_minute=3),dict(start_minute=2,end_minute=4)],[0,600])
        self.assertEqual(v,[[1.,4.]])

    def test_outside_window_rejected(self):
        with self.assertRaises(ValueError):self.g.merge_runs([dict(start_minute=-1,end_minute=3)],[0,600])

if __name__=='__main__':unittest.main(verbosity=2)
