import {curveChart} from './curve-chart.js';
import {detailList, el, table} from './dom.js';
import {badge, number, word} from './terms.js';

function curvePanel(target, curve, minR2) {
  const panel = el('article', null, 'panel curve-panel');
  const top = el('div', null, 'section-head');
  top.append(el('h3', `${target.label} · ${target.id}`), badge(curve.status));
  const range = curve.levels.length ? `${number(curve.levels.at(0))} – ${number(curve.levels.at(-1))}` : '—';
  const facts = detailList([['斜率', number(curve.slope)], ['截距', number(curve.intercept)],
    ['R² / 门槛', `${number(curve.r2)} / ${number(minR2)}`], ['浓度范围', range]]);
  const accepted = curve.points.map(row => [row.injection_id, number(row.level), number(row.area), row.peak_ids.join('、'), '纳入']);
  const removed = curve.excluded.map(row => [row.injection_id, number(row.level), '—', row.peak_ids.join('、') || '—', word(row.reason)]);
  panel.append(top, facts);
  if (curve.points.length) panel.append(curveChart(curve));
  panel.append(table(['标准样', '标称浓度', '峰面积', '候选峰', '拟合处理'], [...accepted, ...removed]));
  return panel;
}

export function renderCurves(element, report) {
  const panels = report.method.targets.map(item => curvePanel(item, report.curves[item.id], report.method.min_r2));
  element.replaceChildren(...panels);
}
