import {HTML_CSP, FRAME_NOBAR} from './mediawire.js';
/** CSP is also in the document, before untrusted bytes: iframe[csp] is not implemented by every browser. */
export function previewDocument(html) {
  // Template contents stay inert while inspected. CSP does not govern document navigations,
  // so remove refresh/nested documents and navigation targets as well as executable nodes.
  const template = document.createElement('template');
  template.innerHTML = String(html || '');
  const clean = root => {
    for (const node of root.querySelectorAll('script,iframe,frame,frameset,object,embed,base,meta,link,animate,animateMotion,animateTransform,set')) node.remove();
    for (const node of root.querySelectorAll('*')) {
      for (const attr of [...node.attributes]) {
        const key = attr.name.toLowerCase();
        if (key.startsWith('on') || ['action','formaction','target','ping','srcdoc'].includes(key)
          || ((key === 'href' || key === 'xlink:href') && !attr.value.startsWith('#'))) node.removeAttribute(attr.name);
      }
      if (node.tagName === 'TEMPLATE') clean(node.content);
    }
  };
  clean(template.content);
  return '<!doctype html><meta http-equiv="Content-Security-Policy" content="' + HTML_CSP + '"><meta charset="utf-8">' + FRAME_NOBAR + template.innerHTML;
}
