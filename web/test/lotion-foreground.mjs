// Measure the rendered foreground on text runs, form values and CSS-generated text.
import assert from 'node:assert/strict';
import { evaluate } from './browser.mjs';

function measureForeground(selector) {
  const root = document.querySelector(selector);
  if (!root?.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) return [];
  const lotion = '250,249,245', noir = '44,43,38';
  const pixel = Object.assign(document.createElement('canvas'), { width: 1, height: 1 }).getContext('2d', { willReadFrequently: true });
  const rgba = value => {
    const match = value.match(/^(rgb\(|rgba\(|color\(srgb )([^)]+)\)$/);
    if (!match) {
      if (!CSS.supports('color', value)) return { rgb: value, alpha: 1 };
      // Chromium can serialize interpolated token colors in OKLab instead of sRGB.
      pixel.clearRect(0, 0, 1, 1);
      pixel.fillStyle = value;
      pixel.fillRect(0, 0, 1, 1);
      const [r, g, b, opacity] = pixel.getImageData(0, 0, 1, 1).data;
      return { rgb: [r, g, b].join(','), alpha: opacity / 255 };
    }
    const parts = match[2].trim().split(/[\s,/]+/).map(Number);
    const scale = match[1].startsWith('color') ? 255 : 1;
    return { rgb: parts.slice(0, 3).map(value => Math.round(value * scale)).join(','), alpha: parts[3] ?? 1 };
  };
  const path = node => {
    if (node === root) return selector;
    if (node.id) return '#' + node.id;
    const peers = [...node.parentElement.children].filter(child => child.localName === node.localName);
    return path(node.parentElement) + ' > ' + node.localName + (peers.length > 1 ? ':nth-of-type(' + (peers.indexOf(node) + 1) + ')' : '');
  };
  const samples = [];
  const record = (node, property, value, sample) => {
    const { rgb, alpha } = rgba(value);
    const button = node.closest('button,[role="button"],input:is([type="button"],[type="submit"],[type="reset"])');
    const { rgb: fill, alpha: opacity } = button ? rgba(getComputedStyle(button).backgroundColor) : {};
    const solidButton = opacity === 1 && fill === lotion && getComputedStyle(button).backgroundImage === 'none';
    samples.push({ node: path(node), property, color: value, sample: sample.trim().slice(0, 96),
      pass: alpha > 0 && (rgb === lotion || (rgb === noir && solidButton)) });
  };
  const check = (node, style, sample, property = 'color') => {
    record(node, property, style.color, sample);
    if (style.webkitTextFillColor && style.webkitTextFillColor !== style.color)
      record(node, property + '/text-fill', style.webkitTextFillColor, sample);
  };
  const range = document.createRange();
  for (const node of [root, ...root.querySelectorAll('*')]) {
    const option = node.matches('option');
    const control = option ? node.closest('select') : node;
    if (!control?.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) continue;
    if (control.closest('details:not([open])') && !control.closest('summary') &&
        control !== control.closest('details:not([open])')) continue;
    const style = getComputedStyle(node);
    for (const child of node.childNodes) {
      if (child.nodeType !== Node.TEXT_NODE || !child.textContent.trim()) continue;
      range.selectNodeContents(child);
      if (option || [...range.getClientRects()].some(rect => rect.width > 0 && rect.height > 0))
        check(node, style, child.textContent);
    }
    if (node.matches('textarea,input:not([type]),input:is([type="text"],[type="password"],[type="search"],[type="number"],[type="email"],[type="tel"],[type="url"])')) {
      check(node, style, 'form value');
      record(node, 'caret-color', style.caretColor, 'form caret');
      if (node.placeholder || node.matches('#elev-input'))
        check(node, getComputedStyle(node, '::placeholder'), 'form placeholder', '::placeholder');
    }
    for (const pseudo of ['::before', '::after']) {
      const generated = getComputedStyle(node, pseudo);
      if (generated.display === 'none' || generated.visibility === 'hidden' || !/^["'].+["']$/.test(generated.content)) continue;
      check(node, generated, generated.content, pseudo);
    }
  }
  return samples;
}

export const foreground = (page, selector) =>
  evaluate(page, `(${measureForeground.toString()})(${JSON.stringify(selector)})`);

export function requireLotion(samples, scenario) {
  assert.ok(samples.length, scenario + ' has no rendered text');
  assert.deepEqual(samples.filter(sample => !sample.pass), [], scenario + ' foreground must use Lotion');
}
