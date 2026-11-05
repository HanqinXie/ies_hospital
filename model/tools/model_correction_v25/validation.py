"""Explicit M25 entry and registered control options, reusing the common runner."""
from pathlib import Path
import copy
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from tools.model_correctness_v23.validation import main as common_main


def resolve_operating_rule(registry,case):
    key=case.get('operating_rule_id',registry.get('default_operating_rule'))
    rules=registry.get('operating_rules',{})
    if not isinstance(key,str) or key not in rules:
        raise ValueError('A registered V25 operating rule is required')
    if 'operating_rule' in case:
        raise ValueError('Use registered operating_rule_id, not an arbitrary rule override')
    return key,copy.deepcopy(rules[key])


def main(argv=None):
    return common_main(argv,registry_path=ROOT/'configs/model_correction_v25/cases.json',
        model_version='v25',worker_script=Path(__file__).resolve())


if __name__=='__main__':raise SystemExit(main())
