"""Targeted existing-date EnergyPlus instrumentation; physical IDF text unchanged."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3

import numpy as np
import pandas as pd

from tools.model_correctness_v23.thermal_data import ZONES, read_native, run_case, sha256, sql_clocks

NODES = ['VAV_OR_OAInlet Node', 'VAV_OR Supply Equipment Inlet Node',
         'VAV_OR_OA-VAV_OR_CoolCNode', 'VAV_OR_CoolC-VAV_OR_HeatCNode',
         'OR1_Flr_2 VAV Box Inlet Node', 'OR1_Flr_2 VAV Box Outlet Node',
         'OR1_Flr_2 Air Node']
CHANNELS = [(node, variable) for node in NODES for variable in
            ['System Node Temperature', 'System Node Mass Flow Rate', 'System Node Humidity Ratio']]
CHANNELS += [(f'{zone} VAV Box Reheat Coil', 'Heating Coil Heating Rate')
             for zone in ['OR1_Flr_2', 'OR2_Mult5_Flr_2', 'OR3_Flr_2', 'OR4_Flr_2']]
CHANNELS += [('VAV_OR Heating Coil', 'Heating Coil Heating Rate'), ('VAV_OR Fan', 'Fan Electricity Rate'),
             ('VAV_OR', 'Air System Outdoor Air Economizer Status'),
             ('VAV_OR', 'Air System Outdoor Air Mass Flow Rate')]


def diagnostic_idf(source_text):
    return source_text + '\n' + '\n'.join(f'Output:Variable,{key},{variable},Timestep;' for key, variable in CHANNELS) + '\n'


def sizing(path):
    result = {}
    for line in path.read_text(errors='replace').splitlines():
        if line.lstrip().startswith('Component Sizing Information,'):
            fields = [f.strip() for f in line.split(',')]
            result[' | '.join(fields[1:-1])] = fields[-1]
    return result


def extract_nodes(path):
    values = {}
    with sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True) as connection:
        for key, variable in CHANNELS:
            rows = connection.execute(
                'SELECT t.Month,t.Day,t.Hour,t.Minute,t.Dst,x.VariableValue,d.VariableUnits '
                'FROM ReportVariableData x JOIN ReportVariableDataDictionary d USING(ReportVariableDataDictionaryIndex) '
                'JOIN Time t USING(TimeIndex) JOIN EnvironmentPeriods e USING(EnvironmentPeriodIndex) '
                'WHERE e.EnvironmentType=3 AND upper(d.KeyValue)=upper(?) AND d.VariableName=? '
                "AND d.ReportingFrequency='Zone Timestep' ORDER BY t.TimeIndex", (key, variable)).fetchall()
            if not rows:
                raise ValueError(f'Missing actual native node output: {key}/{variable}')
            values[f'{key} | {variable} [{rows[0][-1]}]'] = pd.Series(
                [r[5] for r in rows], index=pd.DatetimeIndex([sql_clocks(*r[:5])[1] for r in rows]))
    result = pd.DataFrame(values)
    if result.isna().any().any():
        raise ValueError('Targeted node clocks are incomplete')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--executable', type=Path, required=True)
    parser.add_argument('--weather', type=Path, required=True)
    args = parser.parse_args()
    if args.source.resolve() == args.destination.resolve():
        raise ValueError('Source evidence is read-only')
    args.destination.mkdir(parents=True, exist_ok=True)
    receipts = []
    for season in ['summer', 'winter']:
        for hours in [0, 6]:
            original = args.source/f'native10_cal_{season}_{hours}h.idf'
            identifier = f'nodes_cal_{season}_{hours}h'
            target = args.destination/(identifier+'.idf')
            run = args.destination/identifier
            if run.exists():
                raise ValueError('Do not overwrite an EnergyPlus attempt')
            original_sha = sha256(original)
            target.write_text(diagnostic_idf(original.read_text(encoding='utf-8')), encoding='utf-8')
            receipt = run_case(args.executable, args.weather, target, run, args.destination/'temp')
            old_sizing, new_sizing = sizing(args.source/f'native10_cal_{season}_{hours}h/eplusout.eio'), sizing(run/'eplusout.eio')
            if old_sizing != new_sizing:
                raise RuntimeError('Appending targeted outputs changed physical equipment sizing')
            actual = read_native(run/'eplusout.sql', interval_minutes=10)
            maximum = 0.
            for zone in ZONES:
                archived = pd.read_csv(args.source/f'native10_cal_{season}_{hours}h_{zone}.csv', index_col=0, parse_dates=True)
                if not actual[zone].index.equals(archived.index):
                    raise RuntimeError('Targeted outputs changed the archived native clock')
                maximum = max(maximum, float(np.abs(actual[zone].select_dtypes('number')-archived.select_dtypes('number')).to_numpy().max()))
            if maximum > 1e-9 or sha256(original) != original_sha:
                raise RuntimeError('Targeted instrumentation changed an original thermal trajectory or source')
            frame = extract_nodes(run/'eplusout.sql')
            frame.to_csv(args.destination/(identifier+'.csv'), index_label='timestamp')
            receipt.update(case_id=identifier, source_idf_sha256=original_sha,
                           source_idf_preserved=True, physical_text_unchanged=True,
                           identical_sizing_fields=len(new_sizing),
                           original_all_zone_thermal_max_abs_difference=maximum,
                           requested_diagnostic_channels=CHANNELS)
            receipts.append(receipt)
            (args.destination/'NODE_RUNS.json').write_text(json.dumps(receipts, indent=2), encoding='utf-8')
            print(json.dumps(receipt), flush=True)


if __name__ == '__main__':
    main()
