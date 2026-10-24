function receipt=qa_sci_artifacts(expectedPath,expectedSHA,figureDir,outputDir)
% Future read-only MATLAB audit. Never draws, edits, resaves or simulates.
% This file has NOT yet been run in MATLAB; preparation is not artifact proof.
arguments
    expectedPath (1,1) string
    expectedSHA (1,1) string
    figureDir (1,1) string
    outputDir (1,1) string
end
assert(~isfolder(outputDir),'Fresh output directory required.');
assert(hashFile(expectedPath)==expectedSHA,'Externally trusted EXPECTED SHA differs.');
E=jsondecode(fileread(expectedPath));assert(string(E.schema)=="hospital_ies.matlab_expected.v2");
if isfield(E,'publication_wrapper')
    assert(samePath(figureDir,string(E.publication_wrapper.figure_directory)),'Opened figure directory differs from explicit font-wrapper authority.');
end
assert(E.case_count>0&&~isempty(E.figures),'Empty CASE/figure expectations: NOT_VERIFIED.');
assert(~E.scientific_gate,'Artifact expectations cannot grant scientific acceptance.');
checkInputs(E.input_files);
receiptPath=fullfile(figureDir,'MATLAB_FIGURE_RECEIPT.json');assert(isfile(receiptPath));
R=jsondecode(fileread(receiptPath));assert(string(R.backend)=="MATLAB"&&R.png_dpi==600&&R.scientific_data_only);
assert(samePath(string(R.source_directory),string(E.data_directory)),'Figure source directory differs.');
expected=asCells(E.figures);expected=resolveBoundaryNames(expected,E.data_directory);
files=dir(fullfile(figureDir,'*.fig'));assert(~isempty(files)&&numel(files)==numel(expected),'Unexpected/missing/empty figure count.');
assert(isequal(sort(erase(string({files.name}),'.fig')),sort(string(R.figures(:))')),'Figure receipt/file list differs.');
allFiles=dir(fullfile(figureDir,'*'));allFiles=allFiles(~[allFiles.isdir]);
before=hashSet(figureDir,allFiles);used=false(1,numel(expected));details=cell(numel(files),1);
failures=strings(0,1);
for i=1:numel(files)
    path=fullfile(files(i).folder,files(i).name);f=[];d=struct('file',path,'status','failed');
    try
        f=openfig(path,'invisible');f.Visible='off';
        assert(isstruct(f.UserData)&&f.UserData.no_synthetic_data&&f.UserData.native_intervals_preserved,'FIG provenance flags absent.');
        [~,name]=fileparts(path);candidate=[];
        for j=1:numel(expected)
            x=expected{j};
            if string(x.family)=="F6"
                hit=startsWith(name,'F6_process_')&&samePath(string(f.UserData.actual_source),string(x.actual_source));
            else
                hit=isfield(x,'name')&&string(x.name)==string(name);
            end
            if hit,candidate(end+1)=j;end %#ok<AGROW>
        end
        assert(numel(candidate)==1,'Ambiguous/unbound FIG identity: NOT_VERIFIED.');j=candidate(1);assert(~used(j),'Duplicated expected figure.');used(j)=true;x=expected{j};
        if string(x.family)~="F6",assert(samePath(string(f.UserData.actual_source),string(E.data_directory)),'FIG source metadata differs.');end
        assert(isempty(findall(f,'Type','image')),'Raster image object in FIG.');
        ax=findall(f,'Type','axes');assert(numel(ax)==x.axes_count,'Axes count differs.');
        tiles=zeros(numel(ax),1);for k=1:numel(ax),tiles(k)=ax(k).Layout.Tile;end
        assert(isequal(sort(tiles(:))',(1:x.axes_count)),'Ambiguous/missing Layout.Tile.');
        for k=1:numel(ax)
            tile=tiles(k);a=ax(k);
            switch string(x.family)
                case {"F1","F2"}
                    v=axisByTile(x.axes,tile);ud=a.UserData;
                    assert(isstruct(ud)&&string(ud.source_metric)==string(v.metric));
                    assert(string(ud.independent_unit)=="source_block");
                    assert(isequal(string(ud.plotted_classes(:)),string(v.classes(:))));
                    assert(isequal(string(ud.policy_aliases(:)),string(v.aliases(:))));
                    eqNumeric(ud.source_block_mean_values,v.values);verifyBars(a,v.values,v.colors, numel(v.classes)==1);
                    actualLabels=string(a.XTickLabel);expectedLabels=string(v.expected_xtick_labels);
                    assert(isequal(actualLabels(:),expectedLabels(:)),'Class/source-block tick labels differ, are split or missing.');
                    eqNumeric(a.XTick,1:numel(expectedLabels));
                case "F3"
                    % The actual plotter has no axes UserData for F3. Refuse
                    % any non-unique configuration/window/filename/tile match.
                    assert(string(name)=="F3_mechanism_"+string(x.configuration_id)+"_w"+string(x.window_minutes));
                    texts=allStrings(f);assert(contains(texts,"匹配四组合"),'Four-cell mechanism title absent.');
                    v=axisByTile(x.axes,tile);b=findall(a,'Type','bar');assert(numel(b)==1);eqNumeric(b.YData,v.values);eqNumeric(b.XData,1:4);eqNumeric(b.FaceColor,v.color);
                    assert(isequal(string(a.XTickLabel(:)),["仅备用";"仅准备";"同时采用";"交互项"]));
                case "F4"
                    v=axisByTile(x.axes,tile);ud=a.UserData;
                    assert(string(ud.source_metric)==string(v.metric)&&string(ud.fault_target)==string(x.fault_target));
                    eqNumeric(ud.window_minutes,x.window_minutes);eqNumeric(ud.paired_delta_H1_H2_by_C_A,v.values);
                    assert(ud.only_complete_H0_H1_H2_triples&&~ud.missing_values_zero_filled&&string(ud.independent_unit)=="source_block");
                    assert(isequal(sort(string(ud.source_blocks(:))),sort(string(x.source_blocks(:)))));
                    assert(isequal(sort(string(ud.paired_scenario_ids(:))),sort(string(x.paired_scenarios(:)))));
                    verifyBars(a,v.values,v.colors,false);assert(isequal(string(a.XTickLabel(:)),["H1-H0";"H2-H0"]));
                case "F5"
                    v=axisByTile(x.axes,tile);verifyBoundary(a,v);
                    texts=allStrings(f);assert(contains(texts,"fault_target="+string(x.fault_target)),'Fault target absent from boundary title.');
                    assert(contains(texts,"右删失")&&contains(texts,"不是永久保障"),'Censoring limitation absent.');
                case "F6"
                    verifySeries(a,x.series,tile);
                otherwise,error('Unknown figure family');
            end
        end
        if string(x.family)=="F4",verifyPaired(fullfile(figureDir,x.paired_values_file),x.paired_values);end
        fontResult=verifyFonts(f);
        formats=struct;
        for ext=["fig","pdf","svg","png"]
            p=fullfile(figureDir,string(name)+'.'+ext);assert(isfile(p),'Missing required format.');
            info=dir(p);assert(info.bytes>0,'Empty format.');formats.(ext)=struct('path',p,'bytes',info.bytes,'sha256',hashFile(p));
        end
        % Figure centimetres describe intended export geometry. PNG crop
        % geometry is checked later, not patched into its metadata.
        oldUnits=f.Units;f.Units='centimeters';cm=f.Position(3:4);f.Units=oldUnits;
        d=struct('file',path,'status','passed','family',x.family,'axes',numel(ax),'fonts',fontResult,'formats',formats, ...
            'figure_size_cm',cm,'theoretical_600dpi_pixels',cm/2.54*600,'png_dpi_status','PENDING_EXTERNAL_SCAN','visual_review','NOT_RUN');
    catch exception
        d.error=exception.message;d.identifier=exception.identifier;failures(end+1)=string(path)+": "+exception.message; %#ok<AGROW>
    end
    if ~isempty(f)&&isgraphics(f),close(f);end
    details{i}=d;
end
if any(~used),failures(end+1)="Expected figures were not all matched.";end
checkInputs(E.input_files);assert(hashFile(expectedPath)==expectedSHA,'Expected file changed during audit.');
current=dir(fullfile(figureDir,'*'));current=current(~[current.isdir]);
assert(isequal(string({allFiles.name}),string({current.name})),'Artifact set changed during read-only audit.');
after=hashSet(figureDir,current);assert(isequal(before,after),'Artifacts changed during read-only audit.');
receipt=struct('schema','hospital_ies.matlab_object_audit.v2','expected_path',expectedPath,'expected_sha256',expectedSHA, ...
    'figure_directory',figureDir,'source_binding_sha256',E.source_binding_sha256,'plotter_sha256',E.plotter_sha256, ...
    'MATLAB_release',version('-release'),'details',{details},'failures',failures,'all_expected_matched',all(used), ...
    'expected_figure_count',numel(expected),'matched_figure_count',sum(used), ...
    'object_checks_passed',isempty(failures),'input_and_artifact_hashes_unchanged',true,'artifact_hashes',{before}, ...
    'visual_review','NOT_RUN','pdf_svg_png_structure','PENDING_EXTERNAL_SCAN','scientific_gate',false,'actual_physics_started',0);
mkdir(outputDir);fid=fopen(fullfile(outputDir,'MATLAB_OBJECT_QA.json'),'w','n','UTF-8');assert(fid>=0);c=onCleanup(@()fclose(fid));fprintf(fid,'%s',jsonencode(receipt,'PrettyPrint',true));
if ~isempty(failures),error('ArtifactQA:Failure','%d artifact failures; receipt saved.',numel(failures));end
end

function expected=resolveBoundaryNames(expected,dataDir)
need=any(cellfun(@(x)string(x.family)=="F5",expected));if ~need,return;end
T=readtable(fullfile(dataDir,'CASE_METRICS.csv'),'TextType','string','VariableNamingRule','preserve');
M=load(fullfile(dataDir,'SCI_TABLES.mat'),'CASE_METRICS_numeric','CASE_METRICS_numeric_columns');columns=string(M.CASE_METRICS_numeric_columns);
for j=1:numel(columns),T.(columns(j))=M.CASE_METRICS_numeric(:,j);end
original=(1:height(T))';mask=string(T.phase)=="boundary"&ismember(lower(string(T.eligible)),["true","1"]);B=T(mask,:);original=original(mask);
for i=1:numel(expected)
    x=expected{i};if string(x.family)~="F5",continue;end
    fields=string(x.group_fields);K=table;
    for field=fields(:)',s=string(B.(field));s(ismissing(s))="<missing>";K.(field)=s;end
    [groups,keys]=findgroups(K);hits=[];
    for g=1:height(keys)
        if isequal(sort(original(groups==g)),sort(double(x.source_rows(:)))),hits(end+1)=g;end %#ok<AGROW>
    end
    assert(numel(hits)==1,'F5 MATLAB/Python group source rows differ or ambiguous: NOT_VERIFIED.');
    x.name="F5_"+string(x.mode)+"_slice"+string(hits);expected{i}=x;
end
end

function verifyBars(ax,Y,colors,scalarClasses)
bars=findall(ax,'Type','bar');assert(numel(bars)==size(colors,1),'Bar series count differs.');
used=false(numel(bars),1);
for j=1:size(colors,1)
    hit=[];for k=1:numel(bars),if isnumeric(bars(k).FaceColor)&&numericSame(bars(k).FaceColor,colors(j,:)),hit(end+1)=k;end,end %#ok<AGROW>
    assert(numel(hit)==1&&~used(hit),'Ambiguous alias color mapping.');used(hit)=true;
    b=bars(hit);eqNumeric(b.YData,Y(:,j));
    if scalarClasses,eqNumeric(b.XData,j);else,eqNumeric(b.XData,1:size(Y,1));end
end
end

function verifyBoundary(ax,v)
ud=ax.UserData;assert(string(ud.schema)=="hospital_ies.boundary_patch_grid.v1"&&string(ud.source_metric)==string(v.metric));
assert(ud.discrete_registered_grid&&~ud.interpolated&&~ud.missing_values_zero_filled);
eqNumeric(ud.native_x_values,v.x);eqNumeric(ud.native_y_values,v.y);Z=double(v.values);
assert(isequal(string(ax.XTickLabel(:)),compose('%.5g',double(v.x(:))))&&isequal(string(ax.YTickLabel(:)),compose('%.5g',double(v.y(:)))),'Boundary coordinate labels differ.');
eqNumeric(ud.native_grid_values,Z);assert(isequal(logical(ud.missing_cells),logical(v.missing)));assert(isequal(logical(ud.right_censored_cells),logical(v.right_censored)));
patches=findall(ax,'Tag','native_boundary_cell');labels=findall(ax,'Tag','native_boundary_value_label');
assert(numel(patches)==sum(isfinite(Z),'all')&&numel(labels)==numel(patches),'Missing/extra native cells or zero-filled unknown.');
seen=false(size(Z));
for p=patches(:)'
    u=p.UserData;j=find(double(v.x)==u.x_value);i=find(double(v.y)==u.y_value);
    assert(isscalar(i)&&isscalar(j)&&~seen(i,j)&&isfinite(Z(i,j)));seen(i,j)=true;
    assert(string(u.source_metric)==string(v.metric)&&string(u.x_field)==string(v.x_field)&&string(u.y_field)==string(v.y_field));
    eqNumeric(p.XData,[j-.5,j+.5,j+.5,j-.5]);eqNumeric(p.YData,[i-.5,i-.5,i+.5,i+.5]);eqNumeric(p.CData,Z(i,j));eqNumeric(u.value,Z(i,j));
    assert(logical(u.right_censored)==logical(v.right_censored(i,j))&&string(p.FaceColor)=="flat"&&string(p.CDataMapping)=="scaled");
    found=[];
    for label=labels(:)'
        if numericSame(label.Position(1:2),[j i]),found=[found label];end %#ok<AGROW>
    end
    assert(isscalar(found),'Missing or duplicate boundary label.');suffix="";if v.right_censored(i,j),suffix="*";end
    assert(string(found.String)==string(sprintf('%.1f',Z(i,j)))+suffix);assert(isequaln(found.UserData,u));
end
assert(isequal(seen,isfinite(Z)),'Unknown boundary cell was plotted.');
end

function verifySeries(ax,series,tile)
all=asCells(series);want=all(cellfun(@(x)x.tile==tile,all));objects=findall(ax);
objects=objects(arrayfun(@(h)isprop(h,'XData')&&isprop(h,'YData')&&isprop(h,'Color')&&(isa(h,'matlab.graphics.chart.primitive.Stair')||isa(h,'matlab.graphics.chart.primitive.Line')),objects));
assert(numel(objects)==numel(want),'Native series count differs.');used=false(numel(objects),1);
for j=1:numel(want)
    v=want{j};hits=[];
    for k=1:numel(objects)
        h=objects(k);kind="line";if isa(h,'matlab.graphics.chart.primitive.Stair'),kind="stair";end
        if kind==string(v.kind)&&numericSame(h.Color,v.color)&&string(h.LineStyle)==string(v.line_style),hits(end+1)=k;end %#ok<AGROW>
    end
    assert(numel(hits)==1&&~used(hits),'Ambiguous native port/endpoint mapping.');used(hits)=true;h=objects(hits);
    eqNumeric(h.XData,v.x);eqNumeric(h.YData,v.y);
    if string(v.kind)=="line",assert(string(h.LineStyle)=="none"&&string(h.Marker)==".",'Inventory endpoints became interpolated curve.');end
end
end

function verifyPaired(path,records)
T=readtable(path,'TextType','string','VariableNamingRule','preserve');rows=asCells(records);assert(height(T)==numel(rows));
fields=fieldnames(rows{1});
for i=1:numel(rows)
    r=rows{i};mask=string(T.scenario_id)==string(r.scenario_id)&string(T.policy_alias)==string(r.policy_alias)&string(T.configuration_id)==string(r.configuration_id);assert(sum(mask)==1);
    for j=1:numel(fields)
        f=fields{j};value=r.(f);actual=T.(f)(mask);
        if isnumeric(value),eqNumeric(actual,value);else,assert(string(actual)==string(value));end
    end
end
end

function result=verifyFonts(f)
song=string(char([23435,20307]));objects=findall(f,'-property','FontName');count=0;chinese=0;
for h=objects(:)'
    font=string(h.FontName);assert(ismember(font,[song,"Times New Roman"]),'Unexpected native font: %s',font);count=count+1;
    if isprop(h,'String')
        pieces=string(h.String);value=join(pieces(:)," ");
        code=double(char(value));if any(code>=19968&code<=40959),assert(font==song,'Chinese text is not Songti.');chinese=chinese+1;end
    end
end
axes=findall(f,'Type','axes');
for a=axes(:)'
    assert(string(a.FontName)=="Times New Roman"&&string(a.YAxis.FontName)=="Times New Roman",'Numeric axes font differs.');
end
result=struct('font_objects',count,'Chinese_objects_checked',chinese,'allowed_fonts',[song,"Times New Roman"]);
end

function v=axisByTile(records,tile)
records=asCells(records);hit=find(cellfun(@(r)r.tile==tile,records));assert(isscalar(hit),'Axis expectation is ambiguous.');v=records{hit};
end
function cells=asCells(value)
if isempty(value),cells={};elseif iscell(value),cells=reshape(value,1,[]);else,cells=arrayfun(@(j)value(j),1:numel(value),'UniformOutput',false);end
end
function yes=samePath(a,b)
yes=strcmpi(replace(a,'/','\'),replace(b,'/','\'));
end
function yes=numericSame(a,b)
a=double(a);b=double(b);if isvector(a)&&isvector(b),a=a(:);b=b(:);end
yes=isequal(size(a),size(b))&&isequal(isnan(a),isnan(b))&&all((abs(a-b)<=1e-10+1e-12*abs(b))|isnan(b),'all');
end
function eqNumeric(a,b)
assert(numericSame(a,b),'Artifact numeric values or unknown masks differ.');
end
function stringsOut=allStrings(f)
objects=findall(f,'-property','String');value=strings(0,1);
for h=objects(:)',pieces=string(h.String);value=[value;pieces(:)];end %#ok<AGROW>
stringsOut=join(value," ");
end
function checkInputs(files)
files=asCells(files);
for j=1:numel(files),x=files{j};info=dir(x.path);assert(isscalar(info)&&info.bytes==x.bytes&&hashFile(x.path)==string(x.sha256),'Bound input changed: %s',x.path);end
end
function values=hashSet(directory,files)
values=cell(numel(files),1);
for i=1:numel(files),values{i}=struct('path',fullfile(directory,files(i).name),'bytes',files(i).bytes,'sha256',hashFile(fullfile(directory,files(i).name)));end
end
function value=hashFile(path)
fid=fopen(path,'rb');assert(fid>=0);c=onCleanup(@()fclose(fid));digest=java.security.MessageDigest.getInstance('SHA-256');
while true,b=fread(fid,1024*1024,'*uint8');if isempty(b),break;end;digest.update(typecast(b,'int8'));end
value=string(lower(reshape(dec2hex(typecast(digest.digest(),'uint8'),2)',1,[])));
end
