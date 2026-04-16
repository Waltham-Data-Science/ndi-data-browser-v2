import UplotReact from 'uplot-react';
import 'uplot/dist/uPlot.min.css';

export function FitcurveChart({ data }: { data: { form: string; x: number[]; y: number[] } }) {
  const options: Parameters<typeof UplotReact>[0]['options'] = {
    width: 800,
    height: 260,
    scales: { x: { time: false } },
    axes: [{ label: 'x' }, { label: 'y' }],
    series: [{}, { label: data.form, stroke: '#17a7ff', width: 1.5 }],
  };
  return (
    <div className="overflow-x-auto">
      <UplotReact
        options={options}
        data={[Float64Array.from(data.x), Float64Array.from(data.y)]}
      />
    </div>
  );
}
