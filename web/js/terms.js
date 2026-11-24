import {el} from './dom.js';

const words = {
  standard: '标准样', blank: '空白', qc: '核查样', sample: '待测样',
  reportable: '可交付', held: '暂缓', aligned: '已对齐', marker_missing: '定位峰缺失',
  marker_ambiguous: '定位峰多义', marker_saturated: '定位峰饱和',
  matched: '已匹配', ambiguous: '候选峰多义', missing: '未见候选峰', saturated: '峰饱和',
  unaligned: '对齐不可用', used: '已纳入', excluded: '已排除', clear: '空白合格',
  contaminated: '空白污染', unresolved: '证据不足', passed: '核查合格', failed: '核查超差',
  out_of_range: '超量程', curve_unavailable: '曲线不可用', blank_absent: '缺少前序空白',
  blank_blocked: '前序空白阻断', qc_absent: '缺少前序核查样', qc_blocked: '前序核查样阻断',
  ready: '可用于定量', insufficient_levels: '有效浓度水平不足', non_positive_slope: '斜率非正',
  poor_fit: '拟合未达门槛',
};
const good = new Set(['reportable', 'aligned', 'matched', 'used', 'clear', 'passed', 'ready']);
export const word = status => words[status] ?? status;
export const badge = status => el('span', word(status), `badge ${good.has(status) ? 'good' : 'caution'}`);
const formatter = new Intl.NumberFormat('zh-CN', {maximumFractionDigits: 6, useGrouping: false});
export const number = value => value == null ? '—' : formatter.format(value);
export const percent = value => value == null ? '—' : `${number(value * 100)}%`;
