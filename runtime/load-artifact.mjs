// Mechanical import helper; no composition or style defaults.
import fs from 'node:fs';
import {fileURLToPath,pathToFileURL} from 'node:url';
import path from 'node:path';
export async function loadArtifact(){
 const cfg=JSON.parse(fs.readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)),'runtime.local.json'),'utf8'));
 if(!cfg.artifact_tool_entry)throw Error('Configure your legally available artifact_tool_entry');
 return import(pathToFileURL(cfg.artifact_tool_entry).href);
}
