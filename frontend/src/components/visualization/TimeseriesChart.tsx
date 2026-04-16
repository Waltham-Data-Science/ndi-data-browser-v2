import UplotReact from 'uplot-react';
import 'uplot/dist/uPlot.min.css';
import { useMemo } from 'react';
import type { TimeseriesData } from '@/api/binary';

export function TimeseriesChart({ data }: { data: TimeseriesData }) {
  const { xs, ys } = useMemo(() => {
    const y = Array.isArray(data.y) && Array.isArray((data.y as unknown[])[0])
      ? (data.y as number[][]).map((ch) => Float64Array.from(ch))
      : [Float64Array.from(data.y as number[])];
    const n = y[0]?.length ?? 0;
    const dt = 1 / (data.sampleRate || 1);
    const xs = new Float64Array(n);
    for (let i = 0; i < n; i++) xs[i] = i * dt;
    return { xs, ys: y };
  }, [data]);

  const options: Parameters<typeof UplotReact>[0]['options'] = {
    width: 800,
    height: 280,
    scales: { x: { time: false } },
    axes: [
      { label: 'Time (s)' },
      { label: 'Amplitude' },
    ],
    series: [
      {},
      ...ys.map((_, i) => ({
        label: ys.length > 1 ? `ch${i}` : 'signal',
        stroke: i === 0 ? '#0284c7' : `hsl(${(i * 67) % 360}, 70%, 45%)`,
        width: 1.25,
      })),
    ],
  };
  return (
    <div className="overflow-x-auto">
      <UplotReact options={options} data={[xs, ...ys]} />
    </div>
  );
}
