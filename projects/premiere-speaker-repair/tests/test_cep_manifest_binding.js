const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const vm = require('vm');

const extensionPath = fs.mkdtempSync(path.join(os.tmpdir(), 'narration-cep-'));
const dataDir = path.join(extensionPath, 'data');
fs.mkdirSync(dataDir, {recursive: true});
fs.mkdirSync(path.join(extensionPath, 'js'), {recursive: true});
fs.copyFileSync(
  path.join(__dirname, '..', 'deployment', 'local-runtime', 'cep-and-app', 'studio-launcher.js'),
  path.join(extensionPath, 'js', 'studio-launcher.js'),
);

const projectPath = '/案件/サンプル案件/サンプル案件.prproj';
const manifest = {
  schemaVersion: 3,
  caseName: 'サンプル案件',
  revision: 'revision-new',
  updatedAt: '2026-08-07T16:42:03+09:00',
  srtPath: '/tmp/latest.srt',
  premiereBinding: {projectPath},
};
fs.writeFileSync(
  path.join(dataDir, 'current_narration_export.json'),
  JSON.stringify(manifest),
  'utf8',
);
fs.writeFileSync(
  path.join(dataDir, 'premiere_project_bindings.json'),
  JSON.stringify({
    schemaVersion: 1,
    projects: {
      [projectPath]: {manifestPath: '/unreadable/old.json', revision: 'revision-old'},
    },
  }),
  'utf8',
);

const elements = new Map();
function element(id) {
  if (!elements.has(id)) elements.set(id, {disabled: false, textContent: '', value: '1', onclick: null});
  return elements.get(id);
}

const sandbox = {
  require,
  console,
  SystemPath: {EXTENSION: 'extension'},
  CSInterface: function () {
    this.getSystemPath = () => extensionPath;
    this.evalScript = (_script, callback) => callback(projectPath);
  },
  document: {getElementById: element},
  window: {setInterval: () => 0, setTimeout: () => 0, clearInterval: () => {}},
};

const sourcePath = path.join(
  __dirname,
  '..',
  'deployment',
  'local-runtime',
  'cep-and-app',
  'main.js',
);
vm.runInNewContext(fs.readFileSync(sourcePath, 'utf8'), sandbox, {filename: sourcePath});

const stored = JSON.parse(fs.readFileSync(path.join(dataDir, 'premiere_project_bindings.json'), 'utf8'));
const binding = stored.projects[projectPath];
assert.strictEqual(binding.revision, 'revision-new');
assert.ok(binding.manifestPath.startsWith(path.join(dataDir, 'project_manifests')));
assert.deepStrictEqual(JSON.parse(fs.readFileSync(binding.manifestPath, 'utf8')), manifest);
assert.strictEqual(element('caseName').textContent, 'サンプル案件');
assert.strictEqual(element('applyButton').disabled, false);

fs.rmSync(extensionPath, {recursive: true, force: true});
console.log('CEP latest manifest binding: OK');
