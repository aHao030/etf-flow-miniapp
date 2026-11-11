// Build both candidates from one captured source snapshot in a separate directory.
import path from 'node:path';
import fs from 'node:fs';
import {fileURLToPath} from 'node:url';
import {build,options} from '../build.mjs';
const root=fileURLToPath(new URL('../',import.meta.url));
const [output,...args]=process.argv.slice(2);
if(!output)throw Error('缺少暂存目录');
const reference=path.join(output,'known-files.json');
const known=fs.existsSync(reference)?JSON.parse(fs.readFileSync(reference,'utf8')):null;
console.log(JSON.stringify(build(root,options(args),path.resolve(output),known)));
