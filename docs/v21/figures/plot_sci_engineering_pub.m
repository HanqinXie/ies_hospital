function receipt=plot_sci_engineering_pub(dataDir,outputDir)
% Publication wrapper: original numeric plotter plus explicit font-only repair.
% The original plotter remains unchanged. All original exports are generated
% first in this new directory; only unused/default Helvetica font properties
% are changed to Times New Roman before final publication exports.
arguments
    dataDir (1,1) string
    outputDir (1,1) string
end
assert(~isfolder(outputDir),'Fresh immutable publication directory required.');
original=string(which('plot_sci_engineering'));
expected="069489e31952f39b8b29d8db71823e0a3eb4ec982bc2d1955ebf1c9f8bceb5a9";
assert(isfile(original)&&hashFile(original)==expected,'Original numeric plotter changed.');
native=plot_sci_engineering(dataDir,outputDir);
names=string(native.figures(:));assert(~isempty(names),'No actual figures to publish.');
details=cell(numel(names),1);
for i=1:numel(names)
    stem=fullfile(outputDir,names(i));f=openfig(stem+'.fig','invisible');
    cleanup=onCleanup(@()close(f));before=numericObjects(f);
    objects=findall(f,'-property','FontName');changed=0;
    for h=objects(:)'
        if string(h.FontName)=="Helvetica",h.FontName='Times New Roman';changed=changed+1;end
    end
    fonts=unique(string(get(findall(f,'-property','FontName'),'FontName')));
    assert(all(ismember(fonts,["Times New Roman",string(char([23435 20307]))])), ...
        'Unexpected font remains; do not silently substitute.');
    after=numericObjects(f);assert(isequaln(before,after),'Font repair changed numeric graphics.');
    drawnow;
    savefig(f,stem+'.fig');
    print(f,stem+'.svg','-dsvg');
    exportgraphics(f,stem+'.pdf','ContentType','vector','BackgroundColor','white');
    exportgraphics(f,stem+'.png','Resolution',600,'BackgroundColor','white');
    assert(isequaln(before,numericObjects(f)),'Export changed numeric graphics.');
    formats=struct;
    for ext=["fig","svg","pdf","png"]
        path=stem+'.'+ext;info=dir(path);assert(isscalar(info)&&info.bytes>0);
        formats.(ext)=struct('path',path,'bytes',info.bytes,'sha256',hashFile(path));
    end
    details{i}=struct('name',names(i),'font_properties_changed',changed, ...
        'only_font_properties_changed',true,'numeric_graphics_exact_before_after',true, ...
        'numeric_graphics_sha256',hashText(jsonencode(before)), ...
        'allowed_fonts',fonts,'formats',formats);
    clear cleanup;
end
assert(hashFile(original)==expected,'Original plotter changed during publication.');
receipt=struct('schema','hospital_ies.publication_font_wrapper.v1', ...
    'backend','MATLAB','release',version('-release'),'source_directory',dataDir, ...
    'original_plotter_path',original,'original_plotter_sha256',expected, ...
    'publication_wrapper_path',string(mfilename('fullpath'))+'.m', ...
    'publication_wrapper_sha256',hashFile(string(mfilename('fullpath'))+'.m'), ...
    'native_figure_receipt_sha256',hashFile(fullfile(outputDir,'MATLAB_FIGURE_RECEIPT.json')), ...
    'font_change','Helvetica FontName to Times New Roman only', ...
    'only_font_properties_changed',true,'all_numeric_graphics_unchanged',true, ...
    'scientific_data_or_statistics_changed',false,'physical_attempts_started',0, ...
    'figures',{details},'formal_validation','PENDING_SOURCE_OBJECT_FORMAT_AND_VISUAL_QA');
fid=fopen(fullfile(outputDir,'PUBLICATION_WRAPPER_RECEIPT.json'),'w','n','UTF-8');assert(fid>=0);
c=onCleanup(@()fclose(fid));fprintf(fid,'%s',jsonencode(receipt,'PrettyPrint',true));
end

function values=numericObjects(f)
objects=findall(f);values=cell(0,1);
for h=objects(:)'
    row=struct('class',class(h));found=false;
    for name={'XData','YData','ZData','CData'}
        key=name{1};
        if isprop(h,key)
            v=h.(key);
            if isnumeric(v)||islogical(v),row.(key)=v;found=true;end
        end
    end
    if found,values{end+1,1}=row;end %#ok<AGROW>
end
end

function value=hashText(text)
digest=java.security.MessageDigest.getInstance('SHA-256');
digest.update(typecast(unicode2native(char(text),'UTF-8'),'int8'));
value=string(lower(reshape(dec2hex(typecast(digest.digest(),'uint8'),2)',1,[])));
end

function value=hashFile(path)
fid=fopen(path,'rb');assert(fid>=0);c=onCleanup(@()fclose(fid));
digest=java.security.MessageDigest.getInstance('SHA-256');
while true,b=fread(fid,1024*1024,'*uint8');if isempty(b),break;end;digest.update(typecast(b,'int8'));end
value=string(lower(reshape(dec2hex(typecast(digest.digest(),'uint8'),2)',1,[])));
end
