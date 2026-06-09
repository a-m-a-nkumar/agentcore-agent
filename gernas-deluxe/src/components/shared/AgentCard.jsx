import { useNavigate } from 'react-router-dom'
import StatusBadge from './StatusBadge'
import SegmentTag from './SegmentTag'
import { Bot, CheckCircle, Clock } from 'lucide-react'

const segmentColors = {
  merchant: '#0EA5E9',
  print:    '#6B7280',
  b2b:      '#8B5CF6',
  data:     '#10B981',
}

const segmentPaths = {
  merchant: '/merchant',
  print:    '/print',
  b2b:      '/b2b',
  data:     '/data',
}

export default function AgentCard({ agent }) {
  const navigate = useNavigate()
  const borderColor = segmentColors[agent.segment] || '#718096'

  return (
    <div
      className="card min-w-[280px] p-5 cursor-pointer transition-all duration-200 hover:-translate-y-0.5"
      style={{
        borderLeft: `4px solid ${agent.status === 'running' ? borderColor : '#E2E8F0'}`,
        boxShadow: agent.status === 'running'
          ? `var(--shadow-md), 0 0 0 0 ${borderColor}`
          : 'var(--shadow-sm)',
      }}
      onClick={() => navigate(segmentPaths[agent.segment] || '/dashboard')}
    >
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center gap-2">
          <div
            className="w-9 h-9 rounded-lg flex items-center justify-center"
            style={{ background: `${borderColor}15` }}
          >
            {agent.status === 'running'
              ? <span className="agent-pulse"><Bot size={18} style={{ color: borderColor }} /></span>
              : <Bot size={18} style={{ color: borderColor }} />}
          </div>
          <div>
            <p className="text-sm font-semibold text-[#1A2340] leading-tight">{agent.name}</p>
            <SegmentTag segment={agent.segment} />
          </div>
        </div>
        <StatusBadge status={agent.status} />
      </div>

      <div className="grid grid-cols-2 gap-3 mt-3">
        <div>
          <p className="text-xs text-[#718096]">Tasks Today</p>
          <p className="text-lg font-bold text-[#1A2340]">{agent.tasksToday.toLocaleString()}</p>
        </div>
        <div>
          <p className="text-xs text-[#718096]">Success Rate</p>
          <p className="text-lg font-bold text-emerald-600">{agent.successRate}%</p>
        </div>
      </div>

      <div className="flex items-center justify-between mt-3 pt-3 border-t border-[#E2E8F0]">
        <div className="flex items-center gap-1 text-xs text-[#718096]">
          <Clock size={11} />
          <span>Last run: {agent.lastRun}</span>
        </div>
        <div className="flex items-center gap-1 text-xs text-emerald-600 font-medium">
          <CheckCircle size={11} />
          <span>${(agent.costSavings / 1000).toFixed(1)}k saved</span>
        </div>
      </div>
    </div>
  )
}
