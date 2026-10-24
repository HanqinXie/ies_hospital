function receipt=replay_fixed3_local(bundleRoot,outputRoot,knownMemberManifestSHA)
% Artifact-only local replay. It does not recreate native scientific authority.
arguments
    bundleRoot (1,1) string
    outputRoot (1,1) string
    knownMemberManifestSHA (1,1) string
end
assert(isfolder(bundleRoot)&&~isfolder(outputRoot),'Existing verified bundle and fresh output required.');
manifest=fullfile(bundleRoot,'REPLAY_MEMBER_MANIFEST.json');assert(hashFile(manifest)==knownMemberManifestSHA);
B=jsondecode(fileread(manifest));assert(string(B.schema)=="hospital_ies.fixed3_local_replay_members.v1");
for k=1:numel(B.members)
    v=B.members(k);p=localMember(bundleRoot,string(v.path));assert(hashFile(p)==string(v.sha256));
    d=dir(p);assert(isscalar(d)&&d.bytes==v.bytes);
end
code=fullfile(bundleRoot,'code');addpath(code,'-begin');
for name=["plot_sci_engineering_publication","style_fixed3_vector_fullpage", ...
        "style_fixed_process_v2","style_fixed3_empty_title_font","style_fixed3_first_legend", ...
        "vectorize_boundary_display_layout_coordinates","verify_boundary_vector_bands_figure"]
    assert(strcmpi(char(java.io.File(which(name)).getCanonicalPath()), ...
        char(java.io.File(fullfile(code,name+'.m')).getCanonicalPath())),'Unexpected code root.');
end
data=fullfile(bundleRoot,'assembled01','tables');mp=fullfile(bundleRoot,'assembled01','FIXED3_ACTUAL_MANIFEST.json');
M=jsondecode(fileread(mp));assert(string(M.schema)=="hospital_ies.fixed3_actual_process_manifest.v2");
assert(M.complete_native_CASE_count==252&&M.original_registered_CASE_count==256 ...
    &&M.known_constructor_failures_retained==4&&~M.original256_all_passed);
plot_sci_engineering_publication(data,fullfile(outputRoot,'native'));
style_fixed3_vector_fullpage(fullfile(outputRoot,'native'),fullfile(outputRoot,'vector'));
style_fixed_process_v2(fullfile(outputRoot,'vector'),fullfile(outputRoot,'clock_style'),mp,hashFile(mp));
style_fixed3_empty_title_font(fullfile(outputRoot,'clock_style'),fullfile(outputRoot,'font_style'));
style_fixed3_first_legend(fullfile(outputRoot,'font_style'),fullfile(outputRoot,'final'));
for k=1:numel(B.members),v=B.members(k);assert(hashFile(localMember(bundleRoot,string(v.path)))==string(v.sha256));end
receipt=struct('schema','hospital_ies.fixed3_artifact_only_local_replay.v1','backend','MATLAB', ...
    'bundle_member_manifest_sha256',knownMemberManifestSHA,'all_inputs_unchanged',true, ...
    'original_E_authority_paths_remapped_or_rewritten',false,'full_native_science_reaudit',false, ...
    'local_data_and_matlab_code_only',true,'actual_physics_started',0,'scientific_gate',false, ...
    'object_format_visual_QA','PENDING_FOR_THIS_LOCAL_RUN');
fid=fopen(fullfile(outputRoot,'LOCAL_ARTIFACT_REPLAY_RECEIPT.json'),'w','n','UTF-8');assert(fid>=0);
c=onCleanup(@()fclose(fid));fprintf(fid,'%s',jsonencode(receipt,'PrettyPrint',true));
end
function p=localMember(root,relative)
assert(~isempty(relative)&&isempty(regexp(relative,'^[A-Za-z]:|^[\\/]|(^|[\\/])\.\.([\\/]|$)','once')),'Unsafe bundle member.');
p=string(java.io.File(fullfile(root,relative)).getCanonicalPath());base=string(java.io.File(root).getCanonicalPath());
assert(startsWith(lower(p),lower(base+filesep)),'Bundle member escapes root.');
end
function value=hashFile(path)
fid=fopen(path,'rb');assert(fid>=0);c=onCleanup(@()fclose(fid));d=java.security.MessageDigest.getInstance('SHA-256');
while true,b=fread(fid,1024*1024,'*uint8');if isempty(b),break;end;d.update(typecast(b,'int8'));end
value=string(lower(reshape(dec2hex(typecast(d.digest(),'uint8'),2)',1,[])));
end
