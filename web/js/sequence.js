import {button, el} from './dom.js';
import {word} from './terms.js';

function resultLabel(row) {
  if (row.kind !== 'sample') {
    const states = [...new Set(Object.values(row.decisions).map(item => word(item.status)))];
    return states.join(' · ');
  }
  const decisions = Object.values(row.decisions);
  const eligible = decisions.filter(item => item.status === 'reportable').length;
  return `${eligible}/${decisions.length} 组分可交付`;
}

export function renderSequence(target, injections, filter, current, onSelect) {
  const fragment = document.createDocumentFragment();
  for (const row of injections) {
    if (filter !== 'all' && row.kind !== filter) continue;
    const entry = button('', () => onSelect(row.id), `injection-row kind-${row.kind}`);
    if (row.id === current) entry.setAttribute('aria-current', 'true');
    const about = el('span', null, 'injection-about');
    about.append(el('strong', row.id), el('small', `${word(row.kind)} · ${resultLabel(row)}`));
    entry.append(el('span', String(row.order).padStart(2, '0'), 'ordinal'), about);
    fragment.append(entry);
  }
  if (!fragment.childElementCount) fragment.append(el('p', '此类型没有进样。', 'detail'));
  target.replaceChildren(fragment);
}
