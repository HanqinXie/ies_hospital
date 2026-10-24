function receipt=reexport_verified_hardware22_figs(bundleRoot,freshOutputRoot)
% 已验证原生FIG的可迁移再导出；不是CSV重算或科学重放。
% Preparation only: this portable entry has NOT been executed in MATLAB.
arguments
    bundleRoot (1,1) string
    freshOutputRoot (1,1) string
end
bundleRoot=canonicalPath(bundleRoot);freshOutputRoot=canonicalPath(freshOutputRoot);
assert(isfolder(bundleRoot)&&~isfolder(freshOutputRoot)&&~isfile(freshOutputRoot),'Existing bundle and fresh output required.');
assert(~samePath(bundleRoot,freshOutputRoot)&&~startsWith(lower(freshOutputRoot),lower(bundleRoot+string(filesep))),'Output must be outside the read-only bundle.');
here=string(fileparts(mfilename('fullpath')));contractPath=fullfile(here,'PORTABLE_INPUT_CONTRACT.json');
contractSHA="e960f63eb85cd915f264e65379d2126f4c4c91b0895c4edeb3ce6b7f2d252d08";
assert(hashFile(contractPath)==contractSHA,'Portable input contract differs.');
C=jsondecode(fileread(contractPath));assert(string(C.schema)=="hospital_ies.hardware22_portable_native_FIG_reexport_inputs.v1"&&C.file_count==286&&numel(C.files)==286);
verifyLocalFiles(bundleRoot,C.files);
f4Manifest=localFile(bundleRoot,"data_and_code/f4_bundle/LOCAL_DATA_MANIFEST.json");
assert(hashFile(f4Manifest)=="da08cafd99494d49cee2f6c036e3bb83ca7715ed9c05ca05a0b855adaceb3905");
F=jsondecode(fileread(f4Manifest));assert(string(F.stage)=="hardware126"&&F.all_native_tables_preserved&&~F.M_rows_removed&&~F.scientific_gate);
verifyLocalFiles(string(fileparts(f4Manifest)),F.members);
hwManifest=localFile(bundleRoot,"data_and_code/hw18/HW18_ACTUAL_MANIFEST.json");
hwSHA="8fa4872d98493c137f4abe4dabe3f1dacc694ff392aefa7632b5c5c24b1875a0";assert(hashFile(hwManifest)==hwSHA);
H=jsondecode(fileread(hwManifest));assert(H.source_CASE_count==126&&numel(H.cases)==18&&H.whole_hardware_pool_complete&&~H.science_gate);
for k=1:18
    folder=string(H.cases(k).folder);
    assert(hashFile(localFile(string(fileparts(hwManifest)),folder+"/PROCESS.csv"))==string(H.cases(k).csv_sha256));
    assert(hashFile(localFile(string(fileparts(hwManifest)),folder+"/PROCESS.mat"))==string(H.cases(k).mat_sha256));
end
style=localFile(bundleRoot,"code/actual_entrypoints/style_hardware_page_legends_v1.m");
styleSHA="923b7ef0269cf91f3dddaf581e5be8fe0b7b25d2bdc603fb18da6fbff46659b4";assert(hashFile(style)==styleSHA);
available=listfonts;assert(any(strcmpi(available,'Times New Roman'))&&any(strcmpi(available,'宋体')),'Original fonts required.');
mkdir(freshOutputRoot);stage=fullfile(freshOutputRoot,'staging');
f4Stage=fullfile(stage,'f4_figures');f4AuditStage=fullfile(stage,'f4_objects');hwStage=fullfile(stage,'hw18_figures');
mkdir(f4Stage);mkdir(f4AuditStage);mkdir(hwStage);
copyExact(localFile(bundleRoot,"provenance/matlab01/f4_figures/MATLAB_FIGURE_RECEIPT.json"),fullfile(f4Stage,'MATLAB_FIGURE_RECEIPT.json'));
copyExact(localFile(bundleRoot,"provenance/matlab01/f4_objects/MATLAB_OBJECT_QA.json"),fullfile(f4AuditStage,'MATLAB_OBJECT_QA.json'));
copyExact(localFile(bundleRoot,"provenance/matlab01/hw18_figures/HW18_MATLAB_OBJECT_QA.json"),fullfile(hwStage,'HW18_MATLAB_OBJECT_QA.json'));
R=jsondecode(fileread(fullfile(f4Stage,'MATLAB_FIGURE_RECEIPT.json')));f4Names=string(R.figures(:));
hwNames=string({H.cases.output_name})';assert(numel(f4Names)==4&&numel(unique(f4Names))==4&&numel(unique(hwNames))==18);
for name=f4Names'
    assert(startsWith(name,'F4_')&&safeName(name));
    copyExact(localFile(bundleRoot,"provenance/initial_native_figures/f4_figures/"+name+".fig"),fullfile(f4Stage,name+'.fig'));
    copyExact(localFile(bundleRoot,"figures/f4_figures/"+name+"_PAIRED_VALUES.csv"),fullfile(f4Stage,name+'_PAIRED_VALUES.csv'));
end
for name=hwNames'
    assert(startsWith(name,'HW18_')&&safeName(name));
    copyExact(localFile(bundleRoot,"provenance/initial_native_figures/hw18_figures/"+name+".fig"),fullfile(hwStage,name+'.fig'));
end
oldPath=path;restorePath=onCleanup(@()path(oldPath));addpath(fileparts(style),'-begin');
assert(samePath(string(which('style_hardware_page_legends_v1')),style),'Style resolved outside bundle.');
finalRoot=fullfile(freshOutputRoot,'final');mkdir(finalRoot);
style_hardware_page_legends_v1(f4Stage,fullfile(finalRoot,'f4_figures'),f4Names,hwManifest,hwSHA);
style_hardware_page_legends_v1(hwStage,fullfile(finalRoot,'hw18_figures'),hwNames,hwManifest,hwSHA);
verifyLocalFiles(bundleRoot,C.files);assert(hashFile(contractPath)==contractSHA&&hashFile(style)==styleSHA);
receipt=struct('schema','hospital_ies.portable_verified_native_FIG_reexport.v1','mode','verified_native_FIG_reexport_only', ...
    'bundle_root',bundleRoot,'fresh_output_root',freshOutputRoot,'source_contract_sha256',contractSHA, ...
    'original_style_sha256',styleSHA,'source_files_unchanged',true,'F4_figures',4,'HW18_figures',18, ...
    'CSV_metric_recomputation',false,'physics_started',0,'raw_or_sidecar_read',false, ...
    'historical_absolute_metadata_retained_as_provenance_only',true, ...
    'format_and_complete_visual_QA_after_this_reexport','NOT_RUN','scientific_gate',false);
fid=fopen(fullfile(freshOutputRoot,'PORTABLE_FIG_REEXPORT_RECEIPT.json'),'w','n','UTF-8');assert(fid>=0);
closeFile=onCleanup(@()fclose(fid));fprintf(fid,'%s',jsonencode(receipt,'PrettyPrint',true));
end

function verifyLocalFiles(root,members)
assert(~isempty(members));
for k=1:numel(members)
    p=localFile(root,string(members(k).path));info=dir(p);
    assert(isscalar(info)&&info.bytes==members(k).bytes&&hashFile(p)==string(members(k).sha256),'Local source bytes/SHA differ.');
end
end
function p=localFile(root,relative)
parts=split(relative,"/");assert(strlength(relative)>0&&~startsWith(relative,"/")&&~contains(relative,":")&&~contains(relative,char(92))&&~any(parts==".."|parts=="."|parts==""),'Root-bound local relative file required.');
root=canonicalPath(root);p=canonicalPath(fullfile(root,relative));
assert(startsWith(lower(p),lower(root+string(filesep)))&&isfile(p),'Required local bundle member missing or escaped.');
end
function valid=safeName(name)
valid=~contains(name,["/",string(char(92)),":"])&&name~=".."&&name~=".";
end
function p=canonicalPath(p)
p=string(java.io.File(char(p)).getCanonicalPath());
end
function yes=samePath(a,b)
yes=strcmpi(char(canonicalPath(a)),char(canonicalPath(b)));
end
function copyExact(source,destination)
assert(isfile(source)&&~isfile(destination)&&~isfolder(destination),'Fresh staging file required.');
before=hashFile(source);copyfile(source,destination);
assert(hashFile(destination)==before&&hashFile(source)==before,'Staging copy changed source bytes.');
end
function value=hashFile(path)
fid=fopen(path,'rb');assert(fid>=0);cleanup=onCleanup(@()fclose(fid));digest=java.security.MessageDigest.getInstance('SHA-256');
while true,b=fread(fid,1024*1024,'*uint8');if isempty(b),break;end;digest.update(typecast(b,'int8'));end
value=string(lower(reshape(dec2hex(typecast(digest.digest(),'uint8'),2)',1,[])));
end
