export function auditRuntime(meta) {
  const vendor = e => e.closest('.xterm,.katex');
  const paths = new WeakMap();
  const nodePath = e => {
    if (paths.has(e)) return paths.get(e);
    const parts = [];
    for (let n = e; n?.nodeType === 1; n = n.parentElement) {
      let i = 1;
      for (let p = n.previousElementSibling; p; p = p.previousElementSibling) if (p.tagName === n.tagName) i++;
      parts.unshift(n.tagName.toLowerCase() + ':nth-of-type(' + i + ')');
    }
    const p = parts.join('>');
    paths.set(e, p);
    return p;
  };
  const rootContracts = new WeakMap(), resolvedRoots = new WeakMap();
  for (const node of document.querySelectorAll('[data-aj-owner]')) if (node !== document.body && node !== document.documentElement) rootContracts.set(node,{owner:node.getAttribute('data-aj-owner'),score:0,selector:'[data-aj-owner]',evidence:'Explicit component/control root in real DOM; nested boundary preferred; direct interactive ownership independently required'});
  for (const rule of meta.canonicalRoots || []) for (const selector of rule.selector.split(',')) {
    const score = selector.includes('#') ? 100 : selector.split(/\s+/).length;
    for (const node of document.querySelectorAll(selector)) if (node !== document.body && node !== document.documentElement && (!rootContracts.has(node) || rootContracts.get(node).score < score)) rootContracts.set(node, {...rule, score});
  }
  const nearestRoot = e => {
    if (!e || e === document.body || e === document.documentElement) return null;
    if (resolvedRoots.has(e)) return resolvedRoots.get(e);
    const rule = rootContracts.get(e);
    const root = rule ? {
      owner: rule.owner === '$sheet' ? /^(q|question)\|/.test(e.dataset.card || '') || document.body.dataset.kind === 'question' ? 'aj-choice' : 'aj-approval' : rule.owner,
      path: nodePath(e),
      nativeState:{previousElementPath:e.previousElementSibling?nodePath(e.previousElementSibling):null,attributes:Object.fromEntries(['hidden','disabled','aria-pressed','aria-selected','aria-disabled','data-out','data-unpaired','data-state'].map(k=>[k,e.getAttribute(k)])),pseudos:Object.fromEntries(['hover','active','focus','focus-visible','disabled'].map(k=>[k,e.matches(':'+k)]))}, selector: rule.selector, evidence: rule.evidence
    } : nearestRoot(e.parentElement);
    resolvedRoots.set(e, root);
    return root;
  };
  const canonicalRoot = e => e === document.body || e === document.documentElement ? {owner:'aj-shell', path:nodePath(e), selector:e.tagName.toLowerCase(), evidence:'technical viewport root only; never an ancestor fallback'} : nearestRoot(e);
  const descendantUnion = origins => {
    const set=new Set(origins);
    const top=origins.filter(e=>{for(let p=e.parentElement;p;p=p.parentElement)if(set.has(p))return false;return true});
    return [...new Set(top.flatMap(e=>[...e.querySelectorAll('*')]))];
  };
  const labels = new WeakMap();const nodeEvidence={};const nodes={};let nextNode=0;
  const label = e => {
    if (labels.has(e)) return labels.get(e);
    const root = canonicalRoot(e);
    const x = {
      canonicalRoot: root,
      technicalBoundary: ['head','script','style','link','meta','title','base','template'].includes(e.localName) ? {kind:'non-UI-document-resource',reason:'Exact native resource/metadata/template tag; never exempts ordinary hidden UI'} : null,
      path: nodePath(e),
      nativeState:{previousElementPath:e.previousElementSibling?nodePath(e.previousElementSibling):null,attributes:Object.fromEntries(['hidden','disabled','aria-pressed','aria-selected','aria-disabled','data-out','data-unpaired','data-state'].map(k=>[k,e.getAttribute(k)])),pseudos:Object.fromEntries(['hover','active','focus','focus-visible','disabled'].map(k=>[k,e.matches(':'+k)]))},
      expectedOwners: [...new Set((meta.contracts || []).filter(c => e.matches(c.selector)).flatMap(c => c.owners))],
      registeredIdOwner: meta.idOwners?.[e.id]?.owner || null,
      tag: e.tagName.toLowerCase(),
      id: e.id || null,
      classes: [...e.classList],
      owner: e.getAttribute('data-aj-owner') || null,
      inheritedOwner: root?.owner || null,
      visible: !!e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden'
    };
    const rect=e.getBoundingClientRect(),style=getComputedStyle(e);nodeEvidence[x.path]={id:e.id||null,tag:e.tagName.toLowerCase(),visible:x.visible,geometry:{x:rect.x,y:rect.y,width:rect.width,height:rect.height,scrollWidth:e.scrollWidth,clientWidth:e.clientWidth,scrollHeight:e.scrollHeight,clientHeight:e.clientHeight},appearance:Object.fromEntries(['animationName','animationDuration','animationTimingFunction','animationIterationCount','animationPlayState','transitionProperty','transitionDuration','userSelect','webkitUserSelect','webkitTouchCallout','touchAction','pointerEvents','font','fontFamily','fontSize','lineHeight','textAlign','alignItems','justifyContent','padding','border','borderRadius','color','backgroundColor','whiteSpace','webkitLineClamp','overflowX','overflowY','opacity','transform','outline'].map(p=>[p,style[p]]))};
x.nodeId='n'+nextNode++; nodes[x.nodeId]=x; labels.set(e, x);
    nodeEvidence[x.path].animationEvidence=x.visible?e.getAnimations().map(a=>({name:a.animationName||null,playState:a.playState,currentTime:a.currentTime,playbackRate:a.playbackRate})):[];
    return x;
  };
  const controls = [...document.querySelectorAll('button,select,[role="button"],a[href],input:not([type=hidden]),textarea,summary,[tabindex],[role="toolbar"],[contenteditable],audio[controls],video[controls],iframe')].filter(e => e.isContentEditable || e.matches('button,select,[role=button],a[href],input:not([type=hidden]),textarea,summary,[tabindex],[role=toolbar],audio[controls],video[controls],iframe'));
  const excluded = controls.filter(vendor).map(e => ({
    ...label(e),
    reason: e.closest('.xterm') ? 'xterm vendor' : 'KaTeX vendor'
  }));
  const typography = [...document.querySelectorAll('h1,h2,h3,h4,h5,h6,a,small,hr,code,kbd,samp,pre')];
  const targets = [...new Set([...controls, ...typography])];
  for(const actor of controls)for(let ancestor=actor;ancestor;ancestor=ancestor.parentElement){label(ancestor);if(ancestor.previousElementSibling)label(ancestor.previousElementSibling);}
  const own = targets.filter(e => !vendor(e)).map(label);
  const missing = own.filter(e => !e.owner);
  const actionContextMissing = [...document.querySelectorAll('.ajmodal .aj-action,.ajmodal .aj-icon-action,.ajmodal .aj-navigation-action')].filter(e => e.getAttribute('data-aj-action-context') !== 'dialog').map(label);
  const nativeMedia = controls.filter(e => e.matches('audio[controls],video[controls]')).map(e => ({
    ...label(e),
    boundary: 'UA native controls shadow tree; host owner required, internal browser controls not patched'
  }));
  let rules = [],
    errors = [];
  const walk = (rs, file, conditions = []) => {
    for (let i = 0; i < rs.length; i++) {
      const r = rs[i];
      if (r.media && !matchMedia(r.conditionText).matches) continue;
      if (r instanceof CSSSupportsRule && !CSS.supports(r.conditionText)) continue;
      if (r.selectorText) {
        const selector = r.selectorText;
        try {
          const highlight = selector.match(/::highlight\(([^)]+)\)/);
          let highlightEvidence;
          let origins;
          if (highlight) {
            const ranges = [...(CSS.highlights?.get(highlight[1]) || [])];
            origins = [...new Set(ranges.flatMap(range => {
              const root = range.commonAncestorContainer.nodeType === 1 ? range.commonAncestorContainer : range.commonAncestorContainer.parentElement;
              return root ? [root, ...root.querySelectorAll('*')].filter(node => { try { return range.intersectsNode(node); } catch { return false; } }) : [];
            }))];
            highlightEvidence = {name: highlight[1], active: ranges.length > 0, rangeCount: ranges.length, reason: ranges.length ? 'real CSS.highlights ranges' : 'No registered range in this exact scene'};
          } else {
            const query = selector.replace(/(^|,)\s*(?=::)/g, '$1 *').replace(/::(?:before|after|marker|placeholder|selection|backdrop|file-selector-button)\b/g, '');
            origins = [...document.querySelectorAll(query)];
          }
          const matches = origins.filter(e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden').map(label);
          const inherited = [...r.style].some(p => p.startsWith('--') || /^(color|font($|-)|line-height|text-align|white-space|visibility|cursor|letter-spacing|word-spacing|direction|fill|stroke)/.test(p));
          const descendants = inherited ? descendantUnion(origins).map(label) : [];
          if (matches.length || highlightEvidence) rules.push({
            file,
            assetPath: file.startsWith(meta.assetBasePath || '\0') ? file.slice(meta.assetBasePath.length) : file.replace(/^\//, ''),
            seatPrefix: meta.assetBasePath,
            ruleIndex: i,
            selector,
            conditions,
            pseudos: [...new Set(selector.match(/::[\w-]+/g) || [])],
            highlightEvidence,
            properties: [...r.style],
            inherited,
            descendants: descendants.map(n => ({nodeId:n.nodeId})),
            matches: matches.map(n => ({nodeId:n.nodeId}))
          });
        } catch (e) {
          errors.push({
            file,
            selector,
            error: String(e)
          });
        }
      } else if (r.cssRules) walk(r.cssRules, file, conditions.concat(r.conditionText || r.name || ''));
    }
  };
  for (const sheet of document.styleSheets) {
    try {
      walk(sheet.cssRules, sheet.href ? new URL(sheet.href).pathname : 'inline');
    } catch (e) {
      errors.push({
        file: sheet.href,
        error: String(e)
      });
    }
  }
  for (const forced of meta.forcedRules || []) {
    const origin = document.querySelector(meta.forcedSelector);
    if (origin) rules.push({
      ...forced,
      assetPath: forced.file?.startsWith(meta.assetBasePath || '\0') ? forced.file.slice(meta.assetBasePath.length) : forced.file?.replace(/^\//, ''),
      seatPrefix: meta.assetBasePath,
      matches: [label(origin)],
      descendants: [...origin.querySelectorAll('*')].map(e => ({nodeId:label(e).nodeId})),
      forcedPseudo: 'active',
      sourceEvidence: 'CDP CSS.getMatchedStylesForNode after forcePseudoState'
    });
  }
  ;
  const unknownOwners = own.filter(x => x.owner && meta.knownOwners && !meta.knownOwners.includes(x.owner));
  const semanticNodes = [...new Set((meta.contracts || []).flatMap(c => [...document.querySelectorAll(c.selector)]))].map(label);
  const semanticMismatches = semanticNodes.filter(x => x.expectedOwners.length && (x.owner || x.registeredIdOwner) && !x.expectedOwners.includes(x.owner || x.registeredIdOwner));
  const paintEvidence=[...new Set((meta.paintSelectors||[]).flatMap(selector=>[...document.querySelectorAll(selector)]))].map(e=>{
    const node=label(e),r=e.getBoundingClientRect(),float64Bits={};for(const key of ['x','y','width','height','top','left','right','bottom']){const buffer=new ArrayBuffer(8);new DataView(buffer).setFloat64(0,r[key]);float64Bits[key]=[...new Uint8Array(buffer)].map(x=>x.toString(16).padStart(2,'0')).join('')}
    const style=getComputedStyle(e),media=e instanceof HTMLMediaElement?{readyState:e.readyState,networkState:e.networkState,currentTime:e.currentTime,duration:Number.isFinite(e.duration)?e.duration:null,paused:e.paused,buffered:Array.from({length:e.buffered.length},(_,i)=>[e.buffered.start(i),e.buffered.end(i)])}:null;
    return {nodeId:node.nodeId,path:node.path,id:e.id,tag:e.localName,rect:r.toJSON(),float64Bits,computed:Object.fromEntries([...style].map(key=>[key,style.getPropertyValue(key)])),inlineStyle:e.getAttribute('style'),media};
  });
  return {
    ...meta,
    nodeDictionaryVersion: 2,
    paintEvidence,
    fontEvidence:paintEvidence.length?[...document.fonts].map(f=>({family:f.family,status:f.status,style:f.style,weight:f.weight})):[],
    nodes,
    nodeEvidence,
    unknownOwners,
    semanticMismatches,
    controls: controls.filter(e => !vendor(e)).map(label),
    typography: typography.filter(e => !vendor(e)).map(label),
    ownerTargets: own,
    actionContextMissing,
    nativeMedia,
    nativeFrames:[...document.querySelectorAll('iframe')].map(e=>({...label(e),boundary:'Iframe focus host required; opaque empty-sandbox document is a separate browser security boundary and is not traversed'})),
    directMissing: missing,
    inheritedCandidates: missing.filter(x => x.inheritedOwner),
    missing,
    excluded,
    rules,
    errors
  };
}
