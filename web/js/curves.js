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

function dualGroupRows(detail) {
  const rows = [];
  for (const registration of detail.registrations) {
    for (const group of registration.groups) {
      const range = group.levels.length ? `${number(group.levels.at(0))} – ${number(group.levels.at(-1))}` : '—';
      rows.push([
        `组 ${group.group_index + 1}`, registration.registration_id, badge(group.status),
        number(group.slope), number(group.intercept), range,
      ]);
    }
  }
  return rows;
}

function dualPanel(target, detail) {
  const panel = el('article', null, 'panel curve-panel');
  const selection = detail.selection;
  const top = el('div', null, 'section-head');
  top.append(el('h3', `${target.label} · ${target.id} · 双端校准`), badge(selection.status));
  panel.append(top);

  const chosen = selection.chosen?.registration_ids;
  panel.append(detailList([
    ['候选组合数', selection.combinations.length],
    ['通过指定核查的组合', selection.supported.length],
    ['采用登记', chosen ? chosen.join('、') : '—'],
  ]));
  panel.append(el('h4', '各登记在每个标准组的曲线'));
  panel.append(table(['标准组', '登记编号', '状态', '斜率', '截距', '浓度范围'], dualGroupRows(detail)));

  if (detail.designated_checks.length) {
    panel.append(el('h4', '指定核查对各候选组合的判定'));
    const rows = [];
    for (const check of detail.designated_checks) {
      for (const evidence of check.evaluations) {
        rows.push([
          check.injection_id, evidence.registration_ids.join('、'), badge(evidence.status),
          number(evidence.observed), number(evidence.expected),
          evidence.relative_error == null ? '—' : number(evidence.relative_error),
          evidence.reason ? word(evidence.reason) : '—',
        ]);
      }
    }
    panel.append(table(['指定核查', '候选登记组合', '判定', '观测', '标称', '相对误差', '原因'], rows));
  }
  return panel;
}

export function renderCurves(element, report) {
  if (report.mode === 'dual_end') {
    const panels = report.method.targets.map(target => dualPanel(target, report.dual_calibration[target.id]));
    element.replaceChildren(...panels);
    return;
  }
  const panels = report.method.targets.map(item => curvePanel(item, report.curves[item.id], report.method.min_r2));
  element.replaceChildren(...panels);
}
