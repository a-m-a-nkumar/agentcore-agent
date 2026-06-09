import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer } from 'recharts'

const CustomTooltip = ({ active, payload }) => {
  if (!active || !payload?.length) return null
  const d = payload[0].payload
  return (
    <div className="card px-3 py-2 text-sm">
      <p className="font-semibold text-[#1A2340]">{d.name}</p>
      <p className="text-[#718096]">Revenue share: <strong>{d.revenue}%</strong></p>
    </div>
  )
}

export default function SegmentDonut({ data }) {
  if (!data) return null
  return (
    <div className="flex flex-col items-center">
      <ResponsiveContainer width="100%" height={180}>
        <PieChart>
          <Pie data={data} dataKey="revenue" cx="50%" cy="50%" innerRadius={52} outerRadius={78} paddingAngle={3}>
            {data.map((entry, i) => (
              <Cell key={i} fill={entry.color} stroke="none" />
            ))}
          </Pie>
          <Tooltip content={<CustomTooltip />} />
        </PieChart>
      </ResponsiveContainer>
      <div className="grid grid-cols-2 gap-x-6 gap-y-2 mt-2">
        {data.map((seg, i) => (
          <div key={i} className="flex items-center gap-2">
            <span className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ background: seg.color }} />
            <span className="text-xs text-[#4A5568]">{seg.name}</span>
            <span className="text-xs font-semibold text-[#1A2340]">{seg.revenue}%</span>
          </div>
        ))}
      </div>
    </div>
  )
}
