import { useState, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { useNavigate } from 'react-router-dom'
import {
  Plus, GitMerge, Activity, Clock, Zap,
  Send, Upload, X, CheckCircle2, Wrench,
  ArrowRight, Bot, Workflow, ChevronDown, CheckCheck,
  Cpu, Tag, Layers, ChevronRight, Terminal
} from 'lucide-react'
import useStore from '../store/useStore'

/* ─── Hardcoded live workflows ───────────────────────────────────────────────── */
const LIVE_WORKFLOWS = [
  {
    id:          'wf-002',
    name:        'Invoice-to-Cash Reconciliation',
    description: 'Invoice ingestion → PO matching → GL posting → exception handling',
    segmentKey:  'b2b',
    segment:     'B2B Payments',
    agentCount:  4,
    sla:         97.8,
    tasksToday:  289,
    lastRun:     '8 min ago',
    avgRunTime:  '2.8 min',
    status:      'live',
    trigger:     'Invoice Received (Email / EDI)',
    output:      'Payment Cleared — Ledger Updated',
    agents: [
      { id: 'b1', name: 'Invoice Ingestion Agent', role: 'Parses & classifies incoming invoices from email and EDI feeds',  tools: ['ocr-parser', 'email-inbox', 'edi-reader'],  status: 'full' },
      { id: 'b2', name: 'PO Matching Engine',      role: 'Matches invoices to purchase orders using fuzzy matching',       tools: ['erp-read', 'match-algo', 'gl-lookup'],      status: 'full' },
      { id: 'b3', name: 'GL Posting Agent',        role: 'Posts matched invoices to general ledger with audit trail',      tools: ['gl-write', 'audit-log', 'erp-write'],       status: 'full' },
      { id: 'b4', name: 'Exception Handler',       role: 'Flags unmatched invoices & routes to finance team for review',   tools: ['notify-send', 'ticket-create', 'jira-api'], status: 'partial' },
    ],
  },
]

const WF_SEG_COLORS = {
  merchant: '#0EA5E9',
  b2b:      '#8B5CF6',
  print:    '#6B7280',
  data:     '#10B981',
  platform: '#C8102E',
}

/* ─── Live workflow detail modal (same canvas style as AgentPool) ────────────── */
function LiveWorkflowDetailModal({ wf, onClose }) {
  const [selectedAgent, setSelectedAgent] = useState(null)
  const color  = WF_SEG_COLORS[wf.segmentKey] || '#C8102E'
  const agents = wf.agents || []

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ backdropFilter: 'blur(10px)', background: 'rgba(10,18,40,0.72)' }}
      onClick={e => e.target === e.currentTarget && onClose()}
    >
      <motion.div
        initial={{ opacity: 0, scale: 0.93, y: 24 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.93, y: 24 }}
        transition={{ duration: 0.25, ease: 'easeOut' }}
        className="bg-white rounded-3xl flex flex-col overflow-hidden"
        style={{ width: '92vw', maxWidth: 920, maxHeight: '90vh', boxShadow: '0 40px 100px rgba(0,0,0,0.45)' }}
      >
        {/* ── Header ── */}
        <div className="px-7 py-5 flex items-center justify-between flex-shrink-0"
          style={{ background: 'linear-gradient(135deg,#1A2340 0%,#2D3A5C 100%)' }}>
          <div className="flex items-center gap-3.5">
            <div className="w-11 h-11 rounded-2xl flex items-center justify-center flex-shrink-0"
              style={{ background: `${color}25`, border: `1.5px solid ${color}40` }}>
              <GitMerge size={21} style={{ color }} />
            </div>
            <div>
              <p className="text-white font-bold text-base leading-tight">{wf.name}</p>
              <div className="flex items-center gap-3 mt-1">
                <span className="flex items-center gap-1.5 text-xs text-emerald-400 font-semibold">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse inline-block" /> Live
                </span>
                <span className="text-white/40 text-xs">{agents.length} agents</span>
                <span className="text-white/40 text-xs">·</span>
                <span className="text-white/40 text-xs">{wf.segment}</span>
              </div>
            </div>
          </div>
          <button onClick={onClose}
            className="w-9 h-9 rounded-xl bg-white/10 hover:bg-white/20 flex items-center justify-center transition-all">
            <X size={15} className="text-white" />
          </button>
        </div>

        {/* ── Stats bar ── */}
        <div className="grid grid-cols-4 border-b border-[#E2E8F0] flex-shrink-0">
          {[
            { label: 'Tasks Today',  value: wf.tasksToday?.toLocaleString(), color },
            { label: 'SLA',          value: `${wf.sla}%`,    color: '#10B981' },
            { label: 'Last Run',     value: wf.lastRun,      color: '#718096' },
            { label: 'Avg Duration', value: wf.avgRunTime,   color: '#718096' },
          ].map((s, i) => (
            <div key={s.label} className={`px-6 py-3.5 ${i > 0 ? 'border-l border-[#E2E8F0]' : ''}`}>
              <p className="text-xs text-[#718096] mb-0.5">{s.label}</p>
              <p className="text-xl font-bold" style={{ color: s.color }}>{s.value}</p>
            </div>
          ))}
        </div>

        {/* ── Pipeline canvas ── */}
        <div className="flex-1 overflow-auto px-7 py-7"
          style={{ background: 'linear-gradient(135deg,#F7F9FF 0%,#EEF2FF 100%)' }}>
          <p className="text-xs font-bold uppercase tracking-widest text-[#9BA8BA] mb-6 flex items-center gap-1.5">
            <GitMerge size={11} /> Full Pipeline — click an agent node to inspect
          </p>

          <div className="flex items-start overflow-x-auto pb-6">
            <PCanvasTrigger label={wf.trigger} />
            <PCanvasConnector />
            {agents.map((agent, i) => (
              <div key={agent.id} className="flex items-start">
                <motion.div
                  onClick={() => setSelectedAgent(a => a?.id === agent.id ? null : agent)}
                  whileHover={{ scale: 1.06 }} whileTap={{ scale: 0.97 }}
                  className="flex flex-col items-center flex-shrink-0 cursor-pointer"
                  style={{ width: 120 }}
                >
                  <div className="relative w-16 h-16 rounded-2xl flex items-center justify-center shadow-md transition-all duration-200"
                    style={{
                      background:  selectedAgent?.id === agent.id ? color : 'white',
                      border:      `2.5px solid ${selectedAgent?.id === agent.id ? color : color + '45'}`,
                      boxShadow:   selectedAgent?.id === agent.id ? `0 8px 24px ${color}30` : '0 2px 10px rgba(0,0,0,0.07)',
                    }}>
                    <Bot size={22} style={{ color: selectedAgent?.id === agent.id ? 'white' : color }} />
                    <div className="absolute -top-1.5 -right-1.5 w-5 h-5 rounded-full border-2 border-white flex items-center justify-center"
                      style={{ background: agent.status === 'full' ? '#10B981' : '#F59E0B' }}>
                      <span className="text-white font-black" style={{ fontSize: 8 }}>{i + 1}</span>
                    </div>
                  </div>
                  <p className="text-[10px] font-bold uppercase tracking-wider mt-1.5 mb-1"
                    style={{ color: selectedAgent?.id === agent.id ? color : '#9BA8BA' }}>Agent {i + 1}</p>
                  <p className="text-xs text-center font-semibold leading-tight px-1"
                    style={{ color: selectedAgent?.id === agent.id ? '#1A2340' : '#4A5568' }}>
                    {agent.name.split(' ').slice(0, 3).join(' ')}
                  </p>
                </motion.div>
                {i < agents.length - 1 && <PCanvasConnector />}
              </div>
            ))}
            <PCanvasConnector />
            <div className="flex flex-col items-center flex-shrink-0" style={{ width: 120 }}>
              <div className="w-16 h-16 rounded-2xl flex items-center justify-center shadow-sm bg-emerald-50"
                style={{ border: '2px solid #BBF7D0' }}>
                <CheckCircle2 size={22} className="text-emerald-500" />
              </div>
              <p className="text-[10px] font-bold uppercase tracking-widest mt-1.5 mb-1 text-emerald-500">Output</p>
              <p className="text-xs text-center font-semibold text-[#1A2340] leading-tight px-1">{wf.output}</p>
            </div>
          </div>

          {/* Selected agent detail */}
          <AnimatePresence>
            {selectedAgent && (
              <motion.div
                key={selectedAgent.id}
                initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 6 }}
                transition={{ duration: 0.2 }}
                className="mt-2 rounded-2xl border-2 bg-white overflow-hidden"
                style={{ borderColor: color + '40' }}
              >
                <div className="h-1" style={{ background: color }} />
                <div className="p-5">
                  <div className="flex items-center gap-3 mb-4">
                    <div className="w-10 h-10 rounded-xl flex items-center justify-center flex-shrink-0"
                      style={{ background: `${color}15` }}>
                      <Bot size={19} style={{ color }} />
                    </div>
                    <div className="flex-1">
                      <p className="font-bold text-sm text-[#1A2340]">{selectedAgent.name}</p>
                      <p className="text-xs text-[#718096] mt-0.5">{selectedAgent.role}</p>
                    </div>
                    <span className="px-2.5 py-1 rounded-full text-xs font-bold"
                      style={{
                        background: selectedAgent.status === 'full' ? '#D1FAE5' : '#FEF3C7',
                        color:      selectedAgent.status === 'full' ? '#065F46' : '#92400E',
                      }}>
                      {selectedAgent.status === 'full' ? '✓ Active' : '⚠ Needs Work'}
                    </span>
                  </div>
                  <p className="text-xs font-semibold text-[#718096] uppercase tracking-wider mb-2">Authorised Tools</p>
                  <div className="flex flex-wrap gap-1.5">
                    {(selectedAgent.tools || []).map(t => (
                      <span key={t} className="px-2.5 py-1 rounded-lg text-xs font-mono bg-[#F0F4FF] border border-[#C7D2FE] text-[#4338CA]">{t}</span>
                    ))}
                  </div>
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        {/* ── Footer ── */}
        <div className="px-7 py-4 border-t border-[#E2E8F0] flex items-center gap-3 flex-shrink-0 bg-white">
          <button className="flex items-center gap-2 px-5 py-2.5 rounded-xl text-sm font-bold text-white hover:opacity-90 transition-all"
            style={{ background: color }}>
            <Activity size={13} /> View Live Metrics
          </button>
          <button onClick={onClose}
            className="ml-auto flex items-center gap-2 px-5 py-2.5 rounded-xl text-sm font-medium text-[#718096] hover:text-[#4A5568] transition-all">
            Close
          </button>
        </div>
      </motion.div>
    </div>
  )
}

/* ─── Live workflow card ─────────────────────────────────────────────────────── */
function LiveWorkflowCard({ wf, onClick }) {
  const color      = WF_SEG_COLORS[wf.segmentKey] || '#C8102E'
  const agents     = wf.agents || []
  const allDone    = agents.length > 0 && agents.every(a => a.status === 'full')
  return (
    <motion.div
      whileHover={{ y: -2 }}
      transition={{ duration: 0.15 }}
      className="rounded-2xl border border-[#E2E8F0] overflow-hidden hover:border-[#CBD5E0] hover:shadow-md transition-all"
    >
      <div className="h-1" style={{ background: color }} />
      <div className="p-4">
        {/* Header */}
        <div className="flex items-start gap-2 mb-3">
          <div className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0 mt-0.5"
            style={{ background: `${color}15` }}>
            <GitMerge size={15} style={{ color }} />
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-bold text-[#1A2340] leading-tight">{wf.name}</p>
            <p className="text-xs text-[#718096] mt-0.5 leading-relaxed">{wf.description}</p>
          </div>
          {allDone ? (
            <span className="flex items-center gap-1 text-xs font-semibold text-emerald-600 flex-shrink-0">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse inline-block" /> Live
            </span>
          ) : (
            <span className="flex items-center gap-1.5 px-2.5 py-1 rounded-xl text-xs font-bold border flex-shrink-0"
              style={{ background: '#FEF3C7', color: '#92400E', borderColor: '#FDE68A' }}>
              <span className="w-1.5 h-1.5 rounded-full bg-amber-500 inline-block" /> Incomplete
            </span>
          )}
        </div>

        {/* Full agent list — same style as pending card */}
        {agents.length > 0 && (
          <div className="space-y-1.5 mb-3">
            {agents.map((agent, i) => (
              <div
                key={agent.id}
                onClick={onClick}
                className="flex items-center gap-2.5 px-3 py-2.5 rounded-xl bg-white border border-[#E8ECF2] cursor-pointer hover:border-[#C7D2FE] hover:bg-[#F8F9FF] transition-all"
              >
                <div className="w-6 h-6 rounded-full flex items-center justify-center text-white text-xs font-black flex-shrink-0"
                  style={{ background: agent.status === 'full' ? '#10B981' : '#F59E0B' }}>
                  {agent.status === 'full' ? '✓' : i + 1}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-1.5">
                    <p className="text-xs font-semibold text-[#1A2340]">{agent.name}</p>
                    <span className="text-xs font-bold px-1.5 py-0.5 rounded-md"
                      style={agent.status === 'full'
                        ? { background: '#D1FAE5', color: '#065F46', border: '1px solid #A7F3D0' }
                        : { background: '#FEF3C7', color: '#92400E', border: '1px solid #FDE68A' }}>
                      {agent.status === 'full' ? '✓ Process Complete' : 'Incomplete'}
                    </span>
                  </div>
                  {agent.role && <p className="text-xs text-[#718096] mt-0.5 line-clamp-1">{agent.role}</p>}
                </div>
                <ChevronRight size={13} className="text-[#CBD5E0] flex-shrink-0" />
              </div>
            ))}
          </div>
        )}

        {/* Footer stats */}
        <div className="flex items-center justify-between pt-2.5 border-t border-[#F0F2F5]">
          <div className="flex items-center gap-3">
            <span className="text-xs text-[#718096]">{wf.agentCount} agents</span>
            <span className="text-xs font-semibold text-emerald-600">{wf.sla}% SLA</span>
          </div>
          <div className="flex items-center gap-1 text-xs text-[#9BA8BA]">
            <Activity size={10} />
            {wf.tasksToday.toLocaleString()} tasks/day
          </div>
        </div>
      </div>
    </motion.div>
  )
}

/* ─── Pipeline canvas helpers (mirrors AgentPool style) ─────────────────────── */
function PCanvasTrigger({ label }) {
  return (
    <div className="flex flex-col items-center flex-shrink-0" style={{ width: 120 }}>
      <div className="w-16 h-16 rounded-2xl flex items-center justify-center shadow-sm"
        style={{ background: '#F5F3FF', border: '2px dashed #8B5CF655' }}>
        <Zap size={22} className="text-violet-500" />
      </div>
      <p className="text-[10px] font-bold uppercase tracking-widest mt-1.5 mb-1 text-violet-500">Trigger</p>
      <p className="text-xs text-center font-semibold text-[#1A2340] leading-tight px-1">{label || 'Workflow Start'}</p>
    </div>
  )
}

function PCanvasConnector() {
  return (
    <div className="flex items-center flex-shrink-0 mx-1 mt-[-18px]" style={{ width: 44 }}>
      <div className="flex-1 h-0.5 bg-[#CBD5E0]" />
      <div style={{ borderLeft: '7px solid #CBD5E0', borderTop: '4px solid transparent', borderBottom: '4px solid transparent', width: 0, height: 0 }} />
    </div>
  )
}

function PCanvasAgentNode({ agent, index, isBuilt, isSkipped, isApproved, isSelected, onClick }) {
  const nodeColor = isApproved || isBuilt ? '#10B981' : isSkipped ? '#9CA3AF' : '#8B5CF6'
  const dotColor  = isApproved || isBuilt ? '#10B981' : isSkipped ? '#9CA3AF' : '#F59E0B'
  const agentName = agent.agent_name || agent.name || `Agent ${index + 1}`
  return (
    <motion.div
      onClick={onClick}
      whileHover={{ scale: 1.06 }} whileTap={{ scale: 0.97 }}
      className="flex flex-col items-center flex-shrink-0 cursor-pointer"
      style={{ width: 120 }}
    >
      <div className="relative w-16 h-16 rounded-2xl flex items-center justify-center shadow-md transition-all duration-200"
        style={{
          background:  isSelected ? nodeColor : 'white',
          border:      `2.5px solid ${isSelected ? nodeColor : nodeColor + '55'}`,
          boxShadow:   isSelected ? `0 8px 24px ${nodeColor}35` : '0 2px 10px rgba(0,0,0,0.07)',
        }}>
        <Bot size={22} style={{ color: isSelected ? 'white' : nodeColor }} />
        <div className="absolute -top-1.5 -right-1.5 w-5 h-5 rounded-full border-2 border-white flex items-center justify-center"
          style={{ background: dotColor }}>
          <span className="text-white font-black" style={{ fontSize: 8 }}>
            {isApproved || isBuilt ? '✓' : index + 1}
          </span>
        </div>
      </div>
      <p className="text-[10px] font-bold uppercase tracking-wider mt-1.5 mb-1"
        style={{ color: isSelected ? nodeColor : '#9BA8BA' }}>Agent {index + 1}</p>
      <p className="text-xs text-center font-semibold leading-tight px-1"
        style={{ color: isSelected ? '#1A2340' : '#4A5568' }}>
        {agentName.split(' ').slice(0, 3).join(' ')}
      </p>
    </motion.div>
  )
}

function PCanvasOutput() {
  return (
    <div className="flex flex-col items-center flex-shrink-0" style={{ width: 120 }}>
      <div className="w-16 h-16 rounded-2xl flex items-center justify-center shadow-sm bg-emerald-50"
        style={{ border: '2px solid #BBF7D0' }}>
        <CheckCircle2 size={22} className="text-emerald-500" />
      </div>
      <p className="text-[10px] font-bold uppercase tracking-widest mt-1.5 mb-1 text-emerald-500">Output</p>
      <p className="text-xs text-center font-semibold text-[#1A2340] leading-tight px-1">Process Complete</p>
    </div>
  )
}

/* ─── Pending workflow pipeline modal ───────────────────────────────────────── */
function PendingWorkflowDetailModal({ wf, onClose }) {
  const [selectedIdx, setSelectedIdx] = useState(null)
  const chain      = wf.chain || []
  const isApproved = wf.status === 'approved'
  const submittedTime = wf.submittedAt
    ? new Date(wf.submittedAt).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })
    : 'just now'
  const builtCount   = chain.filter((_, i) => !!wf.builtSteps?.[i]).length
  const skippedCount = chain.filter((_, i) => !!wf.skippedSteps?.[i]).length
  const selectedAgent = selectedIdx !== null ? chain[selectedIdx] : null

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ backdropFilter: 'blur(10px)', background: 'rgba(10,18,40,0.72)' }}
      onClick={e => e.target === e.currentTarget && onClose()}
    >
      <motion.div
        initial={{ opacity: 0, scale: 0.93, y: 24 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.93, y: 24 }}
        transition={{ duration: 0.25, ease: 'easeOut' }}
        className="bg-white rounded-3xl flex flex-col overflow-hidden"
        style={{ width: '92vw', maxWidth: 920, maxHeight: '90vh', boxShadow: '0 40px 100px rgba(0,0,0,0.45)' }}
      >
        {/* ── Header ── */}
        <div className="px-7 py-5 flex items-center justify-between flex-shrink-0"
          style={{ background: 'linear-gradient(135deg,#1A2340 0%,#2D3A5C 100%)' }}>
          <div className="flex items-center gap-3.5">
            <div className="w-11 h-11 rounded-2xl flex items-center justify-center flex-shrink-0"
              style={{ background: 'rgba(139,92,246,0.2)', border: '1.5px solid rgba(139,92,246,0.35)' }}>
              <GitMerge size={21} className="text-violet-400" />
            </div>
            <div>
              <p className="text-white font-bold text-base leading-tight">{wf.name}</p>
              <div className="flex items-center gap-3 mt-1">
                {isApproved ? (
                  <span className="flex items-center gap-1.5 text-xs text-emerald-400 font-semibold">
                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse inline-block" /> Process Complete
                  </span>
                ) : (
                  <span className="flex items-center gap-1.5 text-xs text-amber-400 font-semibold">
                    <span className="w-1.5 h-1.5 rounded-full bg-amber-400 inline-block" /> Approval Pending
                  </span>
                )}
                <span className="text-white/40 text-xs">{chain.length} agents</span>
                <span className="text-white/40 text-xs">· Submitted {submittedTime}</span>
              </div>
            </div>
          </div>
          <button onClick={onClose}
            className="w-9 h-9 rounded-xl bg-white/10 hover:bg-white/20 flex items-center justify-center transition-all">
            <X size={15} className="text-white" />
          </button>
        </div>

        {/* ── Stats bar ── */}
        <div className="grid grid-cols-4 border-b border-[#E2E8F0] flex-shrink-0">
          {[
            { label: 'Total Agents',   value: chain.length,   color: '#8B5CF6' },
            { label: 'Built in Velox', value: builtCount,     color: '#10B981' },
            { label: 'Skipped',        value: skippedCount,   color: '#9CA3AF' },
            { label: 'Submitted',      value: submittedTime,  color: '#718096' },
          ].map((s, i) => (
            <div key={s.label} className={`px-6 py-3.5 ${i > 0 ? 'border-l border-[#E2E8F0]' : ''}`}>
              <p className="text-xs text-[#718096] mb-0.5">{s.label}</p>
              <p className="text-xl font-bold" style={{ color: s.color }}>{s.value}</p>
            </div>
          ))}
        </div>

        {/* ── Pipeline canvas ── */}
        <div className="flex-1 overflow-auto px-7 py-7"
          style={{ background: 'linear-gradient(135deg,#F7F9FF 0%,#EEF2FF 100%)' }}>
          <p className="text-xs font-bold uppercase tracking-widest text-[#9BA8BA] mb-6 flex items-center gap-1.5">
            <GitMerge size={11} /> Full Pipeline — click an agent node to inspect
          </p>

          {/* Horizontal nodes */}
          <div className="flex items-start overflow-x-auto pb-6">
            <PCanvasTrigger label={wf.trigger} />
            <PCanvasConnector />
            {chain.map((agent, i) => (
              <div key={i} className="flex items-start">
                <PCanvasAgentNode
                  agent={agent} index={i}
                  isBuilt={!!wf.builtSteps?.[i]}
                  isSkipped={!!wf.skippedSteps?.[i]}
                  isApproved={isApproved}
                  isSelected={selectedIdx === i}
                  onClick={() => setSelectedIdx(v => v === i ? null : i)}
                />
                {i < chain.length - 1 && <PCanvasConnector />}
              </div>
            ))}
            <PCanvasConnector />
            <PCanvasOutput />
          </div>

          {/* Selected agent detail */}
          <AnimatePresence>
            {selectedAgent && (
              <motion.div
                key={selectedIdx}
                initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 6 }}
                transition={{ duration: 0.2 }}
                className="mt-2 rounded-2xl border-2 bg-white overflow-hidden"
                style={{ borderColor: isApproved || wf.builtSteps?.[selectedIdx] ? '#A7F3D0' : '#E9D5FF' }}
              >
                <div className="h-1" style={{ background: isApproved || wf.builtSteps?.[selectedIdx] ? '#10B981' : '#8B5CF6' }} />
                <div className="p-5">
                  <div className="flex items-center gap-3 mb-4">
                    <div className="w-10 h-10 rounded-xl flex items-center justify-center flex-shrink-0 bg-violet-50 border border-violet-100">
                      <Bot size={19} className="text-violet-500" />
                    </div>
                    <div className="flex-1">
                      <p className="font-bold text-sm text-[#1A2340]">
                        {selectedAgent.agent_name || selectedAgent.name || `Agent ${selectedIdx + 1}`}
                      </p>
                      <p className="text-xs text-[#718096] mt-0.5">
                        {selectedAgent.agent_role || selectedAgent.role || selectedAgent.description || selectedAgent.covers || ''}
                      </p>
                    </div>
                    <span className="px-2.5 py-1 rounded-full text-xs font-bold"
                      style={
                        isApproved || wf.builtSteps?.[selectedIdx]
                          ? { background: '#D1FAE5', color: '#065F46' }
                          : wf.skippedSteps?.[selectedIdx]
                          ? { background: '#F3F4F6', color: '#6B7280' }
                          : { background: '#FEF3C7', color: '#92400E' }
                      }>
                      {isApproved || wf.builtSteps?.[selectedIdx] ? '✓ Active'
                        : wf.skippedSteps?.[selectedIdx] ? 'Skipped' : 'Pending Build'}
                    </span>
                  </div>

                  {/* Tools */}
                  {(selectedAgent.tools || []).length > 0 && (
                    <div>
                      <p className="text-xs font-semibold text-[#718096] uppercase tracking-wider mb-2">Authorised Tools</p>
                      <div className="flex flex-wrap gap-1.5">
                        {(selectedAgent.tools || []).map(t => (
                          <span key={t} className="px-2.5 py-1 rounded-lg text-xs font-mono bg-[#F0F4FF] border border-[#C7D2FE] text-[#4338CA]">{t}</span>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Model */}
                  {selectedAgent.model && (
                    <div className="mt-3 flex items-center gap-2">
                      <p className="text-xs font-semibold text-[#718096] uppercase tracking-wider">Model:</p>
                      <span className="px-2.5 py-1 rounded-lg text-xs font-semibold bg-emerald-50 border border-emerald-200 text-emerald-700">
                        {selectedAgent.model}
                      </span>
                    </div>
                  )}
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        {/* ── Footer ── */}
        <div className="px-7 py-4 border-t border-[#E2E8F0] flex items-center gap-3 flex-shrink-0 bg-white">
          <button onClick={onClose}
            className="ml-auto flex items-center gap-2 px-5 py-2.5 rounded-xl text-sm font-medium text-[#718096] hover:text-[#4A5568] transition-all">
            Close
          </button>
        </div>
      </motion.div>
    </div>
  )
}

/* ─── User-built agent/workflow card ────────────────────────────────────────── */
function UserBuiltCard({ item, type, onClick }) {
  const label = item.statusLabel || (
    item.status === 'active' ? 'Approved' :
    item.status === 'rejected' ? 'Rejected' : 'Under Review'
  )
  const badgeCls =
    label === 'Approved'     ? 'bg-emerald-100 text-emerald-700' :
    label === 'Rejected'     ? 'bg-red-100 text-red-700' :
                               'bg-amber-100 text-amber-700'
  return (
    <motion.div whileHover={{ x: 2 }} transition={{ duration: 0.15 }}
      onClick={onClick}
      className={'flex items-center gap-3 px-4 py-3 rounded-xl border bg-white hover:border-[#CBD5E0] hover:shadow-sm transition-all cursor-pointer group ' +
        (label === 'Rejected' ? 'opacity-60 border-red-100' : 'border-[#E2E8F0]')}>
      <div className="w-8 h-8 rounded-xl flex items-center justify-center flex-shrink-0" style={{ background: '#1A2340' + '10' }}>
        <Bot size={14} className="text-[#1A2340]" />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <p className={'text-sm font-semibold truncate ' + (label === 'Rejected' ? 'line-through text-[#9BA8BA]' : 'text-[#1A2340]')}>{item.name}</p>
          <span className={'px-1.5 py-0.5 rounded-full text-[10px] font-bold ' + badgeCls}>● {label}</span>
        </div>
        <div className="flex items-center gap-2 mt-0.5">
          {item.author && <span className="text-[10px] text-[#9BA8BA] flex items-center gap-1"><Tag size={8} /> {item.author}</span>}
          {(item.tools || []).length > 0 && <span className="text-[10px] text-[#9BA8BA]">{item.tools.length} tools</span>}
        </div>
      </div>
      <ChevronRight size={13} className="text-[#CBD5E0] group-hover:text-[#9BA8BA] flex-shrink-0" />
    </motion.div>
  )
}

/* ─── Pending workflow card ──────────────────────────────────────────────────── */
function PendingWorkflowCard({ wf }) {
  const [showModal, setShowModal] = useState(false)
  const submittedTime = wf.submittedAt
    ? new Date(wf.submittedAt).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })
    : 'just now'
  const chain      = wf.chain || []
  const isApproved = wf.status === 'approved'

  return (
    <>
      <AnimatePresence>
        {showModal && (
          <PendingWorkflowDetailModal wf={wf} onClose={() => setShowModal(false)} />
        )}
      </AnimatePresence>

      <motion.div
        initial={{ opacity: 0, y: 6 }}
        animate={{ opacity: 1, y: 0 }}
        className="rounded-2xl border overflow-hidden"
        style={{
          borderColor: isApproved ? '#A7F3D0' : '#FCD34D',
          background:  isApproved ? 'rgba(16,185,129,0.04)' : 'rgba(251,191,36,0.03)',
        }}
      >
        {/* Top colour bar */}
        <div className="h-1" style={{
          background: isApproved
            ? 'linear-gradient(90deg,#10B981,#059669)'
            : 'linear-gradient(90deg,#F59E0B,#D97706)',
        }} />

        <div className="p-4">
          {/* Header row */}
          <div className="flex items-start gap-3 mb-3">
            <div className="w-9 h-9 rounded-xl flex items-center justify-center flex-shrink-0"
              style={{ background: isApproved ? '#D1FAE5' : '#FEF3C7' }}>
              {isApproved
                ? <CheckCheck size={16} className="text-emerald-600" />
                : <GitMerge   size={16} className="text-amber-600"   />
              }
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-bold text-[#1A2340] leading-snug">{wf.name}</p>
              <p className="text-xs text-[#718096] mt-0.5">
                {wf.agentCount} agent{wf.agentCount !== 1 ? 's' : ''} · Submitted {submittedTime}
              </p>
            </div>
            {isApproved ? (
              <span className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-xl text-xs font-bold border flex-shrink-0"
                style={{ background: '#D1FAE5', color: '#065F46', borderColor: '#A7F3D0' }}>
                <CheckCheck size={10} /> Process Complete
              </span>
            ) : (
              <span className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-xl text-xs font-bold bg-amber-100 text-amber-700 border border-amber-200 flex-shrink-0">
                <Clock size={9} /> Approval Pending
              </span>
            )}
          </div>

          {/* Agent rows — click any to open full pipeline modal */}
          {chain.length > 0 && (
            <div className="space-y-1.5">
              {chain.map((agent, i) => {
                const agentName = agent.agent_name || agent.name || `Agent ${i + 1}`
                const agentRole = agent.agent_role  || agent.role || agent.description || agent.covers || ''
                const isBuilt   = !!wf.builtSteps?.[i]
                const isSkipped = !!wf.skippedSteps?.[i]
                return (
                  <div
                    key={i}
                    onClick={() => setShowModal(true)}
                    className="flex items-center gap-2.5 px-3 py-2.5 rounded-xl bg-white border border-[#E8ECF2] cursor-pointer hover:border-[#C7D2FE] hover:bg-[#F8F9FF] transition-all"
                  >
                    <div className="w-6 h-6 rounded-full flex items-center justify-center text-white text-xs font-black flex-shrink-0"
                      style={{ background: isApproved || isBuilt ? '#10B981' : isSkipped ? '#9CA3AF' : '#1A2340' }}>
                      {isApproved || isBuilt ? '✓' : i + 1}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-1.5 flex-wrap">
                        <p className="text-xs font-semibold text-[#1A2340]">{agentName}</p>
                        {isBuilt && !isApproved && (
                          <span className="text-xs font-bold px-1.5 py-0.5 rounded-md"
                            style={{ background: '#D1FAE5', color: '#065F46', border: '1px solid #A7F3D0' }}>Built ✓</span>
                        )}
                        {isSkipped && (
                          <span className="text-xs font-semibold px-1.5 py-0.5 rounded-md bg-gray-100 text-gray-500 border border-gray-200">Skipped</span>
                        )}
                        {isApproved && (
                          <span className="text-xs font-bold px-1.5 py-0.5 rounded-md"
                            style={{ background: '#D1FAE5', color: '#065F46', border: '1px solid #A7F3D0' }}>✓ Active</span>
                        )}
                      </div>
                      {agentRole && <p className="text-xs text-[#718096] mt-0.5 line-clamp-1 leading-relaxed">{agentRole}</p>}
                    </div>
                    <ChevronRight size={13} className="text-[#CBD5E0] flex-shrink-0" />
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </motion.div>
    </>
  )
}

/* ─── Workflow upload modal ──────────────────────────────────────────────────── */
function WorkflowUploadModal({ config, onClose, onApprove, approved }) {
  // Parse flexibly — support both workflow-config format and legacy agent-config format
  const wfMeta   = config.workflow  || {}
  const agentMeta = config.agent   || {}
  const name        = wfMeta.name        || agentMeta.name        || 'Uploaded Workflow'
  const description = wfMeta.description || agentMeta.description || ''
  const trigger     = wfMeta.trigger     || config.trigger        || ''
  const output      = wfMeta.output      || config.output         || ''

  // Agent list — from config.agents (new format) or single legacy agent
  const agents = config.agents || config.chain || []
  const legacyTools = (config.tools || []).filter(t => t.enabled !== false)

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ backdropFilter: 'blur(6px)', background: 'rgba(10,18,40,0.62)' }}
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <motion.div
        initial={{ opacity: 0, scale: 0.95, y: 24 }}
        animate={{ opacity: 1, scale: 1,    y: 0  }}
        exit={  { opacity: 0, scale: 0.95, y: 24 }}
        transition={{ duration: 0.22, ease: 'easeOut' }}
        className="bg-white rounded-2xl shadow-2xl w-full max-w-2xl flex flex-col"
        style={{ maxHeight: '88vh', boxShadow: '0 30px 70px rgba(0,0,0,0.38)' }}
      >
        {/* ── Header ── */}
        <div
          className="px-6 py-4 flex items-center justify-between flex-shrink-0"
          style={{ background: 'linear-gradient(135deg,#1A2340 0%,#2D3A5C 100%)', borderRadius: '1rem 1rem 0 0' }}
        >
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-white/10 flex items-center justify-center">
              <Workflow size={16} className="text-white" />
            </div>
            <div>
              <p className="text-white font-bold text-sm">{name}</p>
              <p className="text-white/50 text-xs">Workflow Configuration · Review &amp; Submit</p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="w-7 h-7 rounded-lg bg-white/10 hover:bg-white/20 flex items-center justify-center transition-all"
          >
            <X size={13} className="text-white" />
          </button>
        </div>

        {/* ── Scrollable body ── */}
        <div className="overflow-y-auto flex-1 px-6 py-5 space-y-5">

          {/* Description */}
          {description && (
            <div className="px-4 py-3 rounded-xl bg-[#F7F8FA] border border-[#E2E8F0]">
              <p className="text-xs text-[#718096] leading-relaxed">{description}</p>
            </div>
          )}

          {/* Trigger → Output flow */}
          {(trigger || output) && (
            <div className="flex items-center gap-2 flex-wrap">
              {trigger && (
                <span className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-violet-50 border border-violet-200 text-xs font-semibold text-violet-700">
                  <Zap size={11} /> {trigger}
                </span>
              )}
              {trigger && output && <ArrowRight size={14} className="text-[#CBD5E0]" />}
              {output && (
                <span className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-emerald-50 border border-emerald-200 text-xs font-semibold text-emerald-700">
                  <CheckCircle2 size={11} /> {output}
                </span>
              )}
            </div>
          )}

          {/* Agent pipeline */}
          {agents.length > 0 && (
            <div>
              <p className="text-xs font-bold text-[#1A2340] uppercase tracking-wider mb-3 flex items-center gap-2">
                <Bot size={12} /> Agent Pipeline — {agents.length} agent{agents.length !== 1 ? 's' : ''}
              </p>
              <div className="space-y-2">
                {agents.map((agent, i) => {
                  const agentName  = agent.name       || agent.agent_name  || `Agent ${i + 1}`
                  const agentRole  = agent.role       || agent.description || agent.agent_role || ''
                  const agentTools = agent.tools      || []
                  return (
                    <div key={i} className="flex items-start gap-3 px-4 py-3 rounded-xl bg-[#F7F8FA] border border-[#E2E8F0]">
                      <div
                        className="w-7 h-7 rounded-full flex items-center justify-center text-white text-xs font-black flex-shrink-0"
                        style={{ background: '#1A2340' }}
                      >
                        {i + 1}
                      </div>
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-semibold text-[#1A2340]">{agentName}</p>
                        {agentRole && <p className="text-xs text-[#718096] mt-0.5">{agentRole}</p>}
                        {agentTools.length > 0 && (
                          <div className="flex flex-wrap gap-1 mt-1.5">
                            {agentTools.map(t => (
                              <span key={t} className="px-1.5 py-0.5 rounded text-xs font-mono bg-[#EEF2FF] border border-[#C7D2FE] text-[#4338CA]">{t}</span>
                            ))}
                          </div>
                        )}
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {/* Legacy: tools only (no agent list) */}
          {agents.length === 0 && legacyTools.length > 0 && (
            <div>
              <p className="text-xs font-bold text-[#1A2340] uppercase tracking-wider mb-3 flex items-center gap-2">
                <Wrench size={12} /> Tools Enabled ({legacyTools.length})
              </p>
              <div className="flex flex-wrap gap-1.5">
                {legacyTools.map(t => (
                  <span key={t.id || t.name} className="px-2.5 py-1 rounded-lg text-xs font-medium bg-violet-50 text-violet-700 border border-violet-100">
                    {t.name}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Version / framework metadata */}
          {(agentMeta.version || agentMeta.framework) && (
            <div className="flex items-center gap-2 flex-wrap">
              {agentMeta.version && (
                <span className="px-2.5 py-1 rounded-full text-xs font-bold bg-blue-50 text-blue-600 border border-blue-100">
                  v{agentMeta.version}
                </span>
              )}
              {agentMeta.framework && (
                <span className="px-2.5 py-1 rounded-full text-xs font-medium bg-[#1A2340]/5 text-[#1A2340]">
                  {agentMeta.framework}
                </span>
              )}
            </div>
          )}
        </div>

        {/* ── Sticky footer ── */}
        <div
          className="px-6 py-4 border-t border-[#E2E8F0] flex items-center justify-between flex-shrink-0"
          style={{ background: '#F7F8FA', borderRadius: '0 0 1rem 1rem' }}
        >
          <p className="text-xs text-[#718096]">
            {approved
              ? 'Workflow submitted — pending approval on Homepage.'
              : 'Review the workflow before submitting for approval.'}
          </p>
          {approved ? (
            <div className="flex items-center gap-2 px-4 py-2 rounded-lg bg-emerald-50 text-emerald-700 border border-emerald-200">
              <CheckCircle2 size={13} />
              <span className="text-xs font-semibold">Sent for Approval</span>
            </div>
          ) : (
            <button
              onClick={onApprove}
              className="flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-semibold text-white transition-all hover:opacity-90 active:scale-95"
              style={{ background: 'linear-gradient(135deg,#C8102E 0%,#9B0D24 100%)' }}
            >
              <Send size={12} /> Send for Approval
            </button>
          )}
        </div>
      </motion.div>
    </div>
  )
}

/* ─── Animation variants ─────────────────────────────────────────────────────── */
const container = { hidden: { opacity: 0 }, show: { opacity: 1, transition: { staggerChildren: 0.08 } } }
const item      = { hidden: { opacity: 0, y: 14 }, show: { opacity: 1, y: 0, transition: { duration: 0.3 } } }

/* ─── Dashboard ──────────────────────────────────────────────────────────────── */
export default function Dashboard() {
  const navigate = useNavigate()
  const { metrics, pendingWorkflows, addPendingWorkflow, builtAgents = [], platformApprovals = {} } = useStore()
  const [uploadedConfig,  setUploadedConfig]  = useState(null)
  const [approvalSent,    setApprovalSent]    = useState(false)
  const [selectedLiveWf,  setSelectedLiveWf]  = useState(null)
  const [selectedUserItem, setSelectedUserItem] = useState(null)
  const fileInputRef = useRef(null)

  function handleFileUpload(e) {
    const file = e.target.files[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = (ev) => {
      try {
        const json = JSON.parse(ev.target.result)
        setUploadedConfig(json)
        setApprovalSent(false)
      } catch { /* ignore invalid JSON */ }
    }
    reader.readAsText(file)
    e.target.value = ''
  }

  function handleApprove() {
    if (!uploadedConfig) return
    const wfMeta  = uploadedConfig.workflow || {}
    const agentMeta = uploadedConfig.agent  || {}
    const name    = wfMeta.name    || agentMeta.name    || 'Uploaded Workflow'
    const agents  = uploadedConfig.agents || uploadedConfig.chain || []
    addPendingWorkflow({
      name,
      agentCount: agents.length || 1,
      chain:      agents,
      summary:    wfMeta.description || agentMeta.description || '',
    })
    setApprovalSent(true)
  }

  function handleModalClose() {
    setUploadedConfig(null)
    setApprovalSent(false)
  }

  if (!metrics) {
    return (
      <div className="grid grid-cols-3 gap-4">
        {[...Array(3)].map((_, i) => <div key={i} className="card p-5 h-28 skeleton" />)}
      </div>
    )
  }

  const ov = metrics.overview

  return (
    <motion.div variants={container} initial="hidden" animate="show" className="space-y-6">

      {/* Hidden file input */}
      <input
        ref={fileInputRef}
        type="file"
        accept=".json,.config"
        className="hidden"
        onChange={handleFileUpload}
      />

      <AnimatePresence>
        {selectedUserItem && (
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
            style={{ backdropFilter: 'blur(10px)', background: 'rgba(10,18,40,0.72)' }}
            onClick={e => e.target === e.currentTarget && setSelectedUserItem(null)}>
            <motion.div initial={{ opacity: 0, scale: 0.95, y: 16 }} animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.95, y: 16 }} transition={{ duration: 0.2 }}
              className="bg-white rounded-3xl overflow-hidden"
              style={{ width: '92vw', maxWidth: 540, maxHeight: '88vh', boxShadow: '0 40px 80px rgba(0,0,0,0.35)' }}>
              <div className="px-6 py-5 flex items-center justify-between"
                style={{ background: 'linear-gradient(135deg,#1A2340 0%,#2D3A5C 100%)' }}>
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 rounded-2xl bg-white/10 flex items-center justify-center">
                    <Bot size={18} className="text-white" />
                  </div>
                  <div>
                    <p className="text-white font-bold text-sm">{selectedUserItem.name}</p>
                    <p className="text-white/50 text-xs mt-0.5">{selectedUserItem.segment || 'Custom'} · {selectedUserItem.status === 'active' ? 'Active' : 'Under Review'}</p>
                  </div>
                </div>
                <button onClick={() => setSelectedUserItem(null)}
                  className="w-8 h-8 rounded-xl bg-white/10 hover:bg-white/20 flex items-center justify-center transition-all">
                  <X size={14} className="text-white" />
                </button>
              </div>
              <div className="p-6 space-y-5 overflow-y-auto" style={{ maxHeight: 'calc(88vh - 130px)' }}>
                {selectedUserItem.description && <p className="text-sm text-[#4A5568] leading-relaxed">{selectedUserItem.description}</p>}
                {selectedUserItem.author && (
                  <div className="flex items-center gap-2">
                    <Tag size={12} className="text-[#9BA8BA]" />
                    <span className="text-xs text-[#718096]">Author: <span className="font-semibold text-[#1A2340]">{selectedUserItem.author}</span></span>
                  </div>
                )}
                <div className="flex items-center gap-2">
                  <span className={'px-2.5 py-1 rounded-full text-xs font-bold ' + (selectedUserItem.status === 'active' ? 'bg-emerald-100 text-emerald-700' : 'bg-amber-100 text-amber-700')}>
                    {selectedUserItem.status === 'active' ? '● Active' : '● Under Review'}
                  </span>
                  {selectedUserItem.model && <span className="text-xs text-[#718096] font-mono">{selectedUserItem.model}</span>}
                </div>
                {(selectedUserItem.tools || []).length > 0 && (
                  <div>
                    <p className="text-xs font-semibold text-[#718096] uppercase tracking-wider mb-2">Authorised Tools</p>
                    <div className="flex flex-wrap gap-1.5">
                      {selectedUserItem.tools.map(t => <span key={t} className="px-2.5 py-1 rounded-lg text-xs font-mono bg-[#F0F4FF] border border-[#C7D2FE] text-[#4338CA]">{t}</span>)}
                    </div>
                  </div>
                )}
                {selectedUserItem.systemPrompt && (
                  <div>
                    <p className="text-xs font-semibold text-[#718096] uppercase tracking-wider mb-2">System Prompt</p>
                    <pre className="text-xs text-[#4A5568] bg-[#F7F8FA] rounded-xl p-3 overflow-auto max-h-40 whitespace-pre-wrap font-mono leading-relaxed border border-[#E2E8F0]">{selectedUserItem.systemPrompt}</pre>
                  </div>
                )}
              </div>
            </motion.div>
          </div>
        )}
      </AnimatePresence>

      {/* Upload modal */}
      <AnimatePresence>
        {uploadedConfig && (
          <WorkflowUploadModal
            config={uploadedConfig}
            onClose={handleModalClose}
            onApprove={handleApprove}
            approved={approvalSent}
          />
        )}
      </AnimatePresence>

      {/* Live workflow detail modal */}
      <AnimatePresence>
        {selectedLiveWf && (
          <LiveWorkflowDetailModal
            wf={selectedLiveWf}
            onClose={() => setSelectedLiveWf(null)}
          />
        )}
      </AnimatePresence>

      {/* ── Hero header ── */}
      <motion.div variants={item}
        className="relative rounded-2xl overflow-hidden p-6"
        style={{
          background:  'linear-gradient(135deg,#1A2340 0%,#2D3A5C 60%,#1A2340 100%)',
          boxShadow:   'var(--shadow-lg)',
        }}
      >
        <div className="absolute inset-0 opacity-5"
          style={{ backgroundImage: 'radial-gradient(circle at 80% 50%,#C8102E 0%,transparent 60%)' }} />
        <div className="relative flex items-center justify-between">
          <div>
            <p className="text-white/50 text-xs uppercase tracking-widest mb-1">DLX_AGENTIC_OS — Deluxe Corporation</p>
            <h1 className="font-display text-2xl font-bold text-white">My Profile</h1>
            <p className="text-white/60 text-sm mt-1">Real-time workflow intelligence across all business segments</p>
          </div>
          <div className="text-right">
            <p className="text-white/40 text-xs mb-1">System Status</p>
            <div className="flex items-center gap-2 justify-end">
              <span className="agent-pulse w-2 h-2 rounded-full bg-emerald-400 inline-block" />
              <span className="text-emerald-400 text-sm font-semibold">{ov.uptimePercent}% Uptime</span>
            </div>
            <p className="text-white/40 text-xs mt-1">{ov.activeAgents} of {ov.totalAgents} agents active</p>
          </div>
        </div>
      </motion.div>

      {/* ── Your Workflows — single unified panel ── */}
      <motion.div variants={item}>
        <div className="card overflow-hidden">

          {/* Panel header */}
          <div className="px-5 py-4 border-b border-[#E2E8F0] flex items-center justify-between">
            <div className="flex items-center gap-2.5">
              <div className="w-7 h-7 rounded-lg border border-[#1A2340]/15 flex items-center justify-center"
                style={{ background: 'rgba(26,35,64,0.06)' }}>
                <GitMerge size={13} className="text-[#1A2340]" />
              </div>
              <div>
                <p className="text-sm font-semibold text-[#1A2340]">Your Workflows</p>
                <p className="text-xs text-[#718096]">Live &amp; pending approval</p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => fileInputRef.current?.click()}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold text-[#1A2340] border border-[#E2E8F0] hover:border-[#CBD5E0] hover:bg-[#F7F9FF] transition-all"
              >
                <Upload size={11} /> Upload Existing
              </button>
              <button
                onClick={() => navigate('/studio')}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold text-white hover:opacity-90 transition-all"
                style={{ background: '#C8102E' }}
              >
                <Plus size={11} /> Build Workflow
              </button>
            </div>
          </div>

          {/* Content */}
          <div className="p-4 space-y-5">

            {/* ── Active / Approved ── */}
            {(builtAgents.filter(a => a.status === 'active').length > 0 || Object.values(platformApprovals).some(v => v === 'approved')) && (
              <div>
                <p className="text-xs font-bold text-[#9BA8BA] uppercase tracking-wider mb-2.5 flex items-center gap-1.5">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 inline-block animate-pulse" />
                  Approved & Active
                </p>
                <div className="space-y-2">
                  {builtAgents.filter(a => a.status === 'active').map(agent => (
                    <UserBuiltCard key={agent.id} item={{ ...agent, statusLabel: 'Approved' }} type="agent" onClick={() => setSelectedUserItem(agent)} />
                  ))}
                </div>
              </div>
            )}

            {/* ── Platform Live ── */}
            <div>
              <p className="text-xs font-bold text-[#9BA8BA] uppercase tracking-wider mb-2.5 flex items-center gap-1.5">
                <span className="w-1.5 h-1.5 rounded-full bg-blue-400 inline-block animate-pulse" />
                Platform Live
              </p>
              <div className="space-y-2">
                {LIVE_WORKFLOWS.map(wf => (
                  <LiveWorkflowCard key={wf.id} wf={wf} onClick={() => setSelectedLiveWf(wf)} />
                ))}
              </div>
            </div>

            {/* ── Under Review ── */}
            {(pendingWorkflows.filter(w => w.status !== 'approved' && w.status !== 'rejected').length > 0 || builtAgents.filter(a => a.status === 'under-review').length > 0) && (
              <div>
                <p className="text-xs font-bold text-[#9BA8BA] uppercase tracking-wider mb-2.5 flex items-center gap-1.5">
                  <Clock size={10} className="text-amber-500" />
                  Under Review
                  <span className="px-1.5 py-0.5 rounded-full text-xs font-bold bg-amber-100 text-amber-600">
                    {pendingWorkflows.filter(w => w.status !== 'approved' && w.status !== 'rejected').length + builtAgents.filter(a => a.status === 'under-review').length}
                  </span>
                </p>
                <div className="space-y-2">
                  {builtAgents.filter(a => a.status === 'under-review').map(agent => (
                    <UserBuiltCard key={agent.id} item={agent} type="agent" onClick={() => setSelectedUserItem(agent)} />
                  ))}
                  {pendingWorkflows.filter(w => w.status !== 'approved' && w.status !== 'rejected').map((wf, i) => (
                    <PendingWorkflowCard key={wf.id || i} wf={wf} />
                  ))}
                </div>
              </div>
            )}

            {/* ── Rejected ── */}
            {(builtAgents.filter(a => a.status === 'rejected').length > 0 || pendingWorkflows.filter(w => w.status === 'rejected').length > 0) && (
              <div>
                <p className="text-xs font-bold text-[#9BA8BA] uppercase tracking-wider mb-2.5 flex items-center gap-1.5">
                  <span className="w-1.5 h-1.5 rounded-full bg-red-400 inline-block" />
                  Rejected
                </p>
                <div className="space-y-2">
                  {builtAgents.filter(a => a.status === 'rejected').map(agent => (
                    <UserBuiltCard key={agent.id} item={{ ...agent, statusLabel: 'Rejected' }} type="agent" onClick={() => setSelectedUserItem(agent)} />
                  ))}
                  {pendingWorkflows.filter(w => w.status === 'rejected').map((wf, i) => (
                    <PendingWorkflowCard key={wf.id || i} wf={wf} />
                  ))}
                </div>
              </div>
            )}

            {/* ── Process Complete (approved workflows) ── */}
            {pendingWorkflows && pendingWorkflows.filter(w => w.status === 'approved').length > 0 && (
              <div>
                <p className="text-xs font-bold text-[#9BA8BA] uppercase tracking-wider mb-2.5 flex items-center gap-1.5">
                  <CheckCheck size={10} className="text-emerald-500" />
                  Approved Workflows
                  <span className="px-1.5 py-0.5 rounded-full text-xs font-bold bg-emerald-100 text-emerald-600">
                    {pendingWorkflows.filter(w => w.status === 'approved').length}
                  </span>
                </p>
                <div className="space-y-2">
                  {pendingWorkflows.filter(w => w.status === 'approved').map((wf, i) => (
                    <PendingWorkflowCard key={wf.id || i} wf={wf} />
                  ))}
                </div>
              </div>
            )}

            {/* Footer link */}
            <div className="flex justify-end pt-1 border-t border-[#F0F2F5]">
              <button
                onClick={() => navigate('/agent-pool')}
                className="flex items-center gap-1 text-xs text-[#C8102E] font-medium hover:gap-2 transition-all"
              >
                View all workflows <ArrowRight size={11} />
              </button>
            </div>
          </div>
        </div>
      </motion.div>

    </motion.div>
  )
}
