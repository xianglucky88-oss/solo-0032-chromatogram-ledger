import {el} from './dom.js';
import {number} from './terms.js';

export function curveChart(curve) {
  const graph = el('canvas', '标准点明细见下方表格。', 'curve-chart');
  graph.width = 700;
  graph.height = 220;
  graph.setAttribute('role', 'img');
  graph.setAttribute('aria-label', '浓度与峰面积标准点散点图与拟合线');
  const paint = graph.getContext('2d');
  const right = 648;
  const left = 102;
  const top = 29;
  const bottom = 168;
  const highestLevel = curve.levels.at(-1);
  const fittedMax = curve.slope === null ? 0 : curve.slope * highestLevel + curve.intercept;
  const maxArea = Math.max(1, fittedMax, ...curve.points.map(point => point.area)) * 1.08;
  const x = value => left + value / highestLevel * (right - left);
  const y = value => bottom - value / maxArea * (bottom - top);

  paint.fillStyle = '#f5f9fb';
  paint.fillRect(0, 0, graph.width, graph.height);
  paint.font = '13px system-ui, sans-serif';
  paint.strokeStyle = '#d0dce4';
  paint.fillStyle = '#405c6a';
  for (let step = 0; step <= 4; step++) {
    const level = step / 4 * highestLevel;
    const area = step / 4 * maxArea;
    paint.textAlign = 'right';
    paint.fillText(number(Math.round(area)), left - 7, y(area) + 4);
    paint.textAlign = 'center';
    paint.fillText(number(level), x(level), bottom + 20);
    paint.beginPath();
    paint.moveTo(left, y(area));
    paint.lineTo(right, y(area));
    paint.stroke();
  }
  paint.textAlign = 'left';
  paint.fillText('峰面积', 15, 20);
  paint.textAlign = 'right';
  paint.fillText('标准浓度（mg/L）', right, 209);
  if (curve.slope !== null) {
    const low = curve.levels[0];
    paint.beginPath();
    paint.lineWidth = 2;
    paint.strokeStyle = '#8b5b86';
    paint.moveTo(x(low), y(curve.slope * low + curve.intercept));
    paint.lineTo(x(highestLevel), y(fittedMax));
    paint.stroke();
  }
  paint.fillStyle = '#19727a';
  for (const point of curve.points) {
    paint.beginPath();
    paint.arc(x(point.level), y(point.area), 4.5, 0, Math.PI * 2);
    paint.fill();
  }
  return graph;
}
