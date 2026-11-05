"""Explicit M24 registry/identity, reusing the failure-preserving common runner."""
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from tools.model_correctness_v23.validation import main as common_main

def main(argv=None):
    return common_main(argv,registry_path=ROOT/'configs/engineering_completion_v24/cases.json',
        model_version='v24',worker_script=Path(__file__).resolve())

if __name__=='__main__':
    raise SystemExit(main())
