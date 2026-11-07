// Normalize fixture inputs only while the owned Page contains a blank document.
// Device dimensions below are CSS pixels; the capture separately binds DPR.
export function mappedDeviceViewport(visual){if(!visual||!['width','height','scale'].every(k=>Number.isFinite(visual[k])&&visual[k]>0))throw Error('Actual visual viewport metrics unavailable');return {width:Math.round(visual.width*visual.scale),height:Math.round(visual.height*visual.scale)};}
const sameSize=(a,b)=>a?.width===b?.width&&a?.height===b?.height;
export async function resetBlankInput({page,context,cdp,options,touchTracker}){
 if(page.url()!=='about:blank')throw Error('Fixture input reset is restricted to about:blank');
 await page.setViewportSize(options.viewport);
 await page.mouse.move(0,0);const releasedButtons=['left','middle','right'],releasedModifiers=['Shift','Control','Alt','Meta'];
 for(const button of releasedButtons)await page.mouse.up({button});for(const key of releasedModifiers)await page.keyboard.up(key);
 const touchReset=options.hasTouch?(touchTracker?await touchTracker.reset({atBlank:true}):(()=>{throw Error('Unknown touch sessions: touch fixture requires its owned input tracker')})()):{status:'not-enabled',attempted:false,dispatched:false,sessions:[],playwrightTaps:{started:0,completed:0,inFlight:0,unknown:false}};
 await page.emulateMedia({colorScheme:options.colorScheme,reducedMotion:options.reducedMotion});
 await context.clearPermissions();const granted=[...(options.permissions||[])];if(granted.length)await context.grantPermissions(granted);
 // Locale/timezone are immutable BrowserContext options. New realms restore native
 // browser APIs; assert their actual values rather than install a second override.
 const observed=await page.evaluate(()=>({layoutViewport:{width:innerWidth,height:innerHeight},screen:{width:screen.width,height:screen.height,availWidth:screen.availWidth,availHeight:screen.availHeight},visualViewport:visualViewport?{width:visualViewport.width,height:visualViewport.height,scale:visualViewport.scale,offsetLeft:visualViewport.offsetLeft,offsetTop:visualViewport.offsetTop}:null,devicePixelRatio,locale:navigator.language,timeZone:Intl.DateTimeFormat().resolvedOptions().timeZone,media:{dark:matchMedia('(prefers-color-scheme: dark)').matches,reduce:matchMedia('(prefers-reduced-motion: reduce)').matches}}));
 const playwrightViewport=page.viewportSize(),metrics=await cdp.send('Page.getLayoutMetrics'),visual=metrics.cssVisualViewport,cdpMapped=mappedDeviceViewport(visual&&{width:visual.clientWidth,height:visual.clientHeight,scale:visual.scale}),windowMapped=mappedDeviceViewport(observed.visualViewport),viewport={width:observed.screen.width,height:observed.screen.height};
 if(observed.locale!==options.locale||observed.timeZone!==options.timezoneId||!sameSize(viewport,options.viewport)||!sameSize(playwrightViewport,options.viewport)||!sameSize(windowMapped,options.viewport)||!sameSize(cdpMapped,options.viewport)||observed.devicePixelRatio!==(options.deviceScaleFactor??1)||observed.media.dark!==(options.colorScheme==='dark')||observed.media.reduce!==(options.reducedMotion==='reduce'))throw Error('Actual blank input environment differs from original configuration');
 await cdp.send('Page.resetNavigationHistory');const history=await cdp.send('Page.getNavigationHistory');
 if(history.currentIndex!==0||history.entries?.length!==1||history.entries[0].url!=='about:blank')throw Error('Blank history retains a previous source document');
 return {pointer:{x:0,y:0,releasedButtons,releasedModifiers},touchCancelDispatched:touchReset.dispatched,touchReset,...observed,viewport,playwrightViewport,cdpMetrics:{cssLayoutViewport:metrics.cssLayoutViewport,cssVisualViewport:visual,mappedDeviceViewport:cdpMapped},media:{colorScheme:options.colorScheme,reducedMotion:options.reducedMotion,observed:observed.media},localeTimeZoneRestoration:'original-immutable-context-new-native-realm',permissions:{cleared:true,granted},history,historyReset:true};
}
export async function replaceBlankSource({page,cdp,url}){
 if(page.url()!=='about:blank')throw Error('First source replacement must begin from reset about:blank');
 await Promise.all([page.waitForNavigation({waitUntil:'load'}),page.evaluate(url=>{location.replace(url);},url)]);
 const history=await cdp.send('Page.getNavigationHistory');if(history.currentIndex!==0||history.entries?.length!==1||history.entries[0].url!==page.url())throw Error('First source replacement retained unequal navigation history');
 return {method:'renderer-location.replace',from:'about:blank',url:page.url(),history};
}
