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
const selection = (register, version) => version
  ? `register=${encodeURIComponent(register)}&version=${version}` : '';
export const api = {
  example: () => request('example'),
  catalog: (offset = 0) => request(`runs?offset=${offset}`),
  open: id => request(run(id)),
  trace: (id, injection) => request(`${run(id)}/injections/${encodeURIComponent(injection)}`),
  compare: (id, against) => request(`${run(id)}/compare?against=${encodeURIComponent(against)}`),
  compareWithRegister: (id, against, register, version) =>
    request(`${run(id)}/compare?against=${encodeURIComponent(against)}&${selection(register, version)}`),
  registers: id => request(`${run(id)}/registers`),
  registerDetail: (id, register, version) =>
    request(`${run(id)}/registers/${encodeURIComponent(register)}${version ? `?version=${version}` : ''}`),
  saveRegister: (id, value) => request(`${run(id)}/registers`, value),
  preview: content => request('preview', content),
  ingest: content => request('runs', content),
  freeze: id => request(`${run(id)}/snapshot`, {}),
  replay: id => request(`${run(id)}/snapshot/replay`),
  download: (id, kind) => `${prefix}${run(id)}/snapshot.${kind}`,
};
