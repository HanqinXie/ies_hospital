function receipt=replay_followup_publication(knownManifestSHA,outputDirectory)
% Artifact-only local replay from byte-exact actual native CSV/MAT.
% Does not manufacture a portable native authority or repeat a physical audit.
arguments
    knownManifestSHA (1,1) string
    outputDirectory (1,1) string
end
root=string(fileparts(mfilename('fullpath')));mp=fullfile(root,'PUBLICATION_LOCAL_MANIFEST.json');
assert(hashFile(mp)==knownManifestSHA,'Externally known local manifest SHA differs.');
M=jsondecode(fileread(mp));assert(string(M.schema)=="hospital_ies.followup_publication_local_data.v1");
assert(M.supported_cases==252&&M.original_registered==256&&M.known_constructor_failures_retained==4);
assert(~isfolder(outputDirectory),'Fresh output required.');verifyMembers(root,M.members);
oldPath=path;cleanup=onCleanup(@()path(oldPath));addpath(fullfile(root,'code'),'-begin');
addpath(fullfile(root,'code','publication'),'-begin');
assert(string(which('plot_sci_engineering_publication'))==fullfile(root,'code','plot_sci_engineering_publication.m'));
assert(string(which('style_followup_vector_fullpage_v3'))==fullfile(root,'code','publication','style_followup_vector_fullpage_v3.m'));
assert(string(which('vectorize_boundary_display_layout_coordinates'))==fullfile(root,'code','publication','vectorize_boundary_display_layout_coordinates.m'));
mkdir(outputDirectory);data=fullfile(root,'data','followup_supported252','tables');
native=fullfile(outputDirectory,'native_figures');publication=fullfile(outputDirectory,'publication_figures');
plot_sci_engineering_publication(data,native);style_followup_vector_fullpage_v3(native,publication);
R=jsondecode(fileread(fullfile(publication,'MATLAB_FIGURE_RECEIPT.json')));assert(numel(R.figures)==9);
verifyMembers(root,M.members);assert(hashFile(mp)==knownManifestSHA);
receipt=struct('schema','hospital_ies.local_followup_publication_replay.v1','backend','MATLAB', ...
    'trusted_local_manifest_sha256',knownManifestSHA,'actual_data_directory',data, ...
    'actual_plotter',string(which('plot_sci_engineering_publication')), ...
    'publication_directory',publication,'source_data_and_code_members_unchanged',true, ...
    'original_authority_receipts_rewritten',false,'full_native_authority_repeated',false, ...
    'source_binding_sha256',string(M.source_binding_sha256),'physics',0,'scientific_acceptance',false, ...
    'limit','Local data/artifact replay only. Full external-native authority QA still requires its original evidence chain or explicit separately verified path mapping.');
fid=fopen(fullfile(outputDirectory,'LOCAL_PUBLICATION_REPLAY_RECEIPT.json'),'w','n','UTF-8');assert(fid>=0);c=onCleanup(@()fclose(fid));
fprintf(fid,'%s',jsonencode(receipt,'PrettyPrint',true));
end
function verifyMembers(root,members)
assert(~isempty(members));
for k=1:numel(members)
    relative=string(members(k).path);parts=split(replace(relative,'\','/'),'/');
    assert(~startsWith(relative,["/","\"])&&isempty(regexp(relative,'^[A-Za-z]:','once'))&&~any(parts==".."));
    p=fullfile(root,relative);info=dir(p);assert(isscalar(info)&&~info.isdir&&info.bytes==members(k).bytes);
    assert(hashFile(p)==string(members(k).sha256),'Local member changed.');
end
end
function value=hashFile(path)
fid=fopen(path,'rb');assert(fid>=0);c=onCleanup(@()fclose(fid));md=java.security.MessageDigest.getInstance('SHA-256');
while true,b=fread(fid,1048576,'*uint8');if isempty(b),break;end;md.update(typecast(b,'int8'));end
value=string(lower(reshape(dec2hex(typecast(md.digest(),'uint8'),2)',1,[])));
end
