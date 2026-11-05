"""Extract compact V21 auxiliary inputs from explicit read-only local sources."""
from __future__ import annotations
import argparse
import csv
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]
from tools.sci_engineering.auxiliary import (ALLOCATION_FILE, METADATA_FILE,
    compute_auxiliary_allocation, sha256)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--critical-share-scale', type=float, default=1.)
    args = parser.parse_args()
    if args.destination.exists() and any(args.destination.iterdir()):
        raise ValueError('Refuse to overwrite prior source evidence destination')
    data = compute_auxiliary_allocation(args.root, args.source,
        critical_share_scale=args.critical_share_scale)
    args.destination.mkdir(parents=True, exist_ok=True)
    path = args.destination / ALLOCATION_FILE
    data.to_csv(path, index=False, float_format='%.15g')
    metadata = dict(data.attrs['metadata'], allocation_sha256=sha256(path))
    (args.destination / METADATA_FILE).write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding='utf-8')
    with (args.destination / 'IDF_SERVICE_MAPPING.csv').open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=['airloop', 'fan', 'zone', 'priority', 'terminal', 'design_max_airflow_m3_s'])
        writer.writeheader()
        for item in metadata['fan_mapping']:
            for zone in item['zones']:
                writer.writerow(dict(airloop=item['airloop'], fan=item['fan'], **zone))
    with (args.destination / 'AUXILIARY_PARAMETER_AND_SOURCE_REGISTER.csv').open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=['parameter','value_or_range','unit','source','position','status','scope','sensitivity'])
        writer.writeheader()
        writer.writerows([
            dict(parameter='fan_priority_ownership',value_or_range='connected terminal maximum design air flow share',unit='fraction',source='Original IDF and annual SQL ComponentSizes',position='AirLoopHVAC -> SupplyPath -> ZoneSplitter -> terminal -> ADU -> zone',status='measured baseline fan W; design-flow allocation assumption',scope='all configurations and policies',sensitivity='critical shares x0.75 where conserving existing fan P3 remainder'),
            dict(parameter='CHW_secondary_distribution',value_or_range=metadata['chw_pump_annual_kWh'],unit='kWh/year',source='SQL Pump Electricity Rate/COOLSYS1 PUMP SECONDARY',position='8760 annual hourly W/1000; originally P_e_unresolved',status='existing electricity reassignment; current cooling-share allocation assumption',scope='all configurations and policies',sensitivity='allocation shares; total retained'),
            dict(parameter='HeatSys1_circulation_addition',value_or_range=metadata['added_heat_pump_annual_kWh'],unit='kWh/year',source='SQL Pump Electricity Rate/HEATSYS1 PUMP',position='8760 annual hourly W/1000; originally excluded P_e_plant',status='explicit added electricity; transfer to NMCSD heat path is research assumption',scope='all configurations and policies',sensitivity='common multiplier .5/1/1.5 if selected'),
            dict(parameter='existing_EC_all_in_electricity',value_or_range='.77',unit='kW/RT',source='Local Brown/Dirks 8-page PNNL author-layout full text',position='PDF page 3 hospital electric load reconstruction',status='source assumption for existing plant; includes condenser water pumps and tower fans; CHW left in hospital electricity',scope='existing EC',sensitivity='do not substitute appendix .54+.09'),
            dict(parameter='existing_ABS_auxiliary_electricity',value_or_range='.14',unit='kW/RT',source='Local Brown/Dirks 8-page PNNL author-layout full text',position='PDF page 3; proposed-equipment appendix separately at page 8',status='source assumption for existing plant; includes condenser water pumps and tower fans',scope='existing ABS',sensitivity='no extra same-purpose tower/condenser electricity'),
        ])
    print(json.dumps(dict(status='complete',destination=str(args.destination.resolve()),
        hours=len(data),allocation_sha256=metadata['allocation_sha256'],
        fan_annual_kWh=metadata['fan_annual_kWh'],chw_pump_annual_kWh=metadata['chw_pump_annual_kWh'],
        added_heat_pump_annual_kWh=metadata['added_heat_pump_annual_kWh'])))


if __name__ == '__main__':
    main()
