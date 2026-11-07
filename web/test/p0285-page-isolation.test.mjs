import test from 'node:test';import assert from 'node:assert/strict';
globalThis.window={};
const{sharesPopupLayout,NOT_CARD_SURFACES}=await import('../public/js/cardplace.js');
const node=selector=>({matches:s=>s===selector});
test('every explicitly declared page is excluded from shared popup geometry',()=>{for(const selector of Object.keys(NOT_CARD_SURFACES))assert.equal(sharesPopupLayout(node(selector)),false,selector);});
test('existing known confirmation and pickup cards keep the shared geometry',()=>{for(const selector of ['#sheet','#confirm','.ajmodal.pkp','.ajmodal.sout'])assert.equal(sharesPopupLayout(node(selector)),true,selector);});
test('exceptions and invalid nodes remain outside, while unrelated generic dialogs retain previous behavior',()=>{assert.equal(sharesPopupLayout(node('.ajmodal.sout.sout-full')),false);assert.equal(sharesPopupLayout(null),false);assert.equal(sharesPopupLayout(node('#generic-confirmation')),true);});
