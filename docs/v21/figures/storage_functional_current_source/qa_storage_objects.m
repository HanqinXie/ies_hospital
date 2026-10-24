function qa_storage_objects(dataRoot,figureRoot,outputFile)
% Read-only FIG validation against separate original CSV fields, not UserData.
assert(~isfile(outputFile),'Create-only QA output required');
details=struct([]);
for id={'H1','H2'}
    config=id{1};
    meta=jsondecode(fileread(fullfile(dataRoot,config,'PLOT_CASE.json')));
    T=readtable(fullfile(dataRoot,config,'PROCESS.csv'),'VariableNamingRule','preserve');
    assert(height(T)>0 && meta.functional_check_only && ~meta.paired_H0);
    if strcmp(config,'H1'),store='ups';power='ups';else,store='cold_store';power='cold';end
    s=T.start_minute;e=T.end_minute;intervalX=reshape([s e].',[],1);
    before=T.([store '_initial_kWh']);after=T.([store '_final_kWh']);
    tags={'inventory_endpoints','configured_capacity','grid_import_kW', ...
        'sum_actual_GT_electric_kW','diesel_kW','critical_e_request', ...
        'critical_e_delivered','critical_h_request','critical_h_delivered', ...
        'critical_c_request','critical_c_delivered',[power '_discharge_kW'], ...
        [power '_charge_kW'],'cold_storage_pump_kW'};
    expectedX={[s(1);e],[0;meta.horizon_minutes]};
    expectedY={[before(1);after],[meta.store.capacity_kWh;meta.store.capacity_kWh]};
    values={T.grid_import_kW,T.gt_electric_kW_1+T.gt_electric_kW_2+T.gt_electric_kW_3,T.diesel_kW, ...
        T.P0_e_requested_kW+T.P1_e_requested_kW,T.P0_e_delivered_kW+T.P1_e_delivered_kW, ...
        T.P0_h_requested_kW+T.P1_h_requested_kW,T.P0_h_delivered_kW+T.P1_h_delivered_kW, ...
        T.P0_c_requested_kW+T.P1_c_requested_kW,T.P0_c_delivered_kW+T.P1_c_delivered_kW, ...
        T.([power '_discharge_kW']),T.([power '_charge_kW']),T.cold_storage_pump_kW};
    for k=1:numel(values)
        expectedX{end+1}=intervalX; %#ok<AGROW>
        expectedY{end+1}=repelem(values{k},2); %#ok<AGROW>
    end
    assert(numel(tags)==14 && numel(expectedX)==14 && numel(expectedY)==14);
    fig=openfig(fullfile(figureRoot,[config '_functional_process.fig']),'invisible');
    closeOnError=onCleanup(@() close(fig));
    allLines=findall(fig,'Type','line');
    tagged=allLines(arrayfun(@(x) ~isempty(x.Tag),allLines));
    assert(numel(tagged)==14 && numel(unique(string({tagged.Tag})))==14,'Exact source line set required');
    checks=struct([]);
    for k=1:14
        obj=findall(fig,'Type','line','Tag',tags{k});
        assert(numel(obj)==1,['Missing/duplicate native line: ' tags{k}]);
        assert(isequaln(obj.XData(:),expectedX{k}(:)),['CSV x mismatch: ' tags{k}]);
        assert(isequaln(obj.YData(:),expectedY{k}(:)),['CSV y mismatch: ' tags{k}]);
        checks=[checks;struct('tag',tags{k},'points',numel(expectedX{k}), ...
            'source_csv_x_exact',true,'source_csv_y_exact',true)]; %#ok<AGROW>
    end
    assert(isempty(findall(fig,'Type','image')),'Unexpected raster object');
    fontObjects=findall(fig,'-property','FontName');
    fonts=unique(string(get(fontObjects,'FontName')));
    assert(all(ismember(fonts,["Times New Roman","宋体"])),'Unexpected font');
    details=[details;struct('configuration_id',config,'native_intervals',height(T), ...
        'line_checks',checks,'all_source_lines_exact',true,'raster_objects',0, ...
        'fonts',{cellstr(fonts)},'functional_check_only',true,'paired_H0',false)]; %#ok<AGROW>
    clear closeOnError;
end
assert(numel(details)==2,'Both registered functional checks required');
result=struct('status','passed','backend','MATLAB','version',version, ...
    'source_comparison_uses_UserData',false,'physics_attempts',0,'cases',details);
f=fopen(outputFile,'w','n','UTF-8');assert(f>=0);fwrite(f,jsonencode(result),'char');fclose(f);
disp(jsonencode(result));
end
