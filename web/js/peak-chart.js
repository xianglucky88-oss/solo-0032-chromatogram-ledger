import {el} from './dom.js';
import {number} from './terms.js';

export function peakChart(peaks, alignment, evidence, corrected) {
  const chart = el('canvas', '峰图可在下方表格中逐项复核。', 'peak-chart');
  chart.width = 820;
  chart.height = 235;
  chart.setAttribute('role', 'img');
  chart.setAttribute('aria-label', `${corrected ? '校正后' : '原始'}保留时间与峰面积图，下方提供证据表`);
  const tx = chart.getContext('2d');
  const shift = corrected ? alignment.shift : 0;
  const times = peaks.map(peak => peak.time - shift);
  const windows = evidence.targets.flatMap(target => {
    const bounds = corrected ? target.aligned : target.raw;
    return bounds ? [{...bounds, id: target.id}] : [];
  });
  if (!corrected) windows.unshift({...evidence.marker, id: '定位', marker: true});
  const minimum = Math.min(0, ...times, ...windows.map(window => window.min));
  const maximum = Math.max(1, ...times, ...windows.map(window => window.max)) * 1.03;
  const maxArea = Math.max(1, ...peaks.map(peak => peak.area));
  const x = value => 52 + (value - minimum) / (maximum - minimum) * 734;
  const y = value => 190 - value / maxArea * 142;

  tx.fillStyle = '#f6f9fb';
  tx.fillRect(0, 0, chart.width, chart.height);
  tx.font = '15px system-ui, sans-serif';
  tx.fillStyle = '#4b6171';
  tx.textAlign = 'left';
  tx.fillText('峰面积', 15, 25);
  tx.textAlign = 'center';
  for (const window of windows) {
    const left = x(window.min);
    const right = x(window.max);
    tx.fillStyle = window.marker ? '#f4e5ed' : '#e0f0ef';
    tx.fillRect(left, 42, right - left, 148);
    tx.fillStyle = window.marker ? '#863653' : '#326a6b';
    tx.fillText(window.id, (left + right) / 2, 35);
  }
  tx.strokeStyle = '#aebdc8';
  tx.lineWidth = 1;
  tx.beginPath();
  tx.moveTo(x(minimum), 39);
  tx.lineTo(x(minimum), y(0));
  tx.lineTo(x(maximum), y(0));
  tx.stroke();
  tx.fillStyle = '#516575';
  tx.textAlign = 'center';
  for (let i = 0; i <= 5; i++) {
    const tick = minimum + i / 5 * (maximum - minimum);
    tx.fillText(number(Math.round(tick * 100) / 100), x(tick), 210);
  }
  tx.textAlign = 'right';
  tx.fillText('保留时间（分钟）', 788, 229);
  for (const [index, peak] of peaks.entries()) {
    tx.strokeStyle = peak.saturated ? '#a5572b' : peak.id === alignment.peak_id ? '#a74b70' : '#276f78';
    tx.lineWidth = peak.id === alignment.peak_id ? 5 : 3;
    const position = x(times[index]);
    tx.beginPath();
    tx.moveTo(position, y(0));
    tx.lineTo(position, y(peak.area));
    tx.stroke();
  }
  return chart;
}
