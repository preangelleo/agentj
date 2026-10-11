// Device names are local descriptive data, editable by the owner.
export function describeDevice(ua, standalone = false) {
  const huawei = /HarmonyOS|OpenHarmony|HUAWEI/i.test(ua);
  const os = huawei ? (/Mobile|Phone/i.test(ua) && !/Tablet|Pad|MatePad/i.test(ua) ? '华为手机' : '华为平板')
    : /Android/.test(ua) ? 'Android' : /iPhone|iPad|iPod/.test(ua) ? 'iOS' : /Mac OS X/.test(ua) ? 'macOS'
    : /Windows/.test(ua) ? 'Windows' : /Linux|CrOS/.test(ua) ? 'Linux' : '';
  const br = standalone ? '主屏幕' : /Edg\//.test(ua) ? 'Edge' : /Firefox\/|FxiOS/.test(ua) ? 'Firefox'
    : /CriOS|Chrome\//.test(ua) ? 'Chrome' : /Safari\//.test(ua) ? 'Safari' : '浏览器';
  return ('网页 · ' + [os, br].filter(Boolean).join(' ')).slice(0, 64);
}
