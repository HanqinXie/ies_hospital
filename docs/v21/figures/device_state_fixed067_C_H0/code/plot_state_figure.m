function receipt=plot_state_figure(manifestPath,knownSHA,outputDir)
% One predeclared 067 C/H0 state view; native intervals and observation points.
arguments
    manifestPath (1,1) string
    knownSHA (1,1) string
    outputDir (1,1) string
end
assert(hashFile(manifestPath)==knownSHA,'Known source manifest SHA differs.');
M=jsondecode(fileread(manifestPath));base=string(fileparts(manifestPath));
assert(string(M.schema)=="hospital_ies.fixed067_state_figure_data.v1");
assert(string(M.source_binding_sha256)=="4dfe62ae105d2696b7782dc36c83ec63c6551e2cded601d6f15c0d4083058add");
assert(string(M.scenario_id)=="m21_boundary_a_5c16d21d8dff33fef94e"&&string(M.policy_id)=="C_sci_v21"&&string(M.configuration_id)=="H0");
assert(M.segment_count==75&&M.observation_count==60&&M.window_minutes==900);
assert(M.original_registered_CASE_count==256&&M.complete_supported_CASE_count==252&&M.known_constructor_failures_retained==4);
assert(~isfolder(outputDir),'Fresh output directory required.');
for k=1:numel(M.members)
    e=M.members(k);p=localMember(base,string(e.path));v=dir(p);
    assert(isscalar(v)&&v.bytes==e.bytes&&hashFile(p)==string(e.sha256),'Source member differs.');
end
D=load(localMember(base,string(M.state_data_mat)),'segment_start_min','segment_end_min', ...
    'bus_code','dg_pre_code','dg_post_code','observation_min','gt_status_code');
assert(isequal(size(D.segment_start_min),[75 1])&&isequal(size(D.gt_status_code),[60 3]));
assert(all(D.segment_end_min>D.segment_start_min)&&D.segment_start_min(1)==0&&D.segment_end_min(end)==900);
assert(isequal(D.segment_end_min(1:end-1),D.segment_start_min(2:end)));
assert(all(diff(D.observation_min)>0)&&all(D.observation_min>=0&D.observation_min<=900));
mkdir(outputDir);
f=figure('Visible','off','Color','white','Units','centimeters','Position',[2 2 18 20], ...
    'DefaultAxesFontName','Times New Roman','DefaultTextFontName','Times New Roman');
cleanup=onCleanup(@()close(f));
f.UserData=struct('schema','hospital_ies.fixed067_state_native_figure.v1', ...
    'manifest_path',manifestPath,'manifest_sha256',knownSHA,'source_binding_sha256',string(M.source_binding_sha256), ...
    'scenario_id',string(M.scenario_id),'policy_id',string(M.policy_id),'configuration_id','H0', ...
    'native_segment_count',75,'native_observation_count',60,'interpolation',false, ...
    'DG_interval_is_pre_label_not_backfilled_post',true,'GT_samples_not_switch_time',true, ...
    'source_csv','SEGMENT_STATE_VALUES.csv / GT_OBSERVATION_VALUES.csv','scientific_gate',false);
t=tiledlayout(f,3,1,'Padding','loose','TileSpacing','loose');
t.OuterPosition=[.035 .195 .945 .715];t.Title.FontName='Times New Roman';
t.XLabel.FontName='Times New Roman';t.YLabel.FontName='Times New Roman';
annotation(f,'textbox',[.035 .93 .93 .045],'String',string(M.figure_contract.labels.title), ...
    'FontName','宋体','FontSize',11,'HorizontalAlignment','center','EdgeColor','none','Interpreter','none', ...
    'Tag','MAIN_TITLE');
a1=nexttile(t,1);setupAxis(a1,'BUS_AXIS',[-.6 4.6]);hold(a1,'on');
for i=1:75
    nativePatch(a1,D.segment_start_min(i),D.segment_end_min(i),D.bus_code(i), ...
        M.palette.bus(D.bus_code(i)+1,:),sprintf('BUS_SEG_%03d',i),i);
end
a1.YTick=0:4;a1.YTickLabel=string(M.categories.bus(:));
ylabel(a1,string(M.figure_contract.labels.bus_y),'FontName','宋体','Interpreter','none');
title(a1,'原生区间日志标签（非连续电气暂态）','FontName','宋体','FontSize',8,'Interpreter','none');
a2=nexttile(t,2);setupAxis(a2,'DG_AXIS',[-.6 6.6]);hold(a2,'on');
for i=1:75
    nativePatch(a2,D.segment_start_min(i),D.segment_end_min(i),D.dg_pre_code(i), ...
        M.palette.dg(D.dg_pre_code(i)+1,:),sprintf('DG_SEG_%03d',i),i);
end
post=scatter(a2,D.segment_end_min,D.dg_post_code,14,'k','d','filled','Tag','DG_POST');
post.UserData=struct('native_segment_indices',(0:74)','semantics','recorded post status at actual native endpoint');
a2.YTick=0:6;a2.YTickLabel=string(M.categories.dg(:));
ylabel(a2,string(M.figure_contract.labels.dg_y),'FontName','宋体','Interpreter','none');
title(a2,'区间起端快照（色块）；末端快照（菱形）','FontName','宋体','FontSize',8,'Interpreter','none');
legend(a2,post,'段末状态点','Location','northoutside','Orientation','horizontal', ...
    'FontName','宋体','FontSize',8,'Interpreter','none','Box','off');
a3=nexttile(t,3);setupAxis(a3,'GT_AXIS',[.4 3.6]);hold(a3,'on');
for g=1:3
    code=D.gt_status_code(:,g);rgb=M.palette.gt(code+1,:);
    h=scatter(a3,D.observation_min,ones(60,1)*g,20,rgb,'o','filled','Tag',sprintf('GT_%d',g));
    h.UserData=struct('gt_index',g,'native_observation_indices',(0:59)', ...
        'status_code',code,'categories',{M.categories.gt},'no_lines_or_forward_fill',true);
end
a3.YTick=1:3;a3.YTickLabel={'GT1','GT2','GT3'};
ylabel(a3,string(M.figure_contract.labels.gt_y),'FontName','宋体','Interpreter','none');
title(a3,'观测中的 GT physical status（原 observation_time）','FontName','宋体','FontSize',8,'Interpreter','none');
xlabel(a3,string(M.figure_contract.labels.x),'FontName','宋体','Interpreter','none');
% The category key is text only, never an extra scientific point/sample.
observed=unique(D.gt_status_code(:));n=numel(observed);
for j=1:n
    code=observed(j);annotation(f,'textbox',[.08+(j-1)*.88/n .154 .88/n .022], ...
        'String',string(M.categories.gt{code+1}),'Color',M.palette.gt(code+1,:), ...
        'FontName','Times New Roman','FontSize',8,'EdgeColor','none','Interpreter','none', ...
        'HorizontalAlignment','center','Tag',sprintf('GT_CATEGORY_LEGEND_%d',code));
end
notes={ ...
    '母线色块为原区间日志标签；DG 色块为起端快照，菱形为末端快照。'; ...
    '色块不证明整段连续状态；GT 仅显示原 observation_time 采样点。'; ...
    '同钟可有不同执行阶段和命令ID，不据此拼接因果或推断切换时刻。'; ...
    'GRID+ALL_GT：60–780 min 为登记故障窗，780–900 min 为恢复观察尾段。'; ...
    '许可不等于就绪/实供：首段 DG 许可900 kW，但状态OFF、实际0 kW。'; ...
    '段内 GT/HRSG 就绪字段缺失，保留未知；本图不是设备实际送达功率图。'};
annotation(f,'textbox',[.035 .017 .94 .119],'String',notes,'FontName','宋体','FontSize',8, ...
    'Interpreter','none','EdgeColor','none','HorizontalAlignment','center','Tag','STATE_SEMANTICS_NOTE');
% Native white unfilled page anchor; it is not a scientific data series.
canvas=annotation(f,'rectangle',[0 0 1 1],'FaceColor','none','Color',[1 1 1], ...
    'LineWidth',.1,'Tag','publication_page_canvas');
assert(string(canvas.FaceColor)=="none"&&isequal(canvas.Position,[0 0 1 1]));
f.PaperUnits='centimeters';f.PaperSize=[18 20];f.PaperPosition=[0 0 18 20];f.PaperPositionMode='manual';
drawnow;
stem=fullfile(outputDir,string(M.figure_contract.name));
savefig(f,stem+'.fig');print(f,stem+'.svg','-dsvg','-painters');
exportgraphics(f,stem+'.pdf','ContentType','vector','BackgroundColor','white');
print(f,stem+'.png','-dpng','-r600');
formats=struct;
for ext=["fig","svg","pdf","png"]
    p=stem+'.'+ext;v=dir(p);assert(isscalar(v)&&v.bytes>0);
    formats.(ext)=struct('path',p,'bytes',v.bytes,'sha256',hashFile(p));
end
for k=1:numel(M.members)
    e=M.members(k);assert(hashFile(localMember(base,string(e.path)))==string(e.sha256));
end
assert(hashFile(manifestPath)==knownSHA);
sourcePath=string(mfilename('fullpath'))+'.m';
receipt=struct('schema','hospital_ies.fixed067_state_matlab_export.v1','status','completed', ...
    'backend','MATLAB','release',version('-release'),'plotter_path',sourcePath, ...
    'plotter_sha256',hashFile(sourcePath),'manifest_path',manifestPath,'manifest_sha256',knownSHA, ...
    'source_binding_sha256',string(M.source_binding_sha256),'figures',{{string(M.figure_contract.name)}}, ...
    'formats',formats,'figure_size_cm',[18 20],'native_segments',75,'native_observation_points_per_GT',60, ...
    'no_new_raw_or_sidecar_read',true,'scientific_gate',false,'actual_physics_started',0, ...
    'object_validation','PENDING independent reopen','visual_review','NOT_RUN');
fid=fopen(fullfile(outputDir,'MATLAB_FIGURE_RECEIPT.json'),'w','n','UTF-8');assert(fid>=0);
finish=onCleanup(@()fclose(fid));fprintf(fid,'%s',jsonencode(receipt,'PrettyPrint',true));
end

function setupAxis(a,tag,yrange)
a.Tag=tag;a.FontName='Times New Roman';a.FontSize=8;a.TickLabelInterpreter='none';
a.XLim=[0 900];a.YLim=yrange;a.XTick=0:150:900;a.Box='on';a.Layer='top';
a.XGrid='on';a.YGrid='off';a.GridAlpha=.15;
a.UserData=struct('axis_tag',tag,'native_times_not_resampled',true);
end
function nativePatch(a,start,finish,code,rgb,tag,index)
h=patch(a,[start finish finish start],[code-.34 code-.34 code+.34 code+.34],rgb, ...
    'EdgeColor','white','LineWidth',.2,'Tag',tag);
h.UserData=struct('native_segment_index',index-1,'start_minute',start,'end_minute',finish, ...
    'recorded_category_code',code,'interval_convention','[start,end)','not_interpolated',true);
end
function p=localMember(base,rel)
assert(~startsWith(rel,["/","\\"])&&~contains(rel,":")&&~any(split(replace(rel,"\\","/"),"/")==".."),'Unsafe local member.');
p=string(java.io.File(fullfile(base,rel)).getCanonicalPath());root=string(java.io.File(base).getCanonicalPath());
assert(startsWith(lower(p),lower(root+string(filesep))),'Local member escaped source directory.');
end
function value=hashFile(path)
fid=fopen(path,'rb');assert(fid>=0);c=onCleanup(@()fclose(fid));digest=java.security.MessageDigest.getInstance('SHA-256');
while true,b=fread(fid,1024*1024,'*uint8');if isempty(b),break;end;digest.update(typecast(b,'int8'));end
value=string(lower(reshape(dec2hex(typecast(digest.digest(),'uint8'),2)',1,[])));
end
