// Local ASR setup owns only the current request. All displayed reasons/stages are fixed codes, never CLI output.
export function createAsrSetup({t, notify, confirm, install, ready, repaint}) {
  let installing = false;
  const stages = new Set(['download', 'extract', 'install', 'test']);
  const progress = stage => notify({kind:'progress', title:t('asrFix.title'),
    text:t('p387Voice.' + (stages.has(stage) ? stage : 'prepare')), ms:0});
  const run = async () => {
    if (installing) { progress('prepare'); return; }
    if (!(await confirm())) return;
    installing = true; repaint(); progress('prepare');
    let result;
    try { result = await install(stage => progress(stage)); }
    catch { result = {ok:false,why:'failed'}; }
    installing = false;
    if (result.ok && result.state === 'ready') {
      ready(); repaint();
      notify({kind:'success', title:t('p387Voice.readyTitle'), text:t('p387Voice.ready'), ms:0,
        actions:[{label:t('p387Voice.dismiss'),fn:()=>{}}]});
    } else {
      repaint();
      const why = ['download_failed','extract_failed','install_failed','test_failed','prepare_failed','off','broken','busy','offline','timeout'].includes(result.why) ? result.why : 'failed';
      notify({kind:'error', title:t('p387Voice.failedTitle'), text:t('p387Voice.' + why), ms:0,
        actions:[{label:t('p387Voice.retry'),fn:run}]});
    }
  };
  return {run, busy:()=>installing};
}
