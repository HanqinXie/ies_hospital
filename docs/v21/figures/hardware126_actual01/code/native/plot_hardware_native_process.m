function receipt=plot_hardware_native_process(manifestPath,knownManifestSHA,outputRoot)
% Formal HW18 from real native PROCESS only. Preparation version: NOT RUN.
% Field/CSV-MAT/intervalLine/nativeLine semantics follow original source
% 897b6f8ea46b6bc051f575f5375b2e5000221ad44606b6f5b2d799fc3da16dd2.
arguments
    manifestPath (1,1) string
    knownManifestSHA (1,1) string
    outputRoot (1,1) string
end
assert(hashFile(manifestPath)==knownManifestSHA,'Externally trusted HW18 manifest differs.');
M=jsondecode(fileread(manifestPath));base=fileparts(manifestPath);
assert(string(M.schema)=="hospital_ies.hardware18_actual_process_manifest.v1");
assert(string(M.source_binding_sha256)=="4dfe62ae105d2696b7782dc36c83ec63c6551e2cded601d6f15c0d4083058add");
assert(M.whole_hardware_pool_complete&&M.source_CASE_count==126&&numel(M.cases)==18&&~M.science_gate);
assert(~isfolder(outputRoot),'Fresh hardware figure directory required.');mkdir(outputRoot);
available=listfonts;assert(any(strcmpi(available,'Times New Roman'))&&any(strcmpi(available,'宋体')));
set(groot,'defaultAxesFontName','Times New Roman','defaultTextFontName','Times New Roman');
details=cell(18,1);identities=strings(18,1);
for n=1:18
    c=M.cases(n);identities(n)=string(c.scenario_id)+"/"+string(c.policy_id)+"/"+string(c.configuration_id);
end
assert(numel(unique(identities))==18,'Duplicate predeclared HW18 identity.');
try
for n=1:18
    c=M.cases(n);folder=fullfile(base,string(c.folder));csvPath=fullfile(folder,'PROCESS.csv');matPath=fullfile(folder,'PROCESS.mat');
    assert(c.window_minutes==360&&c.event_start_minute==60&&c.event_end_minute==180&&string(c.physical_result_status)=="completed");
    assert(hashFile(csvPath)==string(c.csv_sha256)&&hashFile(matPath)==string(c.mat_sha256));
    T=readtable(csvPath,'VariableNamingRule','preserve');A=load(matPath);columns=cellstr(string(A.columns(:)));
    [found,order]=ismember(columns,T.Properties.VariableNames);assert(all(found));values=table2array(T(:,order));
    assert(isequal(size(values),size(A.values))&&isequal(isnan(values),isnan(A.values)),'CSV/MAT shape or unknown mask differs');
    errorValue=abs(values(:)-A.values(:));assert(max(errorValue,[],'omitnan')<=1e-12,'CSV/MAT numeric mismatch');
    assert(string(A.raw_sha256)==string(c.raw_sha256),'Native MAT raw identity differs');
    s=field(T,'start_minute');e=field(T,'end_minute');
    assert(s(1)==0&&abs(e(end)-c.window_minutes)<1e-7&&all(e>s)&&all(abs(s(2:end)-e(1:end-1))<1e-7));
    f=figure('Visible','off','Color','w','Units','centimeters','Position',[1 1 30 25]);
    L=tiledlayout(f,3,3,'TileSpacing','compact','Padding','loose');L.OuterPosition=[.02 .08 .96 .85];
    requestedNames={'关键电服务','关键热服务','关键冷服务'};units={'kW_e','kW_h','kW_c'};
    for k=1:3
        carriers={'e','h','c'};carrier=carriers{k};ax=nexttile(L,k);setup(ax,c);
        request=field(T,['P0_' carrier '_requested_kW'])+field(T,['P1_' carrier '_requested_kW']);
        delivered=field(T,['P0_' carrier '_delivered_kW'])+field(T,['P1_' carrier '_delivered_kW']);
        assert(all(delivered>=-1e-7&delivered<=request+1e-7));
        a=intervalLine(ax,s,e,request,[.25 .25 .25],['critical_' carrier '_requested'],'--');
        b=intervalLine(ax,s,e,delivered,[.00 .43 .42],['critical_' carrier '_delivered'],'-');
        title(ax,requestedNames{k},'FontName','宋体');ylabel(ax,mixed('P0+P1',units{k}),'Interpreter','tex');
        legend(ax,[a b],{'原请求','实际交付'},'FontName','宋体','Box','off','Location','best');
    end
    ax=nexttile(L,4);setup(ax,c);
    grid=field(T,'grid_import_kW');gt=field(T,'gt_electric_kW_1')+field(T,'gt_electric_kW_2')+field(T,'gt_electric_kW_3');
    dg=field(T,'diesel_kW');ups=field(T,'ups_discharge_kW');
    h(1)=intervalLine(ax,s,e,grid,[.2 .2 .2],'grid_import_kW','-');
    h(2)=intervalLine(ax,s,e,gt,[.76 .39 .1],'actual_GT_total_kW','-');
    h(3)=intervalLine(ax,s,e,dg,[.45 .24 .62],'diesel_kW','-');
    h(4)=intervalLine(ax,s,e,ups,[.12 .34 .62],'ups_source_discharge_kW','-');
    title(ax,'实际电源端口','FontName','宋体');ylabel(ax,mixed('电功率','kW_e'),'Interpreter','tex');
    legend(ax,h,{'GRID','GT','DG','UPS 放电'},'FontName','宋体','Box','off','Location','best');
    ax=nexttile(L,5);setup(ax,c);
    a=intervalLine(ax,s,e,field(T,'ups_discharge_kW'),[.12 .34 .62],'ups_discharge_kW','-');
    b=intervalLine(ax,s,e,field(T,'ups_charge_kW'),[.77 .30 .12],'ups_charge_kW','--');
    title(ax,'UPS 实际充放功率','FontName','宋体');ylabel(ax,mixed('电功率','kW_e'),'Interpreter','tex');
    legend(ax,[a b],{'放电','充电输入'},'FontName','宋体','Box','off','Location','best');
    ax=nexttile(L,6);setup(ax,c);
    a=intervalLine(ax,s,e,field(T,'cold_discharge_kW'),[.00 .43 .42],'cold_discharge_kW','-');
    b=intervalLine(ax,s,e,field(T,'cold_charge_kW'),[.77 .30 .12],'cold_charge_kW','--');
    ylabel(ax,mixed('冷功率','kW_c'),'Interpreter','tex');yyaxis(ax,'right');
    d=intervalLine(ax,s,e,field(T,'cold_storage_pump_kW'),[.35 .35 .35],'cold_storage_pump_kW',':');
    ylabel(ax,mixed('冷储泵电','kW_e'),'Interpreter','tex');yyaxis(ax,'left');
    title(ax,'蓄冷实际充放与泵电','FontName','宋体');
    legend(ax,[a b d],{'放冷','充冷输入','冷储泵电'},'FontName','宋体','Box','off','Location','best');
    prefixes={'ups','cold_store','diesel'};tags={'ups_inventory_kWh','cold_inventory_kWh','dg_shaft_inventory_kWh'};
    labels={'UPS 电储量','独立冷储量','DG 轴功等价库存'};storeUnits={'kWh_e','kWh_c','kWh_shaft'};
    for k=1:3
        ax=nexttile(L,k+6);setup(ax,c);
        if k==3,before=field(T,'pre_diesel_energy_remaining_kWh');after=field(T,'post_diesel_energy_remaining_kWh');
        else,before=field(T,[prefixes{k} '_initial_kWh']);after=field(T,[prefixes{k} '_final_kWh']);end
        assert(all(abs(before(2:end)-after(1:end-1))<1e-7),'Native inventory discontinuity');
        nativeLine(ax,[s(1);e],[before(1);after],[.12 .34 .62],tags{k},'Native initial/final inventory endpoints', ...
            'LineStyle','none','Marker','.','MarkerSize',6);
        title(ax,labels{k},'FontName','宋体');ylabel(ax,mixed('库存',storeUnits{k}),'Interpreter','tex');
    end
    title(L,string(c.policy_id)+" / "+string(c.configuration_id)+" / "+string(c.fault_target)+" / 360 min",'Interpreter','none','FontName','Times New Roman','FontSize',11);
    annotation(f,'textbox',[.02 .01 .96 .045],'String', ...
        {'正式硬件预声明代表；登记事件窗为60–180 min，边界线不是设备实际就绪时刻。', ...
        '全部功率为原生区间实值；库存仅画原生端点，不用库存差补造功率；H0零硬件值亦来自实际CSV。'}, ...
        'FontName','宋体','FontSize',9,'EdgeColor','none','HorizontalAlignment','center');
    fontObjects=findall(f,'-property','FontName');
    for j=1:numel(fontObjects),if strcmp(fontObjects(j).FontName,'Helvetica'),fontObjects(j).FontName='Times New Roman';end,end
    f.UserData=struct('schema','hospital_ies.hardware_native_process_figure.v1','manifest_sha256',knownManifestSHA, ...
        'scenario_id',c.scenario_id,'policy_id',c.policy_id,'configuration_id',c.configuration_id,'actual_source',folder, ...
        'no_synthetic_data',true,'native_intervals_preserved',true,'functional_check_only',false);
    drawnow;basename=fullfile(outputRoot,string(c.output_name));savefig(f,basename+'.fig');
    print(f,basename+'.svg','-dsvg');exportgraphics(f,basename+'.pdf','ContentType','vector','BackgroundColor','white');
    exportgraphics(f,basename+'.png','Resolution',600,'BackgroundColor','white');close(f);
    % Match by stable Tag against independently read native CSV/MAT vectors.
    proof=openfig(basename+'.fig','invisible');v=c.expected_vectors.series;
    lines=findall(proof,'Type','line');assert(numel(v)==18&&numel(lines)==18);
    for j=1:numel(v)
        target=findall(proof,'Type','line','Tag',string(v(j).tag));assert(isscalar(target),'Ambiguous source quantity');
        assert(isequaln(target.XData(:),v(j).x(:))&&isequaln(target.YData(:),v(j).y(:)),'Actual FIG/native source vector mismatch');
        assert(target.Parent.Layout.Tile==v(j).tile);
    end
    assert(isempty(findall(proof,'Type','image')));fonts=findall(proof,'-property','FontName');
    for j=1:numel(fonts),assert(ismember(string(fonts(j).FontName),["宋体","Times New Roman"]));end
    formats=struct;for ext=["fig","pdf","svg","png"],p=basename+'.'+ext;info=dir(p);formats.(ext)=struct('path',p,'bytes',info.bytes,'sha256',hashFile(p));end
    details{n}=struct('file',basename+'.fig','status','passed','figure_size_cm',proof.Position(3:4),'formats',formats, ...
        'source_csv_sha256',string(c.csv_sha256),'native_series_checked',18,'request_power_inventory_interpolated',false);
    close(proof);assert(hashFile(csvPath)==string(c.csv_sha256)&&hashFile(matPath)==string(c.mat_sha256));
end
assert(hashFile(manifestPath)==knownManifestSHA);
receipt=struct('schema','hospital_ies.matlab_object_audit.v2','auditor','hardware_process_v1 source-vector reopen; not qa_sci_artifacts', ...
    'figure_count',18,'matched_figure_count',18,'details',{details},'object_checks_passed',true, ...
    'all_expected_matched',true,'failures',{{}},'manifest_sha256',knownManifestSHA,'scientific_gate',false,'physics_started',0);
writeJSON(fullfile(outputRoot,'HW18_MATLAB_OBJECT_QA.json'),receipt);
catch error
    writeJSON(fullfile(outputRoot,'HW18_MATLAB_FAILURE.json'),struct('status','failed','identifier',error.identifier,'message',error.message,'partial_outputs_retained',true));
    rethrow(error);
end
end

function value=field(T,name)
assert(ismember(name,T.Properties.VariableNames),['Missing native field: ' name]);
value=T.(name);assert(isnumeric(value)&&all(isfinite(value)),['Missing/nonfinite native field: ' name]);
value=value(:);
end

function setup(ax,c)
hold(ax,'on');box(ax,'on');xlim(ax,[0 c.window_minutes]);xticks(ax,0:60:c.window_minutes);
set(ax,'FontName','Times New Roman','FontSize',9,'TickDir','out','LineWidth',.7);
xline(ax,c.event_start_minute,':','Color',[.65 .65 .65],'HandleVisibility','off');
xline(ax,c.event_end_minute,':','Color',[.65 .65 .65],'HandleVisibility','off');
xlabel(ax,mixed('时间','min'),'Interpreter','tex');
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

function value=hashFile(path)
fid=fopen(path,'rb');assert(fid>=0);cleaner=onCleanup(@()fclose(fid));digest=java.security.MessageDigest.getInstance('SHA-256');
while true,b=fread(fid,1048576,'*uint8');if isempty(b),break;end;digest.update(typecast(b,'int8'));end
value=string(lower(reshape(dec2hex(typecast(digest.digest(),'uint8'),2)',1,[])));
end

function writeJSON(path,value)
fid=fopen(path,'w','n','UTF-8');assert(fid>=0);cleaner=onCleanup(@()fclose(fid));fprintf(fid,'%s',jsonencode(value,'PrettyPrint',true));
end
