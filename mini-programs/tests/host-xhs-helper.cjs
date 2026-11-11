'use strict';

const { createHash } = require('node:crypto');
const fs = require('node:fs/promises');
const { createRequire } = require('node:module');
const path = require('node:path');

function fingerprint(text) {
  return createHash('sha256').update(text).digest('hex');
}

// These are webpack loaders. Their callback returns the generated source;
// reading it never executes the generated mini program or its event handlers.
function runLoader(loader, compiler, resourcePath, source, options, warnings) {
  return new Promise((resolve, reject) => {
    const context = {
      resourcePath, resourceQuery: '', _compiler: compiler,
      getOptions: () => options,
      addDependency() {},
      emitWarning: warning => warnings.push(String(warning.message || warning)),
      emitError: reject,
      async: () => (err, code) => err ? reject(err) : resolve(code)
    };
    Promise.resolve(loader.call(context, source)).catch(reject);
  });
}

function fileInfo(file, input, output) {
  return {
    file, sourceSha256: fingerprint(input), outputSha256: fingerprint(output),
    outputBytes: Buffer.byteLength(output)
  };
}

async function validateXhs({ projectRoot, toolRoot, templates, styles }) {
  const toolRequire = createRequire(path.resolve(toolRoot, 'package.json'));
  const cliRequire = createRequire(toolRequire.resolve('xhs-mp-cli'));
  const coreRequire = createRequire(cliRequire.resolve('xhs-mp-core'));
  const compilerEntry = coreRequire.resolve('xhs-mp-compiler-cli');
  const compilerRequire = createRequire(compilerEntry);
  const compilerDir = path.dirname(compilerEntry);
  const { getPlatform } = compilerRequire('xhs-mp-compiler-utils');
  if (getPlatform() !== 'xhs') throw new Error('The XHS compiler must use the xhs platform.');

  const mlLoader = compilerRequire('xhs-mp-ml-loader');
  const cssLoader = require(path.join(compilerDir, 'presets/loaders/wxss-loader.js'));
  const styleModule = require(path.join(compilerDir, 'presets/loaders/mini-style-loader.js'));
  const { ENV } = require(path.join(compilerDir, 'types/index.js'));
  const { parse } = compilerRequire('@babel/parser');
  const miniprogramDir = path.resolve(projectRoot).split(path.sep).join('/');
  const { appJSON } = compilerRequire('xhs-mp-project').utils.transformAppJson('', miniprogramDir);
  const compiler = { project: { miniprogramDir, appJSON }, packSetting: { env: ENV.production, compressCss: false } };
  const packageJson = JSON.parse(await fs.readFile(path.join(compilerDir, '../package.json'), 'utf8'));
  const result = {
    compiler: packageJson.name, version: packageJson.version,
    templateLoader: 'xhs-mp-ml-loader', styleLoaders: ['wxss-loader', 'mini-style-loader'],
    templates: [], styles: [], warnings: []
  };

  // The ML loader's parser and generator share internal state across calls.
  for (const file of templates) {
    const fullPath = path.resolve(projectRoot, file);
    try {
      const source = await fs.readFile(fullPath, 'utf8');
      const output = await runLoader(mlLoader, compiler, fullPath, source, { removeComentNode: true }, result.warnings);
      parse(output, { sourceType: 'module' });
      if (!/export default\s*\{/.test(output) || !/render:\s*function/.test(output)) {
        throw new Error('No render-function module was generated.');
      }
      result.templates.push({
        ...fileInfo(file, source, output),
        renderLists: (output.match(/\brenderList\(/g) || []).length,
        tapHandlers: [...new Set(Array.from(output.matchAll(/"xhs__p:tap"\s*:\s*"([^"]+)"/g), match => match[1]))]
      });
    } catch (err) { throw new Error(file + ': ' + (err.message || err)); }
  }

  for (const file of styles) {
    const fullPath = path.resolve(projectRoot, file);
    try {
      const source = await fs.readFile(fullPath, 'utf8');
      const css = await runLoader(cssLoader, compiler, fullPath, source, {}, result.warnings);
      const output = await runLoader(styleModule, compiler, fullPath, css, { independentFile: false }, result.warnings);
      parse(output, { sourceType: 'module' });
      if (css.trim() && !output.includes('__XHS_ADD_STYLE(')) throw new Error('No XHS style module was generated.');
      result.styles.push({ ...fileInfo(file, source, output), transformedCssBytes: Buffer.byteLength(css) });
    } catch (err) { throw new Error(file + ': ' + (err.message || err)); }
  }
  return result;
}

module.exports = { validateXhs };
