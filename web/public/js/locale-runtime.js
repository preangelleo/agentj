// The selected language is ready before application modules render their first frame.
import {common, loaders} from './locale-assets.js';
const words = {zh: common.zh, en: common.en};
const ready = new Set();
const pending = new Map();
const attempts = {zh: 0, en: 0};

function load(lang) {
  if (ready.has(lang)) return null;
  if (!pending.has(lang)) {
    pending.set(lang, loaders[lang](attempts[lang]++).then(module => {
      words[lang] = module.default;
      ready.add(lang);
    }).finally(() => pending.delete(lang)));
  }
  return pending.get(lang);
}

const runtime = globalThis.window?.AJLang;
if (runtime?.prepareLanguage) {
  runtime.prepareLanguage(load);
  await load(runtime.get());
} else {
  // Standalone module consumers without the page language switch can ask for either language.
  await Promise.all([load('zh'), load('en')]);
}
export default words;
