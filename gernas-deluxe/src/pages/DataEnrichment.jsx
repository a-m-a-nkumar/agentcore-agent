import React, { useState, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Database, Globe, TrendingUp, BarChart2 } from 'lucide-react'
import StatusBadge from '../components/shared/StatusBadge'

const SEG_COLOR = '#10B981'

const FALLBACK_AGENTS = [
  {
    id: 'agent-005',
    name: 'Data Enrichment Agent',
    description: 'Enriches Deluxe SMB profiles with industry signals, credit data, and payment intelligence.',
    status: 'running', successRate: 99, tasksToday: 892,
    Icon: Database,
  },
]

const PROFILES = [
  {
    id: 'SMB-4421', name: 'Crawford & Sons',
    before: { industry: 'Unknown',      employees: null, creditScore: null, payCycle: null },
    after:  { industry: 'Professional Services', employees: '34', creditScore: '720', payCycle: 'Net-30' },
  },
  {
    id: 'SMB-8812', name: 'Meridian Law Group',
    before: { industry: 'Legal',        employees: null, creditScore: null, payCycle: null },
    after:  { industry: 'Legal Services',        employees: '87', creditScore: '810', payCycle: 'Net-15' },
  },
  {
    id: 'SMB-2290', name: 'Riverside Realty',
    before: { industry: 'Real Estate',  employees: null, creditScore: null, payCycle: null },
    after:  { industry: 'Residential RE Brokerage', employees: '22', creditScore: '755', payCycle: 'Net-30' },
  },
]

const SOURCES = [
  { name: 'IRS Business Registry', icon: Database,   records: 12400 },
  { name: 'D&B Hoovers',           icon: Globe,      records: 892   },
  { name: 'Payment History API',   icon: TrendingUp, records: 3201  },
  { name: 'Industry Signals DB',   icon: BarChart2,  records: 44000 },
]

const FIELDS       = ['industry', 'employees', 'creditScore', 'payCycle']
const FIELD_LABELS = { industry: 'Industry', employees: 'Employees', creditScore: 'Credit', payCycle: 'Pay Cycle' }

function AgentCard({ agent, selected, onSelect }) {
  const Icon = agent.Icon || Database
  return (
    <button
      onClick={() => onSelect(agent.id)}
      className="flex-shrink-0 w-72 p-4 rounded-xl border-2 text-left transition-all"
      style={selected
        ? { borderColor: SEG_COLOR, background: `${SEG_COLOR}08` }
        : { borderColor: '#E2E8F0', background: '#fff' }}
    >
      <div className="flex items-start justify-between mb-2">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-lg flex items-center justify-center" style={{ background: `${SEG_COLOR}15` }}>
            <Icon size={15} style={{ color: SEG_COLOR }} />
          </div>
          <p className="text-sm font-semibold text-[#1A2340]">{agent.name}</p>
        </div>
        <StatusBadge status={agent.status || 'running'} />
      </div>
      <p className="text-xs text-[#718096] mb-3 leading-relaxed line-clamp-2">{agent.description}</p>
      <div className="flex items-center gap-2 text-xs text-[#718096]">
        <span className="font-semibold text-emerald-600">{agent.successRate}%</span>
        <span>success ·</span>
        <span>{(agent.tasksToday || 0).toLocaleString()} tasks/day</span>
      </div>
    </button>
  )
}

function DataEnrichmentDetail() {
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-4 gap-4">
        {[
          { label: 'Profiles Enriched Today', value: '892',   sub: 'SMB records updated',      highlight: true },
          { label: 'Fields Added',             value: '3,568', sub: 'previously null fields'                    },
          { label: 'Sources Queried',          value: '4',     sub: 'external data providers'                   },
          { label: 'Accuracy Rate',            value: '99.1%', sub: 'validation pass rate'                      },
        ].map((s) => (
          <div key={s.label} className={`card p-4 ${s.highlight ? 'border-emerald-200 bg-emerald-50' : ''}`}>
            <p className="text-xs text-[#718096] uppercase tracking-wider mb-1">{s.label}</p>
            <p className={`font-display text-2xl font-bold ${s.highlight ? 'text-emerald-700' : 'text-[#1A2340]'}`}>{s.value}</p>
            <p className="text-xs text-[#718096]">{s.sub}</p>
          </div>
        ))}
      </div>

      <div className="card overflow-hidden">
        <div className="px-5 py-4 border-b border-[#E2E8F0]">
          <h2 className="text-sm font-semibold text-[#1A2340]">Profile Enrichment — Before vs After</h2>
        </div>
        <div className="overflow-x-auto">
          <table>
            <thead>
              <tr>
                <th>Business</th>
                {FIELDS.map((f) => (
                  <th key={f} colSpan={2} className="text-center">{FIELD_LABELS[f]}</th>
                ))}
              </tr>
              <tr style={{ background: '#F7F8FA' }}>
                <td className="px-4 py-1" />
                {FIELDS.map((f) => (
                  <React.Fragment key={f}>
                    <td className="px-3 py-1 text-xs text-[#718096] border-r border-[#E2E8F0]">Before</td>
                    <td className="px-3 py-1 text-xs text-emerald-700 font-medium">After</td>
                  </React.Fragment>
                ))}
              </tr>
            </thead>
            <tbody>
              {PROFILES.map((p) => (
                <tr key={p.id}>
                  <td>
                    <p className="font-semibold text-[#1A2340] text-sm">{p.name}</p>
                    <p className="text-xs text-[#718096] font-mono">{p.id}</p>
                  </td>
                  {FIELDS.map((f) => (
                    <React.Fragment key={f}>
                      <td className="text-sm text-[#CBD5E0] border-r border-[#E2E8F0] px-3 py-3">
                        {p.before[f] ?? <span className="text-xs italic">—</span>}
                      </td>
                      <td className="px-3 py-3">
                        <span className={`text-sm font-medium rounded-md px-1.5 py-0.5 ${!p.before[f] ? 'bg-emerald-50 text-emerald-700' : 'text-[#1A2340]'}`}>
                          {p.after[f]}
                        </span>
                      </td>
                    </React.Fragment>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div>
        <h2 className="text-sm font-semibold text-[#1A2340] mb-3">Data Sources</h2>
        <div className="grid grid-cols-4 gap-4">
          {SOURCES.map((src) => {
            const Icon = src.icon
            return (
              <div key={src.name} className="card p-4">
                <Icon size={18} className="text-[#C8102E] mb-2" />
                <p className="text-sm font-semibold text-[#1A2340]">{src.name}</p>
                <p className="text-xs text-[#718096] mt-1">{src.records.toLocaleString()} records accessed</p>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

export default function DataSegment() {
  const [selectedId, setSelectedId] = useState('agent-005')
  const [agentList, setAgentList]   = useState(FALLBACK_AGENTS)

  useEffect(() => {
    fetch('/api/nova/agents')
      .then(r => r.ok ? r.json() : [])
      .then(data => {
        const filtered = Array.isArray(data)
          ? data.filter(a => a.segmentKey === 'data' || a.segment === 'Data Solutions')
          : []
        if (filtered.length > 0) {
          setAgentList(filtered.map(a => ({ ...a, Icon: Database })))
          setSelectedId(filtered[0].id)
        }
      })
      .catch(() => {})
  }, [])

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3 }}
      className="space-y-6"
    >
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <p className="text-xs font-semibold uppercase tracking-widest mb-1" style={{ color: SEG_COLOR }}>Data Solutions</p>
          <h1 className="font-display text-xl font-bold text-[#1A2340]">Data Segment Agents</h1>
          <p className="text-sm text-[#718096] mt-1">{agentList.length} agent{agentList.length !== 1 ? 's' : ''} operating in this segment</p>
        </div>
        <div className="flex items-center gap-2 bg-emerald-50 border border-emerald-200 rounded-xl px-3 py-2">
          <span className="agent-pulse w-2 h-2 rounded-full bg-emerald-400 inline-block" />
          <span className="text-xs font-semibold text-emerald-700">Segment Active</span>
        </div>
      </div>

      {/* Agent selector */}
      <div className="flex gap-3 overflow-x-auto pb-1">
        {agentList.map(agent => (
          <AgentCard
            key={agent.id}
            agent={agent}
            selected={agent.id === selectedId}
            onSelect={setSelectedId}
          />
        ))}
      </div>

      {/* Detail view */}
      <AnimatePresence mode="wait">
        <motion.div
          key={selectedId}
          initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
          transition={{ duration: 0.2 }}
        >
          <DataEnrichmentDetail />
        </motion.div>
      </AnimatePresence>
    </motion.div>
  )
}
