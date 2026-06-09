import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  FileText, Mail, BarChart2, MessageSquare,
  CreditCard, Printer, Database, Zap, Users, CheckCircle, Clock, Activity
} from 'lucide-react'
import StatusBadge from '../components/shared/StatusBadge'

const SEG_COLOR = '#7C3AED'

const SEG_BADGES = [
  { key: 'merchant', label: 'Merchant', color: '#0EA5E9' },
  { key: 'print',    label: 'Print',    color: '#6B7280' },
  { key: 'b2b',      label: 'B2B',      color: '#8B5CF6' },
  { key: 'data',     label: 'Data',     color: '#10B981' },
]

// ─── Agent definitions ────────────────────────────────────────────────────────
const COMMON_AGENTS = [
  {
    id: 'common-001',
    name: 'Meeting Summarizer',
    description: 'Processes meeting transcripts and notes — extracts key decisions, action items, and owners.',
    icon: MessageSquare,
    status: 'running',
    successRate: 98,
    tasksToday: 134,
    capabilities: [
      'Transcribes and summarizes meetings from any format',
      'Extracts action items with assigned owners and due dates',
      'Identifies key decisions and open questions',
      'Sends structured summary to relevant stakeholders',
    ],
    segmentUses: [
      { seg: 'merchant', useCase: 'Onboarding kick-off calls, compliance review meetings' },
      { seg: 'print',    useCase: 'Customer retention strategy sessions, account reviews' },
      { seg: 'b2b',      useCase: 'Invoice dispute resolution calls, vendor negotiations' },
      { seg: 'data',     useCase: 'Data governance reviews, enrichment planning sessions' },
    ],
    recentTasks: [
      { segment: 'merchant', task: 'Summarized onboarding call — 3 action items extracted',    ts: '12:41' },
      { segment: 'b2b',      task: 'Vendor negotiation summary sent to AP team',               ts: '12:28' },
      { segment: 'print',    task: 'Quarterly retention review — 5 decisions logged',          ts: '12:09' },
      { segment: 'data',     task: 'Data governance meeting summary distributed',              ts: '11:54' },
    ],
  },
  {
    id: 'common-002',
    name: 'Document Analyzer',
    description: 'Parses uploaded documents of any type and extracts structured, actionable data.',
    icon: FileText,
    status: 'running',
    successRate: 97,
    tasksToday: 289,
    capabilities: [
      'Extracts structured data from PDFs, Word docs, and images',
      'Identifies named entities: people, dates, amounts, and clauses',
      'Flags missing or inconsistent fields for human review',
      'Outputs clean JSON or table format for downstream processing',
    ],
    segmentUses: [
      { seg: 'merchant', useCase: 'KYB document verification, application form parsing' },
      { seg: 'print',    useCase: 'Contract clause extraction, renewal terms identification' },
      { seg: 'b2b',      useCase: 'Invoice field extraction, purchase order parsing' },
      { seg: 'data',     useCase: 'Enrichment source document parsing, registry filings' },
    ],
    recentTasks: [
      { segment: 'b2b',      task: 'Extracted 12 fields from INV-2024-8841',                  ts: '12:39' },
      { segment: 'merchant', task: 'KYB doc parsed — EIN, address, owner confirmed',           ts: '12:22' },
      { segment: 'data',     task: 'IRS filing parsed for Crawford & Sons',                    ts: '12:07' },
      { segment: 'print',    task: 'Contract renewal terms extracted for DLX-8812',            ts: '11:48' },
    ],
  },
  {
    id: 'common-003',
    name: 'Notification Orchestrator',
    description: 'Routes alerts, reminders, and updates to the right people across email, Slack, and CRM.',
    icon: Mail,
    status: 'running',
    successRate: 99,
    tasksToday: 412,
    capabilities: [
      'Sends personalized alerts via email, Slack, or webhook',
      'Applies routing rules based on segment, severity, and role',
      'De-duplicates and batches notifications to reduce noise',
      'Tracks delivery and open rates per notification',
    ],
    segmentUses: [
      { seg: 'merchant', useCase: 'Fraud hold alerts to account managers, onboarding status updates' },
      { seg: 'print',    useCase: 'Churn risk escalations, renewal deadline reminders' },
      { seg: 'b2b',      useCase: 'Exception alerts for unmatched invoices, payment confirmations' },
      { seg: 'data',     useCase: 'Enrichment completion notifications, data quality alerts' },
    ],
    recentTasks: [
      { segment: 'merchant', task: 'Fraud hold alert sent — Txn #TXN-88291',                  ts: '12:44' },
      { segment: 'print',    task: 'Renewal reminder sent to DLX-2290 (78 days overdue)',      ts: '12:31' },
      { segment: 'b2b',      task: 'Exception alert dispatched — INV-2024-8780',               ts: '12:18' },
      { segment: 'data',     task: 'Enrichment batch complete — 892 records notified',         ts: '12:02' },
    ],
  },
  {
    id: 'common-004',
    name: 'Report Generator',
    description: 'Produces formatted on-demand or scheduled reports from any data source across the platform.',
    icon: BarChart2,
    status: 'idle',
    successRate: 96,
    tasksToday: 47,
    capabilities: [
      'Pulls live data from any connected segment or ERP system',
      'Generates PDF, Excel, or HTML reports on schedule or on demand',
      'Supports custom templates per business unit',
      'Distributes reports automatically to defined recipient lists',
    ],
    segmentUses: [
      { seg: 'merchant', useCase: 'Daily merchant onboarding status, fraud summary reports' },
      { seg: 'print',    useCase: 'Weekly at-risk account reports, upsell pipeline summaries' },
      { seg: 'b2b',      useCase: 'Monthly reconciliation reports, exception trend analysis' },
      { seg: 'data',     useCase: 'Enrichment coverage reports, data quality scorecards' },
    ],
    recentTasks: [
      { segment: 'b2b',      task: 'Monthly reconciliation report generated and sent',         ts: '11:00' },
      { segment: 'merchant', task: 'Daily fraud summary — 23 events, 7 holds',                 ts: '08:00' },
      { segment: 'print',    task: 'Weekly at-risk report — 5 accounts flagged',               ts: '07:30' },
      { segment: 'data',     task: 'Enrichment coverage scorecard distributed',                ts: '07:00' },
    ],
  },
]

const SEG_ICON  = { merchant: CreditCard, print: Printer, b2b: FileText, data: Database }
const SEG_COLOR_MAP = { merchant: '#0EA5E9', print: '#6B7280', b2b: '#8B5CF6', data: '#10B981' }

// ─── Sub-components ───────────────────────────────────────────────────────────
function AgentCard({ agent, selected, onSelect }) {
  const Icon = agent.icon
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
        <StatusBadge status={agent.status} />
      </div>
      <p className="text-xs text-[#718096] mb-3 leading-relaxed line-clamp-2">{agent.description}</p>
      {/* Segment dots */}
      <div className="flex items-center gap-1.5 mb-2">
        {SEG_BADGES.map(s => (
          <span key={s.key} className="w-4 h-4 rounded-full flex items-center justify-center text-white font-bold"
            style={{ background: s.color, fontSize: 8 }}>
            {s.label[0]}
          </span>
        ))}
        <span className="text-[10px] text-[#A0AEC0] ml-0.5">all segments</span>
      </div>
      <div className="flex items-center gap-2 text-xs text-[#718096]">
        <span className="font-semibold text-emerald-600">{agent.successRate}%</span>
        <span>success ·</span>
        <span>{agent.tasksToday} tasks/day</span>
      </div>
    </button>
  )
}

function AgentDetail({ agent }) {
  const Icon = agent.icon

  return (
    <div className="space-y-5">

      {/* KPIs */}
      <div className="grid grid-cols-4 gap-4">
        {[
          { label: 'Tasks Today',    value: agent.tasksToday,      sub: 'across all segments',    icon: Activity,    color: SEG_COLOR },
          { label: 'Segments Using', value: '4 / 4',               sub: 'full platform coverage', icon: Users,       color: '#10B981' },
          { label: 'Success Rate',   value: `${agent.successRate}%`, sub: 'this week',             icon: CheckCircle, color: '#10B981' },
          { label: 'Avg Latency',    value: '0.8s',                 sub: 'P50 response time',      icon: Clock,       color: '#0EA5E9' },
        ].map((k) => {
          const KIcon = k.icon
          return (
            <div key={k.label} className="card px-4 py-3 flex items-center gap-3">
              <div className="w-9 h-9 rounded-lg flex items-center justify-center flex-shrink-0" style={{ background: `${k.color}15` }}>
                <KIcon size={16} style={{ color: k.color }} />
              </div>
              <div>
                <p className="text-xs text-[#718096]">{k.label}</p>
                <p className="text-lg font-bold text-[#1A2340] font-display leading-tight">{k.value}</p>
                <p className="text-[10px] text-[#A0AEC0]">{k.sub}</p>
              </div>
            </div>
          )
        })}
      </div>

      {/* Capabilities + Segment reach */}
      <div className="grid grid-cols-2 gap-4">

        {/* Capabilities */}
        <div className="card p-5">
          <div className="flex items-center gap-2 mb-4">
            <div className="w-8 h-8 rounded-lg flex items-center justify-center" style={{ background: `${SEG_COLOR}15` }}>
              <Icon size={15} style={{ color: SEG_COLOR }} />
            </div>
            <div>
              <h2 className="text-sm font-semibold text-[#1A2340]">{agent.name}</h2>
              <p className="text-xs text-[#718096]">Capabilities</p>
            </div>
          </div>
          <ul className="flex flex-col gap-2">
            {agent.capabilities.map((cap, i) => (
              <li key={i} className="flex items-start gap-2">
                <CheckCircle size={13} className="text-emerald-500 flex-shrink-0 mt-0.5" />
                <span className="text-xs text-[#4A5568] leading-relaxed">{cap}</span>
              </li>
            ))}
          </ul>
        </div>

        {/* Segment reach */}
        <div className="card p-5">
          <h2 className="text-sm font-semibold text-[#1A2340] mb-1">Segment Use Cases</h2>
          <p className="text-xs text-[#718096] mb-4">How each segment applies this agent</p>
          <div className="flex flex-col gap-3">
            {agent.segmentUses.map((s) => {
              const SIcon = SEG_ICON[s.seg] || FileText
              const color = SEG_COLOR_MAP[s.seg]
              const label = SEG_BADGES.find(b => b.key === s.seg)?.label
              return (
                <div key={s.seg} className="flex items-start gap-3 p-3 rounded-xl border border-[#E2E8F0]"
                  style={{ borderLeftColor: color, borderLeftWidth: 3 }}>
                  <div className="w-6 h-6 rounded-md flex items-center justify-center flex-shrink-0" style={{ background: `${color}15` }}>
                    <SIcon size={12} style={{ color }} />
                  </div>
                  <div>
                    <p className="text-xs font-semibold text-[#1A2340] mb-0.5">{label}</p>
                    <p className="text-xs text-[#718096] leading-relaxed">{s.useCase}</p>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      </div>

      {/* Recent activity */}
      <div className="card overflow-hidden">
        <div className="px-5 py-4 border-b border-[#E2E8F0] flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="agent-pulse w-2 h-2 rounded-full bg-emerald-400 inline-block" />
            <h2 className="text-sm font-semibold text-[#1A2340]">Recent Activity</h2>
          </div>
          <span className="text-xs text-[#A0AEC0]">Latest runs across segments</span>
        </div>
        <div className="divide-y divide-[#F7F8FA]">
          {agent.recentTasks.map((t, i) => {
            const color = SEG_COLOR_MAP[t.segment]
            const label = SEG_BADGES.find(b => b.key === t.segment)?.label
            return (
              <div key={i} className="flex items-center gap-3 px-5 py-3"
                style={{ borderLeft: `3px solid ${color}` }}>
                <span className="text-xs font-semibold px-2 py-0.5 rounded-full text-white flex-shrink-0"
                  style={{ background: color, fontSize: 10 }}>
                  {label}
                </span>
                <p className="text-xs text-[#4A5568] flex-1">{t.task}</p>
                <span className="text-[10px] text-[#A0AEC0] font-mono flex-shrink-0">{t.ts}</span>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

// ─── Page ─────────────────────────────────────────────────────────────────────
export default function CommonAgents() {
  const [selectedId, setSelectedId] = useState(COMMON_AGENTS[0].id)
  const selected = COMMON_AGENTS.find(a => a.id === selectedId)

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3 }}
      className="space-y-6"
    >
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <p className="text-xs font-semibold uppercase tracking-widest mb-1" style={{ color: SEG_COLOR }}>Shared Agents</p>
          <h1 className="font-display text-xl font-bold text-[#1A2340]">Common Agents</h1>
          <p className="text-sm text-[#718096] mt-1">
            General-purpose agents available to all segments — built once, used everywhere
          </p>
        </div>
        <div className="flex items-center gap-2 rounded-xl px-3 py-2"
          style={{ background: `${SEG_COLOR}10`, border: `1px solid ${SEG_COLOR}30` }}>
          <span className="agent-pulse w-2 h-2 rounded-full inline-block" style={{ background: SEG_COLOR }} />
          <span className="text-xs font-semibold" style={{ color: SEG_COLOR }}>{COMMON_AGENTS.length} agents available</span>
        </div>
      </div>

      {/* Agent selector */}
      <div className="flex gap-3 overflow-x-auto pb-1">
        {COMMON_AGENTS.map(agent => (
          <AgentCard
            key={agent.id}
            agent={agent}
            selected={agent.id === selectedId}
            onSelect={setSelectedId}
          />
        ))}
      </div>

      {/* Detail */}
      <AnimatePresence mode="wait">
        <motion.div
          key={selectedId}
          initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
          transition={{ duration: 0.2 }}
        >
          {selected && <AgentDetail agent={selected} />}
        </motion.div>
      </AnimatePresence>
    </motion.div>
  )
}
