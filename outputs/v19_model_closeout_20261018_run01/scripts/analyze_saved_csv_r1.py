"""External CSV reader capacity adapter for the unchanged frozen analysis CLI.

No field truncation, model/metric/threshold change, simulation or raw read.
"""
import csv
import hashlib
import io
from pathlib import Path
import sys
import unittest

SCRIPT=Path(__file__).resolve();B=SCRIPT.parents[1];ROOT=B.parents[1]
FIELD_LIMIT=100_000_000
def configure_csv_limit():return csv.field_size_limit(FIELD_LIMIT)

class CsvCapacityTests(unittest.TestCase):
    def test_long_field_is_preserved_byte_for_byte(self):
        value='a'*200_000;stream=io.StringIO();csv.writer(stream).writerow([value,'tail'])
        csv.field_size_limit(131072)
        with self.assertRaises(csv.Error):next(csv.reader(io.StringIO(stream.getvalue())))
        configure_csv_limit()
        self.assertEqual(next(csv.reader(io.StringIO(stream.getvalue()))),[value,'tail'])
    def test_actual_saved_producer_csv_reads_without_modification(self):
        p=B/'experiments/dev_contact_r1_000/analysis/EVENT_TIMELINE.csv'
        before=hashlib.sha256(p.read_bytes()).hexdigest();configure_csv_limit();count=0;largest=0
        with p.open(encoding='utf-8-sig',newline='') as f:
            for row in csv.DictReader(f):
                count+=1;largest=max(largest,max(len(v or '') for v in row.values()))
        self.assertGreater(count,0);self.assertGreater(largest,131072)
        self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(),before)
        print('actual_saved_CSV_rows',count,'largest_field_characters',largest,'input_SHA',before)

def main():
    if sys.argv[1:]==['self-test']:
        result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(CsvCapacityTests))
        return 0 if result.wasSuccessful() else 1
    configure_csv_limit();sys.path[:0]=[str(ROOT),str(ROOT/'src')]
    from tools.model_closeout.analysis import main as frozen_main
    return frozen_main(sys.argv[1:])
if __name__=='__main__':raise SystemExit(main())
