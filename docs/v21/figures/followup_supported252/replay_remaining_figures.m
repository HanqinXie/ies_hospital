function receipt=replay_remaining_figures(knownManifestSHA,outputDirectory)
% Local artifact-only replay. Requires externally recorded manifest SHA.
% Calls the byte-exact approved MATLAB plotter; no simulation or Python data.
arguments
    knownManifestSHA (1,1) string
    outputDirectory (1,1) string
end
root=string(fileparts(mfilename('fullpath')));
manifestPath=fullfile(root,'LOCAL_DATA_MANIFEST.json');
assert(hashFile(manifestPath)==knownManifestSHA,'External local manifest SHA differs.');
M=jsondecode(fileread(manifestPath));
assert(string(M.schema)=="hospital_ies.local_matlab_data_mirror.v1"&&~M.scientific_gate);
assert(~isfolder(outputDirectory),'Fresh output directory required.');
verifyMembers(root,M.members);
oldPath=path;cleanup=onCleanup(@()path(oldPath));addpath(fullfile(root,'code'),'-begin');
assert(string(which('plot_sci_engineering_publication'))==fullfile(root,'code','plot_sci_engineering_publication.m'), ...
    'Plotter unexpectedly resolved outside the local bundle.');
dataDirectory=fullfile(root,'data',string(M.stage),'tables');
receipt=plot_sci_engineering_publication(dataDirectory,outputDirectory);
verifyMembers(root,M.members);assert(hashFile(manifestPath)==knownManifestSHA);
local=struct('schema','hospital_ies.local_matlab_artifact_replay.v1', ...
    'trusted_local_manifest_sha256',knownManifestSHA,'local_root',root, ...
    'actual_matlab_plotter',string(which('plot_sci_engineering_publication')), ...
    'original_native_receipts_rewritten',false,'physics_started',0, ...
    'scientific_acceptance',false,'all_original_members_unchanged',true);
f=fopen(fullfile(outputDirectory,'LOCAL_REPLAY_RECEIPT.json'),'w','n','UTF-8');assert(f>=0);
closer=onCleanup(@()fclose(f));fprintf(f,'%s',jsonencode(local,'PrettyPrint',true));
end

function verifyMembers(root,members)
assert(~isempty(members),'Empty local manifest.');
for k=1:numel(members)
    p=fullfile(root,string(members(k).path));
    info=dir(p);assert(numel(info)==1&&~info.isdir&&info.bytes==members(k).bytes);
    assert(hashFile(p)==string(members(k).sha256),'Local data/code member drift.');
end
end

function value=hashFile(path)
fid=fopen(path,'rb');assert(fid>=0);cleanup=onCleanup(@()fclose(fid));
md=java.security.MessageDigest.getInstance('SHA-256');
while ~feof(fid)
    block=fread(fid,1048576,'*uint8');md.update(typecast(block,'int8'));
end
value=string(lower(reshape(dec2hex(typecast(md.digest(),'uint8'),2).',1,[])));
end
