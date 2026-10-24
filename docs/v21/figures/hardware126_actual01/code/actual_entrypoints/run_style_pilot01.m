root="E:/hospital_ies_v21_20261023/reviews/hardware_figures_actual126_01";addpath(root);
manifest=root+"/hw18_source01/HW18_ACTUAL_MANIFEST.json";known="8fa4872d98493c137f4abe4dabe3f1dacc694ff392aefa7632b5c5c24b1875a0";
assert(~isfolder(root+"/style_pilot01"));mkdir(root+"/style_pilot01");
checks=checkcode(root+"/style_hardware_page_legends_v1.m",'-id');fid=fopen(root+"/style_pilot01/CHECKCODE.json",'w','n','UTF-8');assert(fid>=0);fprintf(fid,'%s',jsonencode(checks,'PrettyPrint',true));fclose(fid);
style_hardware_page_legends_v1(root+"/matlab01/f4_figures",root+"/style_pilot01/f4_figures","F4_hardware_TOWER_w360",manifest,known);
style_hardware_page_legends_v1(root+"/matlab01/hw18_figures",root+"/style_pilot01/hw18_figures","HW18_03_C_sci_v21_H2",manifest,known);
disp('ACTUAL_PAGE_LEGEND_PILOT_COMPLETED_TWO_FIGURES');
