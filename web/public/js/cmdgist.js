// cmdgist.js — P341 (backlog #19): one plain line above a command on the approval card when the Agent sent no
// description of its own ("<command>\n— <description>" from the host). It reads only the command's first words — what
// kind of thing it does — and never replaces the command itself: the card still shows (and the device still signs)
// the exact text. Display only; nothing here decides anything.
import { t } from './t.js';

const STEP = /\s*(?:&&|\|\||\||;|\n)\s*/;
const KINDS = [
  [/^(?:rm|rmdir|unlink|trash|shred)$/, 'rm'],
  [/^mv$/, 'mv'],
  [/^cp$/, 'cp'],
  [/^(?:scp|rsync|sftp)$/, 'remote'],
  [/^ssh$/, 'ssh'],
  [/^(?:curl|wget|http|https|nc)$/, 'net'],
  [/^(?:kill|pkill|killall)$/, 'kill'],
  [/^(?:chmod|chown|chgrp)$/, 'perm'],
  [/^(?:docker|podman|docker-compose)$/, 'docker'],
  [/^(?:tee)$/, 'write'],
];
const INSTALL = /^(?:npm|pnpm|yarn|bun|pip|pip3|uv|brew|apt|apt-get|dnf|yum|pacman|cargo|gem|go)$/;
const GIT_LOCAL = /^(?:reset|clean|checkout|restore|rebase|stash|branch)$/;

/** The words of a command's first step, past "sudo", "env" and VAR=value prefixes. */
function words(step){
  const w = step.trim().split(/\s+/).filter(Boolean);
  let sudo = false;
  while (w.length){
    const h = w[0].replace(/^.*\//, '');
    if (h === 'sudo' || h === 'doas'){ sudo = true; w.shift(); while (w[0] && w[0].startsWith('-')) w.shift(); continue; }
    if (h === 'env' || h === 'nohup' || h === 'time' || h === 'exec'){ w.shift(); while (w[0] && w[0].startsWith('-')) w.shift(); continue; }
    if (/^[A-Za-z_][A-Za-z0-9_]*=/.test(w[0])){ w.shift(); continue; }
    break;
  }
  return {w, sudo};
}

function kindOf(step){
  const {w, sudo} = words(step);
  const prog = (w[0] || '').replace(/^.*\//, '');
  if (!prog) return null;
  let k = null;
  if (prog === 'git') k = w[1] === 'push' ? 'push' : GIT_LOCAL.test(w[1] || '') ? 'gitLocal' : 'git';
  else if (INSTALL.test(prog) && /^(?:install|i|add|ci|sync|get)$/.test(w[1] || '')) k = 'install';
  else for (const [re, name] of KINDS) if (re.test(prog)){ k = name; break; }
  if (!k && /(?:^|\s)>{1,2}\s*[^\s&|>]/.test(step.replace(/[12]>&[12]|&>|2>\s*\/dev\/null|>\s*\/dev\/null/g, ''))) k = 'write';
  return {k: k || 'run', prog: prog.slice(0, 40), sudo};
}

/** One line in the page's language, or "" for an empty command. */
export function cmdGist(command){
  const steps = String(command || '').split(STEP).filter(s => s.trim());
  if (!steps.length) return '';
  const first = kindOf(steps[0]);
  if (!first) return '';
  let line = first.k === 'run' ? t('p341.gist.run', {prog: first.prog}) : t('p341.gist.' + first.k);
  if (first.sudo) line = t('p341.gist.sudo', {what: line});
  return steps.length > 2 ? t('p341.gist.more', {what: line, n: steps.length - 1}) : steps.length > 1 ? t('p341.gist.more1', {what: line}) : line;
}
