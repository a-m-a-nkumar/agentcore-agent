import { useEffect, useRef, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import useStore from '../store/useStore'
import StatusBadge from '../components/shared/StatusBadge'
import SegmentTag from '../components/shared/SegmentTag'
import { Activity, Zap, CheckCircle, Clock, AlertTriangle, Layers, GitBranch } from 'lucide-react'

const SEG_COLORS = {
  merchant: '#0EA5E9',
  print:    '#6B7280',
  b2b:      '#8B5CF6',
  data:     '#10B981',
  platform: '#C8102E',
}

const FEED_MSGS = [
  { agent: 'SMB Onboarding Agent',        action: 'KYB verified',              result: 'Sunrise Bakery — approved',         segment: 'merchant' },
  { agent: 'Fraud Detection Agent',        action: 'Transaction flagged',       result: 'Txn #TXN-88291 — hold placed',      segment: 'merchant' },
  { agent: 'Invoice Reconciliation Agent', action: 'Invoice matched',           result: 'INV-2024-8841 — cleared to GL',     segment: 'b2b'      },
  { agent: 'Churn Prevention Agent',       action: 'At-risk customer detected', result: 'Account #DLX-4421 — email sent',    segment: 'print'    },
  { agent: 'Data Enrichment Agent',        action: 'Profile enriched',          result: '892 SMB records updated',           segment: 'data'     },
  { agent: 'Upsell Intelligence Agent',    action: 'Opportunity scored',        result: '18 print→payments leads generated', segment: 'print'    },
]

const PIPELINE = [
  { label: 'Queued',    value: 14,  color: '#F59E0B', bg: '#FEF3C7' },
  { label: 'Running',   value: 23,  color: '#0EA5E9', bg: '#EFF6FF' },
  { label: 'Completed', value: 810, color: '#10B981', bg: '#ECFDF5' },
  { label: 'Failed',    value: 3,   color: '#EF4444', bg: '#FEF2F2' },
]

const SEG_THROUGHPUT = [
  { seg: 'merchant', label: 'Merchant', tasks: 423, pct: 50 },
  { seg: 'print',    label: 'Print',    tasks: 198, pct: 23 },
  { seg: 'b2b',      label: 'B2B',      tasks: 149, pct: 18 },
  { seg: 'data',     label: 'Data',     tasks: 77,  pct: 9  },
]

const QUEUE_DEPTH = [
  { name: 'SMB Onboarding',  seg: 'merchant', queued: 5 },
  { name: 'Fraud Detection', seg: 'merchant', queued: 8 },
  { name: 'Invoice Recon',   seg: 'b2b',      queued: 2 },
  { name: 'Churn Prev.',     seg: 'print',    queued: 6 },
  { name: 'Data Enrichment', seg: 'data',     queued: 3 },
  { name: 'Upsell Intel',    seg: 'print',    queued: 0 },
]

const ALERTS = [
  { type: 'warn',    text: 'Txn #TXN-88292 — retry attempt 2/3',  ts: '12:31' },
  { type: 'warn',    text: 'API timeout: D&B Hoovers endpoint',    ts: '12:28' },
  { type: 'ok',      text: 'Auto-resolved: INV-2024-8839',         ts: '12:25' },
  { type: 'warn',    text: 'Rate limit: KYB API — 94% of quota',   ts: '12:19' },
]

const SLA_DATA = [
  { label: 'On-Time',  pct: 96.2, color: '#10B981' },
  { label: 'At-Risk',  pct: 2.8,  color: '#F59E0B' },
  { label: 'Breached', pct: 1.0,  color: '#EF4444' },
]

const NW = 110, NH = 44

const TOPO_NODES = [
  { id: 'n0', lines: ['SMB', 'Onboarding'],  seg: 'merchant', cx: 110, cy: 85,  running: true  },
  { id: 'n1', lines: ['Fraud', 'Detection'],  seg: 'merchant', cx: 300, cy: 85,  running: true  },
  { id: 'n2', lines: ['Invoice', 'Recon'],    seg: 'b2b',      cx: 490, cy: 85,  running: true  },
  { id: 'n3', lines: ['Churn', 'Prevention'], seg: 'print',    cx: 110, cy: 205, running: true  },
  { id: 'n4', lines: ['Data', 'Enrichment'],  seg: 'data',     cx: 300, cy: 205, running: true  },
  { id: 'n5', lines: ['Upsell', 'Intel'],     seg: 'print',    cx: 490, cy: 205, running: false },
]

const TOPO_EDGES = [
  { id: 'oe0', d: 'M165,85 L245,85',   dur: 2.0, begin: 0.0  },
  { id: 'oe1', d: 'M355,85 L435,85',   dur: 2.5, begin: 0.45 },
  { id: 'oe2', d: 'M110,107 L110,183', dur: 1.8, begin: 0.9  },
  { id: 'oe3', d: 'M300,107 L300,183', dur: 2.2, begin: 1.35 },
  { id: 'oe4', d: 'M490,107 L490,183', dur: 2.8, begin: 1.8  },
  { id: 'oe5', d: 'M165,205 L245,205', dur: 2.0, begin: 2.25 },
  { id: 'oe6', d: 'M355,205 L435,205', dur: 1.5, begin: 2.7  },
]

function TopologyGraph() {
  return (
    <svg viewBox="0 0 600 268" className="w-full" style={{ fontFamily: 'system-ui, sans-serif' }}>
      <defs>
        <pattern id="orch-grid" width="20" height="20" patternUnits="userSpaceOnUse">
          <path d="M20,0 L0,0 0,20" fill="none" stroke="#F0F2F5" strokeWidth="0.5" />
        </pattern>
      </defs>
      <rect width="600" height="268" fill="url(#orch-grid)" rx="10" />

      {TOPO_EDGES.map((edge) => (
        <g key={edge.id}>
          <path id={edge.id} d={edge.d} fill="none" stroke="#CBD5E0" strokeWidth="1.5" strokeDasharray="5 4" />
          <circle r="3.5" fill="#C8102E" opacity="0.85">
            <animateMotion dur={`${edge.dur}s`} repeatCount="indefinite" begin={`${edge.begin}s`}>
              <mpath href={`#${edge.id}`} />
            </animateMotion>
          </circle>
        </g>
      ))}

      {TOPO_NODES.map((node) => {
        const color = SEG_COLORS[node.seg] || '#718096'
        const x = node.cx - NW / 2
        const y = node.cy - NH / 2
        return (
          <g key={node.id}>
            <rect x={x + 2} y={y + 3} width={NW} height={NH} rx="8" fill="rgba(0,0,0,0.05)" />
            <rect x={x} y={y} width={NW} height={NH} rx="8" fill="white" stroke={color} strokeWidth="1.5" />
            <rect x={x} y={y + 8} width="3" height={NH - 16} rx="1.5" fill={color} />
            <text x={node.cx + 3} y={node.cy - 5} textAnchor="middle" fontSize="9.5" fill="#1A2340" fontWeight="700">
              {node.lines[0]}
            </text>
            <text x={node.cx + 3} y={node.cy + 8} textAnchor="middle" fontSize="8.5" fill="#718096">
              {node.lines[1]}
            </text>
            <circle cx={x + NW - 11} cy={y + 11} r="4" fill={node.running ? '#10B981' : '#CBD5E0'}>
              {node.running && (
                <animate attributeName="opacity" values="1;0.35;1" dur="1.8s" repeatCount="indefinite" />
              )}
            </circle>
          </g>
        )
      })}
    </svg>
  )
}

function FeedItem({ item }) {
  const color = SEG_COLORS[item.segment] || '#718096'
  return (
    <motion.div
      initial={{ opacity: 0, y: -8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.22 }}
      className="flex items-start gap-2 px-2.5 py-2 rounded-lg border border-[#E2E8F0] bg-white"
      style={{ borderLeftColor: color, borderLeftWidth: '3px' }}
    >
      <div className="flex-1 min-w-0">
        <p className="text-xs font-semibold text-[#1A2340] truncate">{item.agent}</p>
        <p className="text-xs mt-0.5">
          <span className="font-medium" style={{ color }}>{item.action}</span>
          <span className="text-[#718096]"> — {item.result}</span>
        </p>
      </div>
      <span className="text-[10px] text-[#A0AEC0] flex-shrink-0 font-mono pt-0.5">{item.ts}</span>
    </motion.div>
  )
}

export default function AgentOrchestration() {
  const agents = useStore((s) => s.agents)
  const [view, setView] = useState('isolated')

  const [feed, setFeed] = useState(() =>
    FEED_MSGS.slice(0, 3).map((m, i) => ({
      ...m,
      ts: new Date(Date.now() - i * 45_000).toLocaleTimeString(),
      id: i,
    }))
  )
  const idRef  = useRef(10)
  const msgIdx = useRef(3)

  useEffect(() => {
    const iv = setInterval(() => {
      const msg = FEED_MSGS[msgIdx.current % FEED_MSGS.length]
      msgIdx.current++
      setFeed((prev) => [
        { ...msg, ts: new Date().toLocaleTimeString(), id: idRef.current++ },
        ...prev.slice(0, 10),
      ])
    }, 60000)
    return () => clearInterval(iv)
  }, [])

  const kpis = [
    { label: 'Tasks / Hour',  value: '847',   sub: '+12% vs yesterday', icon: Activity,    color: '#C8102E' },
    { label: 'Active Agents', value: '5 / 6', sub: '1 standby',         icon: Zap,         color: '#10B981' },
    { label: 'Success Rate',  value: '98.4%', sub: '+0.3% this week',   icon: CheckCircle, color: '#10B981' },
    { label: 'Avg Latency',   value: '1.2s',  sub: 'P50 — −80ms',       icon: Clock,       color: '#0EA5E9' },
  ]

  const pipelineTotal = PIPELINE.reduce((s, p) => s + p.value, 0)

  const VIEW_TABS = [
    {
      id: 'isolated',
      icon: Layers,
      label: 'Isolated Agents',
      desc: 'Independent agents — no cross-agent task handoffs',
    },
    {
      id: 'complex',
      icon: GitBranch,
      label: 'Complex Agent Flows',
      desc: 'Multi-agent pipelines with live topology & routing',
    },
  ]

  /* ── shared panel blocks ── */
  const PipelinePanel = () => (
    <div className="card p-4">
      <h2 className="text-sm font-semibold text-[#1A2340] mb-3">Task Pipeline</h2>
      <div className="grid grid-cols-4 gap-2 mb-3">
        {PIPELINE.map((p) => (
          <div key={p.label} className="rounded-xl p-2.5 text-center" style={{ background: p.bg }}>
            <p className="text-xl font-bold font-display leading-tight" style={{ color: p.color }}>{p.value}</p>
            <p className="text-[10px] mt-0.5 font-medium" style={{ color: p.color }}>{p.label}</p>
          </div>
        ))}
      </div>
      <div className="flex h-2 rounded-full overflow-hidden gap-px">
        {PIPELINE.map((p) => (
          <div key={p.label} style={{ width: `${(p.value / pipelineTotal) * 100}%`, background: p.color }} />
        ))}
      </div>
      <div className="flex justify-between mt-1">
        {PIPELINE.map((p) => (
          <span key={p.label} className="text-[10px]" style={{ color: p.color }}>
            {Math.round((p.value / pipelineTotal) * 100)}%
          </span>
        ))}
      </div>
    </div>
  )

  const ThroughputPanel = () => (
    <div className="card p-4">
      <h2 className="text-sm font-semibold text-[#1A2340] mb-3">Throughput by Segment</h2>
      <div className="flex flex-col gap-2.5">
        {SEG_THROUGHPUT.map((s) => (
          <div key={s.seg} className="flex items-center gap-2">
            <span className="text-xs text-[#718096] w-16 flex-shrink-0">{s.label}</span>
            <div className="flex-1 h-2 rounded-full bg-[#E2E8F0] overflow-hidden">
              <motion.div
                className="h-full rounded-full"
                initial={{ width: 0 }}
                animate={{ width: `${s.pct}%` }}
                transition={{ duration: 0.8, delay: 0.1 }}
                style={{ background: SEG_COLORS[s.seg] }}
              />
            </div>
            <span className="text-xs font-mono text-[#1A2340] w-8 text-right flex-shrink-0">{s.tasks}</span>
          </div>
        ))}
      </div>
    </div>
  )

  const LiveFeedPanel = () => (
    <div className="card p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className="agent-pulse w-2 h-2 rounded-full bg-emerald-400 inline-block" />
          <h2 className="text-sm font-semibold text-[#1A2340]">Live Task Stream</h2>
        </div>
        <span className="text-[10px] text-[#A0AEC0]">↻ 60s</span>
      </div>
      <div className="flex flex-col gap-1.5">
        <AnimatePresence initial={false}>
          {feed.slice(0, 4).map((item) => <FeedItem key={item.id} item={item} />)}
        </AnimatePresence>
      </div>
    </div>
  )

  const InsightsRow = () => (
    <div className="grid grid-cols-3 gap-4">
      {/* Agent Queue Depth */}
      <div className="card p-4">
        <h2 className="text-sm font-semibold text-[#1A2340] mb-3">Agent Queue Depth</h2>
        <div className="flex flex-col gap-2">
          {QUEUE_DEPTH.map((q) => {
            const color = SEG_COLORS[q.seg] || '#CBD5E0'
            return (
              <div key={q.name} className="flex items-center gap-2">
                <span className="text-xs text-[#718096] w-28 truncate flex-shrink-0">{q.name}</span>
                <div className="flex-1 h-1.5 rounded-full bg-[#E2E8F0] overflow-hidden">
                  <div className="h-full rounded-full"
                    style={{ width: `${(q.queued / 10) * 100}%`, background: q.queued >= 7 ? '#F59E0B' : color }} />
                </div>
                <span className="text-xs font-mono text-[#1A2340] w-4 text-right flex-shrink-0">{q.queued}</span>
              </div>
            )
          })}
        </div>
        <p className="text-[10px] text-[#A0AEC0] mt-3">tasks pending per agent · amber = high load</p>
      </div>

      {/* System Alerts */}
      <div className="card p-4">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold text-[#1A2340]">System Alerts</h2>
          <span className="px-1.5 py-0.5 rounded-full text-xs font-semibold bg-amber-100 text-amber-700">
            {ALERTS.filter(a => a.type === 'warn').length} active
          </span>
        </div>
        <div className="flex flex-col gap-2">
          {ALERTS.map((a, i) => (
            <div key={i} className="flex items-start gap-2 p-2 rounded-lg"
              style={{ background: a.type === 'warn' ? '#FFFBEB' : '#F0FDF4' }}>
              {a.type === 'warn'
                ? <AlertTriangle size={12} className="text-amber-500 flex-shrink-0 mt-0.5" />
                : <CheckCircle  size={12} className="text-emerald-500 flex-shrink-0 mt-0.5" />}
              <p className="text-xs text-[#4A5568] flex-1 leading-relaxed">{a.text}</p>
              <span className="text-[10px] text-[#A0AEC0] flex-shrink-0 font-mono">{a.ts}</span>
            </div>
          ))}
        </div>
      </div>

      {/* SLA Compliance */}
      <div className="card p-4">
        <h2 className="text-sm font-semibold text-[#1A2340] mb-1">SLA Compliance</h2>
        <p className="text-xs text-[#718096] mb-4">Task completion vs. target latency</p>
        <div className="flex items-center gap-4 mb-4">
          <div className="w-16 h-16 rounded-full flex items-center justify-center flex-shrink-0"
            style={{ background: 'conic-gradient(#10B981 0% 96.2%, #F59E0B 96.2% 99%, #EF4444 99% 100%)', padding: 3 }}>
            <div className="w-full h-full rounded-full bg-white flex items-center justify-center">
              <span className="text-sm font-bold text-[#1A2340]">96%</span>
            </div>
          </div>
          <div className="flex flex-col gap-1">
            {SLA_DATA.map((s) => (
              <div key={s.label} className="flex items-center gap-1.5">
                <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: s.color }} />
                <span className="text-xs text-[#718096]">{s.label}</span>
                <span className="text-xs font-semibold text-[#1A2340] ml-auto">{s.pct}%</span>
              </div>
            ))}
          </div>
        </div>
        <div className="flex flex-col gap-1.5">
          {SLA_DATA.map((s) => (
            <div key={s.label} className="flex items-center gap-2">
              <div className="flex-1 h-1.5 rounded-full bg-[#E2E8F0] overflow-hidden">
                <div className="h-full rounded-full" style={{ width: `${s.pct}%`, background: s.color }} />
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3 }}
      className="space-y-5"
    >
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="font-display text-xl font-bold text-[#1A2340]">Multi-Agent Orchestration</h1>
          <p className="text-sm text-[#718096] mt-1">
            DLX_AGENTIC_OS Kernel — coordinating {agents.length || 6} agents across Deluxe segments
          </p>
        </div>
        <div className="flex items-center gap-2 bg-emerald-50 border border-emerald-200 rounded-xl px-3 py-2">
          <span className="agent-pulse w-2 h-2 rounded-full bg-emerald-400 inline-block" />
          <span className="text-xs font-semibold text-emerald-700">All Systems Operational</span>
        </div>
      </div>

      {/* View selector cards */}
      <div className="grid grid-cols-2 gap-4">
        {VIEW_TABS.map((tab) => {
          const Icon = tab.icon
          const active = view === tab.id
          return (
            <button
              key={tab.id}
              onClick={() => setView(tab.id)}
              className="card p-4 flex items-center gap-4 text-left transition-all duration-200"
              style={active ? {
                borderColor: '#C8102E',
                borderWidth: '2px',
                background: '#FFF5F6',
                boxShadow: '0 0 0 3px rgba(200,16,46,0.08)',
              } : { borderWidth: '2px', borderColor: 'transparent' }}
            >
              <div className="w-10 h-10 rounded-xl flex items-center justify-center flex-shrink-0"
                style={{ background: active ? '#C8102E' : '#F7F8FA' }}>
                <Icon size={18} style={{ color: active ? '#fff' : '#718096' }} />
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-sm font-semibold" style={{ color: active ? '#C8102E' : '#1A2340' }}>{tab.label}</p>
                <p className="text-xs text-[#718096] mt-0.5 leading-snug">{tab.desc}</p>
              </div>
              {active && (
                <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: '#C8102E' }} />
              )}
            </button>
          )
        })}
      </div>

      {/* KPI row */}
      <div className="grid grid-cols-4 gap-4">
        {kpis.map((k) => {
          const Icon = k.icon
          return (
            <div key={k.label} className="card px-4 py-3 flex items-center gap-3">
              <div className="w-9 h-9 rounded-lg flex items-center justify-center flex-shrink-0" style={{ background: `${k.color}15` }}>
                <Icon size={16} style={{ color: k.color }} />
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

      <AnimatePresence mode="wait">
        {view === 'isolated' ? (
          <motion.div key="isolated" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }} transition={{ duration: 0.22 }} className="space-y-5">
            {/* Isolated view: 3-col panel row */}
            <div className="grid grid-cols-3 gap-4">
              <PipelinePanel />
              <ThroughputPanel />
              <LiveFeedPanel />
            </div>
            <InsightsRow />
          </motion.div>
        ) : (
          <motion.div key="complex" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }} transition={{ duration: 0.22 }} className="space-y-5">
            {/* Complex view: topology + right panels */}
            <div className="grid grid-cols-5 gap-4 items-stretch">
              <div className="card p-5 col-span-3 flex flex-col">
                <div className="flex items-center justify-between mb-4">
                  <div>
                    <h2 className="font-semibold text-[#1A2340] text-sm">Agent Network Topology</h2>
                    <p className="text-xs text-[#718096]">Live routing & task handoff visualization</p>
                  </div>
                  <div className="flex items-center gap-3 text-xs text-[#718096]">
                    <span className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-emerald-400 inline-block" />Running</span>
                    <span className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-[#CBD5E0] inline-block" />Standby</span>
                  </div>
                </div>
                <div className="flex-1 flex items-center">
                  <TopologyGraph />
                </div>
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mt-3 pt-3 border-t border-[#F0F2F5]">
                  {[['merchant','Merchant'],['print','Print'],['b2b','B2B'],['data','Data']].map(([k, l]) => (
                    <span key={k} className="flex items-center gap-1 text-xs text-[#718096]">
                      <span className="w-2.5 h-2.5 rounded-sm inline-block" style={{ background: SEG_COLORS[k] }} />{l}
                    </span>
                  ))}
                  <span className="ml-auto flex items-center gap-1.5 text-xs text-[#718096]">
                    <span className="w-5 border-t-2 border-dashed border-[#CBD5E0] inline-block" />task flow
                    <span className="w-2 h-2 rounded-full inline-block bg-[#C8102E] opacity-70" />live packet
                  </span>
                </div>
              </div>
              <div className="col-span-2 flex flex-col gap-4">
                <PipelinePanel />
                <ThroughputPanel />
                <LiveFeedPanel />
              </div>
            </div>
            <InsightsRow />
          </motion.div>
        )}
      </AnimatePresence>

      {/* Agent Roster */}
      <div className="card overflow-hidden">
        <div className="px-5 py-4 border-b border-[#E2E8F0] flex items-center justify-between">
          <div>
            <h2 className="font-semibold text-[#1A2340] text-sm">Agent Roster</h2>
            <p className="text-xs text-[#718096]">Performance & tooling overview</p>
          </div>
          <span className="text-xs text-[#718096]">{agents.length} agents registered</span>
        </div>
        <div>
          {agents.map((agent, i) => {
            const color = SEG_COLORS[agent.segment] || '#CBD5E0'
            const pct   = Math.min(Math.round((agent.tasksToday / 2000) * 100), 100)
            return (
              <div
                key={agent.id}
                className={`flex items-center gap-4 px-5 py-3.5 ${i < agents.length - 1 ? 'border-b border-[#F7F8FA]' : ''}`}
                style={{ borderLeft: `3px solid ${color}` }}
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <p className="text-sm font-semibold text-[#1A2340]">{agent.name}</p>
                    <SegmentTag segment={agent.segment} />
                    <StatusBadge status={agent.status} />
                  </div>
                  <div className="flex items-center gap-2">
                    <div className="w-28 h-1.5 rounded-full bg-[#E2E8F0] overflow-hidden">
                      <div className="h-full rounded-full" style={{ width: `${pct}%`, background: color }} />
                    </div>
                    <span className="text-xs text-[#718096]">{agent.tasksToday.toLocaleString()} tasks today</span>
                    <span className="text-xs font-semibold text-emerald-600">{agent.successRate}%</span>
                  </div>
                </div>
                <div className="flex gap-1 flex-wrap justify-end max-w-[220px]">
                  {agent.tools.slice(0, 4).map((tool) => (
                    <span key={tool} className="px-1.5 py-0.5 rounded text-xs bg-[#F7F8FA] text-[#4A5568] font-mono border border-[#E2E8F0]">
                      {tool}
                    </span>
                  ))}
                  {agent.tools.length > 4 && (
                    <span className="px-1.5 py-0.5 rounded text-xs bg-[#F7F8FA] text-[#718096] border border-[#E2E8F0]">
                      +{agent.tools.length - 4}
                    </span>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      </div>
    </motion.div>
  )
}
