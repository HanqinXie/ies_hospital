% Actual root-approved HW126 F4 and predeclared HW18. No simulation.
root="E:/hospital_ies_v21_20261023/reviews/hardware_figures_actual126_01";
bundle=root+"/f4_prepared01/bundle";
assert(~isfolder(root+"/matlab01"));mkdir(root+"/matlab01");
addpath(bundle);addpath(bundle+"/code/qa_native");
hwCode="E:/hospital_ies_v21_20261023/reviews/remaining_matlab_plan01/hardware_process_v1";
addpath(hwCode);
checks={checkcode(bundle+"/replay_remaining_figures.m",'-id'),checkcode(bundle+"/code/plot_sci_engineering_publication.m",'-id'),checkcode(bundle+"/code/qa_native/qa_sci_artifacts.m",'-id'),checkcode(hwCode+"/plot_hardware_native_process.m",'-id')};
fid=fopen(root+"/matlab01/CHECKCODE_ACTUAL01.json",'w','n','UTF-8');assert(fid>=0);fprintf(fid,'%s',jsonencode(struct('release',version('-release'),'checks',{checks}),'PrettyPrint',true));fclose(fid);
replay_remaining_figures("da08cafd99494d49cee2f6c036e3bb83ca7715ed9c05ca05a0b855adaceb3905",root+"/matlab01/f4_figures");
qa_sci_artifacts(root+"/f4_expected_local01/EXPECTED.json","564b488df3eeca103d4b25d163e22ee5df618cdd80779a946cb54bb302e2f940",root+"/matlab01/f4_figures",root+"/matlab01/f4_objects");
plot_hardware_native_process(root+"/hw18_source01/HW18_ACTUAL_MANIFEST.json","8fa4872d98493c137f4abe4dabe3f1dacc694ff392aefa7632b5c5c24b1875a0",root+"/matlab01/hw18_figures");
disp('ACTUAL_F4_4_AND_PREDECLARED_HW18_NATIVE_MATLAB_QA_COMPLETED');
