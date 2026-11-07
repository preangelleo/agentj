import test from'node:test';import assert from'node:assert/strict';import{parseBrowserArguments,normalizeBrowserArguments,protectBrowserArguments}from'./browser-environment.mjs';
test('P577 actual browser arguments preserve quoted values and normalize only ephemeral identity',()=>{
 const args=parseBrowserArguments('"/public test/chrome" --headless --user-data-dir="/public temp/profile 1" --force-color-profile=srgb --foo="two words"');
 assert.deepEqual(args,['/public test/chrome','--headless','--user-data-dir=/public temp/profile 1','--force-color-profile=srgb','--foo=two words']);
 assert.deepEqual(normalizeBrowserArguments(args),['/public test/chrome','--headless','--user-data-dir=<temporary-isolated-profile>','--force-color-profile=srgb','--foo=two words']);
 assert.throws(()=>parseBrowserArguments('chrome --foo="unfinished'));
});
test('P577 unexpected sensitive browser argument values never enter evidence plaintext',()=>{
 const args=protectBrowserArguments(['--proxy-server=public-synthetic-secret','--password-store=basic','--api-key=public-test-value']);
 assert.ok(args[0].includes('<redacted sha256:'));assert.equal(args[1],'--password-store=basic');assert.ok(!args.join(' ').includes('public-test-value'));
});
