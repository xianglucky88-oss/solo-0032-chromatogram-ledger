const prefix = '/chromaledger/api/';

async function request(path, value) {
  const options = value === undefined ? {} : {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: typeof value === 'string' ? value : JSON.stringify(value),
  };
  const response = await fetch(prefix + path, options);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.message || `请求失败（${response.status}）`);
  return payload;
}

const run = id => `runs/${encodeURIComponent(id)}`;
export const api = {
  example: () => request('example'),
  catalog: (offset = 0) => request(`runs?offset=${offset}`),
  open: id => request(run(id)),
  trace: (id, injection) => request(`${run(id)}/injections/${encodeURIComponent(injection)}`),
  compare: (id, against) => request(`${run(id)}/compare?against=${encodeURIComponent(against)}`),
  preview: content => request('preview', content),
  ingest: content => request('runs', content),
  freeze: id => request(`${run(id)}/snapshot`, {}),
  replay: id => request(`${run(id)}/snapshot/replay`),
  download: (id, kind) => `${prefix}${run(id)}/snapshot.${kind}`,
  deliveryFile: (id, sample, part) => `${prefix}${run(id)}/deliveries/${encodeURIComponent(sample)}/${part}.json`,
};
