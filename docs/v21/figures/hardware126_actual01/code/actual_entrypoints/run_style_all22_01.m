root="E:/hospital_ies_v21_20261023/reviews/hardware_figures_actual126_01";addpath(root);
assert(hashFile(root+"/style_hardware_page_legends_v1.m")=="923b7ef0269cf91f3dddaf581e5be8fe0b7b25d2bdc603fb18da6fbff46659b4");
manifest=root+"/hw18_source01/HW18_ACTUAL_MANIFEST.json";known="8fa4872d98493c137f4abe4dabe3f1dacc694ff392aefa7632b5c5c24b1875a0";
assert(~isfolder(root+"/final_style01"));mkdir(root+"/final_style01");
f4names=["F4_hardware_AUX_CHW_PUMP_w360";"F4_hardware_GRID_w360";"F4_hardware_GRID_ALL_GT_w360";"F4_hardware_TOWER_w360"];
style_hardware_page_legends_v1(root+"/matlab01/f4_figures",root+"/final_style01/f4_figures",f4names,manifest,known);
addpath(root+"/f4_prepared01/bundle/code/qa_native");
qa_sci_artifacts(root+"/f4_expected_local01/EXPECTED.json","564b488df3eeca103d4b25d163e22ee5df618cdd80779a946cb54bb302e2f940",root+"/final_style01/f4_figures",root+"/final_style01/f4_objects");
M=jsondecode(fileread(manifest));hwNames=string({M.cases.output_name})';assert(numel(hwNames)==18);
style_hardware_page_legends_v1(root+"/matlab01/hw18_figures",root+"/final_style01/hw18_figures",hwNames,manifest,known);
disp('ACTUAL_ALL22_PAGE_LEGEND_STYLE_AND_REOPEN_SCIENCE_QA_COMPLETED');
function value=hashFile(path)
fid=fopen(path,'rb');assert(fid>=0);c=onCleanup(@()fclose(fid));d=java.security.MessageDigest.getInstance('SHA-256');while true,b=fread(fid,1024*1024,'*uint8');if isempty(b),break;end;d.update(typecast(b,'int8'));end
value=string(lower(reshape(dec2hex(typecast(d.digest(),'uint8'),2)',1,[])));
end
