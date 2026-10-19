"""External archive gates: dictionaries and in-memory ZIPs only, no physics."""
from __future__ import annotations
import copy
import hashlib
import importlib.util
import io
from pathlib import Path
import sys
import unittest
import zipfile

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('delivery_archive_under_test',HERE/'build_delivery_archive.py')
M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)
ROOT=Path('D:/synthetic_clone').resolve()
SHA='a'*64;COMMIT='b'*40


def clone_fixture():
    marker={'staged_root':str(ROOT.parent/'publication_stage')}
    receipt=dict(command=['git','clone','--branch','main','--single-branch',
        'https://github.com/HanqinXie/ies_hospital.git',str(ROOT)],cwd=str(ROOT.parent),
        starting_destination_existed=False,clone_destination=str(ROOT),
        source_launcher_root=marker['staged_root'],source_launcher_sha256=SHA,
        source_launcher_commit=COMMIT,git_commit=COMMIT,origin_url='https://github.com/HanqinXie/ies_hospital.git',
        branch='main',exit_code=0,helper_validation_status='passed',validation_error=None,
        command_log_sha256=SHA,started_utc='2026-10-19T00:00:00+00:00',finished_utc='2026-10-19T00:01:00+00:00')
    return marker,receipt


def proof_fixture():
    release=dict(marker={'status':'final'},marker_sha256=SHA,selection_sha256=SHA,
        reproduction_wrapper_sha256=SHA,forbidden_roots=[str(ROOT.parent/'original')])
    q={'scenario_id':'scene','policy_id':'E','metrics':{'energy.x'+str(i):float(i) for i in range(94)}}
    metrics=[dict(metric=k,actual=v,reference=v,difference=0.,budget=1e-5,passed=True) for k,v in q['metrics'].items()]
    matching={'matched':True,'fields':{'field'+str(i):True for i in range(9)}}
    target=M.RUN_REL+'/experiments/reproduce_postpush01'
    command=[sys.executable,'-B','-m','tools.load_accounting.runner','run','--directory',target]
    guard=dict(status='passed',token='testtoken',root=str(ROOT),forbidden_roots=release['forbidden_roots'],
        violations=[],project_imports=[dict(module='hospital_ies',path=str(ROOT/'src/hospital_ies/__init__.py'))])
    config=dict(token=guard['token'],root=str(ROOT),forbidden_roots=release['forbidden_roots'],
        receipt=str(ROOT/M.RUN_REL/'review/independent_repository_postpush01/CHILD_GUARD.json'))
    scalar=dict(status='passed',metric_count=94,registered_duration_minutes=600.,exit_code=0,command=command,
        current_root=str(ROOT),actual_registration_directory=str(ROOT/target),comparisons=metrics)
    proof=dict(status='passed',current_root=str(ROOT),release_status='final',error=None,
        release_marker_sha256=SHA,release_selection_sha256=SHA,wrapper_sha256=SHA,
        frozen_scalar_helper_sha256=SHA,frozen_scalar_helper_returncode=0,reference=q,replay=copy.deepcopy(q),
        strict_scalar_comparisons=metrics,matching=matching,actual_registration_directory=target,
        scalar_comparison_proof=str(ROOT/M.RUN_REL/'review/reproduce_postpush01/REPRODUCTION_PROOF.json'),
        child_guard_path=config['receipt'],child_guard_sha256=SHA,
        launches=[dict(command=command,cwd=str(ROOT),guard_token=guard['token'])],
        parent_python_read_guard=dict(active_during_parent_execution=True,root=str(ROOT),
            forbidden_roots=release['forbidden_roots'],violations=[],audited_python_operations=10),
        postflight_project_imports=guard['project_imports'],utc='2026-10-19T00:03:00+00:00')
    return dict(root=ROOT,name='postpush01',release=release,proof=proof,reference=q,replay=copy.deepcopy(q),
        comparisons=metrics,matching=matching,guard=guard,guard_config=config,scalar=scalar,
        hashes=dict(reproduction_wrapper=SHA,scalar_helper=SHA,child_guard=SHA))


class ArchiveTests(unittest.TestCase):
    def fn(self,name):
        fn=getattr(M,name,None);self.assertTrue(callable(fn),'missing archive gate: '+name);return fn
    def validate_clone(self,receipt=None,marker=None,**kw):
        m,r=clone_fixture()
        return self.fn('validate_clone_receipt')(ROOT,marker or m,receipt or r,head=COMMIT,
            origin='https://github.com/HanqinXie/ies_hospital.git',branch='main',origin_main=COMMIT,
            launcher_sha256=SHA,log_sha256=SHA,**kw)
    def test_accepts_actual_bound_clone_receipt(self):self.validate_clone()
    def test_rejects_prepush_stage_as_clone(self):
        marker,r=clone_fixture();marker['staged_root']=str(ROOT)
        with self.assertRaises(ValueError):self.validate_clone(r,marker)
    def test_rejects_wrong_origin_commit_or_launcher(self):
        for key,value in (('origin_url','https://example.test/repo'),('git_commit','0'*40),
            ('source_launcher_commit','0'*40),('source_launcher_sha256','0'*64),('command_log_sha256','0'*64),
            ('helper_validation_status','failed'),('validation_error',{'type':'failure'})):
            with self.subTest(key=key):
                marker,r=clone_fixture();r[key]=value
                with self.assertRaises(ValueError):self.validate_clone(r,marker)
    def test_rejects_clone_overwrite_or_wrong_command(self):
        for key,value in (('starting_destination_existed',True),('cwd',str(ROOT)),
            ('source_launcher_root',str(ROOT)),('branch','other'),('exit_code',1)):
            marker,r=clone_fixture();r[key]=value
            with self.assertRaises(ValueError):self.validate_clone(r,marker)
        marker,r=clone_fixture();r['command'][2]='--mirror'
        with self.assertRaises(ValueError):self.validate_clone(r,marker)
    def test_core_requires_exact_command_root_and_env(self):
        valid=dict(exit_code=0,cwd=str(ROOT),command=[sys.executable,'-B','-m','pytest','-q'],
            environment_overrides={'PYTHONPATH':str(ROOT/'src')+M.os.pathsep+str(ROOT)},
            started_utc='2026-10-19T00:01:10+00:00',finished_utc='2026-10-19T00:02:00+00:00')
        self.fn('validate_core_receipt')(ROOT,valid,'463 passed in 10.00s','2026-10-19T00:01:00+00:00')
        for key,value in (('cwd',str(ROOT.parent)),('command',['echo','463 passed']),('exit_code',1),
            ('environment_overrides',{'PYTHONPATH':'original'})):
            bad=copy.deepcopy(valid);bad[key]=value
            with self.assertRaises(ValueError):M.validate_core_receipt(ROOT,bad,'463 passed in 10.00s','2026-10-19T00:01:00+00:00')
    def test_core_does_not_accept_failed_summary_with_pass_substring(self):
        valid=dict(exit_code=0,cwd=str(ROOT),command=[sys.executable,'-B','-m','pytest','-q'],
            environment_overrides={'PYTHONPATH':str(ROOT/'src')+M.os.pathsep+str(ROOT)},
            started_utc='2026-10-19T00:01:10+00:00',finished_utc='2026-10-19T00:02:00+00:00')
        with self.assertRaises(ValueError):self.fn('validate_core_receipt')(ROOT,valid,'1 failed, 463 passed in 10s','2026-10-19T00:01:00+00:00')
    def test_accepts_recomputed_strict_proof(self):self.fn('validate_saved_proof')(**proof_fixture())
    def test_rejects_stale_marker_selection_or_wrapper(self):
        for field in ('release_marker_sha256','release_selection_sha256','wrapper_sha256','frozen_scalar_helper_sha256'):
            values=proof_fixture();values['proof'][field]='0'*64
            with self.assertRaises(ValueError):self.fn('validate_saved_proof')(**values)
    def test_rejects_false_matching_or_parent_violation(self):
        v=proof_fixture();v['proof']['matching']['matched']=False
        with self.assertRaises(ValueError):self.fn('validate_saved_proof')(**v)
        v=proof_fixture();v['proof']['parent_python_read_guard']['violations']=['original read']
        with self.assertRaises(ValueError):M.validate_saved_proof(**v)
    def test_rejects_changed_actual_metrics_or_qualification(self):
        v=proof_fixture();v['proof']['replay']['metrics']['energy.x0']=2.
        with self.assertRaises(ValueError):self.fn('validate_saved_proof')(**v)
    def test_rejects_child_wrong_token_sha_root_or_import(self):
        for key,value in (('token','wrong'),('root',str(ROOT.parent)),('status','failed'),
            ('violations',['original read']),('project_imports',[{'module':'tools','path':str(ROOT.parent/'tools/x.py')}])):
            v=proof_fixture();v['guard'][key]=value
            with self.assertRaises(ValueError):self.fn('validate_saved_proof')(**v)
        v=proof_fixture();v['hashes']['child_guard']='0'*64
        with self.assertRaises(ValueError):M.validate_saved_proof(**v)
    def test_rejects_unexpected_second_launch(self):
        v=proof_fixture();v['proof']['launches']*=2
        with self.assertRaises(ValueError):self.fn('validate_saved_proof')(**v)
    def test_git_requires_head_index_working_bytes_match(self):
        entry={'x':{'mode':'100644','oid':'f'*40}}
        self.fn('validate_git_entries')(entry,entry,{'x'},{'x':'f'*40})
        with self.assertRaises(ValueError):M.validate_git_entries(entry,entry,{'x'},{'x':'0'*40})
        with self.assertRaises(ValueError):M.validate_git_entries(entry,{'x':{'mode':'100644','oid':'0'*40}},{'x'},{'x':'f'*40})
        with self.assertRaises(ValueError):M.validate_git_entries(entry,entry,{'x','missing'},{'x':'f'*40})
    def test_zip_verifies_manifest_sha_and_payload_crc(self):
        data=b'payload';manifest=b'{"files":[]}';row={'path':'x.txt','bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as z:z.writestr('Hospital_IES_V20/x.txt',data);z.writestr('Hospital_IES_V20/DELIVERY_MANIFEST.json',manifest)
        with zipfile.ZipFile(stream) as z:
            result=self.fn('verify_zip')(z,[row],manifest)
            self.assertEqual(len(result),2);self.assertTrue(all('crc32' in r and 'sha256' in r for r in result))
            with self.assertRaises(ValueError):M.verify_zip(z,[row],b'changed')
    def test_zip_rejects_extra_or_duplicate_members(self):
        for duplicate in (False,True):
            stream=io.BytesIO();data=b'payload';manifest=b'{}'
            with zipfile.ZipFile(stream,'w') as z:
                z.writestr('Hospital_IES_V20/x.txt',data);z.writestr('Hospital_IES_V20/DELIVERY_MANIFEST.json',manifest)
                if duplicate:
                    with self.assertWarns(UserWarning):z.writestr('Hospital_IES_V20/x.txt',data)
                else:z.writestr('extra',data)
            row={'path':'x.txt','bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
            with zipfile.ZipFile(stream) as z:
                with self.assertRaises(ValueError):self.fn('verify_zip')(z,[row],manifest)
    def test_hard_limit_is_strictly_less_than_200million(self):
        self.fn('validate_archive_size')(199_999_999)
        with self.assertRaises(ValueError):M.validate_archive_size(200_000_000)


if __name__=='__main__':unittest.main(verbosity=2)
