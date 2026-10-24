function plot_storage_functional_process(sourceRoot, outputRoot)
% Plot only real M21 native intervals and inventory endpoints in MATLAB.
% Reusable for future paired hardware results; these two pilot cases are
% explicitly unpaired C-only functional checks and are never an H0 comparison.
here=fileparts(mfilename('fullpath'));
if nargin<1,sourceRoot=here;end
if nargin<2,outputRoot=fullfile(here,'figures01');end
if ~exist(outputRoot,'dir'),mkdir(outputRoot);end
available=listfonts;
assert(any(strcmpi(available,'Times New Roman')),'Times New Roman missing');
assert(any(strcmpi(available,'宋体')),'宋体 missing');
set(groot,'defaultAxesFontName','Times New Roman','defaultTextFontName','Times New Roman');
qa=struct([]);
for config={'H1','H2'}
    id=config{1};folder=fullfile(sourceRoot,id);
    meta=jsondecode(fileread(fullfile(folder,'PLOT_CASE.json')));
    assert(meta.functional_check_only && ~meta.paired_H0,'Wrong figure claim scope');
    T=readtable(fullfile(folder,'PROCESS.csv'),'VariableNamingRule','preserve');
    M=load(fullfile(folder,'PROCESS.mat'));
    columns=cellstr(string(M.columns(:)));
    [found,order]=ismember(columns,T.Properties.VariableNames);
    assert(all(found),'CSV and MAT field mismatch');
    actual=table2array(T(:,order));
    assert(isequal(size(actual),size(M.values)) && isequal(isnan(actual),isnan(M.values)),'CSV/MAT data shape or missingness mismatch');
    diffValue=abs(actual(:)-M.values(:));
    csvMatError=max(diffValue,[],'omitnan');
    assert(csvMatError<=1e-12,'CSV and MAT values differ');
    assert(strcmp(string(M.raw_sha256),string(meta.raw_sha256)),'MAT raw binding mismatch');
    s=field(T,'start_minute');e=field(T,'end_minute');
    assert(s(1)==0 && abs(e(end)-meta.horizon_minutes)<1e-7 && all(e>s));
    assert(all(abs(s(2:end)-e(1:end-1))<1e-7),'Unknown interval gap');
    isUPS=strcmp(id,'H1');
    if isUPS
        store='ups';power='ups';color=[.12 .34 .62];unit='kWh';powerUnit='kW';
        heading='H1：有限 UPS 接续功能核验';fault='GRID + 全部 GT 不可用';
    else
        store='cold_store';power='cold';color=[.00 .43 .42];unit='kWhc';powerUnit='kWc';
        heading='H2：有限蓄冷旁路功能核验';fault='共用冷却塔不可用';
    end
    before=field(T,[store '_initial_kWh']);after=field(T,[store '_final_kWh']);
    assert(all(abs(before(2:end)-after(1:end-1))<1e-7),'Inventory reset or missing native receipt');
    inventoryX=[s(1);e];inventoryY=[before(1);after];
    cap=meta.store.capacity_kWh;
    assert(all(inventoryY>=-1e-7 & inventoryY<=cap+1e-7));
    event=(s>=meta.event_start_minute-1e-8 & s<meta.event_end_minute-1e-8);
    eventRelease=sum(field(T,[store '_discharged_kWh']).*event);
    recoveryInput=sum(field(T,[store '_charged_input_kWh']).*(s>=meta.event_end_minute-1e-8));
    depleted=find(after<=1e-6,1,'first');

    fig=figure('Color','w','Units','centimeters','Position',[1 1 24 21], ...
        'Visible','off','Renderer','painters','Name',[id ' native functional process']);
    layout=tiledlayout(fig,3,2,'TileSpacing','compact','Padding','compact');
    layout.OuterPosition=[.02 .105 .96 .825];
    annotation(fig,'textbox',[.025 .947 .95 .045],'String',heading, ...
        'FontName','宋体','FontSize',13,'FontWeight','bold','EdgeColor','none','HorizontalAlignment','center');
    annotation(fig,'textbox',[.025 .91 .95 .035], ...
        'String',['C 方案 | 90 min 功能核验 | 灰带：15–75 min，' fault], ...
        'FontName','宋体','FontSize',9.2,'EdgeColor','none','HorizontalAlignment','center');

    ax=nexttile(layout,1);setup(ax,meta,[0 cap*1.14]);
    nativeLine(ax,inventoryX,inventoryY,color,'inventory_endpoints','Native initial/final inventory endpoints', ...
        'LineStyle','none','Marker','.','MarkerSize',7);
    nativeLine(ax,[0 meta.horizon_minutes],[cap cap],[.6 .6 .6],'configured_capacity','Registered finite capacity', ...
        'LineStyle',':','LineWidth',.8);
    scatter(ax,inventoryX(end),inventoryY(end),24,'o','MarkerEdgeColor',color,'HandleVisibility','off');
    panelTitle(ax,'a','库存原生端点');ylabel(ax,mixed('库存',unit),'Interpreter','tex');
    if ~isempty(depleted)
        xline(ax,e(depleted),':','Color',color,'LineWidth',.85,'HandleVisibility','off');
        text(ax,.40,.83,sprintf('耗尽：%.2f min',e(depleted)),'Units','normalized', ...
            'FontName','宋体','FontSize',8.8,'Color',color);
    end
    text(ax,.58,.70,sprintf('事件放出：%.2f %s\n尾段输入：%.2f %s\n末端库存：%.2f %s', ...
        eventRelease,unit,recoveryInput,unit,after(end),unit),'Units','normalized', ...
        'FontName','宋体','FontSize',8.6,'VerticalAlignment','top');

    ax=nexttile(layout,2);
    grid=field(T,'grid_import_kW');gt=sum([field(T,'gt_electric_kW_1'),field(T,'gt_electric_kW_2'),field(T,'gt_electric_kW_3')],2);
    dg=field(T,'diesel_kW');setup(ax,meta,[0 upper([grid;gt;dg])]);
    h1=intervalLine(ax,s,e,grid,[.20 .20 .20],'grid_import_kW','-');
    h2=intervalLine(ax,s,e,gt,[.76 .39 .10],'sum_actual_GT_electric_kW','-');
    h3=intervalLine(ax,s,e,dg,[.45 .24 .62],'diesel_kW','-');
    panelTitle(ax,'b','实际供电端口');ylabel(ax,mixed('电功率','kW'),'Interpreter','tex');
    legend(ax,[h1 h2 h3],{'电网 GRID','燃机合计 GT','柴油机 DG'}, ...
        'FontName','宋体','FontSize',8,'Box','off','Location','northeast');

    for k=1:3
        carrier={'e','h','c'};names={'关键电服务','关键热服务','关键冷服务'};units={'kW','kW','kWc'};
        c=carrier{k};request=field(T,['P0_' c '_requested_kW'])+field(T,['P1_' c '_requested_kW']);
        delivered=field(T,['P0_' c '_delivered_kW'])+field(T,['P1_' c '_delivered_kW']);
        assert(all(delivered>=-1e-7 & delivered<=request+1e-7));
        ax=nexttile(layout,k+2);setup(ax,meta,[0 upper(request)]);
        hr=intervalLine(ax,s,e,request,[.25 .25 .25],['critical_' c '_request'],'--');
        hd=intervalLine(ax,s,e,delivered,color,['critical_' c '_delivered'],'-');
        panelTitle(ax,char('b'+k),names{k});ylabel(ax,mixed('P0 + P1',units{k}),'Interpreter','tex');
        legend(ax,[hr hd],{'原始请求','实际交付'},'FontName','宋体','FontSize',8,'Box','off','Location','southwest');
        if k==3,xlabel(ax,mixed('时间','min'),'Interpreter','tex');end
    end

    ax=nexttile(layout,6);
    pd=field(T,[power '_discharge_kW']);pc=field(T,[power '_charge_kW']);pump=field(T,'cold_storage_pump_kW');
    setup(ax,meta,[0 max(1,max([pd;pc])*1.65)]);
    hd=intervalLine(ax,s,e,pd,color,[power '_discharge_kW'],'-');
    hc=intervalLine(ax,s,e,pc,[.77 .30 .12],[power '_charge_kW'],'--');
    ylabel(ax,mixed('放充功率',powerUnit),'Interpreter','tex');
    yyaxis(ax,'right');ax.YAxis(2).Color=[.35 .35 .35];
    hp=intervalLine(ax,s,e,pump,[.35 .35 .35],'cold_storage_pump_kW',':');
    ylim(ax,[0 max(3,max(pump)*1.65)]);ylabel(ax,mixed('冷泵电','kW'),'Interpreter','tex');
    yyaxis(ax,'left');ax.YAxis(1).Color=[.15 .15 .15];
    panelTitle(ax,'f','实际放充与冷泵耗电');xlabel(ax,mixed('时间','min'),'Interpreter','tex');
    legend(ax,[hd hc hp],{'放出功率','充入功率','冷泵电功率'},'FontName','宋体','FontSize',8,'Box','off','Location','northeast');

    annotation(fig,'textbox',[.025 .025 .95 .060], ...
        'String',{'仅为本条 90 min、C 方案功能核验；未配对 H0，不能据此给出硬件收益或普遍保障时长。', ...
        '请求/交付/功率为实际原生区间阶梯；库存只画原生端点，无插值。尾段未完成充满保留原状。'}, ...
        'FontName','宋体','FontSize',8.8,'EdgeColor','none','HorizontalAlignment','center');
    % Tiled-layout shared empty labels otherwise retain MATLAB's Helvetica.
    % Set every remaining default object explicitly; preserve Chinese Songti.
    defaults=findall(fig,'-property','FontName');
    for j=1:numel(defaults)
        if strcmp(defaults(j).FontName,'Helvetica'),defaults(j).FontName='Times New Roman';end
    end
    drawnow;
    basename=fullfile(outputRoot,[id '_functional_process']);
    savefig(fig,[basename '.fig']);
    print(fig,[basename '.svg'],'-dsvg');
    exportgraphics(fig,[basename '.pdf'],'ContentType','vector','BackgroundColor','white');
    exportgraphics(fig,[basename '.png'],'Resolution',600,'BackgroundColor','white');
    close(fig);

    % Reopen the actual delivered FIG and compare editable object data exactly.
    reopened=openfig([basename '.fig'],'invisible');
    lineObjects=findall(reopened,'Type','line');checked=0;
    for j=1:numel(lineObjects)
        data=lineObjects(j).UserData;
        if isstruct(data) && isfield(data,'expected_x')
            assert(isequaln(lineObjects(j).XData(:),data.expected_x(:)) && ...
                isequaln(lineObjects(j).YData(:),data.expected_y(:)),'Editable FIG line differs from native source');
            checked=checked+1;
        end
    end
    assert(checked==14,'Unexpected native data line count');
    assert(isempty(findall(reopened,'Type','image')),'Raster object in editable FIG');
    allFontObjects=findall(reopened,'-property','FontName');
    usedFonts=unique(string(get(allFontObjects,'FontName')));
    assert(all(ismember(usedFonts,["Times New Roman","宋体"])),'Unexpected or fallback font');
    entry=struct('configuration_id',id,'native_intervals',height(T),'inventory_endpoint_count',numel(inventoryX), ...
        'observed_start_minute',s(1),'observed_end_minute',e(end),'request_delivery_resampled',false, ...
        'inventory_interpolated',false,'reopened_native_lines_checked',checked,'raster_objects',0, ...
        'csv_mat_max_absolute_difference',csvMatError,'raw_sha256',meta.raw_sha256, ...
        'actual_event_release_kWh',eventRelease,'actual_recovery_input_kWh',recoveryInput, ...
        'actual_final_inventory_kWh',after(end),'full_recharge_observed',abs(after(end)-cap)<=1e-7, ...
        'fonts',{cellstr(usedFonts)},'functional_check_only',true,'paired_H0',false, ...
        'png_resolution_dpi',600,'figure_files',{{[basename '.fig'],[basename '.svg'],[basename '.pdf'],[basename '.png']}});
    if isempty(depleted),entry.depletion_native_endpoint_minute=[];else,entry.depletion_native_endpoint_minute=e(depleted);end
    qa=[qa;entry]; %#ok<AGROW>
    close(reopened);
end
receipt=struct('status','passed','backend','MATLAB','matlab_version',version,'physical_attempts_started',0,'cases',qa);
fid=fopen(fullfile(outputRoot,'MATLAB_FIGURE_QA.json'),'w','n','UTF-8');
fwrite(fid,jsonencode(receipt),'char');fclose(fid);
disp(jsonencode(receipt));
end

function value=field(T,name)
assert(ismember(name,T.Properties.VariableNames),['Missing native field: ' name]);
value=T.(name);assert(isnumeric(value)&&all(isfinite(value)),['Missing/nonfinite native field: ' name]);
value=value(:);
end

function top=upper(value)
top=max(value)*1.23;if top<1,top=1;end
end

function setup(ax,meta,limits)
hold(ax,'on');box(ax,'on');
patch(ax,[meta.event_start_minute meta.event_end_minute meta.event_end_minute meta.event_start_minute], ...
    [limits(1) limits(1) limits(2) limits(2)],[.94 .94 .94],'EdgeColor','none','HandleVisibility','off');
xlim(ax,[0 meta.horizon_minutes]);ylim(ax,limits);xticks(ax,0:15:meta.horizon_minutes);
set(ax,'FontName','Times New Roman','FontSize',9,'TickDir','out','LineWidth',.7,'Layer','top');
xline(ax,meta.event_start_minute,':','Color',[.65 .65 .65],'LineWidth',.7,'HandleVisibility','off');
xline(ax,meta.event_end_minute,':','Color',[.65 .65 .65],'LineWidth',.7,'HandleVisibility','off');
end

function panelTitle(ax,letter,label)
title(ax,['\fontname{Times New Roman}' letter ') \fontname{宋体}' label], ...
    'Interpreter','tex','FontSize',10,'FontWeight','normal');
end

function label=mixed(chinese,unit)
label=['\fontname{宋体}' chinese '\fontname{Times New Roman} (' unit ')'];
end

function h=intervalLine(ax,s,e,value,color,name,style)
% Duplicate only existing native interval bounds, preserving exact discontinuities.
x=reshape([s e].',[],1);y=repelem(value,2);
h=nativeLine(ax,x,y,color,name,'Actual native half-open execution intervals', ...
    'LineStyle',style,'LineWidth',1.25);
end

function h=nativeLine(ax,x,y,color,name,semantics,varargin)
h=plot(ax,x,y,'Color',color,varargin{:});
h.Tag=name;h.UserData=struct('source_quantity',name,'semantics',semantics, ...
    'expected_x',x,'expected_y',y);
end
