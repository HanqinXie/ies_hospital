import atexit,json,os,sys
from pathlib import Path
config=json.loads((Path(__file__).parent/'guard_config.json').read_text(encoding='utf8'))
root=Path(config['root']).resolve();forbidden=[Path(x).resolve() for x in config['forbidden_roots']]
violations=[]
def checked(value):
    if isinstance(value,(str,bytes,os.PathLike)):
        try:path=Path(os.fsdecode(value)).resolve()
        except (OSError,ValueError):return
        if any(path.is_relative_to(old) for old in forbidden):
            violations.append(str(path));raise RuntimeError('Original research tree access forbidden: '+str(path))
def audit(event,args):
    if event in ('open','os.listdir','os.scandir','os.chdir') and args:checked(args[0])
    elif event=='import' and len(args)>1 and args[1]:checked(args[1])
sys.addaudithook(audit)
def finish():
    imports=[]
    for name,module in list(sys.modules.items()):
        if name=='tools' or name.startswith(('tools.','hospital_ies')):
            paths=[]
            if getattr(module,'__file__',None):paths.append(module.__file__)
            paths.extend(list(getattr(module,'__path__',[]) or []))
            for value in paths:
                path=Path(value).resolve();imports.append(dict(module=name,path=str(path)))
                if not path.is_relative_to(root):violations.append('project import escaped root: '+str(path))
    value=dict(status='failed' if violations else 'passed',token=config['token'],root=str(root),
        forbidden_roots=config['forbidden_roots'],violations=violations,project_imports=imports,
        meaning='Audit hook active during child execution; no audited reads/imports from the declared original trees',
        limitation='CPython audit events and project module paths; not an OS sandbox or proof about un-audited native I/O or grandchildren')
    with Path(config['receipt']).open('x',encoding='utf8') as stream:json.dump(value,stream,ensure_ascii=False,indent=2)
atexit.register(finish)
