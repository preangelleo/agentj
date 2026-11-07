export function expectedMatrixConfigs(mode){
 if(!['full','touch'].includes(mode))throw Error('Expected full or touch matrix');
 return (mode==='touch'?[390]:[360,390,768,1440]).flatMap(width=>['light','dark'].flatMap(theme=>['zh','en'].map(lang=>({width,height:width===360?800:width===390?844:width===768?1024:900,theme,lang}))));
}
export const configKey=cfg=>[cfg.width,cfg.height,cfg.theme,cfg.lang].join('|');
export function validateMatrixConfigs(configs,mode){
 const expected=expectedMatrixConfigs(mode).map(configKey),actual=configs.map(configKey),errors=[];
 for(const key of expected)if(actual.filter(x=>x===key).length!==1)errors.push({error:'Required configuration must occur exactly once',key,count:actual.filter(x=>x===key).length});
 for(const key of actual)if(!expected.includes(key))errors.push({error:'Unexpected configuration',key});
 return errors;
}
