"""Portable source/mapping arithmetic replay; no EP, optimizer or old drive reads."""
from pathlib import Path
import argparse,hashlib,json

import numpy as np
import pandas as pd

from tools.model_review_v27.load_source import apply_conditional_deltas


def replay(project,evidence,output):
    annual=pd.read_csv(project/'inputs/loads/hospital_ies_demand_8760.csv')
    zones=pd.read_csv(project/'inputs/loads/critical_zones_8760.csv')
    coils=pd.read_csv(evidence/'ANNUAL_EIGHT_COILS_HOURLY_kW.csv',index_col=0)
    if len(coils)!=8760 or coils.shape[1]!=8 or not np.isfinite(coils.to_numpy(float)).all():
        raise ValueError('Complete original eight-coil annual native extraction required')
    mismatch=float(np.max(abs(coils.sum(axis=1).to_numpy()-annual.Q_c_demand_kW.to_numpy())))
    if mismatch>1e-9:raise ValueError('Original eight-coil source does not reproduce annual cooling')
    finite=evidence/'finite_normal01';series={}
    for name in ['original','min8']:
        c=pd.read_csv(finite/f'{name}_EIGHT_COILS_kW.csv',index_col=0,parse_dates=True)
        z=pd.read_csv(finite/f'{name}_FOURTEEN_REPRESENTATIVE_ZONES_kW.csv',index_col=0,parse_dates=True)
        if not c.index.equals(z.index) or len(c)!=120:raise ValueError('Native two-normal clocks differ')
        series[name]=pd.concat([c.sum(axis=1).rename('total'),z],axis=1)
    delta=series['min8']-series['original']
    saved=pd.read_csv(finite/'SIGNED_CONDITIONAL_DELTAS_120H.csv',index_col=0,parse_dates=True)
    if not saved.index.equals(delta.index) or not saved.columns.equals(delta.columns) or not np.allclose(saved,delta,atol=1e-9,rtol=1e-12):
        raise ValueError('Saved signed finite difference does not reproduce native two-normal records')
    _,_,mapped=apply_conditional_deltas(annual,zones,delta)
    expected=pd.read_csv(finite/'PRIORITY_CONDITIONAL_MAPPING_120H.csv')
    numeric=mapped.select_dtypes(include='number').columns
    maximum=float(np.max(abs(mapped[numeric].to_numpy()-expected[numeric].to_numpy())))
    if maximum>1e-9:raise ValueError('Conditional priority mapping mismatch')
    output.mkdir(parents=True,exist_ok=False)
    mapped.to_csv(output/'REPLAYED_CONDITIONAL_MAPPING_120H.csv',index=False)
    result=dict(status='source arithmetic and finite conditional mapping replay pass',annual_hours=8760,finite_hours=120,
        original_cooling_max_abs_difference_kW=mismatch,mapping_max_abs_difference_kW=maximum,
        interpretation='Conditional numerical-setting perturbation; source arithmetic replay is not annual physical calibration.',
        annual_source_recovered=True,dynamic_water_qualified=False,temperature_qualified=False,
        new_EnergyPlus_runs=0,new_optimizer_calls=0,new_IES_runs=0,old_absolute_paths_opened=False,
        load_source_py_sha256=hashlib.sha256((Path(__file__).parent/'load_source.py').read_bytes()).hexdigest())
    (output/'REPLAY_RECEIPT.json').write_text(json.dumps(result,indent=2),encoding='utf8');print(json.dumps(result))
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--project',type=Path,required=True);p.add_argument('--evidence',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();replay(a.project,a.evidence,a.output)


if __name__=='__main__':main()
