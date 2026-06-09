import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { useNavigate } from 'react-router-dom'
import {
  Search, Filter, Plus, Star, Download, Eye, Zap, CheckCircle,
  CreditCard, Printer, FileText, Database, Shield, Bot,
  ChevronRight, Clock, BarChart2, Users, Lock, Globe, ArrowRight, X
} from 'lucide-react'
import SegmentTag from '../components/shared/SegmentTag'
import StatusBadge from '../components/shared/StatusBadge'

const CATEGORIES = ['All', 'Onboarding', 'Risk & Compliance', 'Revenue', 'Operations', 'Data', 'Fraud']
const SEGMENTS   = ['All Segments', 'Merchant Services', 'Print', 'B2B Payments', 'Data Solutions', 'Platform']
const MODELS     = ['All Models', 'claude-sonnet-4-6', 'claude-opus-4-7', 'claude-haiku-4-5']

const segMap = { 'Merchant Services': 'merchant', 'Print': 'print', 'B2B Payments': 'b2b', 'Data Solutions': 'data' }

const catalogAgents = [
  {
    id: 'cat-001', name: 'SMB Onboarding Agent', category: 'Onboarding', segment: 'merchant',
    model: 'claude-sonnet-4-6', status: 'running', rating: 4.9, installs: 47,
    description: 'End-to-end merchant onboarding: KYB verification, terminal provisioning, account activation — 97% faster than manual.',
    tools: ['kyb-verify', 'crm-write', 'email-send', 'terminal-config'],
    triggers: ['webhook: new-application', 'schedule: daily-queue'],
    metrics: { tasksToday: 47, successRate: 96.2, avgTime: '2.3h' },
    author: 'DLX_AGENTIC_OS Team', verified: true, version: '2.1.4',
    tags: ['KYB', 'Payments', 'Automation'],
  },
  {
    id: 'cat-002', name: 'Churn Prevention Agent', category: 'Revenue', segment: 'print',
    model: 'claude-sonnet-4-6', status: 'running', rating: 4.7, installs: 31,
    description: 'Monitors order frequency, flags at-risk print customers, and triggers multi-channel retention campaigns autonomously.',
    tools: ['crm-read', 'order-history', 'email-send', 'sales-alert'],
    triggers: ['schedule: hourly-scan', 'event: order-gap-detected'],
    metrics: { tasksToday: 31, successRate: 88.7, avgTime: '4.1h' },
    author: 'DLX_AGENTIC_OS Team', verified: true, version: '1.8.2',
    tags: ['Retention', 'Print', 'CRM'],
  },
  {
    id: 'cat-003', name: 'Invoice Reconciliation Agent', category: 'Operations', segment: 'b2b',
    model: 'claude-sonnet-4-6', status: 'idle', rating: 4.8, installs: 124,
    description: 'Auto-matches B2B payments to invoices across ERP systems. Flags exceptions, resolves discrepancies, writes to GL.',
    tools: ['erp-read', 'payment-match', 'gl-write', 'exception-flag'],
    triggers: ['schedule: realtime', 'event: payment-received'],
    metrics: { tasksToday: 124, successRate: 94.1, avgTime: '0.8h' },
    author: 'DLX_AGENTIC_OS Team', verified: true, version: '3.0.1',
    tags: ['ERP', 'Finance', 'Reconciliation'],
  },
  {
    id: 'cat-004', name: 'Data Enrichment Agent', category: 'Data', segment: 'data',
    model: 'claude-sonnet-4-6', status: 'running', rating: 5.0, installs: 892,
    description: 'Enriches SMB profiles with revenue signals, industry classifications, credit data, and payment behavior patterns.',
    tools: ['data-fetch', 'profile-update', 'signal-score', 'warehouse-write'],
    triggers: ['schedule: continuous', 'event: new-record'],
    metrics: { tasksToday: 892, successRate: 99.1, avgTime: '0.2h' },
    author: 'DLX_AGENTIC_OS Team', verified: true, version: '4.2.0',
    tags: ['Enrichment', 'ML', 'Profiles'],
  },
  {
    id: 'cat-005', name: 'Fraud Detection Agent', category: 'Risk & Compliance', segment: 'merchant',
    model: 'claude-sonnet-4-6', status: 'running', rating: 4.9, installs: 3201,
    description: 'Real-time transaction monitoring with anomaly detection, auto-hold, and compliance alerting for merchant accounts.',
    tools: ['txn-stream', 'anomaly-detect', 'hold-trigger', 'compliance-alert'],
    triggers: ['event: transaction-stream', 'realtime: continuous'],
    metrics: { tasksToday: 3201, successRate: 99.8, avgTime: '0.02h' },
    author: 'DLX_AGENTIC_OS Team', verified: true, version: '5.1.0',
    tags: ['Fraud', 'RealTime', 'Compliance'],
  },
  {
    id: 'cat-006', name: 'Upsell Intelligence Agent', category: 'Revenue', segment: 'print',
    model: 'claude-sonnet-4-6', status: 'idle', rating: 4.3, installs: 18,
    description: 'Identifies print customers with high propensity to migrate to payment services. Scores leads and routes to sales.',
    tools: ['crm-read', 'propensity-score', 'proposal-gen', 'sales-route'],
    triggers: ['schedule: weekly-score', 'event: usage-milestone'],
    metrics: { tasksToday: 18, successRate: 72.4, avgTime: '6.2h' },
    author: 'DLX_AGENTIC_OS Team', verified: true, version: '1.2.0',
    tags: ['Upsell', 'Propensity', 'Sales'],
  },
]

const segIconMap = { merchant: CreditCard, print: Printer, b2b: FileText, data: Database }
const segColorMap = { merchant: '#0EA5E9', print: '#6B7280', b2b: '#8B5CF6', data: '#10B981' }

function StarRating({ rating }) {
  return (
    <div className="flex items-center gap-1">
      <Star size={12} className="text-amber-400 fill-amber-400" />
      <span className="text-xs font-semibold text-[#1A2340]">{rating.toFixed(1)}</span>
    </div>
  )
}

function AgentDetailModal({ agent, onClose, onBuild }) {
  if (!agent) return null
  const navigate = useNavigate()
  const Icon = segIconMap[agent.segment] || Bot
  const color = segColorMap[agent.segment] || '#718096'

  return (
    <AnimatePresence>
      <motion.div
        className="fixed inset-0 z-50 flex items-center justify-end"
        initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
      >
        <div className="absolute inset-0 bg-black/30 backdrop-blur-sm" onClick={onClose} />
        <motion.div
          className="relative w-[520px] h-full bg-white shadow-2xl overflow-y-auto flex flex-col"
          initial={{ x: 60, opacity: 0 }} animate={{ x: 0, opacity: 1 }} exit={{ x: 60, opacity: 0 }}
          transition={{ type: 'spring', damping: 26, stiffness: 300 }}
        >
          {/* Header */}
          <div className="p-6 border-b border-[#E2E8F0]" style={{ borderLeft: `4px solid ${color}` }}>
            <div className="flex items-start justify-between mb-3">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 rounded-xl flex items-center justify-center" style={{ background: `${color}20` }}>
                  <Icon size={20} style={{ color }} />
                </div>
                <div>
                  <div className="flex items-center gap-2">
                    <h2 className="font-display text-lg font-bold text-[#1A2340]">{agent.name}</h2>
                    {agent.verified && <CheckCircle size={14} className="text-emerald-500" />}
                  </div>
                  <div className="flex items-center gap-2 mt-0.5">
                    <SegmentTag segment={agent.segment} />
                    <StatusBadge status={agent.status} />
                    <span className="text-xs text-[#718096]">v{agent.version}</span>
                  </div>
                </div>
              </div>
              <button onClick={onClose} className="w-7 h-7 rounded-lg bg-[#F7F8FA] flex items-center justify-center hover:bg-[#EDF0F5]">
                <X size={14} className="text-[#718096]" />
              </button>
            </div>
            <p className="text-sm text-[#4A5568] leading-relaxed">{agent.description}</p>
          </div>

          <div className="flex-1 p-6 space-y-5">
            {/* Metrics */}
            <div>
              <p className="text-xs font-semibold text-[#718096] uppercase tracking-wider mb-3">Live Performance</p>
              <div className="grid grid-cols-2 gap-3">
                {Object.entries(agent.metrics).map(([k, v]) => (
                  <div key={k} className="bg-[#F7F8FA] rounded-xl p-3">
                    <p className="text-xs text-[#718096] capitalize">{k.replace(/([A-Z])/g, ' $1').trim()}</p>
                    <p className="text-base font-bold text-[#1A2340] mt-0.5">{v}</p>
                  </div>
                ))}
              </div>
            </div>

            {/* Tools */}
            <div>
              <p className="text-xs font-semibold text-[#718096] uppercase tracking-wider mb-2">Tools</p>
              <div className="flex flex-wrap gap-1.5">
                {agent.tools.map((t) => (
                  <span key={t} className="px-2 py-1 rounded-lg bg-[#F7F8FA] border border-[#E2E8F0] text-xs font-mono text-[#4A5568]">{t}</span>
                ))}
              </div>
            </div>

            {/* Triggers */}
            <div>
              <p className="text-xs font-semibold text-[#718096] uppercase tracking-wider mb-2">Triggers</p>
              <div className="flex flex-col gap-1.5">
                {agent.triggers.map((t) => (
                  <div key={t} className="flex items-center gap-2 px-3 py-2 rounded-lg bg-[#F7F8FA] border border-[#E2E8F0]">
                    <Zap size={12} className="text-amber-500" />
                    <span className="text-xs font-mono text-[#4A5568]">{t}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* Model */}
            <div>
              <p className="text-xs font-semibold text-[#718096] uppercase tracking-wider mb-2">Model</p>
              <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-[#FDF0F2] border border-[#F5C6CE]">
                <Bot size={13} className="text-[#C8102E]" />
                <span className="text-xs font-mono font-semibold text-[#C8102E]">{agent.model}</span>
              </div>
            </div>

            {/* Tags */}
            <div className="flex flex-wrap gap-1.5">
              {agent.tags.map((t) => (
                <span key={t} className="px-2 py-0.5 rounded-full bg-[#EDF0F5] text-xs text-[#4A5568]">{t}</span>
              ))}
            </div>
          </div>

          {/* Actions */}
          <div className="p-6 border-t border-[#E2E8F0] flex gap-3">
            <button
              onClick={() => { onClose(); navigate('/builder', { state: { template: agent } }) }}
              className="flex-1 flex items-center justify-center gap-2 py-2.5 rounded-xl text-white text-sm font-semibold"
              style={{ background: '#C8102E', boxShadow: 'var(--shadow-red)' }}
            >
              <Plus size={15} /> Clone & Customize
            </button>
            <button className="flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-medium border border-[#E2E8F0] text-[#4A5568] hover:bg-[#F7F8FA]">
              <Download size={14} /> Deploy
            </button>
          </div>
        </motion.div>
      </motion.div>
    </AnimatePresence>
  )
}

function CatalogCard({ agent, onClick }) {
  const Icon = segIconMap[agent.segment] || Bot
  const color = segColorMap[agent.segment] || '#718096'
  return (
    <motion.div
      whileHover={{ y: -2, boxShadow: '0 8px 24px rgba(26,35,64,0.12)' }}
      transition={{ duration: 0.15 }}
      className="card p-5 cursor-pointer flex flex-col"
      style={{ borderTop: `3px solid ${color}` }}
      onClick={() => onClick(agent)}
    >
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center gap-2.5">
          <div className="w-9 h-9 rounded-xl flex items-center justify-center" style={{ background: `${color}15` }}>
            {agent.status === 'running'
              ? <span className="agent-pulse"><Icon size={17} style={{ color }} /></span>
              : <Icon size={17} style={{ color }} />}
          </div>
          <div>
            <div className="flex items-center gap-1.5">
              <p className="text-sm font-semibold text-[#1A2340] leading-tight">{agent.name}</p>
              {agent.verified && <CheckCircle size={12} className="text-emerald-500" />}
            </div>
            <SegmentTag segment={agent.segment} />
          </div>
        </div>
        <StatusBadge status={agent.status} />
      </div>

      <p className="text-xs text-[#718096] leading-relaxed flex-1 mb-3 line-clamp-2">{agent.description}</p>

      <div className="flex flex-wrap gap-1 mb-3">
        {agent.tools.slice(0, 3).map((t) => (
          <span key={t} className="px-1.5 py-0.5 rounded text-xs bg-[#F7F8FA] border border-[#E2E8F0] font-mono text-[#718096]">{t}</span>
        ))}
        {agent.tools.length > 3 && <span className="text-xs text-[#718096]">+{agent.tools.length - 3}</span>}
      </div>

      <div className="flex items-center justify-between pt-3 border-t border-[#E2E8F0]">
        <div className="flex items-center gap-3">
          <StarRating rating={agent.rating} />
          <span className="text-xs text-[#718096] flex items-center gap-1"><Download size={10} />{agent.installs.toLocaleString()}</span>
        </div>
        <ChevronRight size={12} className="text-[#CBD5E0]" />
      </div>
    </motion.div>
  )
}

export default function AgentCatalog() {
  const navigate = useNavigate()
  const [search, setSearch]       = useState('')
  const [category, setCategory]   = useState('All')
  const [segment, setSegment]     = useState('All Segments')
  const [model, setModel]         = useState('All Models')
  const [selected, setSelected]   = useState(null)

  const filtered = catalogAgents.filter((a) => {
    const matchSearch  = !search || a.name.toLowerCase().includes(search.toLowerCase()) || a.description.toLowerCase().includes(search.toLowerCase()) || a.tags.some(t => t.toLowerCase().includes(search.toLowerCase()))
    const matchCat     = category === 'All' || a.category === category
    const matchSeg     = segment === 'All Segments' || segMap[segment] === a.segment
    const matchModel   = model === 'All Models' || a.model === model
    return matchSearch && matchCat && matchSeg && matchModel
  })

  const stats = [
    { label: 'Available Agents', value: catalogAgents.length, icon: Bot },
    { label: 'Running Now',       value: catalogAgents.filter(a => a.status === 'running').length, icon: Zap },
    { label: 'Avg Success Rate',  value: `${(catalogAgents.reduce((s,a) => s + a.metrics.successRate, 0) / catalogAgents.length).toFixed(1)}%`, icon: BarChart2 },
    { label: 'Verified by DLX_AGENTIC_OS', value: catalogAgents.filter(a => a.verified).length, icon: CheckCircle },
  ]

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3 }}
      className="space-y-6"
    >
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="font-display text-xl font-bold text-[#1A2340]">Agent Catalog</h1>
          <p className="text-sm text-[#718096] mt-1">Browse, deploy, and clone AI agents for Deluxe's business segments</p>
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-4 gap-4">
        {stats.map((s, i) => {
          const Icon = s.icon
          return (
            <div key={i} className="card p-4 flex items-center gap-3">
              <div className="w-9 h-9 rounded-xl bg-[#FDF0F2] flex items-center justify-center">
                <Icon size={16} className="text-[#C8102E]" />
              </div>
              <div>
                <p className="text-xl font-bold text-[#1A2340]">{s.value}</p>
                <p className="text-xs text-[#718096]">{s.label}</p>
              </div>
            </div>
          )
        })}
      </div>

      {/* Filters */}
      <div className="card p-4">
        <div className="flex items-center gap-3 flex-wrap">
          <div className="relative flex-1 min-w-[200px]">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-[#718096]" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search agents, tools, tags..."
              className="w-full pl-8 pr-3 py-2 text-sm border border-[#E2E8F0] rounded-xl bg-[#F7F8FA] focus:outline-none focus:border-[#C8102E] focus:bg-white transition-colors"
            />
          </div>
          {[
            { label: 'Category', options: CATEGORIES, value: category, onChange: setCategory },
            { label: 'Segment',  options: SEGMENTS,   value: segment,  onChange: setSegment  },
            { label: 'Model',    options: MODELS,      value: model,    onChange: setModel    },
          ].map((f) => (
            <select
              key={f.label}
              value={f.value}
              onChange={(e) => f.onChange(e.target.value)}
              className="px-3 py-2 text-sm border border-[#E2E8F0] rounded-xl bg-white text-[#4A5568] focus:outline-none focus:border-[#C8102E]"
            >
              {f.options.map((o) => <option key={o}>{o}</option>)}
            </select>
          ))}
          {(search || category !== 'All' || segment !== 'All Segments' || model !== 'All Models') && (
            <button
              onClick={() => { setSearch(''); setCategory('All'); setSegment('All Segments'); setModel('All Models') }}
              className="flex items-center gap-1 text-xs text-[#C8102E] font-medium"
            >
              <X size={12} /> Clear
            </button>
          )}
        </div>

        {/* Category pills */}
        <div className="flex gap-2 mt-3 flex-wrap">
          {CATEGORIES.map((cat) => (
            <button
              key={cat}
              onClick={() => setCategory(cat)}
              className={`px-3 py-1 rounded-full text-xs font-medium transition-all ${
                category === cat
                  ? 'text-white'
                  : 'bg-[#F7F8FA] text-[#718096] hover:bg-[#EDF0F5]'
              }`}
              style={category === cat ? { background: '#C8102E' } : {}}
            >
              {cat}
            </button>
          ))}
        </div>
      </div>

      {/* Results */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <p className="text-sm text-[#718096]">{filtered.length} agent{filtered.length !== 1 ? 's' : ''} found</p>
        </div>
        {filtered.length === 0 ? (
          <div className="card p-12 text-center">
            <Bot size={32} className="text-[#CBD5E0] mx-auto mb-3" />
            <p className="text-[#718096] font-medium">No agents match your filters</p>
            <p className="text-sm text-[#CBD5E0] mt-1">Try adjusting your search or <button onClick={() => navigate('/builder')} className="text-[#C8102E] font-medium">build a custom agent</button></p>
          </div>
        ) : (
          <div className="grid grid-cols-3 gap-4">
            {filtered.map((agent) => (
              <CatalogCard key={agent.id} agent={agent} onClick={setSelected} />
            ))}
          </div>
        )}
      </div>

      <AgentDetailModal agent={selected} onClose={() => setSelected(null)} />
    </motion.div>
  )
}
