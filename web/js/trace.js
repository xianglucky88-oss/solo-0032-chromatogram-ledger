import {button, detailList, el, table} from './dom.js';
import {peakChart} from './peak-chart.js';
import {badge, number, percent, word} from './terms.js';

function resultEvidence(kind, entry) {
  switch (kind) {
    case 'sample': {
      const references = [entry.blank_id, entry.qc_id, entry.qc_after_id].filter(Boolean).join(' / ');
      const issues = entry.issues.map(word).join('、');
      const detail = issues || `交付值 ${number(entry.reported)} mg/L`;
      const registration = entry.registration_ids ? `采用登记 ${entry.registration_ids.join('、')}` : '';
      return [detail, references && `证据 ${references}`, registration].filter(Boolean).join(' · ');
    }
    case 'qc':
      if (entry.status === 'unresolved') {
        return `${word(entry.status)}${entry.reason ? ` · ${word(entry.reason)}` : ''}`;
      }
      return `观测 ${number(entry.observed)} / 标称 ${number(entry.expected)} · 相对误差 ${percent(entry.relative_error)}`;
    case 'blank':
      return `观测面积 ${number(entry.area)} / 限值 ${number(entry.limit)}`;
    default:
      return word(entry.status);
  }
}

function graphPanel(peaks, alignment, windows) {
  const panel = el('div', null, 'chart-panel');
  const axis = el('div', null, 'chart-axis');
  const graph = el('div');
  const raw = button('原始时间', () => render(false), 'chart-button');
  const aligned = button('校正时间', () => render(true), 'chart-button');
  aligned.disabled = alignment.status !== 'aligned';
  axis.append(raw, aligned);
  panel.append(axis, graph);

  function render(corrected) {
    raw.setAttribute('aria-pressed', String(!corrected));
    aligned.setAttribute('aria-pressed', String(corrected));
    graph.replaceChildren(peakChart(peaks, alignment, windows, corrected));
  }
  render(alignment.status === 'aligned');
  return panel;
}

export function renderTrace(target, {source, result, windows}, targets) {
  const header = el('div', null, 'trace-title');
  const title = el('div');
  title.append(el('p', `${word(result.kind)} · 第 ${result.order} 次进样`, 'detail'), el('h3', result.id));
  header.append(title, badge(result.alignment.status));
  const facts = detailList([
    ['样品编号', source.sample_id ?? '—'], ['稀释倍数', number(source.dilution)],
    ['定位候选', result.alignment.candidates.join('、') || '—'], ['时间偏移（分钟）', number(result.alignment.shift)],
  ]);
  const decisions = table(['组分', '原始时间窗', '候选峰', '归属', '判定依据'], targets.map((item, index) => {
    const assignment = result.assignments[item.id];
    const bounds = windows.targets[index].raw;
    const timeRange = bounds ? `${number(bounds.min)} – ${number(bounds.max)}` : '—';
    return [item.label, timeRange, assignment.ids.join('、') || '—', badge(assignment.status), resultEvidence(result.kind, result.decisions[item.id])];
  }));
  const raw = el('details', null, 'raw-peaks');
  const rawRows = source.peaks.map(peak => [peak.id, number(peak.time), number(peak.area), peak.saturated ? '是' : '否']);
  raw.append(el('summary', `原始峰表（${rawRows.length}）`), table(['峰编号', '原始时间', '面积', '饱和'], rawRows));
  target.replaceChildren(header, facts, graphPanel(source.peaks, result.alignment, windows), el('h4', '组分归属和判定'), decisions, raw);
}
