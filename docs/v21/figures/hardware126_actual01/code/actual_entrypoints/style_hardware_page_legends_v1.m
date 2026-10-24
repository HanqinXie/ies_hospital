function receipt=style_hardware_page_legends_v1(sourceDir,outputDir,names,manifestPath,knownManifestSHA)
% Page and legend geometry only, using the proved fixed3 canvas/snapshot method.
arguments
    sourceDir (1,1) string
    outputDir (1,1) string
    names (:,1) string
    manifestPath (1,1) string
    knownManifestSHA (1,1) string
end
assert(isfolder(sourceDir)&&~isfolder(outputDir)&&~isempty(names));
assert(hashFile(manifestPath)==knownManifestSHA);M=jsondecode(fileread(manifestPath));
assert(numel(M.cases)==18&&M.source_CASE_count==126&&~M.science_gate);
isHW=all(startsWith(names,'HW18_'));assert(isHW||all(startsWith(names,'F4_')));
if isHW,sourceReceipt=fullfile(sourceDir,'HW18_MATLAB_OBJECT_QA.json');
else,sourceReceipt=fullfile(sourceDir,'MATLAB_FIGURE_RECEIPT.json');end
receiptSHA=hashFile(sourceReceipt);
if isHW
    assert(receiptSHA=="080d17f92667c3e87368c83d36f253a43abcee046246062b7f8b94318092604b");sourceAudit=jsondecode(fileread(sourceReceipt));
else
    auditPath=fullfile(fileparts(sourceDir),'f4_objects','MATLAB_OBJECT_QA.json');assert(hashFile(auditPath)=="af0b83a1633663836f98a4a09aeb51b5b89792985e4a7b49f7934617b1395c68");sourceAudit=jsondecode(fileread(auditPath));
end
assert(sourceAudit.object_checks_passed&&isempty(sourceAudit.failures));
mkdir(outputDir);details=cell(numel(names),1);changes=cell(numel(names),1);
for i=1:numel(names)
    originalPath=fullfile(sourceDir,names(i)+'.fig');sourceSHA=hashFile(originalPath);matched=false;
    for j=1:numel(sourceAudit.details),[~,refName]=fileparts(string(sourceAudit.details(j).file));if refName==names(i),assert(~matched&&sourceSHA==string(sourceAudit.details(j).formats.fig.sha256));matched=true;end,end
    assert(matched);
    f=openfig(originalPath,'invisible');clean=onCleanup(@()close(f));original=snapshot(f);
    assert(string(f.Units)=="centimeters");cm=f.Position(3:4);
    if isHW,assert(isequal(cm,[30 25]));else,assert(isequal(cm,[24 17]));end
    L=findall(f,'-isa','matlab.graphics.layout.TiledChartLayout');assert(isscalar(L));
    layoutBefore=struct('OuterPosition',L.OuterPosition,'Padding',string(L.Padding),'TileSpacing',string(L.TileSpacing));
    L.Padding='loose';L.TileSpacing='loose';
    if isHW,L.OuterPosition=[.035 .11 .93 .80];else,L.OuterPosition=[.05 .17 .90 .74];end
    legends=findall(f,'Type','legend');legendChanges=cell(numel(legends),1);
    for j=1:numel(legends)
        lg=legends(j);prior=struct('String',{lg.String},'Location',string(lg.Location),'Orientation',string(lg.Orientation),'NumColumns',lg.NumColumns,'Position',lg.Position);
        plotChildren=lg.PlotChildren;lg.Orientation='horizontal';lg.NumColumns=numel(lg.String);lg.Location='northoutside';
        assert(isequal(lg.String,prior.String)&&isequal(lg.PlotChildren,plotChildren));
        legendChanges{j}=struct('before',prior,'after_location',string(lg.Location),'after_orientation',string(lg.Orientation),'after_num_columns',lg.NumColumns,'strings_and_PlotChildren_exact',true);
    end
    note=findall(f,'Type','textboxshape');assert(isscalar(note));noteBefore=note.Position;
    if isHW,note.Position=[.035 .02 .93 .06];else,note.Position=[.04 .025 .92 .115];end
    unitChange=struct('applied',false);
    if isHW
        axes=findall(f,'Type','axes');tiles=arrayfun(@(a)a.Layout.Tile,axes);a=axes(tiles==9);assert(isscalar(a));
        before=string(a.YLabel.String);after=replace(before,'kWh_shaft','kWh_{shaft}');assert(before~=after);a.YLabel.String=after;
        unitChange=struct('applied',true,'tile',9,'before',before,'after',after,'physical_unit_and_data_unchanged',true);
    end
    f.PaperUnits='centimeters';f.PaperSize=cm;f.PaperPosition=[0 0 cm];f.PaperPositionMode='manual';
    canvas=annotation(f,'rectangle',[0 0 1 1],'FaceColor','none','Color',[1 1 1],'LineWidth',.1,'Tag','publication_page_canvas');
    assert(isequal(canvas.Position,[0 0 1 1])&&string(canvas.FaceColor)=="none");
    drawnow;viewChanges=restoreViewLimits(original,f);drawnow;
    assert(isequaln(original,snapshot(f)),'Page/legend styling changed scientific X/Y/CData/RGB/UserData/yyaxis limits.');
    stem=fullfile(outputDir,names(i));savefig(f,stem+'.fig');print(f,stem+'.svg','-dsvg','-painters');
    exportgraphics(f,stem+'.pdf','ContentType','vector','BackgroundColor','white');print(f,stem+'.png','-dpng','-r600');
    assert(isequaln(original,snapshot(f)));
    proof=openfig(stem+'.fig','invisible');proofClean=onCleanup(@()close(proof));
    assert(isequaln(original,snapshot(proof)),'Reopened styled native figure differs from original science.');
    assert(isempty(findall(proof,'Type','image')));
    if isHW
        c=M.cases(find(string({M.cases.output_name})==names(i)));assert(isscalar(c));
        assert(string(proof.UserData.manifest_sha256)==knownManifestSHA&&string(proof.UserData.scenario_id)==string(c.scenario_id)&&string(proof.UserData.policy_id)==string(c.policy_id)&&string(proof.UserData.configuration_id)==string(c.configuration_id));
        v=c.expected_vectors.series;lines=findall(proof,'Type','line');assert(numel(v)==18&&numel(lines)==18);
        for k=1:18,h=findall(proof,'Type','line','Tag',string(v(k).tag));assert(isscalar(h)&&isequaln(h.XData(:),v(k).x(:))&&isequaln(h.YData(:),v(k).y(:))&&h.Parent.Layout.Tile==v(k).tile);end
    end
    fonts=findall(proof,'-property','FontName');for k=1:numel(fonts),assert(ismember(string(fonts(k).FontName),["宋体","Times New Roman"]));end
    formats=struct;for ext=["fig","svg","pdf","png"],p=stem+'.'+ext;info=dir(p);formats.(ext)=struct('path',p,'bytes',info.bytes,'sha256',hashFile(p));end
    details{i}=struct('file',stem+'.fig','status','passed','figure_size_cm',cm,'formats',formats,'source_FIG',originalPath,'source_FIG_sha256',sourceSHA,'scientific_snapshot_exact',true,'native_series_checked',18*isHW,'yyaxis_limits_and_RGB_exact',true);
    changes{i}=struct('name',names(i),'layout_before',layoutBefore,'layout_after',L.OuterPosition,'legend_changes',{legendChanges},'note_position_before',noteBefore,'note_position_after',note.Position,'unit_label_typesetting',unitChange,'original_axis_limits_restored',{viewChanges});
    clear proofClean;clear clean;
    assert(hashFile(originalPath)==sourceSHA);
    if ~isHW
        paired=names(i)+'_PAIRED_VALUES.csv';known=hashFile(fullfile(sourceDir,paired));copyfile(fullfile(sourceDir,paired),fullfile(outputDir,paired));assert(hashFile(fullfile(outputDir,paired))==known);
    end
end
assert(hashFile(sourceReceipt)==receiptSHA&&hashFile(manifestPath)==knownManifestSHA);
if ~isHW
    R=jsondecode(fileread(sourceReceipt));R.figures=names;R.page_legend_only_style_source=string(mfilename('fullpath'))+'.m';R.source_receipt_path=sourceReceipt;R.source_receipt_sha256=receiptSHA;
    writeJSON(fullfile(outputDir,'MATLAB_FIGURE_RECEIPT.json'),R);
end
receipt=struct('schema','hospital_ies.matlab_object_audit.v2','auditor','page_legend_only_wrapper_with_original_scientific_snapshot_and_HW18_exact_vectors','details',{details},'failures',{{}},'expected_figure_count',numel(names),'matched_figure_count',numel(names),'all_expected_matched',true,'object_checks_passed',true,'original_source_receipt',sourceReceipt,'original_source_receipt_sha256',receiptSHA,'manifest_sha256',knownManifestSHA,'style_source_sha256',hashFile(string(mfilename('fullpath'))+'.m'),'style_changes',{changes},'all_source_FIGs_unchanged',true,'scientific_data_or_statistics_changed',false,'scientific_gate',false,'actual_physics_started',0);
writeJSON(fullfile(outputDir,'PAGE_LEGEND_NATIVE_QA.json'),receipt);
end

function changes=restoreViewLimits(original,f)
axes=findall(f,'Type','axes');tiles=arrayfun(@(a)a.Layout.Tile,axes);[~,order]=sort(tiles);axes=axes(order);changes={};
assert(numel(axes)==numel(original.axes));
for k=1:numel(axes)
    a=axes(k);ref=original.axes{k};assert(a.Layout.Tile==ref.tile);
    for field=["XLim","YLim","ZLim"]
        if ~isequaln(a.(field),ref.(field)),prior=a.(field);a.(field)=ref.(field);changes{end+1}=struct('tile',ref.tile,'property',field,'auto_layout_value',prior,'original_restored',ref.(field));end %#ok<AGROW>
    end
    for j=1:numel(a.YAxis)
        if ~isequaln(a.YAxis(j).Limits,ref.YAxesLimits{j}),prior=a.YAxis(j).Limits;a.YAxis(j).Limits=ref.YAxesLimits{j};changes{end+1}=struct('tile',ref.tile,'YAxis',j,'auto_layout_value',prior,'original_restored',ref.YAxesLimits{j});end %#ok<AGROW>
    end
end
end
function result=snapshot(f)
axes=findall(f,'Type','axes');tiles=arrayfun(@(a)a.Layout.Tile,axes);[~,order]=sort(tiles);axes=axes(order);axisState=cell(numel(axes),1);series={};
for k=1:numel(axes)
    a=axes(k);ylim=cell(numel(a.YAxis),1);ycolor=cell(numel(a.YAxis),1);for j=1:numel(a.YAxis),ylim{j}=a.YAxis(j).Limits;ycolor{j}=a.YAxis(j).Color;end
    axisState{k}=struct('tile',a.Layout.Tile,'UserData',a.UserData,'XLim',a.XLim,'YLim',a.YLim,'ZLim',a.ZLim,'YAxesLimits',{ylim},'YAxesColors',{ycolor});
    hs=findall(a);
    for j=1:numel(hs)
        h=hs(j);fields=["XData","YData","ZData","CData"];if ~any(arrayfun(@(v)isprop(h,v),fields)),continue;end
        v=struct('tile',a.Layout.Tile,'class',class(h),'UserData',h.UserData);
        for field=fields,if isprop(h,field),v.(field)=h.(field);end,end
        for field=["Color","FaceColor","EdgeColor","LineStyle","Marker","Tag"],if isprop(h,field),v.(field)=h.(field);end,end
        series{end+1}=v; %#ok<AGROW>
    end
end
result=struct('figure_UserData',f.UserData,'axes',{axisState},'series',{series});
end
function value=hashFile(path)
fid=fopen(path,'rb');assert(fid>=0);c=onCleanup(@()fclose(fid));digest=java.security.MessageDigest.getInstance('SHA-256');while true,b=fread(fid,1024*1024,'*uint8');if isempty(b),break;end;digest.update(typecast(b,'int8'));end
value=string(lower(reshape(dec2hex(typecast(digest.digest(),'uint8'),2)',1,[])));
end
function writeJSON(path,value)
fid=fopen(path,'w','n','UTF-8');assert(fid>=0);c=onCleanup(@()fclose(fid));fprintf(fid,'%s',jsonencode(value,'PrettyPrint',true));
end
