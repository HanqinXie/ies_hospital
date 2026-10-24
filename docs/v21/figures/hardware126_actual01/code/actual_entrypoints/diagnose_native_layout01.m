% Read-only native bbox/legend diagnosis of initial visual failures.
root="E:/hospital_ies_v21_20261023/reviews/hardware_figures_actual126_01";
paths=[root+"/matlab01/f4_figures/F4_hardware_AUX_CHW_PUMP_w360.fig",root+"/matlab01/f4_figures/F4_hardware_TOWER_w360.fig",root+"/matlab01/hw18_figures/HW18_01_C_sci_v21_H0.fig",root+"/matlab01/hw18_figures/HW18_03_C_sci_v21_H2.fig"];
records=cell(4,1);
for n=1:4
    p=paths(n);before=hashFile(p);f=openfig(p,'invisible');f.Units='pixels';page=f.Position(3:4);drawnow;
    ax=findall(f,'Type','axes');axesRecords=cell(numel(ax),1);textRecords={};
    for k=1:numel(ax)
        a=ax(k);pos=getpixelposition(a,true);axesRecords{k}=struct('tile',a.Layout.Tile,'position_figure_pixels',pos,'XLim',a.XLim,'YLim',a.YLim);
        labels=[a.XLabel,a.YLabel,a.Title];
        for j=1:numel(labels)
            t=labels(j);t.Units='pixels';box=t.Extent;box(1:2)=box(1:2)+pos(1:2);
            textRecords{end+1}=struct('tile',a.Layout.Tile,'string',string(t.String),'FontName',t.FontName,'figure_pixel_bbox',box,'figure_pixel_size',page,'bbox_outside_figure',box(1)<0||box(2)<0||box(1)+box(3)>page(1)||box(2)+box(4)>page(2)); %#ok<SAGROW>
        end
    end
    lg=findall(f,'Type','legend');legends=cell(numel(lg),1);
    for k=1:numel(lg),lg(k).Units='normalized';legends{k}=struct('string',string(lg(k).String),'location',lg(k).Location,'position_figure_normalized',lg(k).Position);end
    L=findall(f,'-isa','matlab.graphics.layout.TiledChartLayout');layouts=cell(numel(L),1);
    for k=1:numel(L),layouts{k}=struct('OuterPosition',L(k).OuterPosition,'TileSpacing',L(k).TileSpacing,'Padding',L(k).Padding,'title',string(L(k).Title.String),'title_Units',L(k).Title.Units,'title_Extent_native_units',L(k).Title.Extent);end
    records{n}=struct('source_FIG',p,'source_sha256',before,'figure_pixel_size',page,'axes',{axesRecords},'native_label_extents',{textRecords},'legends',{legends},'layouts',{layouts},'saved_or_modified_source',false);
    close(f);assert(hashFile(p)==before);
end
fid=fopen(root+"/INITIAL_NATIVE_LAYOUT_DIAGNOSIS01.json",'w','n','UTF-8');assert(fid>=0);fprintf(fid,'%s',jsonencode(struct('schema','hospital_ies.initial_native_layout_diagnosis.v1','records',{records},'source_FIGs_unchanged',true,'scientific_gate',false),'PrettyPrint',true));fclose(fid);
disp('ACTUAL_NATIVE_BBOX_DIAGNOSIS_COMPLETED_WITHOUT_SOURCE_RESAVE');
function value=hashFile(path)
fid=fopen(path,'rb');assert(fid>=0);c=onCleanup(@()fclose(fid));d=java.security.MessageDigest.getInstance('SHA-256');while true,b=fread(fid,1048576,'*uint8');if isempty(b),break;end;d.update(typecast(b,'int8'));end
value=string(lower(reshape(dec2hex(typecast(d.digest(),'uint8'),2)',1,[])));
end
