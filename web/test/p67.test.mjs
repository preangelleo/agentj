import {test} from 'node:test';
import assert from 'node:assert/strict';
import {quotaWindows} from '../public/js/dashboard.js';
test('P67 quota windows: measured daily/monthly replace 5h; missing/invalid windows stay hidden', () => {
 assert.deepEqual(quotaWindows({quota_windows:[{window:'weekly',pct:12},{window:'daily',pct:32}]}),[{window:'weekly',pct:12},{window:'daily',pct:32}]);
 assert.deepEqual(quotaWindows({quota_windows:[{window:'monthly',pct:125},{window:'daily',pct:-2}]}),[{window:'monthly',pct:100},{window:'daily',pct:0}]);
 assert.deepEqual(quotaWindows({quota_windows:[],week_pct:10}),[]);
 assert.deepEqual(quotaWindows({quota_windows:[{window:'weekly',pct:null},{window:'daily',pct:NaN},{window:'unknown',pct:20}]}),[]);
 assert.deepEqual(quotaWindows({week_pct:25,five_hour_pct:40}),[{window:'weekly',pct:25},{window:'5h',pct:40}]);
});
