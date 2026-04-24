import { useEffect, useMemo, useRef } from 'react';
import uPlot from 'uplot';
import 'uplot/dist/uPlot.min.css';

import type { TimeseriesData } from '@/api/binary';

/**
 * Timeseries chart — ported from v1 (286 LOC) with v2 adaptations:
 * - uPlot cursor sync key `ndi-sync` preserved for future multi-chart
 *   coupling (e.g. M5 trajectory-over-time linked scrubbing).
 * - Error mapping matches v1's friendly-message branches:
 *   - "no download url" → download unavailable
 *   - "no file uid" / "no file" → no file associated
 *   - "vlt library" → vlt/DID-python extension not installed on server
 * - Sweep detection: NaN-gap splitting for electrophysiology traces with
 *   `ai` (voltage) + `ao` (current) channels; colors each sweep by its
 *   maximum injected current on the turbo colormap.
 */

const CHANNEL_COLORS = [
  '#22d3ee', // cyan
  '#f97316', // orange
  '#a78bfa', // violet
  '#4ade80', // green
  '#f472b6', // pink
  '#facc15', // yellow
  '#60a5fa', // blue
  '#fb923c', // amber
];

/** Turbo colormap polynomial approximation (Google AI). t in [0,1]. */
function turboColor(t: number): string {
  t = Math.max(0, Math.min(1, t));
  const r = Math.round(
    Math.max(
      0,
      Math.min(
        255,
        34.61 + t * (1172.33 - t * (10793.56 - t * (33300.12 - t * (38394.49 - t * 14825.05)))),
      ),
    ),
  );
  const g = Math.round(
    Math.max(
      0,
      Math.min(
        255,
        23.31 + t * (557.33 + t * (1225.33 - t * (3574.96 - t * (1073.77 + t * 707.56)))),
      ),
    ),
  );
  const b = Math.round(
    Math.max(
      0,
      Math.min(
        255,
        27.2 + t * (3211.1 - t * (15327.97 - t * (27814 - t * (22569.18 - t * 6838.66)))),
      ),
    ),
  );
  return `rgb(${r},${g},${b})`;
}

function detectSweeps(
  values: Array<number | null>,
): { sweeps: Array<Array<number | null>>; sweepCurrents: number[] } | null {
  if (!values || values.length < 10) return null;
  let nullCount = 0;
  for (const v of values) if (v === null || v === undefined) nullCount++;
  if (nullCount < 3) return null;

  const sweeps: Array<Array<number | null>> = [];
  let current: Array<number | null> = [];
  for (const v of values) {
    if (v === null || v === undefined) {
      if (current.length > 5) sweeps.push(current);
      current = [];
    } else {
      current.push(v);
    }
  }
  if (current.length > 5) sweeps.push(current);
  if (sweeps.length < 2) return null;

  const sweepCurrents = sweeps.map((sweep) => {
    let maxAbs = 0;
    for (const v of sweep) {
      if (v !== null && v !== undefined) {
        const abs = Math.abs(v);
        if (abs > maxAbs) maxAbs = abs;
      }
    }
    return maxAbs;
  });
  return { sweeps, sweepCurrents };
}

interface TimeseriesChartProps {
  data: TimeseriesData;
  height?: number;
}

export function TimeseriesChart({ data, height = 300 }: TimeseriesChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<uPlot | null>(null);

  const channelNames = useMemo(() => Object.keys(data.channels ?? {}), [data.channels]);

  const sweepInfo = useMemo(() => {
    const ai = data.channels?.ai;
    const ao = data.channels?.ao;
    if (!ai || !ao) return null;
    const aiSweeps = detectSweeps(ai);
    const aoSweeps = detectSweeps(ao);
    if (!aiSweeps || !aoSweeps) return null;
    if (aiSweeps.sweeps.length !== aoSweeps.sweeps.length) return null;
    return { aiSweeps, aoSweeps };
  }, [data.channels]);

  const uplotData = useMemo<uPlot.AlignedData | null>(() => {
    if (channelNames.length === 0) return null;

    if (sweepInfo) {
      const { aiSweeps } = sweepInfo;
      const maxLen = Math.max(...aiSweeps.sweeps.map((s) => s.length));
      const timeAxis = Array.from({ length: maxLen }, (_, i) => i);
      const series: Array<Array<number | null | undefined>> = [timeAxis];
      for (const sweep of aiSweeps.sweeps) {
        const padded = Array.from({ length: maxLen }, (_, i) =>
          i < sweep.length ? (sweep[i] === null ? undefined : sweep[i]) : undefined,
        ) as Array<number | undefined>;
        series.push(padded);
      }
      return series as unknown as uPlot.AlignedData;
    }

    const sampleCount =
      data.sample_count ||
      Math.max(...channelNames.map((k) => data.channels[k]?.length ?? 0));
    const timeAxis =
      data.timestamps && data.timestamps.length > 0
        ? data.timestamps
        : Array.from({ length: sampleCount }, (_, i) => i);
    const series: Array<Array<number | null | undefined>> = [timeAxis];
    for (const name of channelNames) {
      const ch = data.channels[name];
      if (ch) {
        series.push(
          ch.map((v) => (v === null ? undefined : v) as number | undefined),
        );
      }
    }
    return series as unknown as uPlot.AlignedData;
  }, [data, channelNames, sweepInfo]);

  useEffect(() => {
    if (!containerRef.current || !uplotData || channelNames.length === 0) return;
    const width = containerRef.current.clientWidth || 600;

    let seriesConfig: uPlot.Series[];
    if (sweepInfo) {
      const currents = sweepInfo.aoSweeps.sweepCurrents;
      const minC = Math.min(...currents);
      const maxC = Math.max(...currents);
      const range = maxC - minC || 1;
      seriesConfig = [
        { label: 'Sample' },
        ...sweepInfo.aiSweeps.sweeps.map((_, i) => {
          const t = (currents[i] - minC) / range;
          return {
            label: `Sweep ${i + 1}`,
            stroke: turboColor(t),
            width: 1.2,
            spanGaps: false,
            show: true,
          };
        }),
      ];
    } else {
      seriesConfig = [
        { label: data.timestamps ? 'Time (s)' : 'Sample' },
        ...channelNames.map((name, i) => ({
          label: name,
          stroke: CHANNEL_COLORS[i % CHANNEL_COLORS.length],
          width: 1.5,
          spanGaps: false,
        })),
      ];
    }

    const opts: uPlot.Options = {
      width,
      height,
      cursor: {
        sync: { key: 'ndi-sync' } as uPlot.Cursor.Sync,
        drag: { x: true, y: true },
      },
      scales: {
        x: { time: !!data.timestamps && !sweepInfo },
      },
      legend: { show: !sweepInfo || sweepInfo.aiSweeps.sweeps.length <= 20 },
      axes: [
        {
          stroke: '#708090',
          grid: { stroke: 'rgba(128,128,128,0.08)' },
          ticks: { stroke: 'rgba(128,128,128,0.15)' },
          font: "11px ui-monospace, monospace",
          label: sweepInfo ? 'Sample' : data.timestamps ? 'Time (s)' : 'Sample',
        },
        {
          stroke: '#708090',
          grid: { stroke: 'rgba(128,128,128,0.08)' },
          ticks: { stroke: 'rgba(128,128,128,0.15)' },
          font: "11px ui-monospace, monospace",
          label: sweepInfo ? 'Voltage (mV)' : undefined,
        },
      ],
      series: seriesConfig,
    };

    chartRef.current?.destroy();
    chartRef.current = new uPlot(opts, uplotData, containerRef.current);

    const handleResize = () => {
      if (containerRef.current && chartRef.current) {
        chartRef.current.setSize({
          width: containerRef.current.clientWidth,
          height,
        });
      }
    };
    window.addEventListener('resize', handleResize);
    return () => {
      window.removeEventListener('resize', handleResize);
      chartRef.current?.destroy();
      chartRef.current = null;
    };
  }, [uplotData, channelNames, height, data.timestamps, sweepInfo]);

  if (data.error) {
    const lower = String(data.error).toLowerCase();
    const kind = String(data.errorKind ?? '').toLowerCase();
    const friendly =
      kind === 'vlt_library' || lower.includes('vlt') || lower.includes('library')
        ? 'Required VHSB decoding extension (vlt / DID-python) is not installed on this server. The raw binary is still available in the Files section above.'
        : kind === 'no_file' || lower.includes('no file uid') || lower.includes('no timeseries')
          ? 'This document does not have an associated timeseries file.'
          : kind === 'no_download_url' || kind === 'download' || lower.includes('no download') || lower.includes('download')
            ? 'Timeseries preview is not available for this document. The data file may not be accessible from the cloud.'
            : String(data.error);
    return (
      <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
        {friendly}
      </div>
    );
  }

  if (channelNames.length === 0) {
    return (
      <div className="text-sm text-gray-500 p-3">
        No timeseries data available
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-3 text-xs text-gray-500">
        {sweepInfo ? (
          <>
            <span className="font-mono">{sweepInfo.aiSweeps.sweeps.length} sweeps</span>
            <span className="font-mono">
              {sweepInfo.aiSweeps.sweeps[0]?.length.toLocaleString()} samples/sweep
            </span>
            <span className="font-mono uppercase">{data.format}</span>
            <span className="text-[10px] opacity-60">Color: current injection step</span>
          </>
        ) : (
          <>
            <span className="font-mono">{data.sample_count.toLocaleString()} samples</span>
            <span className="font-mono">
              {channelNames.length} channel{channelNames.length > 1 ? 's' : ''}
            </span>
            {data.format && <span className="font-mono uppercase">{data.format}</span>}
          </>
        )}
      </div>
      <div className="flex gap-2">
        <div
          ref={containerRef}
          className="flex-1 rounded-md border border-gray-200 bg-white p-1"
        />
        {sweepInfo && (
          <div className="flex flex-col items-center gap-1 py-2">
            <span className="text-[9px] text-gray-500 font-mono">High</span>
            <div
              className="w-3 flex-1 rounded-sm border border-gray-200"
              style={{
                background: `linear-gradient(to bottom, ${turboColor(1)}, ${turboColor(0.75)}, ${turboColor(0.5)}, ${turboColor(0.25)}, ${turboColor(0)})`,
              }}
            />
            <span className="text-[9px] text-gray-500 font-mono">Low</span>
          </div>
        )}
      </div>
    </div>
  );
}
