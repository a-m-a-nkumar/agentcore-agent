import { useState, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { FileText, Link, AlertTriangle } from 'lucide-react'
import StatusBadge from '../components/shared/StatusBadge'

const SEG_COLOR = '#8B5CF6'

const FALLBACK_AGENTS = [
  {
    id: 'agent-003',
    name: 'Invoice Reconciliation Agent',
    description: 'Auto-matches B2B payments to invoices across ERP systems with 94% straight-through rate.',
    status: 'running', successRate: 94, tasksToday: 149,
    Icon: FileText,
  },
]

const INVOICES = [
  { id: 'INV-2024-8841', vendor: 'Staples Business',  date: '2024-05-18', matched: true,  payment: 'PMT-9921' },
  { id: 'INV-2024-8802', vendor: 'Office Depot Pro',  date: '2024-05-17', matched: true,  payment: 'PMT-9905' },
  { id: 'INV-2024-8799', vendor: 'FedEx Business',    date: '2024-05-17', matched: false, payment: null       },
  { id: 'INV-2024-8791', vendor: 'Dell Technologies', date: '2024-05-16', matched: true,  payment: 'PMT-9888' },
  { id: 'INV-2024-8780', vendor: 'Xerox Corp',        date: '2024-05-15', matched: false, payment: null       },
]

const PAYMENTS = [
  { id: 'PMT-9921', vendor: 'Staples Business',  date: '2024-05-19', matched: true },
  { id: 'PMT-9905', vendor: 'Office Depot Pro',  date: '2024-05-18', matched: true },
  { id: 'PMT-9888', vendor: 'Dell Technologies', date: '2024-05-17', matched: true },
]

const EXCEPTIONS = [
  { id: 'INV-2024-8799', issue: 'No matching payment found', vendor: 'FedEx Business', days: 3 },
  { id: 'INV-2024-8780', issue: 'Amount mismatch detected',  vendor: 'Xerox Corp',     days: 5 },
]

function AgentCard({ agent, selected, onSelect }) {
  const Icon = agent.Icon || FileText
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

function InvoiceReconDetail() {
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-4 gap-4">
        {[
          { label: 'Invoices Processed', value: '124',   sub: 'today'                           },
          { label: 'Auto-matched',        value: '94.1%', sub: 'no human touch', highlight: true },
          { label: 'Exceptions Flagged',  value: '2',     sub: 'need review'                     },
          { label: 'Hours Saved',         value: '18.4h', sub: 'vs manual matching'              },
        ].map((s) => (
          <div key={s.label} className={`card p-4 ${s.highlight ? 'bg-emerald-50 border-emerald-200' : ''}`}>
            <p className="text-xs text-[#718096] uppercase tracking-wider mb-1">{s.label}</p>
            <p className={`font-display text-2xl font-bold ${s.highlight ? 'text-emerald-700' : 'text-[#1A2340]'}`}>{s.value}</p>
            <p className="text-xs text-[#718096]">{s.sub}</p>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div className="card overflow-hidden">
          <div className="px-4 py-3 bg-[#F7F8FA] border-b border-[#E2E8F0]">
            <h2 className="text-sm font-semibold text-[#1A2340]">Incoming Invoices</h2>
          </div>
          <div className="divide-y divide-[#E2E8F0]">
            {INVOICES.map((inv) => (
              <div key={inv.id} className="px-4 py-3 flex items-center justify-between">
                <div>
                  <p className="text-sm font-semibold text-[#1A2340]">{inv.vendor}</p>
                  <p className="text-xs text-[#718096] font-mono">{inv.id}</p>
                </div>
                <span className={`text-xs px-2 py-0.5 rounded-full ${inv.matched ? 'bg-emerald-100 text-emerald-700' : 'bg-amber-100 text-amber-700'}`}>
                  {inv.matched ? 'Matched' : 'Unmatched'}
                </span>
              </div>
            ))}
          </div>
        </div>

        <div className="card overflow-hidden">
          <div className="px-4 py-3 bg-[#F7F8FA] border-b border-[#E2E8F0]">
            <h2 className="text-sm font-semibold text-[#1A2340]">Matched Payments</h2>
          </div>
          <div className="divide-y divide-[#E2E8F0]">
            {PAYMENTS.map((pmt) => (
              <div key={pmt.id} className="px-4 py-3 flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Link size={14} className="text-emerald-500" />
                  <div>
                    <p className="text-sm font-semibold text-[#1A2340]">{pmt.vendor}</p>
                    <p className="text-xs text-[#718096] font-mono">{pmt.id}</p>
                  </div>
                </div>
                <span className="text-xs px-2 py-0.5 rounded-full bg-emerald-100 text-emerald-700">Matched</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="card overflow-hidden">
        <div className="px-5 py-3 border-b border-[#E2E8F0] flex items-center gap-2">
          <AlertTriangle size={15} className="text-amber-500" />
          <h2 className="text-sm font-semibold text-[#1A2340]">Exceptions — Needs Human Review</h2>
        </div>
        <table>
          <thead>
            <tr>
              <th>Invoice ID</th>
              <th>Vendor</th>
              <th>Issue</th>
              <th>Days Outstanding</th>
            </tr>
          </thead>
          <tbody>
            {EXCEPTIONS.map((ex) => (
              <tr key={ex.id}>
                <td className="font-mono text-xs text-[#718096]">{ex.id}</td>
                <td className="font-medium text-[#1A2340]">{ex.vendor}</td>
                <td><span className="px-2 py-0.5 rounded-md bg-amber-50 border border-amber-200 text-amber-700 text-xs">{ex.issue}</span></td>
                <td><span className={`font-semibold ${ex.days > 4 ? 'text-red-500' : 'text-amber-500'}`}>{ex.days} days</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export default function B2BSegment() {
  const [selectedId, setSelectedId] = useState('agent-003')
  const [agentList, setAgentList]   = useState(FALLBACK_AGENTS)

  useEffect(() => {
    fetch('/api/nova/agents')
      .then(r => r.ok ? r.json() : [])
      .then(data => {
        const filtered = Array.isArray(data)
          ? data.filter(a => a.segmentKey === 'b2b' || a.segment === 'B2B Payments')
          : []
        if (filtered.length > 0) {
          setAgentList(filtered.map(a => ({ ...a, Icon: FileText })))
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
          <p className="text-xs font-semibold uppercase tracking-widest mb-1" style={{ color: SEG_COLOR }}>B2B Payments</p>
          <h1 className="font-display text-xl font-bold text-[#1A2340]">B2B Segment Agents</h1>
          <p className="text-sm text-[#718096] mt-1">{agentList.length} agent{agentList.length !== 1 ? 's' : ''} operating in this segment</p>
        </div>
        <div className="flex items-center gap-2 bg-purple-50 border border-purple-200 rounded-xl px-3 py-2">
          <span className="agent-pulse w-2 h-2 rounded-full bg-purple-400 inline-block" />
          <span className="text-xs font-semibold text-purple-700">Segment Active</span>
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
          <InvoiceReconDetail />
        </motion.div>
      </AnimatePresence>
    </motion.div>
  )
}
