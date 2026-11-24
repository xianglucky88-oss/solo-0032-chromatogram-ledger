import {api} from './api.js';
import {byId, el} from './dom.js';

export function bindIntake(onImported) {
  const dialog = byId('intake');
  const upload = byId('upload');
  const peakUpload = byId('peak-upload');
  const action = byId('preview');
  const commit = byId('commit');
  const output = byId('intake-result');
  let prepared = null;

  function reset() {
    prepared = null;
    commit.disabled = true;
    output.replaceChildren();
  }

  function show(message, problem = false) {
    output.replaceChildren(el('p', message, problem ? 'problem' : 'detail'));
  }

  byId('open-intake').addEventListener('click', () => dialog.showModal());
  byId('close-intake').addEventListener('click', () => dialog.close());
  upload.addEventListener('change', reset);
  peakUpload.addEventListener('change', reset);
  action.addEventListener('click', async () => {
    const file = upload.files[0];
    const peaks = peakUpload.files[0];
    if (!file) return show('请先选择 JSON 文件。', true);
    if (file.size + (peaks?.size ?? 0) > 2_000_000) return show('文件超过 2 MB。', true);
    reset();
    action.disabled = upload.disabled = peakUpload.disabled = true;
    try {
      const metadata = await file.text();
      const value = peaks ? {format: 'peak-csv-v1', metadata_json: metadata, peaks_csv: await peaks.text()} : metadata;
      const result = await api.preview(value);
      prepared = value;
      commit.disabled = false;
      show(`${result.id} · ${result.injection_count} 次进样 · ${result.target_count} 个组分 · ${result.counts.reportable} 项可交付`);
    } catch (error) {
      show(error.message, true);
    } finally {
      action.disabled = upload.disabled = peakUpload.disabled = false;
    }
  });

  byId('intake-form').addEventListener('submit', async event => {
    event.preventDefault();
    if (prepared === null) return;
    commit.disabled = action.disabled = upload.disabled = peakUpload.disabled = true;
    try {
      const result = await api.ingest(prepared);
      dialog.close();
      upload.value = peakUpload.value = '';
      reset();
      await onImported(result);
    } catch (error) {
      show(error.message, true);
    } finally {
      action.disabled = upload.disabled = peakUpload.disabled = false;
      commit.disabled = prepared === null;
    }
  });
}
