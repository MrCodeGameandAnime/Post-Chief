/* Apps Script V8 Community Connector. Never store provider credentials here. */
function baseUrl() {
  var value = PropertiesService.getScriptProperties().getProperty('POST_CHIEF_BASE_URL');
  if (!value || !/^https:\/\/[A-Za-z0-9.-]+$/.test(value)) fail('Set POST_CHIEF_BASE_URL to your stable HTTPS origin in Script Properties.');
  return value;
}
function fail(message) {
  DataStudioApp.createCommunityConnector().newUserError().setText(message).throwException();
}
function api(path, key) {
  key = key || PropertiesService.getUserProperties().getProperty('postchief.key');
  if (!key) fail('Connect using a Post Chief key with analytics:read access.');
  var response = UrlFetchApp.fetch(baseUrl() + '/api/reports/looker/v1/' + path, {
    headers: {Authorization: 'Bearer ' + key}, followRedirects: false, muteHttpExceptions: true
  });
  var status = response.getResponseCode();
  if (status === 401 || status === 403) fail('Post Chief access denied. Check the key and analytics:read permission.');
  if (status !== 200) fail('Post Chief could not provide the saved report. Collect a report in its dashboard and check the server connection.');
  var value;
  try { value = JSON.parse(response.getContentText()); }
  catch (error) { fail('Post Chief returned an invalid report response.'); }
  if (value.schema_version !== 1) fail('Unsupported Post Chief export version.');
  return value;
}
function getAuthType() { return {type: 'KEY'}; }
function setCredentials(request) {
  var key = request.key;
  if (typeof key !== 'string' || !key || key.length > 4096) return {errorCode: 'INVALID_CREDENTIALS'};
  try { api('schema', key); }
  catch (error) { return {errorCode: 'INVALID_CREDENTIALS'}; }
  PropertiesService.getUserProperties().setProperty('postchief.key', key);
  return {errorCode: 'NONE'};
}
function isAuthValid() {
  try { api('schema'); return true; } catch (error) { return false; }
}
function resetAuth() { PropertiesService.getUserProperties().deleteProperty('postchief.key'); }
function isAdminUser() { return false; }
function getConfig() {
  var cc = DataStudioApp.createCommunityConnector();
  var config = cc.getConfig();
  config.newInfo().setId('notice').setText('Saved snapshots only. Select one source table; filter charts to one metric and currency. Provider freshness and coverage remain in the exported fields.');
  var select = config.newSelectSingle().setId('source').setName('Post Chief saved report source').setAllowOverride(false);
  var count = 0, offset = 0;
  for (var page = 0; page < 10; page++) {
    var value = api('sources?offset=' + offset);
    value.sources.forEach(function(source) {
      select.addOption(config.newOption().setLabel(source.account_name + ' · ' + source.provider + ' · ' + source.table)
        .setValue(JSON.stringify({account_id: source.account_id, table: source.table})));
      count++;
    });
    if (value.next_offset === null) break;
    offset = value.next_offset;
    if (page === 9) fail('More than 1,000 reporting accounts; use the authenticated API export for this organization.');
  }
  if (!count) fail('No supported saved reports. Collect an advertising account report or a report with snapshot metrics in Post Chief first.');
  config.setDateRangeRequired(true);
  return config.build();
}
function getSchema() { return {schema: api('schema').schema}; }
function getData(request) {
  var selected;
  try { selected = JSON.parse(request.configParams.source); }
  catch (error) { fail('Select a saved report source.'); }
  if (!selected || typeof selected.account_id !== 'string' || typeof selected.table !== 'string') fail('Select a saved report source.');
  var range = request.dateRange || {};
  if (!/^\d{4}-\d{2}-\d{2}$/.test(range.startDate || '') || !/^\d{4}-\d{2}-\d{2}$/.test(range.endDate || '')) fail('Choose a valid report date range.');
  var value = api('data?account_id=' + encodeURIComponent(selected.account_id) + '&source_table=' + encodeURIComponent(selected.table) +
    '&start_date=' + range.startDate + '&end_date=' + range.endDate);
  var names = request.fields.map(function(field) { return field.name; });
  var schema = names.map(function(name) {
    var field = value.schema.find(function(item) { return item.name === name; });
    if (!field) fail('Unknown field. Reconnect to refresh the export schema.');
    return field;
  });
  return {schema: schema, rows: value.rows.map(function(row) {
    return {values: names.map(function(name) { return row[name] === undefined ? null : row[name]; })};
  }), filtersApplied: false};
}
