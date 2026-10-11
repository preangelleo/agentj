import test from 'node:test';
import assert from 'node:assert/strict';
import { describeDevice } from '../public/js/device-label.js';
test('P130 Huawei Linux UA is a Huawei tablet or phone', () => {
  for (const ua of ['Linux; HarmonyOS 4.0; MatePad Chrome/120', 'Linux; HUAWEI; Tablet Chrome/120', 'Linux; OpenHarmony Chrome/120'])
    assert.equal(describeDevice(ua), '网页 · 华为平板 Chrome');
  assert.equal(describeDevice('Linux; HUAWEI; Mobile Chrome/120'), '网页 · 华为手机 Chrome');
  assert.equal(describeDevice('Linux; HUAWEI; MatePad Mobile Chrome/120', true), '网页 · 华为平板 主屏幕');
});
test('P130 existing platform labels remain stable', () => {
  assert.equal(describeDevice('Linux Chrome/120'), '网页 · Linux Chrome');
  assert.equal(describeDevice('Android Mobile Chrome/120'), '网页 · Android Chrome');
  assert.equal(describeDevice('iPhone Safari/605'), '网页 · iOS Safari');
});
