import { useState, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  CheckCircle, Circle, Loader, Play, Clock, Building, Mail,
  Shield, CreditCard, AlertTriangle, Activity
} from 'lucide-react'
import useStore from '../store/useStore'
import StatusBadge from '../components/shared/StatusBadge'

const SEG_COLOR = '#0EA5E9'

const stepDescriptions = {
  1: 'Application received and parsed. All fields validated automatically.',
  2: 'KYB: Business registration verified via state database. Owner identity confirmed.',
  3: 'Risk score computed: transaction history, industry risk, geographic factors.',
  4: 'Payment terminal remotely configured. POS software provisioned.',
  5: 'Merchant account activated. Banking details verified via micro-deposit.',
  6: 'Welcome email sent. Training materials delivered. Account manager assigned.',
}

const FALLBACK_AGENTS = [
  {
    id: 'agent-001',
    name: 'SMB Onboarding Agent',
    description: 'End-to-end merchant onboarding — from application to live account in 2.3 hours.',
    status: 'running', successRate: 98, tasksToday: 847,
    Icon: CreditCard,
  },
  {
    id: 'agent-002',
    name: 'Fraud Detection Agent',
    description: 'Real-time transaction monitoring and anomaly detection across all merchant accounts.',
    status: 'running', successRate: 97, tasksToday: 14892,
    Icon: Shield,
  },
]

const FLAGGED_TXNS = [
  { id: 'TXN-88291', merchant: 'Quick Mart #441',   risk: 94, signal: 'Unusual amount + new IP',    action: 'Hold placed',  status: 'review'    },
  { id: 'TXN-88249', merchant: 'Gas Station Pro',   risk: 81, signal: 'Geo velocity anomaly',       action: 'Hold placed',  status: 'escalated' },
  { id: 'TXN-88188', merchant: 'Online Store XYZ',  risk: 73, signal: 'Card test pattern',          action: 'Monitoring',   status: 'watching'  },
  { id: 'TXN-88102', merchant: 'Restaurant Group',  risk: 67, signal: 'High frequency pattern',     action: 'Monitoring',   status: 'watching'  },
  { id: 'TXN-87991', merchant: 'Electronics Plus',  risk: 55, signal: 'New merchant category',      action: 'Monitoring',   status: 'watching'  },
]

const DETECTION_RULES = [
  { name: 'Velocity Check',    desc: '>5 txns in 10 min → auto-hold' },
  { name: 'Geo Anomaly',       desc: 'IP ≠ card region → flag for review' },
  { name: 'Card Test Pattern', desc: 'Micro + large txn sequence → escalate' },
  { name: 'New Merchant Risk', desc: 'First 30 days — elevated monitoring' },
]

function StepIcon({ status }) {
  if (status === 'completed') return <CheckCircle size={18} className="text-emerald-500" />
  if (status === 'running')   return <Loader size={18} className="text-[#C8102E] animate-spin" />
  return <Circle size={18} className="text-[#CBD5E0]" />
}

function RiskBadge({ risk }) {
  const color = risk >= 80 ? '#EF4444' : risk >= 60 ? '#F59E0B' : '#10B981'
  const bg    = risk >= 80 ? '#FEF2F2' : risk >= 60 ? '#FFFBEB' : '#ECFDF5'
  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-bold" style={{ color, background: bg }}>
      {risk}
    </span>
  )
}

function AgentCard({ agent, selected, onSelect }) {
  const Icon = agent.Icon || CreditCard
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

function SMBOnboardingDetail() {
  const { merchants, selectedMerchant, setSelectedMerchant, advanceMerchantStep, addToast } = useStore()
  const [running, setRunning]       = useState(false)
  const [celebrated, setCelebrated] = useState(false)
  const pendingCount = merchants.filter(m => m.status !== 'completed').length

  const merchant = selectedMerchant || merchants[0]

  const handleRun = async () => {
    if (!merchant || running) return
    if (merchant.status === 'completed') {
      addToast({ type: 'info', title: 'Already complete', message: `${merchant.businessName} is fully onboarded.` })
      return
    }
    setRunning(true)
    const willComplete = merchant.currentStep + 1 >= 6
    advanceMerchantStep(merchant.id)
    if (willComplete) {
      setCelebrated(true)
      setTimeout(() => setCelebrated(false), 2000)
      addToast({ type: 'success', title: 'Merchant onboarded!', message: `${merchant.businessName} is now live.` })
    } else {
      addToast({ type: 'success', title: 'Step completed', message: 'Agent advancing to next onboarding step.' })
    }
    setRunning(false)
  }

  const currentMerchant = merchants.find((m) => m.id === merchant?.id) || merchant

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-3 text-xs text-[#718096]">
        <span className="px-3 py-1.5 rounded-lg bg-gray-100 line-through">Old: 5–7 days</span>
        <span className="px-3 py-1.5 rounded-lg text-emerald-700 bg-emerald-50 font-semibold">DLX_AGENTIC_OS: 2.3 hours</span>
      </div>

      <div className="grid grid-cols-5 gap-4">
        <div className="col-span-2 card p-4">
          <h2 className="text-sm font-semibold text-[#1A2340] mb-3">Merchant Queue</h2>
          <div className="flex flex-col gap-2">
            {merchants.map((m) => (
              <button
                key={m.id}
                onClick={() => setSelectedMerchant(m)}
                className={`text-left p-3 rounded-xl border transition-all ${
                  currentMerchant?.id === m.id
                    ? 'border-[#C8102E] bg-[#FDF0F2]'
                    : 'border-[#E2E8F0] hover:border-[#CBD5E0] hover:bg-[#F7F8FA]'
                }`}
              >
                <div className="flex items-center justify-between mb-1">
                  <p className="text-sm font-semibold text-[#1A2340]">{m.businessName}</p>
                  <StatusBadge status={m.status} />
                </div>
                <p className="text-xs text-[#718096]">{m.owner} · {m.type}</p>
                <div className="flex items-center gap-1 mt-2">
                  {m.steps.map((step) => (
                    <div key={step.id} className="h-1 flex-1 rounded-full"
                      style={{ background: step.status === 'completed' ? '#10B981' : step.status === 'running' ? '#C8102E' : '#E2E8F0' }} />
                  ))}
                </div>
                <p className="text-xs text-[#718096] mt-1">Step {m.currentStep} of 6</p>
              </button>
            ))}
          </div>
        </div>

        <div className={`col-span-3 card p-6 ${celebrated ? 'celebrate' : ''}`}>
          {currentMerchant ? (
            <>
              <div className="flex items-start justify-between mb-5">
                <div>
                  <div className="flex items-center gap-2 mb-1">
                    <Building size={16} className="text-[#C8102E]" />
                    <h2 className="font-display text-lg font-bold text-[#1A2340]">{currentMerchant.businessName}</h2>
                  </div>
                  <div className="flex items-center gap-3 text-xs text-[#718096]">
                    <span className="flex items-center gap-1"><Mail size={11} />{currentMerchant.email}</span>
                    <span>{currentMerchant.type}</span>
                  </div>
                </div>
                <StatusBadge status={currentMerchant.status} />
              </div>

              <div className="flex flex-col gap-0 mb-6">
                {currentMerchant.steps.map((step, i) => (
                  <div key={step.id} className="flex gap-4">
                    <div className="flex flex-col items-center">
                      <StepIcon status={step.status} />
                      {i < currentMerchant.steps.length - 1 && (
                        <div className="w-px flex-1 my-1" style={{ background: step.status === 'completed' ? '#10B981' : '#E2E8F0', minHeight: '24px' }} />
                      )}
                    </div>
                    <div className={`pb-4 flex-1 ${i === currentMerchant.steps.length - 1 ? 'pb-0' : ''}`}>
                      <div className="flex items-center justify-between">
                        <p className={`text-sm font-medium ${step.status === 'completed' ? 'text-[#1A2340]' : step.status === 'running' ? 'text-[#C8102E]' : 'text-[#CBD5E0]'}`}>
                          {step.name}
                        </p>
                        {step.completedAt && <span className="text-xs text-[#718096] flex items-center gap-1"><Clock size={11} /> {step.completedAt}</span>}
                        {step.status === 'running' && <span className="text-xs text-[#C8102E] font-medium animate-pulse">Agent working...</span>}
                      </div>
                      {(step.status === 'running' || step.status === 'completed') && (
                        <p className="text-xs text-[#718096] mt-0.5">{stepDescriptions[step.id]}</p>
                      )}
                    </div>
                  </div>
                ))}
              </div>

              <button
                onClick={handleRun}
                disabled={running || currentMerchant.status === 'completed'}
                className="flex items-center gap-2 px-5 py-2.5 rounded-xl text-white text-sm font-semibold transition-all disabled:opacity-60"
                style={{ background: '#C8102E', boxShadow: 'var(--shadow-red)' }}
              >
                {running ? <Loader size={15} className="animate-spin" /> : <Play size={15} />}
                {running ? 'Agent running...' : currentMerchant.status === 'completed' ? 'Onboarding Complete' : 'Run Agent'}
              </button>

              {currentMerchant.status === 'completed' && (
                <motion.div initial={{ opacity: 0, scale: 0.95 }} animate={{ opacity: 1, scale: 1 }}
                  className="mt-4 p-3 rounded-xl bg-emerald-50 border border-emerald-200 flex items-center gap-2">
                  <CheckCircle size={16} className="text-emerald-600" />
                  <p className="text-sm text-emerald-700 font-medium">{currentMerchant.businessName} is live and processing payments!</p>
                </motion.div>
              )}
            </>
          ) : (
            <div className="flex flex-col items-center justify-center h-48 text-center">
              <p className="text-[#718096]">Agent is standing by.</p>
              <p className="text-sm text-[#CBD5E0]">Select a merchant from the queue to begin.</p>
            </div>
          )}
        </div>
      </div>

      <div className="grid grid-cols-3 gap-4">
        {[
          { label: 'Avg Onboarding Time', value: '2.3 hours',    sub: 'vs 5-7 days manual' },
          { label: 'Completed Today',      value: '47 merchants', sub: 'Processed by agent' },
          { label: 'Queue Size',           value: `${pendingCount} pending`, sub: 'Agent processing continuously' },
        ].map((tile, i) => (
          <div key={i} className="card p-4">
            <p className="text-xs text-[#718096] uppercase tracking-wider mb-1">{tile.label}</p>
            <p className="font-display text-xl font-bold text-[#1A2340]">{tile.value}</p>
            <p className="text-xs text-[#718096] mt-1">{tile.sub}</p>
          </div>
        ))}
      </div>
    </div>
  )
}

function FraudDetectionDetail() {
  const statusMap = {
    review:    'bg-red-100 text-red-700 border border-red-200',
    escalated: 'bg-orange-100 text-orange-700 border border-orange-200',
    watching:  'bg-gray-100 text-gray-600 border border-gray-200',
  }

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-4 gap-4">
        {[
          { label: 'Txns Scanned Today', value: '14,892', sub: 'across all merchants',    highlight: false },
          { label: 'Flagged',            value: '23',     sub: 'anomalies detected',       highlight: false },
          { label: 'Holds Placed',       value: '7',      sub: 'pending review',           highlight: true  },
          { label: 'False Positive Rate',value: '2.1%',   sub: 'industry avg 4.8%',        highlight: false },
        ].map((s) => (
          <div key={s.label} className={`card p-4 ${s.highlight ? 'border-red-200 bg-red-50' : ''}`}>
            <p className="text-xs text-[#718096] uppercase tracking-wider mb-1">{s.label}</p>
            <p className={`font-display text-2xl font-bold ${s.highlight ? 'text-red-600' : 'text-[#1A2340]'}`}>{s.value}</p>
            <p className="text-xs text-[#718096]">{s.sub}</p>
          </div>
        ))}
      </div>

      <div className="card overflow-hidden">
        <div className="px-5 py-4 border-b border-[#E2E8F0] flex items-center justify-between">
          <div className="flex items-center gap-2">
            <AlertTriangle size={15} className="text-amber-500" />
            <h2 className="text-sm font-semibold text-[#1A2340]">Flagged Transactions</h2>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="agent-pulse w-2 h-2 rounded-full bg-red-400 inline-block" />
            <span className="text-xs text-[#718096]">Live monitoring</span>
          </div>
        </div>
        <table>
          <thead>
            <tr>
              <th>Transaction</th>
              <th>Merchant</th>
              <th>Risk Score</th>
              <th>Signal</th>
              <th>Agent Action</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {FLAGGED_TXNS.map((txn) => (
              <tr key={txn.id}>
                <td className="font-mono text-xs text-[#718096]">{txn.id}</td>
                <td className="text-sm font-medium text-[#1A2340]">{txn.merchant}</td>
                <td><RiskBadge risk={txn.risk} /></td>
                <td><span className="px-2 py-0.5 rounded-md bg-amber-50 border border-amber-200 text-amber-700 text-xs">{txn.signal}</span></td>
                <td className="text-sm text-[#4A5568]">{txn.action}</td>
                <td><span className={`inline-flex px-2.5 py-0.5 rounded-full text-xs font-medium ${statusMap[txn.status]}`}>{txn.status.charAt(0).toUpperCase() + txn.status.slice(1)}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card p-5">
        <h2 className="text-sm font-semibold text-[#1A2340] mb-3">Active Detection Rules</h2>
        <div className="grid grid-cols-2 gap-3">
          {DETECTION_RULES.map((rule) => (
            <div key={rule.name} className="flex items-start gap-3 p-3 rounded-xl bg-[#F7F8FA] border border-[#E2E8F0]">
              <div className="w-2 h-2 rounded-full bg-emerald-400 mt-1.5 flex-shrink-0" />
              <div>
                <p className="text-xs font-semibold text-[#1A2340]">{rule.name}</p>
                <p className="text-xs text-[#718096] mt-0.5">{rule.desc}</p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

export default function MerchantServices() {
  const [selectedId, setSelectedId]   = useState('agent-001')
  const [agentList, setAgentList]     = useState(FALLBACK_AGENTS)

  useEffect(() => {
    fetch('/api/nova/agents')
      .then(r => r.ok ? r.json() : [])
      .then(data => {
        const filtered = Array.isArray(data)
          ? data.filter(a => a.segmentKey === 'merchant' || a.segment === 'Merchant Services')
          : []
        if (filtered.length > 0) {
          setAgentList(filtered.map(a => ({ ...a, Icon: a.id === 'agent-002' ? Shield : CreditCard })))
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
          <p className="text-xs font-semibold uppercase tracking-widest mb-1" style={{ color: SEG_COLOR }}>Merchant Services</p>
          <h1 className="font-display text-xl font-bold text-[#1A2340]">Merchant Segment Agents</h1>
          <p className="text-sm text-[#718096] mt-1">{agentList.length} agents operating in this segment</p>
        </div>
        <div className="flex items-center gap-2 bg-sky-50 border border-sky-200 rounded-xl px-3 py-2">
          <span className="agent-pulse w-2 h-2 rounded-full bg-sky-400 inline-block" />
          <span className="text-xs font-semibold text-sky-700">Segment Active</span>
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
          {selectedId === 'agent-001' && <SMBOnboardingDetail />}
          {selectedId === 'agent-002' && <FraudDetectionDetail />}
          {!['agent-001', 'agent-002'].includes(selectedId) && <SMBOnboardingDetail />}
        </motion.div>
      </AnimatePresence>
    </motion.div>
  )
}
