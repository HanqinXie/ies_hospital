function report = qa_temperature_objects(figurePath, sourceRoot, reportPath)
% Read-only FIG object QA against the two unchanged held-out source CSVs.
% No plotting, interpolation, fitted-proxy deployment, savefig, or physics.
assert(nargin==3,'Explicit figurePath, sourceRoot and new reportPath are required');
assert(~isfile(reportPath) && ~isfolder(reportPath),'QA report must be create-only');
expectedRoot='D:/hospital_ies_v21_20261023/docs/v21/figures/temperature_validation/source_data';
assert(samePath(sourceRoot,expectedRoot),'Use the original immutable compact source CSV directory');
plotter='E:/hospital_ies_v21_20261023/reviews/temperature_figure_current_source_review01/publication_source/plot_temperature_validation_publication.m';
plotterSHA="2adac32b2ca73eee5bb550e7879bfc8b260d796442a062f5236fda6c319129ad";
cases={'val_winter_2h','val_summer_4h'};
csvNames={[cases{1} '_unchanged_production_proxy.csv'],[cases{2} '_unchanged_production_proxy.csv'],'TEMPERATURE_VALIDATION_RESULTS.csv'};
csvSHA=["8a60c89b9f11b77e8c0d77d4f115a66fbff0e89ef1dd0cfc221fee5d6b7efbc1", ...
    "adc70ac882115447f0bfc442e92ff32c2e8b011b7c29ddbbde6ec4f7de4ab1db", ...
    "d2ee115857b70cad9c332d1fcf10caf47f261148b21b3ccc1ca2b0b44f1f51cc"];
report=struct('schema','hospital_ies.temperature_native_objects_qa.v1','status','not_run', ...
    'figure_path',figurePath,'source_root',sourceRoot,'plotter_path',plotter, ...
    'plotter_sha256',char(plotterSHA),'interpolation',false,'physical_attempts_started',0, ...
    'MATLAB_figures_generated',0,'comparison','exact isequal X/Y from immutable CSV; no numerical tolerance', ...
    'plotted_samples_used_as_authority',false,'scientific_temperature_validation_claimed',false);
try
    assert(hashFile(plotter)==plotterSHA,'Publication plotter bytes differ from reviewed source');
    for j=1:numel(csvNames),assert(hashFile(fullfile(sourceRoot,csvNames{j}))==csvSHA(j),'Bound source CSV changed');end
    figureSHA=hashFile(figurePath);
    nativeReceiptPath=fullfile(fileparts(figurePath),'temperature_figure_receipt.json');
    nativeReceiptSHA=hashFile(nativeReceiptPath);
    native=jsondecode(fileread(nativeReceiptPath));
    assert(samePath(native.source_root,sourceRoot),'Original generation receipt names another source');
    assert(isequal(string(native.case_ids(:)),string(cases(:))),'Wrong held-out cases');
    assert(isequal(native.native_step_minutes,10) && isequal(native.plotted_rows,158),'Wrong native clock/source-row count');
    assert(isequal(native.resampled,false) && isequal(native.production_proxy_changed,false),'Proxy or sampling changed');
    assert(isequal(native.quantitative_validation_passed,false) && isequal(native.clinical_threshold_asserted,false), ...
        'Negative validation/clinical-scope flags must remain false');
    summary=readtable(fullfile(sourceRoot,csvNames{3}),'TextType','string','VariableNamingRule','preserve');
    f=openfig(figurePath,'new','invisible');cleanup=onCleanup(@()close(f)); %#ok<NASGU>
    assert(isempty(findall(f,'Type','image')),'FIG contains raster image objects');
    axesObjects=findall(f,'Type','axes');assert(numel(axesObjects)==4,'Exactly four data axes required');
    assert(numel(findall(f,'Type','line'))==10,'Exactly ten native data lines required');
    assert(numel(findall(f,'Type','patch'))==4,'Exactly four outage shading patches required');
    assert(isequal(native.line_objects,10) && isequal(native.image_objects,0),'Generation receipt object counts differ');
    caseReports=cell(1,2);sourceRows=0;seriesPairs=0;
    names={'Held-out winter: 2 h cooling loss','Held-out summer: 4 h cooling loss'};
    for k=1:2
        data=readtable(fullfile(sourceRoot,csvNames{k}),'TextType','string','VariableNamingRule','preserve');
        stamps=asDate(data.timestamp,'yyyy-MM-dd HH:mm:ss');
        assert(all(minutes(diff(stamps))==10),'Source must preserve exact 10-minute native endpoints');
        active=strcmpi(string(data.outage_active),'True') | strcmp(string(data.outage_active),'1');
        assert(any(active),'No registered cooling-loss interval in source');
        start=stamps(find(active,1))-minutes(10);last=stamps(find(active,1,'last'));
        outageHours=hours(last-start);
        row=summary(summary.case_id==string(cases{k}) & summary.parameter_label=="unchanged_production_proxy",:);
        assert(height(row)==1 && row.split=="validation",'Exact held-out unchanged production row required');
        assert(row.mass_participation==.5 && row.effective_U_W_m2K==1.5,'Fitted candidate is not production evidence');
        assert(row.zone=="OR1_FLR_2" && row.modeled_airloop=="VAV_OR",'Wrong zone/airloop scope');
        assert(start==asDate(row.event_start,'yyyy-MM-dd''T''HH:mm:ss') && last==asDate(row.event_end,'yyyy-MM-dd''T''HH:mm:ss'), ...
            'CSV endpoint-minus-10min clock differs from independently corrected registered clock');
        assert(isequal(active,stamps>start & stamps<=last),'Outage endpoint convention or active mask changed');
        assert(outageHours==row.outage_hours && outageHours==2*k,'Expected 2 h winter / 4 h summer outage');
        relative=hours(stamps-start);selected=relative>=-1 & relative<=12;x=relative(selected);
        assert(numel(x)==79 && x(1)==-1 && x(end)==12,'Native plotted source rows/window differ');
        actual=data.independent_temperature_C(selected);proxy=data.surrogate_temperature_C(selected);
        baseline=data.baseline_temperature_C(selected);criterion=data.cooling_setpoint_C(selected)+2;
        temperature=axisByTile(axesObjects,k);errorAxis=axisByTile(axesObjects,k+2);
        verifyLines(temperature,x,{actual,proxy,baseline,criterion});
        verifyLines(errorAxis,x,{proxy-actual});
        ymax=ceil(max([actual;proxy;criterion])+1);
        verifyAxis(temperature,outageHours,[20 ymax],false);
        verifyAxis(errorAxis,outageHours,[-8 8],true);
        assert(string(temperature.Title.String)==string([char('a'+k-1) ') ' names{k}]),'Case title differs');
        assert(string(errorAxis.Title.String)==string(sprintf('%c) Error; whole-tail max |error| = %.3f °C','c'+k-1,row.maximum_absolute_temperature_error_C)), ...
            'Whole-tail maximum label is not from unchanged source summary');
        textObjects=findall(temperature,'Type','text');stringsHere=strings(0,1);
        for h=textObjects(:)',value=string(h.String);stringsHere=[stringsHere;value(:)];end %#ok<AGROW>
        assert(sum(stringsHere==string(sprintf('Whole-tail RMSE: %.3f °C',row.rms_temperature_error_C)))==1, ...
            'Whole-tail RMSE label missing or recomputed from displayed 12 h subset');
        assert(string(temperature.YLabel.String)=="Zone temperature (°C)" && string(errorAxis.YLabel.String)=="Proxy minus EnergyPlus (°C)", ...
            'Axis carrier/temperature-error meaning differs');
        assert(string(errorAxis.XLabel.String)=="Hours after cooling interruption",'Event-relative clock label differs');
        sourceRows=sourceRows+numel(x);seriesPairs=seriesPairs+5*numel(x);
        caseReports{k}=struct('case_id',cases{k},'source_csv',fullfile(sourceRoot,csvNames{k}),'source_csv_sha256',char(csvSHA(k)), ...
            'native_full_rows',height(data),'plotted_source_rows',numel(x),'data_series',5,'exact_XY_pairs',5*numel(x), ...
            'event_start',char(string(start,'yyyy-MM-dd HH:mm:ss')),'event_end',char(string(last,'yyyy-MM-dd HH:mm:ss')), ...
            'outage_hours',outageHours,'production_mass_participation',row.mass_participation,'production_effective_U_W_m2K',row.effective_U_W_m2K);
    end
    legendObjects=findall(f,'Type','legend');assert(numel(legendObjects)==1,'Exactly one native legend required');
    expectedLegend=["Independent EnergyPlus";"Unchanged 1R1C proxy";"Paired normal baseline";"Research criterion: setpoint +2°C"];
    assert(isequal(string(legendObjects.String(:)),expectedLegend),'Legend substituted fitted/other curves');
    footnote='{\fontname{SimSun}探索性温度代理：定量验证未通过；设定点 }{\fontname{Times New Roman}+2°C}{\fontname{SimSun} 为研究判据，不是临床安全阈值。}';
    textObjects=findall(f,'-property','String');footnoteCount=0;
    for h=textObjects(:)'
        value=string(h.String);
        if isscalar(value) && value==string(footnote)
            assert(string(h.FontName)=="Times New Roman" && string(h.Interpreter)=="tex",'Footnote mixed-script native font differs');
            footnoteCount=footnoteCount+1;
        end
    end
    assert(footnoteCount==1,'Exact negative-result / nonclinical mixed-font footnote is missing');
    fontReport=verifyFonts(f);
    assert(sourceRows==158 && seriesPairs==790,'Expected 158 source rows and 790 data-series XY pairs');
    assert(hashFile(figurePath)==figureSHA && hashFile(nativeReceiptPath)==nativeReceiptSHA,'FIG/receipt changed during read-only QA');
    assert(hashFile(plotter)==plotterSHA,'Plotter changed during QA');
    for j=1:numel(csvNames),assert(hashFile(fullfile(sourceRoot,csvNames{j}))==csvSHA(j),'CSV changed during QA');end
    report.status='passed';report.figure_sha256=char(figureSHA);report.native_generation_receipt_path=nativeReceiptPath;
    report.native_generation_receipt_sha256=char(nativeReceiptSHA);report.cases=caseReports;
    report.native_data_axes=4;report.native_data_lines=10;report.outage_patches=4;report.image_objects=0;
    report.plotted_source_rows=sourceRows;report.exact_series_XY_pairs=seriesPairs;report.fonts=fontReport;
    report.production_proxy_unchanged=true;report.fitted_candidate_deployed=false;
    report.quantitative_temperature_validation_passed=false;report.clinical_threshold_asserted=false;
    report.current_plotter_bytes_checked=true;report.plotter_execution_origin_requires_external_generation_log=true;
    report.PDF_SVG_fonts_and_visual_QA='NOT_RUN by this FIG-only checker';
    writeNewJSON(reportPath,report);
catch err
    report.status='failed';report.error_identifier=err.identifier;report.error_message=err.message;
    report.error_stack=err.stack;writeNewJSON(reportPath,report);rethrow(err);
end
end

function values=asDate(value,format)
if isdatetime(value),values=value;else,values=datetime(string(value),'InputFormat',format);end
end

function a=axisByTile(objects,tile)
matches=false(size(objects));
for j=1:numel(objects),matches(j)=isequal(objects(j).Layout.Tile,tile);end
assert(nnz(matches)==1,'Data axis tile is absent or ambiguous');a=objects(matches);
end

function verifyLines(a,x,ys)
lines=findall(a,'Type','line');assert(numel(lines)==numel(ys),'Wrong data-line count in axis');used=false(size(lines));
for k=1:numel(ys)
    match=false(size(lines));
    for j=1:numel(lines)
        match(j)=~used(j) && isequal(double(lines(j).XData(:)),double(x(:))) && isequal(double(lines(j).YData(:)),double(ys{k}(:)));
    end
    assert(nnz(match)==1,'Source CSV exact X/Y series missing, changed, duplicated or interpolated');used(match)=true;
end
assert(all(used),'Unexpected data line');
end

function verifyAxis(a,outage,limits,isError)
assert(isequal(double(a.XLim),[-1 12]) && isequal(double(a.XTick),[0 2 4 6 8 10 12]),'Displayed native clock limits/ticks changed');
assert(isequal(double(a.YLim),limits),'Temperature/error display bounds changed');
patches=findall(a,'Type','patch');assert(numel(patches)==1,'Exactly one gray outage patch per axis required');
assert(isequal(double(patches.XData(:)),[0;outage;outage;0]) && isequal(double(patches.YData(:)),[limits(1);limits(1);limits(2);limits(2)]), ...
    'Gray patch does not span exact registered outage/window');
assert(isequal(double(patches.FaceColor),[.94 .94 .94]) && string(patches.EdgeColor)=="none",'Outage patch appearance differs');
constants=findall(a,'Type','constantline');vertical=[];horizontal=[];
for h=constants(:)'
    if string(h.InterceptAxis)=="x",vertical(end+1)=h.Value; %#ok<AGROW>
    elseif string(h.InterceptAxis)=="y",horizontal(end+1)=h.Value; %#ok<AGROW>
    else,error('Unknown ConstantLine intercept axis');end
end
assert(isequal(sort(vertical),[0 outage]),'Outage vertical boundaries missing or shifted');
if isError
    assert(isequal(horizontal,0) && isequal(double(a.YTick),-8:4:8),'Error zero reference/scale differs');
else,assert(isempty(horizontal),'Unexpected horizontal reference in temperature axis');end
end

function result=verifyFonts(f)
objects=findall(f,'-property','FontName');checked=0;nonrendered=0;
for h=objects(:)'
    rendered=true;
    if isprop(h,'String'),rendered=any(strlength(string(h.String))>0,'all');
    elseif isprop(h,'Label') && isa(h,'matlab.graphics.chart.decoration.ConstantLine'),rendered=any(strlength(string(h.Label))>0,'all');end
    if ~rendered,nonrendered=nonrendered+1;continue;end
    assert(ismember(string(h.FontName),["Times New Roman","SimSun"]),'Unexpected rendered native font');checked=checked+1;
end
axesObjects=findall(f,'Type','axes');
for a=axesObjects(:)'
    assert(string(a.FontName)=="Times New Roman" && string(a.XAxis.FontName)=="Times New Roman" && string(a.YAxis.FontName)=="Times New Roman", ...
        'Numeric axes/rulers must use Times New Roman');
end
result=struct('rendered_font_objects_checked',checked,'empty_nonrendered_font_objects_skipped',nonrendered, ...
    'native_fonts',{{'Times New Roman','SimSun'}},'Chinese_TeX_runs','Exact SimSun footnote runs; +2°C exact Times New Roman run');
end

function yes=samePath(a,b)
yes=strcmpi(strrep(char(a),'\','/'),strrep(char(b),'\','/'));
end

function value=hashFile(path)
fid=fopen(path,'rb');assert(fid>=0,'Missing bound input: %s',path);cleanup=onCleanup(@()fclose(fid)); %#ok<NASGU>
md=java.security.MessageDigest.getInstance('SHA-256');
while true,b=fread(fid,1024*1024,'*uint8');if isempty(b),break;end;md.update(typecast(b,'int8'));end
value=string(lower(reshape(dec2hex(typecast(md.digest(),'uint8'),2)',1,[])));
end

function writeNewJSON(path,value)
assert(~isfile(path) && ~isfolder(path),'QA report already exists');
file=java.io.File(path);assert(file.createNewFile(),'Cannot create a new QA report');
[fid,message]=fopen(path,'w','n','UTF-8');assert(fid>=0,'Cannot open new report: %s',message);
cleanup=onCleanup(@()fclose(fid)); %#ok<NASGU>
fwrite(fid,unicode2native(jsonencode(value),'UTF-8'),'uint8');
end
