import { Cell, Pie, PieChart, ResponsiveContainer } from "recharts";

export function Donut({
  segments,
  centerTop,
  centerBottom,
  size = 168,
}: {
  segments: { label: string; value: number; color: string }[];
  centerTop?: string;
  centerBottom?: string;
  size?: number;
}) {
  const total = segments.reduce((a, x) => a + Math.max(0, x.value), 0);
  const data = total > 0 ? segments : [{ label: "خالی", value: 1, color: "hsl(240 4% 16%)" }];
  return (
    <div className="relative" style={{ width: size, height: size }} dir="ltr">
      <ResponsiveContainer width="100%" height="100%">
        <PieChart>
          <Pie
            data={data}
            dataKey="value"
            nameKey="label"
            cx="50%"
            cy="50%"
            innerRadius={size * 0.34}
            outerRadius={size * 0.48}
            paddingAngle={data.length > 1 ? 2 : 0}
            stroke="none"
            startAngle={90}
            endAngle={-270}
          >
            {data.map((seg, i) => (
              <Cell key={i} fill={seg.color} />
            ))}
          </Pie>
        </PieChart>
      </ResponsiveContainer>
      <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
        {centerTop && <span className="text-lg font-black text-white">{centerTop}</span>}
        {centerBottom && <span className="text-[0.62rem] text-muted-foreground">{centerBottom}</span>}
      </div>
    </div>
  );
}
