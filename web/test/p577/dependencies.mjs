import { createRequire } from 'node:module';
import { resolve } from 'node:path';
const require = createRequire(import.meta.url);
export function requireTool(name) {
  try {
    return require(name);
  } catch (original) {
    if (process.env.P577_NODE_MODULES) return require(resolve(process.env.P577_NODE_MODULES, name));
    throw new Error(`P577 needs ${name}. Use --modules <existing node_modules> or P577_NODE_MODULES. No packages are installed automatically.`, {
      cause: original
    });
  }
}

export function resolveTool(name){try{return require.resolve(name)}catch(original){if(process.env.P577_NODE_MODULES)return require.resolve(resolve(process.env.P577_NODE_MODULES,name));throw original}}
