import {test} from 'node:test';
import assert from 'node:assert/strict';
import {cycle, fast, tier, allowedPct, PERIOD_S} from '../public/js/quotapace.js';
test('actual elapsed share, early/late, zero and invalid boundaries', () => {
  const now=1000000000, p=PERIOD_S.weekly;
  const early=cycle('weekly',now/1000+p*.99,now);
  assert.ok(Math.abs(allowedPct(early)-1)<1e-8);
  assert.equal(fast(10,early),true);
  assert.equal(fast(10,cycle('weekly',now/1000+p*.4,now)),false);
  assert.equal(fast(10,cycle('weekly',now/1000+p,now)),null);
  for(const pct of [-1,101,NaN]) { assert.equal(fast(pct,early),null); assert.equal(tier(pct,early),''); }
  for(const reset of [0,-1,now/1000,now/1000+p+1]) assert.equal(cycle('weekly',reset,now),null);
  assert.equal(tier(80,early),'tight'); assert.equal(tier(90,early),'empty');
});

test('cache age and observation in an earlier cycle remain unknown', () => {
  const now=1000000000, p=PERIOD_S.weekly, reset=now/1000+p*.99;
  assert.equal(cycle('weekly',reset,now,now/1000-301),null);
  assert.equal(cycle('weekly',reset,now,reset-p-1),null);
  assert.equal(cycle('weekly',reset,now,now/1000+1),null);
  assert.ok(cycle('weekly',reset,now,now/1000));
});
