export function familyAudit(registry) {
  return registry.components.map(c => {
    const classes = c.existingClasses.flatMap(x => x.startsWith('aj-action') ? [x, x.replace('aj-action', 'aj-btn')] : [x]);
    const selectors = classes.filter(x => typeof x === 'string' && x).map(x => x.endsWith('-') ? '[class^="' + CSS.escape(x) + '"],[class*=" ' + CSS.escape(x) + '"]' : '.' + CSS.escape(x));
    selectors.push('[data-aj-owner="' + CSS.escape(c.namespace) + '"]');
    let matches = [];
    try {
      matches = [...document.querySelectorAll(selectors.join(','))].filter(e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden').map(e => {
        const r = e.getBoundingClientRect(),
          s = getComputedStyle(e);
        return {
          tag: e.tagName.toLowerCase(),
          id: e.id || null,
          classes: [...e.classList],
          owner: e.getAttribute('data-aj-owner'),
          w: r.width,
          h: r.height,
          textChars: e.textContent.trim().length,
          images: e.querySelectorAll('img,svg,canvas,video,audio').length,
          background: s.backgroundImage !== 'none'
        };
      });
    } catch {}
    const roots = (registry.canonicalRoots || []).filter(r => r.owner === c.namespace || r.owner.startsWith(c.namespace + '-')).flatMap(r => [...document.querySelectorAll(r.selector)].filter(e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden').map(e => ({
      selector: r.selector,
      owner: r.owner,
      evidence: r.evidence,
      tag: e.tagName.toLowerCase(),
      id: e.id || null
    })));
    return {
      roots,
      id: c.id,
      name: c.name,
      namespace: c.namespace,
      present: matches.length > 0,
      nonempty: matches.some(x => x.textChars > 0 || x.images > 0 || x.background),
      matches: matches.slice(0, 25)
    };
  });
}
