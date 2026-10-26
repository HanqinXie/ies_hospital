"""V26 study registration, executed by the common M25 physical entry."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT/'src')]
from tools.model_correctness_v23.validation import main as common_main


def main(argv=None):
    return common_main(argv, registry_path=ROOT/'configs/model_correction_v26/cases.json',
        model_version='v25', worker_script=Path(__file__).resolve())


if __name__ == '__main__':
    raise SystemExit(main())
