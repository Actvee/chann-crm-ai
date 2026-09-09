// Exercise permission-based shortcuts using the shipped TypeScript, no browser/auth bypass.
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const assert = require('node:assert/strict');
const presentation = path.resolve(__dirname, '../../presentation');
const ts = require(path.join(presentation, 'node_modules/typescript'));
function load(relative) {
  const filename = path.join(presentation, relative);
  const compiled = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: {module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020},
  });
  const module = new Module(filename, moduleParent);
  module.filename = filename;
  module.paths = Module._nodeModulePaths(path.dirname(filename));
  module._compile(compiled.outputText, filename);
  return module.exports;
}
const moduleParent = module;
const {th} = load('lib/i18n/th.ts');
const {homeEntries, currentKey, navGroups} = load('app/liff/_nav-model.tsx');
const keys = (permissions, owner=false) => homeEntries(th, new Set(permissions), owner).map(e => e.key);
assert.deepEqual(keys(['chat_session.view','ticket.read','approval.view','customer.read']), ['chats','tickets','approvals','reports']);
assert.deepEqual(keys(['customer.read','deal.read','quote.read']), ['customers','deals','quotes']);
assert.deepEqual(keys(['role.manage']), ['members','roles']);
assert.deepEqual(keys([],true), ['chats','customers','deals','quotes']);
const entries = navGroups(th, 'sales').flatMap(g => g.entries);
assert.equal(currentKey(entries, '/liff/sales/reports/ai'), 'aiReports');
console.log('5 dashboard navigation checks passed (source logic, not visual acceptance)');
