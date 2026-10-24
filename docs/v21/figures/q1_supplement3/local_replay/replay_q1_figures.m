function receipt=replay_q1_figures(outputDir)
% Local byte-exact Q1 data/code replay; no canonical-authority replacement.
arguments
    outputDir (1,1) string
end
base=string(fileparts(mfilename('fullpath')));
assert(~isfolder(outputDir),'Fresh output directory required.');
addpath(fullfile(base,'code'));
mkdir(outputDir);
nativeDir=fullfile(outputDir,'native_figures');tickDir=fullfile(outputDir,'tick_figures');figuresDir=fullfile(outputDir,'figures');
source=plot_sci_engineering_publication(fullfile(base,'data'),nativeDir);
styled=style_sci_class_ticks(nativeDir,tickDir);
fullpage=style_q1_fullpage(tickDir,figuresDir);
receipt=struct('schema','hospital_ies.Q1_local_figure_replay.v1', ...
    'source',source,'styled',styled,'fullpage',fullpage,'source_data',fullfile(base,'data'), ...
    'output_figures',figuresDir,'scientific_plot_backend','MATLAB', ...
    'canonical_authority_reaudit_performed',false,'physics_attempts',0);
end
