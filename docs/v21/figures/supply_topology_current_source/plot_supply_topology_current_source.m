function plot_supply_topology_current_source(outputDir, repoRoot)
% Current M21 model schematic. No simulation or result files are read.
% Explicit root, source SHA checks and create-only output prevent stale reuse.
% Chinese: SimSun; English/numbers: Times New Roman; native editable objects.
narginchk(2,2);
here = fileparts(mfilename('fullpath'));
specPath = fullfile(here,'source_spec.json');
csvPath = fullfile(here,'service_dependencies.csv');
spec = jsondecode(fileread(specPath));
assert(strcmp(spec.schema,'hospital_ies.topology_source_spec.v1'),'Unknown source specification');
assert(~exist(outputDir,'dir') && ~exist(outputDir,'file'),'Output must be a new directory');
sourceBefore = verifySources(repoRoot,spec.sources);
plotterPath = [mfilename('fullpath') '.m'];
localBefore = {fileSHA(plotterPath),fileSHA(specPath),fileSHA(csvPath)};
mapping = readtable(fullfile(repoRoot,'inputs','sci_engineering','IDF_SERVICE_MAPPING.csv'), ...
    'TextType','string','VariableNamingRule','preserve');
assert(height(mapping)==spec.mapping_rows,'Mapping row count changed');
mkdir(outputDir);

col.e=[.10 .29 .47]; col.h=[.66 .29 .12]; col.c=[.02 .45 .43];
col.r=[.66 .43 .05]; col.x=[.47 .27 .57]; col.ink=[.16 .19 .23];
fig=figure('Color','w','Units','centimeters','Position',[2 2 31 24], ...
    'Visible','off','Renderer','painters');
ax=axes(fig,'Units','normalized','Position',[0 0 1 1],'XLim',[0 1],'YLim',[0 1],'FontName','Times New Roman');
axis(ax,'off'); hold(ax,'on');
set(fig,'DefaultTextFontName','Times New Roman');
label(ax,.03,.976,'医院综合能源系统：能量路径、服务依赖与有限备用',15,col.ink,'left','bold');
label(ax,.03,.947,'M21 | Aggregate service ports; no room-resolved circuits or hydraulic network',9.3,col.ink,'left');

panel(ax,[.025 .405 .605 .515],'a  能量转换与共同平衡',col.ink);
panel(ax,[.65 .405 .325 .515],'b  聚合末端服务依赖',col.ink);
panel(ax,[.025 .075 .95 .30],'c  独立硬件配置：分别与基准比较',col.ink);

% a: solid = energy flow. Gold dashed = rejected heat, not useful cold.
arrow(ax,[.215 .835;.265 .835],col.e,'-');
arrow(ax,[.415 .835;.475 .835],col.e,'-');
arrow(ax,[.215 .695;.265 .695],col.h,'-');
arrow(ax,[.415 .695;.475 .695],col.h,'-');
arrow(ax,[.34 .6575;.34 .5775],col.h,'-');
arrow(ax,[.415 .835;.446 .835;.446 .600;.390 .600;.390 .5775],col.e,'-');
arrow(ax,[.415 .54;.475 .54],col.c,'-');
arrow(ax,[.265 .54;.215 .54],col.r,'--');
box(ax,[.045 .7975 .17 .075],{'外电、燃气轮机、柴油机','GRID / GT / DG','运行、接点及有限备用约束'},col.e);
box(ax,[.265 .7975 .15 .075],{'共同电平衡','Common electric balance'},col.e);
box(ax,[.475 .7975 .135 .075],{'建筑直接电与必要辅机','Building electricity','分解及资格见右表'},col.e);
box(ax,[.045 .6575 .17 .075],{'余热锅炉或燃气锅炉','GT-HRSG / boiler','热源及执行许可约束'},col.h);
box(ax,[.265 .6575 .15 .075],{'蒸汽与换热平衡','Steam / heat balance'},col.h);
box(ax,[.475 .6575 .135 .075],{'建筑供热','Building heat','需供热泵及相应风机'},col.h);
box(ax,[.265 .5025 .15 .075],{'电制冷机与吸收式冷机','EC / ABS','原中央附机电系数'},col.c);
box(ax,[.475 .5025 .135 .075],{'共同冷量平衡','Useful cooling','需建筑冷冻水泵及风机'},col.c);
box(ax,[.045 .5025 .17 .075],{'共享冷却塔排热约束','Shared heat rejection','不是有用供冷'},col.r);
label(ax,.045,.463,'EC: 0.77 kW/RT; ABS: 0.14 kW/RT',8.6,col.ink,'left');
label(ax,.045,.441,'已含冷凝泵与塔风机；不再另加一次',8.6,col.ink,'left');
label(ax,.045,.420,'建筑冷冻水泵另列；供热循环泵为源数据代理补项',8.2,col.ink,'left');

% b: text table expresses constraints, without inventing physical branches.
label(ax,.666,.864,'P0-P3 x e / h / c',10.6,col.ink,'left','bold');
label(ax,.666,.840,'四等级三载能分别核算，不解析同等级房间交付',8.6,col.ink,'left');
rule(ax,.666,.958,.822);
label(ax,.666,.800,'建筑电',9.2,col.e,'left','bold');
label(ax,.666,.777,'Direct + fan + CHW pump + heat circulation',8.1,col.ink,'left');
label(ax,.666,.754,'直接电不以风机为前置；辅助电只计一次',8.6,col.ink,'left');
rule(ax,.666,.958,.735);
label(ax,.666,.714,'空气侧供热',9.2,col.h,'left','bold');
label(ax,.666,.691,'Heat source + fan + heat circulation pump',8.1,col.ink,'left');
label(ax,.666,.668,'供冷',9.2,col.c,'left','bold');
label(ax,.666,.645,'EC / ABS or H2 + fan + building CHW pump',8.1,col.ink,'left');
rule(ax,.666,.958,.627);
label(ax,.666,.606,'生活热水边界',9.2,col.h,'left','bold');
label(ax,.666,.583,'P3: identifiable non-air heat (DHW) upper bound',8.1,col.ink,'left');
label(ax,.666,.560,'仅此上界不受风机门控；仍需热源及供热泵',8.6,col.ink,'left');
label(ax,.666,.537,'不是生活热水实供、温度或流量计量',8.6,col.ink,'left');
rule(ax,.666,.958,.518);
label(ax,.666,.497,'命名区域仅作负荷追溯与风机加权归属',8.6,col.ink,'left');
label(ax,.666,.474,'OR / ER / ICU: source-flow weighted proxy',8.1,col.ink,'left');
label(ax,.666,.451,'不是实测独立馈线、风管或水管回路',8.6,col.ink,'left');
label(ax,.666,.428,'有电不等于通风、压差或感染控制达标',8.6,col.ink,'left');

% c: hardware eligibility is a bound on common balances, not a wiring map.
box(ax,[.045 .120 .282 .205],{'基准：无新增储能','H0','原外电、燃机、柴油与冷热源','普通共同母线及原执行权限','各方案共用相同服务模型','原始需求分母保持不变'},col.ink);
box(ax,[.359 .120 .282 .205],{'有限不间断电源','H1 = H0 + UPS','P0 / P1 building electricity only','仅合格建筑电需求的聚合放电上限','不能据此宣称中央冷站独立供电','实际库存、功率、损失及回充受限'},col.x,'--');
box(ax,[.673 .120 .282 .205],{'有限蓄冷，不含不间断电源','H2 = H0 + cold store; no UPS','P0 / P1 useful cooling only','额外充放循环泵电来自普通共同母线','仍需建筑冷冻水泵与末端风机','实际充冷还需外电接通及塔可用'},col.x,'--');
label(ax,.045,.098,'H1 / H2 are separate alternatives, not simultaneous additions; initial stock is paid preparation.',8.7,col.ink,'left');
label(ax,.03,.052,'实线为能量路径，金色虚线为排热；配置框为能力与资格，不是已建专用支路。',9,col.ink,'left');
label(ax,.03,.030,sprintf('Source: registered IDF / EnergyPlus mapping (%d records); source SHA checked before and after export.',height(mapping)),8.5,col.ink,'left');
label(ax,.03,.011,'设备可用、命令许可、实际就绪与实际交付须区分；本图不证明保障持续时间或临床合规。',8.5,col.ink,'left');

sourceAfter=verifySources(repoRoot,spec.sources);
assert(isequal(sourceBefore,sourceAfter),'Source changed during export preparation');
stem=fullfile(outputDir,'supply_topology_current_source');
savefig(fig,[stem '.fig']);
print(fig,[stem '.svg'],'-dsvg');
exportgraphics(fig,[stem '.pdf'],'ContentType','vector','BackgroundColor','white');
exportgraphics(fig,[stem '.png'],'Resolution',600,'BackgroundColor','white');
sourceAfter=verifySources(repoRoot,spec.sources);
assert(isequal(localBefore,{fileSHA(plotterPath),fileSHA(specPath),fileSHA(csvPath)}),'Plot source drift');
receipt=struct('schema','hospital_ies.topology_render.v1','source_binding_context',spec.source_binding_context, ...
    'repo_root',repoRoot,'output_directory',outputDir, ...
    'source_files',sourceAfter,'plotter_sha256',localBefore{1},'source_spec_sha256',localBefore{2}, ...
    'service_csv_sha256',localBefore{3},'source_mapping_rows',height(mapping), ...
    'new_physics_attempts',0,'scientific_results_read',false,'native_editable',true, ...
    'line_objects',numel(findall(fig,'Type','line')),'patch_objects',numel(findall(fig,'Type','patch')), ...
    'text_objects',numel(findall(fig,'Type','text')),'image_objects',numel(findall(fig,'Type','image')), ...
    'artifact_qa','NOT_RUN: reopen, font scan and visual inspection required after this export');
fid=fopen(fullfile(outputDir,'RENDER_RECEIPT.json'),'w','n','UTF-8');
assert(fid>=0,'Cannot write receipt'); fwrite(fid,jsonencode(receipt),'char'); fclose(fid);
close(fig);
end

function items=verifySources(root,items)
for k=1:numel(items)
    path=fullfile(root,strrep(items(k).relative_path,'/',filesep));
    assert(strcmp(fileSHA(path),items(k).sha256),['Source SHA mismatch: ' items(k).relative_path]);
end
end

function out=fileSHA(path)
fid=fopen(path,'rb'); assert(fid>=0,['Missing source: ' path]);
cleanup=onCleanup(@()fclose(fid));
md=java.security.MessageDigest.getInstance('SHA-256');
while true
    bytes=fread(fid,1048576,'*uint8'); if isempty(bytes),break;end
    md.update(bytes);
end
out=lower(reshape(dec2hex(typecast(md.digest(),'uint8'),2).',1,[]));
end

function panel(ax,b,title,color)
rectangle(ax,'Position',b,'EdgeColor',[.78 .80 .82],'FaceColor','none','LineWidth',.8);
label(ax,b(1)+.014,b(2)+b(4)-.021,title,11.2,color,'left','bold');
end

function box(ax,b,lines,color,style)
if nargin<5,style='-';end
rectangle(ax,'Position',b,'Curvature',.08,'EdgeColor',color, ...
    'FaceColor',[.975 .979 .982],'LineWidth',1.1,'LineStyle',style);
step=b(4)/(numel(lines)+1);
for k=1:numel(lines)
    label(ax,b(1)+b(3)/2,b(2)+b(4)-k*step,lines{k},8.7,color,'center');
end
end

function label(ax,x,y,str,size,color,align,weight)
if nargin<8,weight='normal';end
% Each run is rendered explicitly; Latin letters/numbers never inherit SimSun.
chars=char(str); chinese=double(chars)>127; rendered='';
begin=1;
while begin<=numel(chars)
    last=begin; while last<numel(chars) && chinese(last+1)==chinese(begin),last=last+1;end
    if chinese(begin),font='SimSun';else,font='Times New Roman';end
    rendered=[rendered '{\fontname{' font '}' chars(begin:last) '}']; %#ok<AGROW>
    begin=last+1;
end
text(ax,x,y,rendered,'Interpreter','tex','FontName','Times New Roman', ...
    'FontSize',size,'FontWeight',weight,'Color',color,'HorizontalAlignment',align,'VerticalAlignment','middle');
end

function rule(ax,x0,x1,y)
plot(ax,[x0 x1],[y y],'Color',[.82 .83 .85],'LineWidth',.6);
end

function arrow(ax,xy,color,style)
plot(ax,xy(:,1),xy(:,2),'Color',color,'LineWidth',1.2,'LineStyle',style);
v=xy(end,:)-xy(end-1,:);v=v/norm(v);n=[-v(2) v(1)];
tip=xy(end,:);base=tip-.007*v;
patch(ax,[tip(1),base(1)+.0027*n(1),base(1)-.0027*n(1)], ...
    [tip(2),base(2)+.0027*n(2),base(2)-.0027*n(2)],color,'EdgeColor',color);
end
