import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Area, AreaChart } from 'recharts'

const CustomTooltip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null
  return (
    <div className="card px-3 py-2 text-sm">
      <p className="font-semibold text-[#1A2340] mb-1">{label}</p>
      <p className="text-[#C8102E]">Tasks: <strong>{payload[0]?.value}</strong></p>
    </div>
  )
}

export default function AgentActivityChart({ data }) {
  if (!data) return null
  return (
    <ResponsiveContainer width="100%" height={200}>
      <AreaChart data={data} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
        <defs>
          <linearGradient id="taskGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%"  stopColor="#C8102E" stopOpacity={0.15} />
            <stop offset="95%" stopColor="#C8102E" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="#E2E8F0" vertical={false} />
        <XAxis dataKey="time" tick={{ fontSize: 11, fill: '#718096' }} axisLine={false} tickLine={false} />
        <YAxis tick={{ fontSize: 11, fill: '#718096' }} axisLine={false} tickLine={false} />
        <Tooltip content={<CustomTooltip />} />
        <Area type="monotone" dataKey="tasks" stroke="#C8102E" strokeWidth={2} fill="url(#taskGrad)" dot={false} />
      </AreaChart>
    </ResponsiveContainer>
  )
}
