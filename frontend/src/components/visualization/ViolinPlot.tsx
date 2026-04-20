import { useMemo } from 'react';
import * as d3Array from 'd3-array';
import * as d3Scale from 'd3-scale';
import * as d3Shape from 'd3-shape';

export interface ViolinGroup {
  name: string;
  values: number[];
  count: number;
  mean: number;
  median: number;
  std: number;
  min: number;
  max: number;
  q1: number;
  q3: number;
}

interface ViolinPlotProps {
  groups: ViolinGroup[];
  yLabel: string;
  xLabel: string;
  width?: number;
  height?: number;
}

/** Gaussian KDE — ported from v1 (same bandwidth math). */
function kernelDensity(
  values: number[],
  bandwidth: number,
  extent: [number, number],
  nBins: number = 80,
): Array<[number, number]> {
  const [lo, hi] = extent;
  const step = (hi - lo) / nBins;
  const points: Array<[number, number]> = [];
  for (let i = 0; i <= nBins; i++) {
    const x = lo + i * step;
    let sum = 0;
    for (const v of values) {
      const u = (x - v) / bandwidth;
      sum += Math.exp(-0.5 * u * u) / (bandwidth * Math.sqrt(2 * Math.PI));
    }
    points.push([x, sum / values.length]);
  }
  return points;
}

function silvermanBandwidth(values: number[]): number {
  const n = values.length;
  if (n < 2) return 1;
  const sorted = [...values].sort((a, b) => a - b);
  const q1 = sorted[Math.floor(n * 0.25)];
  const q3 = sorted[Math.floor(n * 0.75)];
  const iqr = q3 - q1;
  const std = Math.sqrt(d3Array.variance(values) ?? 1);
  return 0.9 * Math.min(std, iqr / 1.34) * Math.pow(n, -0.2);
}

const COLORS = [
  '#0284c7',
  '#f97316',
  '#22c55e',
  '#a855f7',
  '#ef4444',
  '#06b6d4',
  '#eab308',
];
const MARGIN = { top: 20, right: 30, bottom: 50, left: 70 };

/** Violin + box + jitter plot — ported from v1. Deterministic jitter
 * (hashed from index) so re-renders don't reshuffle point positions. */
export function ViolinPlot({
  groups,
  yLabel,
  xLabel,
  width = 600,
  height = 400,
}: ViolinPlotProps) {
  const innerW = width - MARGIN.left - MARGIN.right;
  const innerH = height - MARGIN.top - MARGIN.bottom;

  const { xScale, yScale, violins } = useMemo(() => {
    const allValues = groups.flatMap((g) => g.values);
    const yMin = d3Array.min(allValues) ?? 0;
    const yMax = d3Array.max(allValues) ?? 1;
    const yPad = (yMax - yMin) * 0.1 || 1;

    const yScale = d3Scale
      .scaleLinear()
      .domain([yMin - yPad, yMax + yPad])
      .range([innerH, 0]);
    const xScale = d3Scale
      .scaleBand()
      .domain(groups.map((g) => g.name))
      .range([0, innerW])
      .padding(0.2);

    const violins = groups.map((group) => {
      if (group.values.length < 2) {
        return { group, pathLeft: '', pathRight: '', densityMax: 0 };
      }
      const bw = silvermanBandwidth(group.values);
      const density = kernelDensity(group.values, bw, [yMin - yPad, yMax + yPad]);
      const densityMax = d3Array.max(density, (d) => d[1]) ?? 1;
      const halfWidth = (xScale.bandwidth() / 2) * 0.9;
      const areaLeft = d3Shape
        .area<[number, number]>()
        .x0((d) => -((d[1] / densityMax) * halfWidth))
        .x1(() => 0)
        .y((d) => yScale(d[0]))
        .curve(d3Shape.curveBasis)(density);
      const areaRight = d3Shape
        .area<[number, number]>()
        .x0(() => 0)
        .x1((d) => (d[1] / densityMax) * halfWidth)
        .y((d) => yScale(d[0]))
        .curve(d3Shape.curveBasis)(density);
      return { group, pathLeft: areaLeft ?? '', pathRight: areaRight ?? '', densityMax };
    });

    return { xScale, yScale, violins };
  }, [groups, innerW, innerH]);

  const yTicks = yScale.ticks(6);

  return (
    <div className="overflow-x-auto">
      <svg
        width={width}
        height={height}
        className="font-mono text-[10px] text-gray-700 dark:text-gray-300"
      >
        <g transform={`translate(${MARGIN.left},${MARGIN.top})`}>
          {/* Grid */}
          {yTicks.map((tick) => (
            <line
              key={tick}
              x1={0}
              x2={innerW}
              y1={yScale(tick)}
              y2={yScale(tick)}
              stroke="currentColor"
              strokeOpacity={0.08}
            />
          ))}
          <line x1={0} x2={0} y1={0} y2={innerH} stroke="currentColor" strokeOpacity={0.2} />
          {yTicks.map((tick) => (
            <g key={tick} transform={`translate(0,${yScale(tick)})`}>
              <line x1={-4} x2={0} stroke="currentColor" strokeOpacity={0.3} />
              <text x={-8} dy="0.32em" textAnchor="end" fill="currentColor" fillOpacity={0.6}>
                {tick}
              </text>
            </g>
          ))}
          <text
            transform={`translate(-50,${innerH / 2}) rotate(-90)`}
            textAnchor="middle"
            fill="currentColor"
            fillOpacity={0.7}
            className="text-[11px]"
          >
            {yLabel.length > 50 ? yLabel.slice(0, 47) + '…' : yLabel}
          </text>
          <line x1={0} x2={innerW} y1={innerH} y2={innerH} stroke="currentColor" strokeOpacity={0.2} />

          {violins.map(({ group, pathLeft, pathRight }, i) => {
            const cx = (xScale(group.name) ?? 0) + xScale.bandwidth() / 2;
            const color = COLORS[i % COLORS.length];
            return (
              <g key={group.name} transform={`translate(${cx},0)`}>
                {pathLeft && (
                  <path d={pathLeft} fill={color} fillOpacity={0.25} stroke={color} strokeWidth={1} />
                )}
                {pathRight && (
                  <path d={pathRight} fill={color} fillOpacity={0.25} stroke={color} strokeWidth={1} />
                )}
                <rect
                  x={-4}
                  y={yScale(group.q3)}
                  width={8}
                  height={Math.max(1, yScale(group.q1) - yScale(group.q3))}
                  fill={color}
                  fillOpacity={0.5}
                  rx={1}
                />
                <line
                  x1={-6}
                  x2={6}
                  y1={yScale(group.median)}
                  y2={yScale(group.median)}
                  stroke="white"
                  strokeWidth={2}
                />
                {group.values.length <= 100 &&
                  group.values.map((v, j) => (
                    <circle
                      key={j}
                      cx={_hashJitter(group.name, j)}
                      cy={yScale(v)}
                      r={1.5}
                      fill={color}
                      fillOpacity={0.5}
                    />
                  ))}
                <text y={innerH + 16} textAnchor="middle" fill="currentColor" fillOpacity={0.7}>
                  {group.name.length > 12 ? group.name.slice(0, 12) + '…' : group.name}
                </text>
                <text
                  y={innerH + 28}
                  textAnchor="middle"
                  fill="currentColor"
                  fillOpacity={0.4}
                  className="text-[9px]"
                >
                  n={group.count}
                </text>
              </g>
            );
          })}
          <text
            x={innerW / 2}
            y={innerH + 44}
            textAnchor="middle"
            fill="currentColor"
            fillOpacity={0.7}
            className="text-[11px]"
          >
            {xLabel}
          </text>
        </g>
      </svg>
    </div>
  );
}

/** Deterministic hash-based jitter (±6px). Stable across re-renders. */
function _hashJitter(key: string, i: number): number {
  const s = `${key}_${i}`;
  let h = 0;
  for (let c = 0; c < s.length; c++) {
    h = (h * 31 + s.charCodeAt(c)) | 0;
  }
  // Map to [-6, 6].
  const norm = ((h % 2000) + 2000) % 2000;
  return (norm / 1000 - 1) * 6;
}
