import DICT from '../i18n.js';
// Device names are local descriptive data, editable by the owner.
export function describeDevice(ua, standalone = false, translate = (key, vars) => DICT.zh[key].replace(/\{(\w+)\}/g, (m, k) => vars?.[k] ?? m)) {
  const huawei = /HarmonyOS|OpenHarmony|HUAWEI/i.test(ua);
  const os = huawei ? (/Mobile|Phone/i.test(ua) && !/Tablet|Pad|MatePad/i.test(ua) ? translate('device.huawei_phone') : translate('device.huawei_tablet'))
    : /Android/.test(ua) ? 'Android' : /iPhone|iPad|iPod/.test(ua) ? 'iOS' : /Mac OS X/.test(ua) ? 'macOS'
    : /Windows/.test(ua) ? 'Windows' : /Linux|CrOS/.test(ua) ? 'Linux' : '';
  const br = standalone ? translate('device.home_screen') : /Edg\//.test(ua) ? 'Edge' : /Firefox\/|FxiOS/.test(ua) ? 'Firefox'
    : /CriOS|Chrome\//.test(ua) ? 'Chrome' : /Safari\//.test(ua) ? 'Safari' : translate('device.browser');
  return translate('device.web', {device: [os, br].filter(Boolean).join(' ')}).slice(0, 64);
}
