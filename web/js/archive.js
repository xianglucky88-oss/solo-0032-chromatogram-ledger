import {api} from './api.js';
import {byId, button, detailList, el} from './dom.js';
import {renderSequence} from './sequence.js';
import {renderTrace} from './trace.js';

export function bindArchive(notify) {
  const view = byId('archive-view');
  const stationList = byId('archive-stations');
  const recordList = byId('archive-records');
  const detail = byId('archive-detail');
  let station = null;
  let opened = null;

  function renderStations(stations) {
    stationList.replaceChildren();
    const total = stations.reduce((sum, row) => sum + row.batch_count, 0);
    const all = button('', () => selectStation(null), 'run-tile');
    all.append(el('strong', '全部来源'), el('small', `${total} 条收档记录`));
    if (station === null) all.setAttribute('aria-current', 'true');
    stationList.append(all);
    for (const row of stations) {
      const tile = button('', () => selectStation(row.station_id), 'run-tile');
      if (row.station_id === station) tile.setAttribute('aria-current', 'true');
      tile.append(el('span', row.station_id, 'identifier'), el('strong', row.station_id),
        el('small', `${row.batch_count} 条记录 · ${row.snapshot_count} 份封存`));
      stationList.append(tile);
    }
  }

  function renderRecords(items) {
    recordList.replaceChildren();
    for (const item of items) {
      const tile = button('', () => openRecord(item.station_id, item.batch_id), 'run-tile');
      if (opened && opened.station === item.station_id && opened.batch === item.batch_id) {
        tile.setAttribute('aria-current', 'true');
      }
      tile.append(
        el('span', `${item.station_id} / ${item.batch_id}`, 'identifier'),
        el('strong', item.title),
        el('small', `${item.counts.reportable} 可交付 · ${item.counts.held} 暂缓${item.snapshot_hash ? ' · 已封存' : ''}`),
      );
      recordList.append(tile);
    }
    if (!items.length) recordList.append(el('p', '该来源尚无收档记录。', 'detail catalog-empty'));
  }

  function snapshotBlock(record) {
    const panel = el('section', null, 'panel delivery-panel');
    panel.append(el('h3', '封存与原件下载'));
    if (!record.snapshot) {
      panel.append(el('p', '该批次尚未封存，后续备份补齐封存后会在此出现。', 'detail'));
      return panel;
    }
    panel.append(el('p', record.snapshot.hash, 'digest'));
    const actions = el('div', null, 'download-actions');
    for (const format of ['json', 'csv']) {
      const link = el('a', `下载 ${format.toUpperCase()}`);
      link.href = api.archiveDownload(record.station_id, record.id, format);
      actions.append(link);
    }
    const replay = button('复算原摘要', async () => {
      replay.disabled = true;
      try {
        const result = await api.archiveReplay(record.station_id, record.id);
        notify(result.matches ? '复算摘要与原样封存件一致。' : '复算摘要存在差异。', !result.matches);
      } catch (error) {
        notify(error.message, true);
      } finally {
        replay.disabled = false;
      }
    });
    actions.append(replay);
    panel.append(actions);
    return panel;
  }

  function sourceBlock(stationId, record) {
    const panel = el('section', null, 'panel delivery-panel');
    panel.append(el('h3', '原输入与收件信息'),
      detailList([['输入摘要', record.input_hash], ['收件时间', record.received_at]]),
      button('打开原输入', async () => {
        const value = await api.archiveSource(stationId, record.id);
        notify(`原输入 ${value.id}：${value.injections.length} 次进样、方法 ${value.method.id}。`);
      }));
    return panel;
  }

  function evidenceBlock(stationId, record) {
    const panel = el('section', null, 'panel sequence-panel');
    panel.append(el('h3', '进样证据'));
    const grid = el('div', null, 'sequence-grid');
    const listPanel = el('section', null, 'panel sequence-panel');
    const list = el('div');
    list.id = 'archive-injections';
    listPanel.append(list);
    const tracePanel = el('section', null, 'panel evidence-panel');
    const traceTarget = el('div');
    traceTarget.id = 'archive-trace';
    tracePanel.append(traceTarget);
    grid.append(listPanel, tracePanel);
    panel.append(grid);

    async function selectInjection(injectionId) {
      renderSequence(list, record.injections, 'all', injectionId, selectInjection);
      if (!injectionId) return;
      try {
        const evidence = await api.archiveTrace(stationId, record.id, injectionId);
        renderTrace(traceTarget, evidence, record.method.targets);
      } catch (error) {
        notify(error.message, true);
      }
    }

    const first = record.injections.find(row => row.kind === 'sample') ?? record.injections[0];
    panel.afterOpened = () => selectInjection(first ? first.id : null);
    return panel;
  }

  async function selectStation(next) {
    station = next;
    opened = null;
    const [stations, catalog] = await Promise.all([
      api.archiveStations(),
      api.archiveCatalog(station),
    ]);
    renderStations(stations.items);
    renderRecords(catalog.items);
    detail.replaceChildren(el('p', '请选择一条收档记录查看原输入、报告与进样证据。', 'detail'));
  }

  async function openRecord(stationId, batchId) {
    const record = await api.archiveOpen(stationId, batchId);
    opened = {station: stationId, batch: batchId};
    const catalog = await api.archiveCatalog(stationId);
    renderRecords(catalog.items);
    detail.replaceChildren();
    const head = el('section', null, 'panel batch-head');
    const heading = el('div');
    heading.append(
      el('p', `${stationId} · 工作站归档`, 'identifier'),
      el('h2', batchId),
      el('p', `方法 ${record.method.id} / ${record.method.revision} · ${record.injections.length} 次进样`, 'detail'),
    );
    head.append(heading, detailList([['可交付组分', record.counts.reportable], ['暂缓组分', record.counts.held]]));
    const evidence = evidenceBlock(stationId, record);
    detail.append(head, snapshotBlock(record), sourceBlock(stationId, record), evidence);
    evidence.afterOpened();
  }

  return {
    async show() {
      view.hidden = false;
      await selectStation(null);
    },
    hide() {
      view.hidden = true;
    },
  };
}
