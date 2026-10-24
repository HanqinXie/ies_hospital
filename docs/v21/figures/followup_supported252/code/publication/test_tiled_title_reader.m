function test_tiled_title_reader(outputPath)
% Temporary empty graphics objects only. No scientific FIG/PDF/curve is saved.
assert(~isfile(outputPath));song=char([23435,20307]);
f=figure('Visible','off');cleanup=onCleanup(@()close(f));L=tiledlayout(f,1,1);
a=nexttile(L);a.FontName='Times New Roman';
allFonts=findall(f,'-property','FontName');for h=allFonts(:)',h.FontName='Times New Roman';end
title(L,'匹配四组合；fault_target=GRID','FontName',song);
assert(contains(allStrings(f),"匹配四组合")&&contains(allStrings(f),"fault_target=GRID"));
verifyFonts(f);
L.Title.String='wrong title';assert(~contains(allStrings(f),"匹配四组合"));
badTitleRejected=false;try,assert(contains(allStrings(f),"匹配四组合"));catch,badTitleRejected=true;end
assert(badTitleRejected);
L.Title.String='匹配四组合；fault_target=GRID';L.Title.FontName='Helvetica';
badFontRejected=false;try,verifyFonts(f);catch,badFontRejected=true;end;assert(badFontRejected);
L.Title.FontName='Times New Roman';badChineseFontRejected=false;
try,verifyFonts(f);catch,badChineseFontRejected=true;end;assert(badChineseFontRejected);
L.Title.FontName=song;verifyFonts(f);
result=struct('schema','hospital_ies.temporary_tiled_title_regression.v1', ...
    'valid_title_and_font_accepted',true,'wrong_title_rejected',badTitleRejected, ...
    'Helvetica_title_rejected',badFontRejected,'Chinese_in_TNR_title_rejected',badChineseFontRejected, ...
    'scientific_figures_written',0,'physics',0);
fid=fopen(outputPath,'w','n','UTF-8');assert(fid>=0);closer=onCleanup(@()fclose(fid));
fprintf(fid,'%s',jsonencode(result,'PrettyPrint',true));
end
function result=verifyFonts(f)
song=string(char([23435,20307]));objects=findall(f,'-property','FontName');count=0;chinese=0;
% Native tiledlayout title is a property reference, absent from findall children.
layouts=findall(f,'Type','tiledlayout');
for layout=layouts(:)',objects=[objects;layout.Title];end %#ok<AGROW>
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

function stringsOut=allStrings(f)
objects=findall(f,'-property','String');value=strings(0,1);
for h=objects(:)',pieces=string(h.String);value=[value;pieces(:)];end %#ok<AGROW>
% Keep the original exact text requirement; read the actual omitted title.
layouts=findall(f,'Type','tiledlayout');
for layout=layouts(:)',pieces=string(layout.Title.String);value=[value;pieces(:)];end %#ok<AGROW>
stringsOut=join(value," ");
end
