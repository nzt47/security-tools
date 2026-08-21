/**
 * RadarChart —— 五维效果评估雷达图（SVG 手绘，不引入图表库）
 * 从 PromptLab.tsx 拆分（原 53-93 行，逻辑不变）。
 */

interface RadarChartProps {
  data: { label: string; value: number }[];
}

export default function RadarChart({ data }: RadarChartProps) {
  const size = 220;
  const cx = size / 2;
  const cy = size / 2;
  const r = 74;
  const n = data.length;
  const point = (i: number, scale: number): [number, number] => {
    const angle = (Math.PI * 2 * i) / n - Math.PI / 2;
    return [cx + Math.cos(angle) * r * scale, cy + Math.sin(angle) * r * scale];
  };
  const poly = (scale: number) =>
    data.map((_, i) => point(i, scale).map((v) => v.toFixed(1)).join(',')).join(' ');
  const valuePoly = data.map((d, i) => point(i, Math.max(0.03, d.value / 100)).join(',')).join(' ');

  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="pl-radar" role="img" aria-label="效果评估雷达图">
      {[0.25, 0.5, 0.75, 1].map((s) => (
        <polygon key={s} points={poly(s)} fill="none" stroke="rgba(148,163,184,0.25)" strokeWidth={1} />
      ))}
      {data.map((_, i) => {
        const [x, y] = point(i, 1);
        return <line key={i} x1={cx} y1={cy} x2={x} y2={y} stroke="rgba(148,163,184,0.18)" strokeWidth={1} />;
      })}
      <polygon points={valuePoly} fill="rgba(34,211,238,0.25)" stroke="#22d3ee" strokeWidth={2} />
      {data.map((d, i) => {
        const [x, y] = point(i, d.value / 100);
        return (
          <g key={d.label}>
            <circle cx={x} cy={y} r={3.5} fill="#22d3ee" />
            <text x={point(i, 1.18)[0]} y={point(i, 1.18)[1]} textAnchor="middle" dominantBaseline="middle" className="pl-radar-label">
              {d.label}
            </text>
            <text x={point(i, 0.88)[0]} y={point(i, 0.88)[1]} textAnchor="middle" dominantBaseline="middle" className="pl-radar-value">
              {d.value}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
