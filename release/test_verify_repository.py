"""Synthetic publication guards; no hospital environment or solver."""
import importlib.util,json,tempfile,unittest
from pathlib import Path
HERE=Path(__file__).parent
def module():
    spec=importlib.util.spec_from_file_location('publication_verify',HERE/'verify_repository.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
class Guards(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory(dir=HERE);self.root=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def test_frozen_bytes_mutation_is_rejected(self):
        m=module();p=self.root/'x.py';p.write_bytes(b'frozen');mapping={'x.py':m.sha(p)}
        m.verify_files(self.root,mapping);p.write_bytes(b'changed')
        with self.assertRaises(ValueError):m.verify_files(self.root,mapping)
    def test_missing_frozen_member_is_rejected(self):
        with self.assertRaises((ValueError,FileNotFoundError)):module().verify_files(self.root,{'missing.py':'0'*64})
    def test_paths_cannot_escape_repository(self):
        for name in ('../outside','/rooted','D:/drive','x\\wrong'):
            with self.subTest(name=name),self.assertRaises(ValueError):module().safe_member(self.root,name)
    def test_extra_model_source_changes_binding(self):
        m=module();p=self.root/'tools/a.py';p.parent.mkdir();p.write_bytes(b'first')
        before=m.source_binding(self.root);(p.parent/'b.py').write_bytes(b'new')
        self.assertNotEqual(before,m.source_binding(self.root))
    def test_release_scripts_do_not_change_model_binding(self):
        m=module();p=self.root/'run.py';p.write_bytes(b'frozen');before=m.source_binding(self.root)
        helper=self.root/'release/helper.py';helper.parent.mkdir();helper.write_bytes(b'readonly')
        self.assertEqual(before,m.source_binding(self.root))
    def test_catalog_missing_or_changed_results_rejected(self):
        m=module();r=self.root/'results';r.mkdir();p=r/'x.json';p.write_bytes(b'actual')
        catalog={'copied_artifacts':[dict(staged_path='x.json',bytes=6,sha256=m.sha(p))]}
        (r/'CATALOG.json').write_text(json.dumps(catalog),encoding='utf8');self.assertEqual(m.verify_results(self.root),1)
        p.write_bytes(b'change')
        with self.assertRaises(ValueError):m.verify_results(self.root)
if __name__=='__main__':unittest.main()
