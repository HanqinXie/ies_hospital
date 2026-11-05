"""Run from any directory: python run.py --case doe_capacity_matched_exploratory."""
from __future__ import annotations
import argparse
from pathlib import Path
import sys
import json
sys.path.insert(0,str(Path(__file__).resolve().parent/'src'))
from hospital_ies.pipeline import run

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description='DOE+NMCSD normal-operation energy hub dispatch')
    parser.add_argument('--case',choices=['doe_capacity_matched_exploratory','original_historical_capacity'],default='doe_capacity_matched_exploratory')
    parser.add_argument('--hours',type=int,default=None,help='Quick test: first N hours (default complete year)')
    parser.add_argument('--no-figures',action='store_true')
    parser.add_argument('--scenario-tag',type=str,default=None,help='Safe output suffix for a named scenario')
    parser.add_argument('--fan-ratio',type=float,default=1.0,
                        help='1.0=use DOE fans as already counted; 0.5=fan electricity half, illustrative ONLY')
    oxygen=parser.add_mutually_exclusive_group()
    oxygen.add_argument('--oxygen-flow-nm3-h',type=float,default=None,
                        help='Explicit example PSA production in Nm3/h, adds electricity to DOE baseline')
    oxygen.add_argument('--oxygen-profile-csv',type=Path,default=None,
                        help='CSV with hour_of_year,oxygen_production_Nm3_h (8760 rows)')
    parser.add_argument('--oxygen-specific-kwh-nm3',type=float,default=1.04,
                        help='Specific PSA consumption, default 1.04 from CEJ 2025 Table 7 example')
    parser.add_argument('--thermal-demo',action='store_true',
                        help='Output critical-zone illustrative 1R1C cooling-failure response; NOT a coupled optimization')
    parser.add_argument('--event-hour',type=int,default=24,help='1-based start hour of interruption')
    parser.add_argument('--event-duration',type=int,default=4,help='Duration of interrupted cooling in hours')
    parser.add_argument('--cooling-loss-fraction',type=float,default=1.0)
    parser.add_argument('--mass-participation',type=float,default=0.5,
                        help='Assumed fraction of IDF internal mass contributing to effective 1R1C capacity')
    parser.add_argument('--effective-U',type=float,default=1.5,
                        help='Assumed effective heat-loss coefficient, W/(m2 K) floor area')
    args=parser.parse_args()
    result=run(Path(__file__).resolve().parent,args.case,args.hours,not args.no_figures,
               fan_ratio=args.fan_ratio,oxygen_flow_Nm3_h=args.oxygen_flow_nm3_h,
               oxygen_profile_csv=args.oxygen_profile_csv,oxygen_specific_kWh_Nm3=args.oxygen_specific_kwh_nm3,
               thermal_demo=args.thermal_demo,event_hour=args.event_hour,
               event_duration=args.event_duration,cooling_loss_fraction=args.cooling_loss_fraction,
               mass_participation=args.mass_participation,effective_U_W_m2K=args.effective_U,
               scenario_tag=args.scenario_tag)
    print(json.dumps({'case':result['case'],'hours':result['hours'],
                      'load_met_all_hours':result['load_met_all_hours'],
                      'annual_cost_USD_h_sum':result['annual_integrals']['operating_cost_USD_h']},indent=2))
