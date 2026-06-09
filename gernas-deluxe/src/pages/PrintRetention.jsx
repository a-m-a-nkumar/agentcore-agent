import { useState, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Printer, TrendingUp } from 'lucide-react'
import StatusBadge from '../components/shared/StatusBadge'

const SEG_COLOR = '#6B7280'

const FALLBACK_AGENTS = [
  {
    id: 'agent-004',
    name: 'Churn Prevention Agent',
    description: 'Proactive AI intervention to detect and prevent print customer churn before it happens.',
    status: 'running', successRate: 96, tasksToday: 312,
    Icon: Printer,
  },
  {
    id: 'agent-006',
    name: 'Upsell Intelligence Agent',
    description: 'Identifies cross-sell and upsell opportunities across the print customer base.',
    status: 'running', successRate: 94, tasksToday: 198,
    Icon: TrendingUp,
  },
]

const AT_RISK_ACCOUNTS = [
  { id: 'DLX-4421', name: 'Crawford & Sons',       risk: 87, signal: 'Order frequency -60%', lastOrder: '62 days ago', action: 'Retention email sent',  status: 'contacted'  },
  { id: 'DLX-8812', name: 'Meridian Law Group',    risk: 72, signal: 'Competitor inquiry',   lastOrder: '41 days ago', action: 'Sales call scheduled', status: 'escalated'  },
  { id: 'DLX-2290', name: 'Riverside Realty',      risk: 91, signal: 'Contract expiring',    lastOrder: '78 days ago', action: 'Renewal offer sent',   status: 'contacted'  },
  { id: 'DLX-5567', name: 'Blue Anchor Logistics', risk: 45, signal: 'Volume declining',     lastOrder: '19 days ago', action: 'Monitoring',           status: 'watching'   },
  { id: 'DLX-9901', name: 'Summit Dental Group',   risk: 68, signal: 'Reduced reorder',      lastOrder: '33 days ago', action: 'Discount offer sent',  status: 'contacted'  },
]

const UPSELL_CANDIDATES = [
  { id: 'DLX-4421', name: 'Crawford & Sons',     current: 'Print Basic',    opportunity: 'Deluxe Check Services', score: 87, action: 'Email queued' },
  { id: 'DLX-7741', name: 'Summit Financial',    current: 'Print Premium',  opportunity: 'Merchant Payments',     score: 81, action: 'Sales call'   },
  { id: 'DLX-5567', name: 'Blue Anchor',         current: 'Print Standard', opportunity: 'B2B Invoice Platform',  score: 74, action: 'Email queued' },
  { id: 'DLX-8812', name: 'Meridian Law Group',  current: 'Print Basic',    opportunity: 'Merchant Payments',     score: 71, action: 'Monitoring'   },
  { id: 'DLX-2290', name: 'Riverside Realty',    current: 'Print Standard', opportunity: 'Data Solutions',        score: 65, action: 'Email queued' },
]

const CROSS_SELL_STREAMS = [
  { from: 'Print', to: 'Merchant Payments',    count: 34, color: '#0EA5E9' },
  { from: 'Print', to: 'B2B Payments',         count: 28, color: '#8B5CF6' },
  { from: 'Print', to: 'Data Solutions',       count: 27, color: '#10B981' },
]

const STATUS_COLORS = {
  contacted: 'bg-blue-100 text-blue-800 border border-blue-200',
  escalated: 'bg-orange-100 text-orange-800 border border-orange-200',
  watching:  'bg-gray-100 text-gray-600 border border-gray-200',
}

function RiskBar({ risk }) {
  const color = risk >= 80 ? '#EF4444' : risk >= 60 ? '#F59E0B' : '#10B981'
  return (
    <div className="flex items-center gap-2">
      <div className="w-24 h-2 bg-[#E2E8F0] rounded-full overflow-hidden">
        <div className="h-full rounded-full" style={{ width: `${risk}%`, background: color }} />
      </div>
      <span className="text-sm font-semibold" style={{ color }}>{risk}</span>
    </div>
  )
}

function AgentCard({ agent, selected, onSelect }) {
  const Icon = agent.Icon || Printer
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

function ChurnPreventionDetail() {
  return (
    <div className="space-y-5">
      <div className="card overflow-hidden">
        <div className="px-5 py-4 border-b border-[#E2E8F0] flex items-center justify-between">
          <h2 className="text-sm font-semibold text-[#1A2340]">At-Risk Accounts</h2>
          <span className="text-xs text-[#718096]">{AT_RISK_ACCOUNTS.length} accounts flagged</span>
        </div>
        <table>
          <thead>
            <tr>
              <th>Account</th>
              <th>Risk Score</th>
              <th>Signal</th>
              <th>Last Order</th>
              <th>Agent Action</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {AT_RISK_ACCOUNTS.map((acct) => (
              <tr key={acct.id}>
                <td>
                  <p className="text-sm font-semibold text-[#1A2340]">{acct.name}</p>
                  <p className="text-xs text-[#718096] font-mono">{acct.id}</p>
                </td>
                <td><RiskBar risk={acct.risk} /></td>
                <td><span className="px-2 py-0.5 rounded-md bg-amber-50 text-amber-700 border border-amber-200 text-xs">{acct.signal}</span></td>
                <td className="text-sm text-[#718096]">{acct.lastOrder}</td>
                <td className="text-sm text-[#4A5568]">{acct.action}</td>
                <td><span className={`inline-flex px-2.5 py-0.5 rounded-full text-xs font-medium ${STATUS_COLORS[acct.status]}`}>{acct.status.charAt(0).toUpperCase() + acct.status.slice(1)}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="grid grid-cols-3 gap-4">
        {[
          { label: 'Accounts Saved This Month', value: '47',      sub: 'Churn interventions succeeded' },
          { label: 'Accounts Contacted',         value: '31',      sub: 'Retention actions taken'       },
          { label: 'Avg Days to Intervention',   value: '58 days', sub: 'Before contract expiry'        },
        ].map((tile, i) => (
          <div key={i} className="card p-5">
            <p className="text-xs text-[#718096] uppercase tracking-wider mb-1">{tile.label}</p>
            <p className="font-display text-2xl font-bold text-[#1A2340]">{tile.value}</p>
            <p className="text-xs text-[#718096] mt-1">{tile.sub}</p>
          </div>
        ))}
      </div>
    </div>
  )
}

function UpsellIntelligenceDetail() {
  const actionStyle = {
    'Email queued': 'bg-blue-100 text-blue-700 border border-blue-200',
    'Sales call':   'bg-purple-100 text-purple-700 border border-purple-200',
    'Monitoring':   'bg-gray-100 text-gray-600 border border-gray-200',
  }

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-4 gap-4">
        {[
          { label: 'Accounts Analyzed',    value: '847', sub: 'in print segment'           },
          { label: 'Opportunities Found',  value: '89',  sub: 'cross-sell ready'            },
          { label: 'High Priority',        value: '23',  sub: 'score > 80', highlight: true },
          { label: 'Avg Propensity Score', value: '71',  sub: 'out of 100'                  },
        ].map((s) => (
          <div key={s.label} className={`card p-4 ${s.highlight ? 'border-purple-200 bg-purple-50' : ''}`}>
            <p className="text-xs text-[#718096] uppercase tracking-wider mb-1">{s.label}</p>
            <p className={`font-display text-2xl font-bold ${s.highlight ? 'text-purple-700' : 'text-[#1A2340]'}`}>{s.value}</p>
            <p className="text-xs text-[#718096]">{s.sub}</p>
          </div>
        ))}
      </div>

      <div className="card overflow-hidden">
        <div className="px-5 py-4 border-b border-[#E2E8F0] flex items-center justify-between">
          <h2 className="text-sm font-semibold text-[#1A2340]">Top Cross-sell Candidates</h2>
          <span className="text-xs text-[#718096]">Ranked by propensity score</span>
        </div>
        <table>
          <thead>
            <tr>
              <th>Account</th>
              <th>Current Product</th>
              <th>Opportunity</th>
              <th>Score</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody>
            {UPSELL_CANDIDATES.map((c) => (
              <tr key={c.id}>
                <td>
                  <p className="text-sm font-semibold text-[#1A2340]">{c.name}</p>
                  <p className="text-xs text-[#718096] font-mono">{c.id}</p>
                </td>
                <td className="text-sm text-[#718096]">{c.current}</td>
                <td>
                  <span className="px-2 py-0.5 rounded-md bg-emerald-50 border border-emerald-200 text-emerald-700 text-xs font-medium">{c.opportunity}</span>
                </td>
                <td>
                  <div className="flex items-center gap-2">
                    <div className="w-16 h-1.5 rounded-full bg-[#E2E8F0] overflow-hidden">
                      <div className="h-full rounded-full bg-purple-500" style={{ width: `${c.score}%` }} />
                    </div>
                    <span className="text-xs font-bold text-purple-600">{c.score}</span>
                  </div>
                </td>
                <td><span className={`inline-flex px-2 py-0.5 rounded-full text-xs font-medium ${actionStyle[c.action]}`}>{c.action}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card p-5">
        <h2 className="text-sm font-semibold text-[#1A2340] mb-3">Cross-sell Opportunity Streams</h2>
        <div className="flex flex-col gap-3">
          {CROSS_SELL_STREAMS.map((s) => (
            <div key={s.to} className="flex items-center gap-3">
              <span className="text-xs text-[#718096] w-48">Print → {s.to}</span>
              <div className="flex-1 h-2 rounded-full bg-[#E2E8F0] overflow-hidden">
                <div className="h-full rounded-full" style={{ width: `${(s.count / 89) * 100}%`, background: s.color }} />
              </div>
              <span className="text-xs font-semibold text-[#1A2340] w-6 text-right">{s.count}</span>
            </div>
          ))}
        </div>
        <p className="text-xs text-[#A0AEC0] mt-3">accounts ready for cross-sell introduction</p>
      </div>
    </div>
  )
}

export default function PrintSegment() {
  const [selectedId, setSelectedId] = useState('agent-004')
  const [agentList, setAgentList]   = useState(FALLBACK_AGENTS)

  useEffect(() => {
    fetch('/api/nova/agents')
      .then(r => r.ok ? r.json() : [])
      .then(data => {
        const filtered = Array.isArray(data)
          ? data.filter(a => a.segmentKey === 'print' || a.segment === 'Print')
          : []
        if (filtered.length > 0) {
          setAgentList(filtered.map(a => ({ ...a, Icon: a.id === 'agent-006' ? TrendingUp : Printer })))
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
          <p className="text-xs font-semibold uppercase tracking-widest mb-1" style={{ color: SEG_COLOR }}>Print &amp; Retention</p>
          <h1 className="font-display text-xl font-bold text-[#1A2340]">Print Segment Agents</h1>
          <p className="text-sm text-[#718096] mt-1">{agentList.length} agents operating in this segment</p>
        </div>
        <div className="flex items-center gap-2 bg-gray-50 border border-gray-200 rounded-xl px-3 py-2">
          <span className="agent-pulse w-2 h-2 rounded-full bg-gray-400 inline-block" />
          <span className="text-xs font-semibold text-gray-600">Segment Active</span>
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
          {selectedId === 'agent-004' && <ChurnPreventionDetail />}
          {selectedId === 'agent-006' && <UpsellIntelligenceDetail />}
          {!['agent-004', 'agent-006'].includes(selectedId) && <ChurnPreventionDetail />}
        </motion.div>
      </AnimatePresence>
    </motion.div>
  )
}
