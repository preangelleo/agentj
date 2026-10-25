import {test} from 'node:test';
import assert from 'node:assert/strict';
import {APPROVED_CONTRAST,approvedContrast} from './p523_contrast_exceptions.mjs';
test('chairman exceptions only match exact locations, colour pairs and original control-label violation',()=>{
 for(const x of APPROVED_CONTRAST){const row={approvedLocation:x.id,fg:x.fg,bg:x.bg,ratio:x.ratio,bad:[`control label ${x.ratio.toFixed(2)}:1`]};assert.equal(approvedContrast(row),x.id);
 for(const delta of [{approvedLocation:null},{fg:'rgb(0,0,0)'},{bg:'rgb(255,255,255)'},{ratio:2},{bad:[...row.bad,'dark text on a heavy ground']}])assert.equal(approvedContrast({...row,...delta}),null);
 }
 assert.equal(APPROVED_CONTRAST.length,2);
});
