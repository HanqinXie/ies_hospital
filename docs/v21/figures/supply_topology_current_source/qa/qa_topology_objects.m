function qa_topology_objects(figureDir, outputFile)
% Read-only reopen of an actual topology FIG; no scientific recomputation.
assert(~isfile(outputFile),'Create-only QA receipt');
figPath=fullfile(figureDir,'supply_topology_current_source.fig');
before=fileSHA(figPath);
fig=openfig(figPath,'invisible'); cleanup=onCleanup(@()close(fig));
assert(isempty(findall(fig,'Type','image')),'Raster object in editable FIG');
ax=findall(fig,'Type','axes'); assert(numel(ax)==1,'Unexpected axes count');
drawnow;
txt=findall(fig,'Type','text'); plain=strings(numel(txt),1);
for k=1:numel(txt)
    s=string(txt(k).String); assert(isscalar(s),'Unexpected multiline text object');
    s=regexprep(s,'\\fontname\{[^}]+\}','');
    plain(k)=erase(erase(s,'{'),'}');
    e=txt(k).Extent;
    if strlength(plain(k))>0
        assert(e(1)>=0 && e(2)>=0 && e(1)+e(3)<=1 && e(2)+e(4)<=1, ...
            ['Text outside normalized figure bounds: ' char(plain(k))]);
    end
end
allText=join(plain,newline);
required=["H1 = H0 + UPS","P0 / P1 building electricity only", ...
    "H2 = H0 + cold store; no UPS","P0 / P1 useful cooling only", ...
    "额外充放循环泵电来自普通共同母线","仍需建筑冷冻水泵与末端风机", ...
    "仅此上界不受风机门控；仍需热源及供热泵", ...
    "直接电不以风机为前置；辅助电只计一次", ...
    "已含冷凝泵与塔风机；不再另加一次", ...
    "四等级三载能分别核算，不解析同等级房间交付"];
for s=required,assert(contains(allText,s),['Missing semantics: ' char(s)]);end
fontObjects=findall(fig,'-property','FontName');
fontNames=unique(string(get(fontObjects,'FontName')));
assert(all(ismember(fontNames,["Times New Roman","SimSun","宋体"])), ...
    'Unexpected native object font');
lines=findall(fig,'Type','line'); expectedX=[.415 .446 .446 .390 .390];
expectedY=[.835 .835 .600 .600 .5775]; revisedInputCount=0;
for k=1:numel(lines)
    if isequal(lines(k).XData,expectedX) && isequal(lines(k).YData,expectedY)
        revisedInputCount=revisedInputCount+1;
        assert(isequal(lines(k).Color,[.10 .29 .47]));
    end
end
assert(revisedInputCount==1,'Electric input must enter distinct converter top port');
set(fig,'Units','centimeters'); geometry=fig.Position;
assert(all(abs(geometry(3:4)-[31 24])<=1e-8),'Unexpected native figure dimensions');
assert(strcmp(before,fileSHA(figPath)),'FIG bytes changed during read-only reopen');
value=struct('schema','hospital_ies.topology_native_object_qa.v1', ...
    'status','passed_native_object_checks_visual_review_required', ...
    'figure_path',figPath,'figure_sha256',before,'axes',numel(ax), ...
    'text_objects',numel(txt),'line_objects',numel(lines), ...
    'patch_objects',numel(findall(fig,'Type','patch')), ...
    'image_objects',0,'font_names',fontNames,'required_semantic_labels',required, ...
    'all_text_within_figure',true,'distinct_electric_converter_port',true, ...
    'figure_dimensions_cm',geometry(3:4),'read_only_figure_sha_unchanged',true, ...
    'physics_attempts',0,'scientific_results_read',false);
fid=fopen(outputFile,'w','n','UTF-8');assert(fid>=0);
fwrite(fid,jsonencode(value),'char');fclose(fid);
end

function out=fileSHA(path)
fid=fopen(path,'rb');assert(fid>=0);cleanup=onCleanup(@()fclose(fid));
md=java.security.MessageDigest.getInstance('SHA-256');
while true
    bytes=fread(fid,1048576,'*uint8');if isempty(bytes),break;end
    md.update(bytes);
end
out=lower(reshape(dec2hex(typecast(md.digest(),'uint8'),2).',1,[]));
end
