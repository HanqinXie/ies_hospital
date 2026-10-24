function replay_state_local(bundleRoot,knownManifestSHA,outputRoot)
% Relative data/code replay only; not a replacement for original science authority.
arguments
    bundleRoot (1,1) string
    knownManifestSHA (1,1) string
    outputRoot (1,1) string
end
assert(isfolder(bundleRoot)&&~isfolder(outputRoot),'Existing bundle and fresh output required.');
codeRoot=fullfile(bundleRoot,'code');dataRoot=fullfile(bundleRoot,'data');
assert(isfile(fullfile(codeRoot,'plot_state_figure.m'))&&isfile(fullfile(dataRoot,'MANIFEST.json')));
addpath(codeRoot);
assert(samePath(string(which('plot_state_figure')),fullfile(codeRoot,'plot_state_figure.m')));
assert(samePath(string(which('qa_state_figure')),fullfile(codeRoot,'qa_state_figure.m')));
mkdir(outputRoot);
plot_state_figure(fullfile(dataRoot,'MANIFEST.json'),knownManifestSHA,fullfile(outputRoot,'figures'));
qa_state_figure(fullfile(dataRoot,'MANIFEST.json'),knownManifestSHA,fullfile(outputRoot,'figures'),fullfile(outputRoot,'object_qa'));
disp('RELATIVE_STATE_REPLAY_COMPLETED; original science authority not reissued.');
end
function result=samePath(a,b)
result=strcmpi(char(java.io.File(a).getCanonicalPath()),char(java.io.File(b).getCanonicalPath()));
end
