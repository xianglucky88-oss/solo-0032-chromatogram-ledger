import {api} from './api.js';
import {byId, button, el, table} from './dom.js';
import {number, word} from './terms.js';

function describe(entry) {
  if (!entry) return '—';
  return entry.status === 'reportable' ? number(entry.value) : entry.issues.map(word).join('、');
}

export function bindDelivery(current, refresh, notify) {
  const freeze = byId('freeze');
  const snap = byId('snapshot');
  const comparison = byId('compare-result');
  const input = byId('compare-id');

  function render(report, preserveComparison) {
    if (!preserveComparison) {
      input.value = '';
      comparison.replaceChildren();
    }
    freeze.hidden = Boolean(report.snapshot);
    freeze.disabled = report.counts.reportable === 0;
    if (!report.snapshot) {
      const message = report.counts.reportable ?
        `封存 ${report.counts.reportable} 项可交付组分。暂缓组分留在进样证据中。` : '当前没有可交付组分。';
      snap.replaceChildren(el('p', message, 'detail'));
      return;
    }
    const actions = el('div', null, 'download-actions');
    for (const format of ['json', 'csv']) {
      const link = el('a', `下载 ${format.toUpperCase()}`);
      link.href = api.download(report.id, format);
      actions.append(link);
    }
    const replay = button('复算核对', async () => {
      replay.disabled = true;
      try {
        const result = await api.replay(report.id);
        if (current()?.id === report.id) notify(result.matches ? '复算摘要与封存快照一致。' : '复算摘要存在差异。', !result.matches);
      } catch (error) {
        notify(error.message, true);
      } finally {
        replay.disabled = false;
      }
    });
    actions.append(replay);
    const parts = [el('p', report.snapshot.hash, 'digest'), actions];
    if (report.deliveries?.length) {
      const rows = report.deliveries.map(item => {
        const credential = el('a', '下载凭据');
        credential.href = api.deliveryFile(report.id, item.sample_id, 'credential');
        const drop = el('a', '下载交付件');
        drop.href = api.deliveryFile(report.id, item.sample_id, 'delivery');
        return [item.sample_id, item.row_count, credential, drop];
      });
      parts.push(el('p', '分样交付：封存时已为下列样品签发凭据。先把对应样品的凭据交给委托方，再转交交付件；' +
        '委托方用 python3 -m chromaledger.verify 离线验件，只凭先收到的凭据核对。', 'detail'));
      parts.push(table(['样品', '交付行数', '封存凭据', '交付件'], rows));
    } else {
      parts.push(el('p', '此次封存未签发分样凭据，只能整包下载。', 'detail'));
    }
    snap.replaceChildren(...parts);
  }

  freeze.addEventListener('click', async () => {
    const report = current();
    if (!report) return;
    freeze.disabled = true;
    try {
      await api.freeze(report.id);
      if (current()?.id === report.id) await refresh(report.id);
      notify('已封存可交付结果。');
    } catch (error) {
      if (current()?.id === report.id) freeze.disabled = false;
      notify(error.message, true);
    }
  });

  byId('compare-form').addEventListener('submit', async event => {
    event.preventDefault();
    const report = current();
    if (!report) return;
    const against = input.value.trim();
    comparison.replaceChildren(el('p', '比较中…', 'detail'));
    try {
      const result = await api.compare(report.id, against);
      if (current()?.id !== report.id || input.value.trim() !== against) return;
      const rows = result.changes.map(change => [change.sample_id, change.injection_id, change.target_id,
        describe(change.before), describe(change.after)]);
      comparison.replaceChildren(el('p', `${result.left} → ${result.right} · ${rows.length} 项变化，${result.unchanged} 项一致`, 'detail'));
      if (rows.length) comparison.append(table(['样品', '进样', '组分', '当前值或原因', '对比值或原因'], rows));
    } catch (error) {
      if (current()?.id === report.id && input.value.trim() === against) comparison.replaceChildren(el('p', error.message, 'problem'));
    }
  });
  return render;
}
