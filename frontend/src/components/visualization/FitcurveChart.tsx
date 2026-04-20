import { useEffect, useRef } from 'react';
import uPlot from 'uplot';
import 'uplot/dist/uPlot.min.css';

import type { FitcurveData } from '@/api/binary';

interface FitcurveChartProps {
  data: FitcurveData;
  height?: number;
}

/** Fitcurve preview — thin uPlot line chart of the backend's evaluated
 * (x, y) arrays. The real fit is computed server-side in
 * `binary_service.evaluate_fitcurve()`. */
export function FitcurveChart({ data, height = 260 }: FitcurveChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<uPlot | null>(null);

  useEffect(() => {
    if (!containerRef.current || !data.x || !data.y || data.x.length === 0) return;
    const width = containerRef.current.clientWidth || 600;
    const aligned: uPlot.AlignedData = [data.x, data.y];
    const opts: uPlot.Options = {
      width,
      height,
      cursor: { sync: { key: 'ndi-sync' } as uPlot.Cursor.Sync, drag: { x: true, y: true } },
      legend: { show: true },
      axes: [
        { stroke: '#708090', grid: { stroke: 'rgba(128,128,128,0.08)' }, font: '11px ui-monospace, monospace' },
        { stroke: '#708090', grid: { stroke: 'rgba(128,128,128,0.08)' }, font: '11px ui-monospace, monospace' },
      ],
      series: [
        { label: 'x' },
        { label: data.form, stroke: '#22d3ee', width: 1.75 },
      ],
    };
    chartRef.current?.destroy();
    chartRef.current = new uPlot(opts, aligned, containerRef.current);
    const resize = () => {
      if (containerRef.current && chartRef.current) {
        chartRef.current.setSize({
          width: containerRef.current.clientWidth,
          height,
        });
      }
    };
    window.addEventListener('resize', resize);
    return () => {
      window.removeEventListener('resize', resize);
      chartRef.current?.destroy();
      chartRef.current = null;
    };
  }, [data, height]);

  if (data.error) {
    return (
      <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
        {data.error}
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-3 text-xs text-gray-500 dark:text-gray-400 font-mono">
        <span>Form: {data.form}</span>
        {data.parameters?.length > 0 && (
          <span>
            Parameters: [
            {data.parameters.map((p) => p.toFixed(4)).join(', ')}
            ]
          </span>
        )}
      </div>
      <div
        ref={containerRef}
        className="rounded-md border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 p-1"
      />
    </div>
  );
}
