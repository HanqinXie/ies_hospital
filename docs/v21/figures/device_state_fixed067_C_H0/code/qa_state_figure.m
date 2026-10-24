function audit = qa_state_figure(manifestPath, knownSHA, figureDir, outputDir)
%QA_STATE_FIGURE Reopen and inspect the one predeclared native state figure.
% DG pre is only the label recorded at the interval start, not proof that
% the state persisted throughout that interval. DG post is an endpoint point.
% GT states are only the original observation points: no transition times,
% interpolation, forward fill, permission-to-ready inference, or physics run.
% This custom state-object audit is not the original scientific release gate.

assert(nargin == 4, 'stateqa:Arguments', 'Four explicit arguments are required.');
assert(~isfolder(outputDir) && ~isfile(outputDir), 'stateqa:FreshOutput', ...
    'The audit output directory must not already exist.');
[ok, message] = mkdir(outputDir);
assert(ok, 'stateqa:OutputDirectory', '%s', message);
audit = struct('schema', 'hospital_ies.matlab_object_audit.v2', ...
    'audit_kind', 'custom_state_audit', 'expected_count', 1, 'matches', 0, ...
    'expected_figure_count', 1, 'matched_figure_count', 0, 'object_checks_passed', false, ...
    'all_expected_matched', false, 'failures', {{}}, 'details', struct([]), ...
    'physics_attempts_started', 0, 'scientific_gate', false, ...
    'scientific_gate_status', 'NOT_RUN', 'matlab_object_audit_status', 'RUNNING');
reportPath = fullfile(outputDir, 'MATLAB_OBJECT_AUDIT.json');
try
    sourceSHA = '4dfe62ae105d2696b7782dc36c83ec63c6551e2cded601d6f15c0d4083058add';
    knownSHA = char(knownSHA);
    must(~isempty(regexp(knownSHA, '^[0-9a-f]{64}$', 'once')), 'KnownSHA', ...
        'The external manifest SHA must be 64 lowercase hexadecimal characters.');
    must(strcmp(fileSHA(manifestPath), knownSHA), 'ManifestSHA', ...
        'The manifest differs from its external known SHA.');
    manifest = jsondecode(fileread(manifestPath));
    must(strcmp(manifest.schema, 'hospital_ies.fixed067_state_figure_data.v1'), ...
        'ManifestSchema', 'Unexpected data-manifest schema.');
    must(strcmp(manifest.source_binding_sha256, sourceSHA), 'SourceBinding', ...
        'The exact predeclared source binding differs.');
    must(strcmp(manifest.scenario_id, 'm21_boundary_a_5c16d21d8dff33fef94e') && ...
        strcmp(manifest.policy_id, 'C_sci_v21') && strcmp(manifest.configuration_id, 'H0'), ...
        'Identity', 'The figure is not the predeclared 067 C/H0 case.');
    must(manifest.segment_count == 75 && manifest.observation_count == 60 && ...
        manifest.window_minutes == 900, 'Counts', 'The fixed record/window counts differ.');
    members = verifyMembers(manifestPath, manifest.members);
    must(strcmp(manifest.state_data_mat, 'STATE_DATA.mat') && ...
        any(strcmp(members, 'STATE_DATA.mat')), 'MATMember', 'STATE_DATA.mat must be a verified member.');
    [palette, labels, categories] = fixedContract();
    for name = {'bus', 'dg', 'gt'}
        key = name{1};
        must(isequal(manifest.palette.(key), palette.(key)), 'Palette', ...
            ['The fixed ' key ' palette differs.']);
        must(isequal(string(manifest.categories.(key)(:)), string(categories.(key)(:))), ...
            'Categories', ['The fixed ' key ' category mapping differs.']);
    end
    contract = manifest.figure_contract;
    must(strcmp(contract.name, 'STATE_fixed067_C_H0') && ...
        isequal(contract.physical_size_cm(:).', [18 20]), 'FigureContract', 'Name or physical size differs.');
    for name = fieldnames(labels).'
        key = name{1};
        must(strcmp(contract.labels.(key), labels.(key)), 'Labels', ['The fixed ' key ' label differs.']);
    end
    must(strcmp(contract.bus_axis_tag, 'BUS_AXIS') && strcmp(contract.dg_axis_tag, 'DG_AXIS') && ...
        strcmp(contract.gt_axis_tag, 'GT_AXIS') && contract.GT_points_only && ...
        contract.DG_pre_is_interval_start_label && contract.DG_post_is_endpoint_point && ...
        contract.no_forward_fill && contract.no_permission_to_ready_conversion && ...
        contract.unknown_code == 0 && contract.unknown_is_not_physical_zero, ...
        'Semantics', 'The recorded-label/point-only contract differs.');
    usedFields = {'segment_start_min', 'segment_end_min', 'bus_code', 'dg_pre_code', ...
        'dg_post_code', 'observation_min', 'gt_status_code'};
    data = load(fullfile(fileparts(manifestPath), manifest.state_data_mat), usedFields{:});
    requireNumeric(data, 'segment_start_min', [75 1]);
    requireNumeric(data, 'segment_end_min', [75 1]);
    requireCodes(data, 'bus_code', [75 1], 4);
    requireCodes(data, 'dg_pre_code', [75 1], 6);
    requireCodes(data, 'dg_post_code', [75 1], 6);
    requireNumeric(data, 'observation_min', [60 1]);
    requireCodes(data, 'gt_status_code', [60 3], 7);
    starts = double(data.segment_start_min); ends = double(data.segment_end_min);
    observations = double(data.observation_min);
    must(starts(1) == 0 && ends(end) == 900 && all(ends > starts) && ...
        isequal(ends(1:end-1), starts(2:end)), 'IntervalCoverage', 'Native intervals are not contiguous [0,900].');
    must(all(diff(observations) > 0) && observations(1) == 0 && all(observations < 900), ...
        'ObservationClock', 'Observation points must retain their strictly increasing native clock.');

    files = dir(fullfile(figureDir, '*.fig'));
    must(numel(files) == 1 && strcmp(files(1).name, 'STATE_fixed067_C_H0.fig'), ...
        'UniqueFIG', 'Exactly the predeclared native FIG is required.');
    figPath = fullfile(figureDir, files(1).name); originalSHA = fileSHA(figPath);
    fig = openfig(figPath, 'new', 'invisible');
    cleanupFigure = onCleanup(@() close(fig)); %#ok<NASGU>
    must(isstruct(fig.UserData) && isfield(fig.UserData, 'source_binding_sha256') && ...
        isfield(fig.UserData, 'manifest_sha256') && ...
        strcmp(fig.UserData.source_binding_sha256, sourceSHA) && ...
        strcmp(fig.UserData.manifest_sha256, knownSHA), 'FigureBinding', 'FIG source/manifest bindings differ.');
    must(strcmp(fig.Units, 'centimeters') && isequal(fig.Position(3:4), [18 20]), ...
        'PhysicalSize', 'Saved native figure size must be exactly 18 by 20 cm.');
    must(isempty(findall(fig, 'Type', 'image')), 'Images', 'Raster image objects are forbidden.');
    allAxes = findall(fig, 'Type', 'axes');
    must(numel(allAxes) == 3, 'AxesCount', 'Exactly three native axes are required.');
    layout = findall(fig, '-isa', 'matlab.graphics.layout.TiledChartLayout');
    must(numel(layout) == 1 && isequal(layout.GridSize, [3 1]), 'Layout', 'Exactly one 3 by 1 tiled layout is required.');
    titleObject = findall(fig, 'Tag', 'MAIN_TITLE');
    must(numel(titleObject) == 1 && isprop(titleObject, 'String') && ...
        strcmp(textValue(titleObject.String), labels.title), 'Title', 'The native MAIN_TITLE text differs.');
    must(isempty(textValue(layout.Title.String)), 'LayoutTitle', 'The reserved layout title must remain empty.');
    tags = {'BUS_AXIS', 'DG_AXIS', 'GT_AXIS'};
    yLimits = {[-.6 4.6], [-.6 6.6], [.4 3.6]};
    yLabels = {labels.bus_y, labels.dg_y, labels.gt_y};
    axisTitles = {'原生区间日志标签（非连续电气暂态）', ...
        '区间起端快照（色块）；末端快照（菱形）', ...
        '观测中的 GT physical status（原 observation_time）'};
    axisHandles = cell(1, 3);
    for k = 1:3
        axisHandles{k} = uniqueTag(fig, tags{k}, 'axes'); a = axisHandles{k};
        must(a.Layout.Tile == k && isequal(a.XLim, [0 900]) && isequal(a.YLim, yLimits{k}), ...
            'AxesMapping', ['Native tile/limits differ: ' tags{k}]);
        must(strcmp(textValue(a.YLabel.String), yLabels{k}), 'YLabel', ['Wrong Y label: ' tags{k}]);
        must(strcmp(textValue(a.Title.String), axisTitles{k}), 'AxisTitle', ['Wrong stage-specific title: ' tags{k}]);
        must(isempty(findall(a, 'Type', 'line')) && isempty(findall(a, 'Type', 'surface')), ...
            'UnexpectedSeries', 'Continuous lines/surfaces are not recorded state points or interval labels.');
    end
    busAxes = axisHandles{1}; dgAxes = axisHandles{2}; gtAxes = axisHandles{3};
    must(isequal(busAxes.YTick, 0:4) && ...
        isequal(string(busAxes.YTickLabel(:)), string(categories.bus(:))), 'BusTicks', 'Bus categorical mapping differs.');
    must(isequal(dgAxes.YTick, 0:6) && ...
        isequal(string(dgAxes.YTickLabel(:)), string(categories.dg(:))), 'DGTicks', 'DG categorical mapping differs.');
    must(isequal(gtAxes.YTick, 1:3) && ...
        isequal(string(gtAxes.YTickLabel(:)), ["GT1"; "GT2"; "GT3"]), 'GTTicks', 'GT row mapping differs.');
    must(strcmp(textValue(gtAxes.XLabel.String), labels.x), 'XLabel', 'The time-axis label differs.');
    must(numel(findall(busAxes, 'Type', 'patch')) == 75 && ...
        numel(findall(dgAxes, 'Type', 'patch')) == 75 && isempty(findall(gtAxes, 'Type', 'patch')) && ...
        isempty(findall(busAxes, 'Type', 'scatter')) && ...
        numel(findall(dgAxes, 'Type', 'scatter')) == 1 && ...
        numel(findall(gtAxes, 'Type', 'scatter')) == 3, 'NativeObjects', 'Unexpected native patch/scatter counts.');
    checkSegments(fig, busAxes, 'BUS_SEG_', starts, ends, double(data.bus_code), palette.bus);
    checkSegments(fig, dgAxes, 'DG_SEG_', starts, ends, double(data.dg_pre_code), palette.dg);
    post = uniqueTag(fig, 'DG_POST', 'scatter');
    must(isequal(ancestor(post, 'axes'), dgAxes) && ...
        isequal(double(post.XData(:)), ends) && ...
        isequal(double(post.YData(:)), double(data.dg_post_code)), 'DGPostCoordinates', 'DG post must retain all 75 original endpoints.');
    must(any(strcmp(post.Marker, {'d', 'diamond'})) && blackMarker(post, 75), ...
        'DGPostStyle', 'DG post must be a pure-black diamond marker.');
    for k = 1:3
        point = uniqueTag(fig, sprintf('GT_%d', k), 'scatter');
        must(isequal(ancestor(point, 'axes'), gtAxes) && ...
            isequal(double(point.XData(:)), observations) && ...
            isequal(double(point.YData(:)), ones(60, 1) * k), ...
            'GTCoordinates', sprintf('GT%d must retain only its 60 original observation points.', k));
        expectedRGB = palette.gt(double(data.gt_status_code(:, k)) + 1, :);
        must(isequal(point.CData, expectedRGB), 'GTColors', sprintf('GT%d true CData differs from the fixed mapping.', k));
        must(strcmp(point.MarkerFaceColor, 'flat') && point.MarkerFaceAlpha == 1 && ...
            (strcmp(point.MarkerEdgeColor, 'none') || strcmp(point.MarkerEdgeColor, 'flat')), ...
            'GTVisibleColors', 'GT marker styling must display its true palette CData without transparency.');
        must(isstruct(point.UserData) && isfield(point.UserData, 'gt_index') && ...
            isfield(point.UserData, 'status_code') && isfield(point.UserData, 'categories') && ...
            point.UserData.gt_index == k && ...
            isequal(double(point.UserData.status_code(:)), double(data.gt_status_code(:, k))) && ...
            isequal(string(point.UserData.categories(:)), string(categories.gt(:))), ...
            'GTTraceability', 'GT index/status/category traceability differs.');
    end
    fontCount = checkFonts(fig, layout);
    verifyMembers(manifestPath, manifest.members);
    must(strcmp(fileSHA(figPath), originalSHA) && strcmp(fileSHA(manifestPath), knownSHA), ...
        'ImmutableInputs', 'FIG or manifest bytes changed while auditing.');
    formats = inspectFormats(figureDir, 'STATE_fixed067_C_H0');
    audit.matches = 1; audit.all_expected_matched = true;
    audit.matched_figure_count = 1; audit.object_checks_passed = true;
    audit.matlab_object_audit_status = 'PASS';
    audit.details = {struct('file', figPath, 'status', 'passed', 'formats', formats, ...
        'figure_size_cm', [18 20], 'axes_count', 3, 'segment_patch_count', 150, ...
        'scatter_count', 4, 'image_count', 0, 'font_object_count', fontCount, ...
        'source_binding_sha256', sourceSHA, 'manifest_sha256', knownSHA, ...
        'coordinate_check', 'EXACT_NATIVE_X_Y_CDATA', 'semantics', ...
        'DG_PRE_START_LABEL_DG_POST_ENDPOINT_GT_OBSERVATION_POINTS_ONLY')};
    writeAudit(reportPath, audit);
catch exception
    audit.matlab_object_audit_status = 'FAIL';
    audit.matches = 0; audit.matched_figure_count = 0;
    audit.object_checks_passed = false; audit.all_expected_matched = false;
    audit.details = struct([]);
    audit.failures = {[exception.identifier ': ' exception.message]};
    writeAudit(reportPath, audit);
    rethrow(exception);
end
end

function members = verifyMembers(manifestPath, records)
root = char(java.io.File(fileparts(manifestPath)).getCanonicalPath());
members = cell(numel(records), 1);
must(~isempty(records), 'Members', 'Manifest members must not be empty.');
for k = 1:numel(records)
    relative = char(records(k).path); components = regexp(relative, '[\\/]', 'split');
    must(~isempty(relative) && isempty(regexp(relative, '^([A-Za-z]:|[\\/])', 'once')) && ...
        ~any(strcmp(components, '..')) && ~any(strcmp(components, '.')), ...
        'MemberPath', 'Manifest members must be root-bound relative paths.');
    resolved = char(java.io.File(fullfile(root, relative)).getCanonicalPath());
    must(startsWith(lower(resolved), lower([root filesep])), 'MemberRoot', 'Member path escapes the manifest root.');
    info = dir(resolved);
    must(numel(info) == 1 && ~info.isdir && info.bytes == records(k).bytes, 'MemberBytes', ['Member size differs: ' relative]);
    must(strcmp(fileSHA(resolved), records(k).sha256), 'MemberSHA', ['Member SHA differs: ' relative]);
    members{k} = relative;
end
must(numel(unique(lower(string(members)))) == numel(members), 'DuplicateMember', 'Duplicate member paths are forbidden.');
end

function requireNumeric(data, key, dimensions)
must(isfield(data, key) && isnumeric(data.(key)) && isreal(data.(key)) && ...
    isequal(size(data.(key)), dimensions) && all(isfinite(data.(key)), 'all'), ...
    'MATField', ['Missing, invalid or wrong-shaped MAT field: ' key]);
end

function requireCodes(data, key, dimensions, maximum)
requireNumeric(data, key, dimensions); values = double(data.(key));
must(all(values >= 0 & values <= maximum & values == fix(values), 'all'), ...
    'MATCodes', ['Out-of-mapping status code: ' key]);
end

function checkSegments(fig, axesHandle, prefix, starts, ends, codes, palette)
for k = 1:75
    point = uniqueTag(fig, sprintf('%s%03d', prefix, k), 'patch');
    must(isequal(ancestor(point, 'axes'), axesHandle), 'PatchAxis', 'Segment patch is on the wrong native axis.');
    expectedX = [starts(k) ends(k) ends(k) starts(k)];
    expectedY = [codes(k)-.34 codes(k)-.34 codes(k)+.34 codes(k)+.34];
    must(isequal(double(point.XData(:).'), expectedX) && ...
        isequal(double(point.YData(:).'), expectedY), 'PatchCoordinates', ['True patch coordinates differ: ' point.Tag]);
    must(isequal(point.FaceColor, palette(codes(k)+1, :)) && point.FaceAlpha == 1, ...
        'PatchColor', ['True patch color differs: ' point.Tag]);
end
end

function handle = uniqueTag(fig, tag, objectType)
handle = findall(fig, 'Tag', tag);
must(numel(handle) == 1 && strcmp(handle.Type, objectType), 'Tag', ['Missing, duplicate or wrong-type native object: ' tag]);
end

function result = blackMarker(point, count)
colors = double(point.CData);
result = (isequal(colors, [0 0 0]) || isequal(colors, zeros(count, 3))) && ...
    blackProperty(point.MarkerFaceColor, true) && blackProperty(point.MarkerEdgeColor, false) && ...
    point.MarkerFaceAlpha == 1;
end

function result = blackProperty(value, isFace)
if isnumeric(value)
    result = isequal(value, [0 0 0]);
else
    result = any(strcmp(char(value), {'flat', 'k', 'black'})) || (~isFace && strcmp(char(value), 'none'));
end
end

function count = checkFonts(fig, layout)
% findall(-property,FontName) does not reliably visit layout-owned Text.
% Inspect all three explicitly, including the empty layout title/labels.
objects = num2cell(findall(fig, '-property', 'FontName'));
objects = [objects(:); {layout.Title; layout.XLabel; layout.YLabel}];
count = numel(objects);
must(count > 0, 'Fonts', 'No native font objects were found.');
for k = 1:count
    item = objects{k}; font = char(item.FontName);
    if isprop(item, 'String')
        text = textValue(item.String);
        hasChinese = any(double(text) >= 19968 & double(text) <= 40959);
        if isempty(text)
            valid = any(strcmpi(font, {'SimSun', '宋体', 'Times New Roman'}));
        elseif hasChinese
            valid = any(strcmpi(font, {'SimSun', '宋体'}));
        else
            valid = strcmpi(font, 'Times New Roman');
        end
    else
        valid = strcmpi(font, 'Times New Roman');
    end
    must(valid, 'Fonts', ['Native FontName violates the Chinese/Latin rule: ' font]);
end
end

function value = textValue(value)
% A character row is one string. Do not reshape it into one string per char.
if ischar(value)
    if isempty(value) || size(value, 1) == 1, return; end
    value = cellstr(value);
end
value = char(join(string(value(:)), newline));
end

function formats = inspectFormats(folder, base)
formats = struct();
for extension = {'fig', 'svg', 'pdf', 'png'}
    key = extension{1}; path = fullfile(folder, [base '.' key]); info = dir(path);
    must(numel(info) == 1 && ~info.isdir && info.bytes > 0, 'Format', ['Missing or empty required output: ' path]);
    hash = fileSHA(path);
    formats.(key) = struct('path', path, 'sha', hash, 'sha256', hash, 'bytes', info.bytes);
end
end

function [palette, labels, categories] = fixedContract()
palette.bus = [.7 .7 .7; .2 .55 .3; .9 .45 .15; .35 .55 .8; .65 .4 .75];
palette.dg = [.7 .7 .7; .8 .8 .8; .9 .6 .2; .4 .65 .8; .2 .55 .3; .8 .2 .2; .4 .15 .15];
palette.gt = [.7 .7 .7; .65 .65 .65; .9 .65 .35; .3 .6 .8; .15 .5 .25; .8 .2 .2; .55 .2 .65; .45 .3 .15];
labels = struct('title', '既定复合扰动：设备状态记录（067 C/H0）', 'bus_y', '母线区间标签', ...
    'dg_y', 'DG 起端标签及末端点', 'gt_y', 'GT 原始观测点', 'x', '时间 / min');
categories.bus = {'UNKNOWN', 'GRID', 'ISLAND', 'RETURN_PREP', 'TRANSFER_OPEN'};
categories.dg = {'UNKNOWN', 'OFF', 'STARTING', 'READY', 'CONNECTED', 'EMPTY', 'FAILED'};
categories.gt = {'UNKNOWN', 'OFF', 'WAIT_MIN_OFF', 'STARTING', 'RUNNING', ...
    'EXOGENOUS_UNAVAILABLE', 'PROTECTION_LOCKED', 'INTERLOCK_LATCHED'};
end

function hash = fileSHA(path)
stream = fopen(path, 'rb'); must(stream >= 0, 'ReadFile', ['Cannot read: ' char(path)]);
cleanupStream = onCleanup(@() fclose(stream)); %#ok<NASGU>
digest = java.security.MessageDigest.getInstance('SHA-256');
while ~feof(stream)
    bytes = fread(stream, 1024*1024, '*uint8');
    if ~isempty(bytes), digest.update(typecast(bytes, 'int8')); end
end
hash = lower(reshape(dec2hex(typecast(digest.digest(), 'uint8'), 2).', 1, []));
end

function writeAudit(path, audit)
stream = fopen(path, 'w', 'n', 'UTF-8');
assert(stream >= 0, 'stateqa:AuditWrite', 'Cannot create audit report.');
cleanupStream = onCleanup(@() fclose(stream)); %#ok<NASGU>
fprintf(stream, '%s\n', jsonencode(audit, 'PrettyPrint', true));
end

function must(condition, code, message)
assert(isscalar(condition) && condition, ['stateqa:' code], '%s', message);
end
