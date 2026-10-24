function test_exact_boundary_RGB(outputPath)
% Temporary one-cell graphics fixture only. No scientific file is generated.
assert(~isfile(outputPath));f=figure('Visible','off');cleanup=onCleanup(@()close(f));
a=axes(f);colormap(a,parula(256));a.CLim=[0 1];a.XTick=1;a.YTick=1;a.XTickLabel={'0'};a.YTickLabel={'0'};
v=struct('metric','test_fixture_metric','x_field','test_x','y_field','test_y', ...
    'x',0,'y',0,'values',0,'missing',false,'right_censored',false);
a.UserData=struct('schema','hospital_ies.boundary_patch_grid.v1','source_metric',v.metric, ...
    'discrete_registered_grid',true,'interpolated',false,'missing_values_zero_filled',false, ...
    'native_x_values',0,'native_y_values',0,'native_grid_values',0,'missing_cells',false,'right_censored_cells',false);
u=struct('source_metric',v.metric,'x_field',v.x_field,'y_field',v.y_field,'x_value',0,'y_value',0,'value',0,'right_censored',false);
cmap=parula(256);p=patch(a,[.5 1.5 1.5 .5],[.5 .5 1.5 1.5],0,'FaceColor',cmap(1,:),'CDataMapping','scaled');
p.Tag='native_boundary_cell';p.UserData=u;t=text(a,1,1,'0.0');t.Tag='native_boundary_value_label';t.UserData=u;
verifyBoundary(a,v);
p.FaceColor=[1 0 1];badRGB=false;try,verifyBoundary(a,v);catch,badRGB=true;end;assert(badRGB);
p.FaceColor=cmap(1,:);p.CData=1;badScalar=false;try,verifyBoundary(a,v);catch,badScalar=true;end;assert(badScalar);p.CData=0;
a.UserData.missing_cells=true;badUnknown=false;try,verifyBoundary(a,v);catch,badUnknown=true;end;assert(badUnknown);a.UserData.missing_cells=false;
p.UserData.right_censored=true;badCensor=false;try,verifyBoundary(a,v);catch,badCensor=true;end;assert(badCensor);p.UserData=u;
verifyBoundary(a,v);p.FaceColor='flat';verifyBoundary(a,v);
result=struct('schema','hospital_ies.exact_boundary_RGB_temporary_regression.v1', ...
    'exact_mapped_RGB_accepted',true,'original_flat_accepted',true,'wrong_RGB_rejected',badRGB, ...
    'changed_scalar_rejected',badScalar,'changed_unknown_mask_rejected',badUnknown, ...
    'changed_right_censor_rejected',badCensor,'scientific_figures_written',0,'physics',0);
fid=fopen(outputPath,'w','n','UTF-8');assert(fid>=0);c=onCleanup(@()fclose(fid));fprintf(fid,'%s',jsonencode(result,'PrettyPrint',true));
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
    assert(logical(u.right_censored)==logical(v.right_censored(i,j))&&string(p.CDataMapping)=="scaled");
    if isnumeric(p.FaceColor)
        cmap=colormap(ax);limits=ax.CLim;
        assert(isequaln(cmap,parula(256)),'Publication display must retain exact original parula256.');
        expectedTop=max(Z(isfinite(Z)));if expectedTop==0,expectedTop=1;end
        assert(isequaln(limits,[0 expectedTop]),'Publication display changed original numeric CLim.');
        index=min(size(cmap,1),max(1,1+floor((Z(i,j)-limits(1))/diff(limits)*size(cmap,1))));
        assert(isequaln(p.FaceColor,cmap(index,:)),'Boundary RGB differs from exact original scaled mapping.');
    else
        assert(string(p.FaceColor)=="flat",'Unknown boundary color rendering.');
    end
    found=[];
    for label=labels(:)'
        if numericSame(label.Position(1:2),[j i]),found=[found label];end %#ok<AGROW>
    end
    assert(isscalar(found),'Missing or duplicate boundary label.');suffix="";if v.right_censored(i,j),suffix="*";end
    assert(string(found.String)==string(sprintf('%.1f',Z(i,j)))+suffix);assert(isequaln(found.UserData,u));
end
assert(isequal(seen,isfinite(Z)),'Unknown boundary cell was plotted.');
end

function yes=numericSame(a,b)
a=double(a);b=double(b);if isvector(a)&&isvector(b),a=a(:);b=b(:);end
yes=isequal(size(a),size(b))&&isequal(isnan(a),isnan(b))&&all((abs(a-b)<=1e-10+1e-12*abs(b))|isnan(b),'all');
end
function eqNumeric(a,b)
assert(numericSame(a,b),'Artifact numeric values or unknown masks differ.');
end
