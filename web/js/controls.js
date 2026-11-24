import {button, el, table} from './dom.js';
import {badge} from './terms.js';

function usageByTarget(injections) {
  const usage = new Map();
  for (const row of injections) {
    if (row.kind !== 'sample') continue;
    for (const [target, result] of Object.entries(row.decisions)) {
      for (const control of [result.blank_id, result.qc_id, result.qc_after_id]) {
        if (!control) continue;
        const byTarget = usage.get(control) ?? new Map();
        const count = byTarget.get(target) ?? {held: 0, reportable: 0};
        count[result.status]++;
        byTarget.set(target, count);
        usage.set(control, byTarget);
      }
    }
  }
  return usage;
}

export function renderControls(container, report, onSelect) {
  const controls = report.injections.filter(row => row.kind === 'blank' || row.kind === 'qc');
  const counts = usageByTarget(report.injections);
  const panel = el('section', null, 'panel curve-panel');
  panel.append(el('h3', '空白和核查样的作用范围'));
  panel.append(el('p', '引用次数按组分计算。暂缓次数也包括由其他证据阻断的结果。', 'detail controls-intro'));
  if (controls.length) {
    const rows = controls.map(row => {
      const control = button(`${row.order} · ${row.id}`, () => onSelect(row.id), 'quiet');
      const columns = report.method.targets.map(target => {
        const evidence = el('div', null, 'control-cell');
        const count = counts.get(row.id)?.get(target.id);
        evidence.append(badge(row.decisions[target.id].status));
        if (count) evidence.append(el('small', `${count.reportable} 可交付 · ${count.held} 暂缓`));
        else evidence.append(el('small', '未被引用'));
        return evidence;
      });
      return [control, ...columns];
    });
    panel.append(table(['进样', ...report.method.targets.map(target => target.label)], rows));
  } else {
    panel.append(el('p', '序列没有空白或核查样。', 'problem'));
  }
  container.replaceChildren(panel);
}
