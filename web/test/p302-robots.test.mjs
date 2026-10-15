// P302 追加 22: a robot never wears the colour of its ground — every card ground × all 16 robots (CIEDE2000 ≥ MIN_DE),
// stable colours kept when they already stand out, the robot body colours in step with robots.json + the palette.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { deltaE, robotOn, ROBOT_HEX, MIN_DE } from '../public/js/robot-color.js';

const PUB = join(dirname(fileURLToPath(import.meta.url)), '..', 'public');
const css = (f) => readFileSync(join(PUB, f), 'utf8');
const TINTS = [...css('five-pages.css').matchAll(/\.fp-tint-(\d)\{background:(#[0-9a-f]{6})\}/g)].map((m) => m[2]);
const MENU = [...css('five-pages.css').matchAll(/\.fp-dark-[0-3]\{background:(#[0-9a-f]{6})\}/g)].map((m) => m[1]);
const PAL = css('brand/palette.css');
const tok = (n) => new RegExp(`--c-${n}:\\s*(#[0-9a-f]{6})`).exec(PAL)[1];
const GROUNDS = { ...Object.fromEntries(TINTS.map((h, i) => ['tint-' + i, h])), noir: tok('noir'), lotion: tok('lotion'),
  lama: '#dbb293', ...Object.fromEntries(MENU.map((h, i) => ['menu-' + i, h])) };

test('the ten card tints, the menu cards, Noir, Lotion and Lama are all checked', () => {
  assert.equal(TINTS.length, 10);
  assert.equal(MENU.length, 4);
});

test('ROBOT_HEX = the body colour of robots.json, hex from leo-palette (via the palette names)', () => {
  const lib = JSON.parse(readFileSync(join(PUB, 'assets/robots.json'), 'utf8')).robots;
  assert.equal(lib.length, ROBOT_HEX.length);
  const NAME = { 'Milky Waves': '#71bbde', 'Somewhere in a Fairytale': '#d1a7e8', 'Strawberry Field': '#f7827e', Lama: '#dbb293',
    'Elastic Pink': '#f0aad4', Buttery: '#fcbe7c', 'Pasta Luego': '#f7e17c', 'Sage Sensation': '#b7e396', 'Cold Blue': '#78dedc',
    Windstorm: '#6a9bcc', 'Magic Carpet': '#827dbd', 'Emerald Oasis': '#629887', 'Peachy Feeling': '#d97757',
    'Banana Republic': '#ffe292', 'Elephant in the Room': '#acacac' };
  lib.forEach((r, i) => assert.equal(ROBOT_HEX[i], NAME[r.body], `${r.id} ${r.body}`));
});

test('ΔE00 sanity: identical 0, black ↔ white ≈ 100, symmetric', () => {
  assert.equal(deltaE('#ffe292', '#ffe292'), 0);
  assert.ok(Math.abs(deltaE('#000000', '#ffffff') - 100) < 0.5);
  assert.ok(Math.abs(deltaE('#71bbde', '#ffe292') - deltaE('#ffe292', '#71bbde')) < 1e-9);
});

for (const [name, bg] of Object.entries(GROUNDS)) {
  test(`every robot stands out on ${name} ${bg}`, () => {
    for (let r = 0; r < 16; r++) {
      const got = robotOn(r, bg);
      assert.ok(deltaE(ROBOT_HEX[got], bg) >= MIN_DE, `robot ${r} on ${name} → ${got}`);
      if (deltaE(ROBOT_HEX[r], bg) >= MIN_DE) assert.equal(got, r, `robot ${r} already stands out on ${name}: kept`);
      else {   // swapped for the one farthest from the ground
        const best = Math.max(...ROBOT_HEX.map((h) => deltaE(h, bg)));
        assert.equal(deltaE(ROBOT_HEX[got], bg), best, `robot ${r} on ${name}: the farthest`);
      }
    }
  });
}

test("the owner's case: yellow robots on the yellow card (Banana Republic) are swapped, a swap skips robots already there", () => {
  assert.notEqual(robotOn(6, '#ffe292'), 6);
  assert.notEqual(robotOn(13, '#ffe292'), 13);
  const first = robotOn(6, '#ffe292'), second = robotOn(13, '#ffe292', new Set([first]));
  assert.notEqual(first, second);
  assert.ok(deltaE(ROBOT_HEX[second], '#ffe292') >= MIN_DE);
});

test('every place that shows robots goes through settleRobots', () => {
  for (const f of ['js/five-pages.js', 'js/busy.js']) assert.match(css(f), /settleRobots/, f);
  assert.match(css('js/five-pages.js'), /function render\(\)\{renderNow\(\);settleRobots/);
});
