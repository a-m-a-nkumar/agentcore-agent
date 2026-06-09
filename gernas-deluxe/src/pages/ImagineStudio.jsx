import { useState, useRef, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { useNavigate, useLocation } from 'react-router-dom'
import {
  Sparkles, Send, FileText, CheckCircle, AlertCircle, XCircle,
  Bot, Zap, ArrowRight, Plus, Edit3, RotateCcw, Loader,
  TrendingUp, Users, Clock, DollarSign, ChevronRight, ChevronLeft, Upload, Lightbulb,
  Play, Terminal, ChevronDown, GitBranch, Search, Download, GitMerge, Layers,
  X, Cpu
} from 'lucide-react'
import useStore from '../store/useStore'

const EXAMPLE_PROMPTS = [
  "We're losing print customers because we don't catch them early enough before they churn",
  "Our merchant onboarding takes 5–7 days — we need to cut that to under 24 hours",
  "We process 500+ invoices daily and our team spends 3 days matching payments manually",
  "We need real-time fraud detection across all merchant transactions",
]

// ─── Sub-components ──────────────────────────────────────────────────────────

function TypingIndicator() {
  return (
    <div className="flex items-center gap-1 px-4 py-3 rounded-2xl rounded-tl-sm bg-white border border-[#E2E8F0] w-fit">
      {[0, 1, 2].map(i => (
        <motion.span
          key={i} className="w-1.5 h-1.5 rounded-full bg-[#CBD5E0]"
          animate={{ y: [0, -4, 0] }}
          transition={{ duration: 0.6, repeat: Infinity, delay: i * 0.15 }}
        />
      ))}
    </div>
  )
}

function NeedsCard({ need, match, onBuild, onModify, index }) {
  const statusConfig = {
    solved:   { icon: CheckCircle,  color: '#10B981', bg: '#F0FDF4', border: '#BBF7D0', label: 'Solved' },
    partial:  { icon: AlertCircle,  color: '#F59E0B', bg: '#FFFBEB', border: '#FDE68A', label: 'Partial Match' },
    unsolved: { icon: XCircle,      color: '#EF4444', bg: '#FEF2F2', border: '#FECACA', label: 'No Agent Yet' },
  }
  const cfg = statusConfig[match?.status] || statusConfig.unsolved
  const Icon = cfg.icon

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.12 }}
      className="rounded-xl border overflow-hidden"
      style={{ borderColor: cfg.border, background: cfg.bg }}
    >
      <div className="px-4 py-3">
        <div className="flex items-start justify-between mb-1.5">
          <div className="flex items-start gap-2 flex-1">
            <Icon size={15} style={{ color: cfg.color }} className="mt-0.5 flex-shrink-0" />
            <div>
              <p className="text-sm font-semibold text-[#1A2340]">{need.label}</p>
              <p className="text-xs text-[#718096] mt-0.5 leading-relaxed">{need.detail}</p>
            </div>
          </div>
          <span
            className="flex-shrink-0 ml-3 px-2 py-0.5 rounded-full text-xs font-semibold"
            style={{ color: cfg.color, background: `${cfg.color}18` }}
          >
            {cfg.label}
          </span>
        </div>

        {match?.agentName && (
          <div className="mt-2 pt-2 border-t" style={{ borderColor: cfg.border }}>
            <div className="flex items-center gap-1.5 mb-1">
              <Bot size={11} style={{ color: cfg.color }} />
              <span className="text-xs font-semibold" style={{ color: cfg.color }}>{match.agentName}</span>
              {match.score > 0 && (
                <span className="text-xs text-[#718096]">— {match.score}% match</span>
              )}
            </div>
            {match.solves && <p className="text-xs text-[#4A5568]">✓ {match.solves}</p>}
            {match.gap    && <p className="text-xs text-amber-600 mt-0.5">⚠ Gap: {match.gap}</p>}
          </div>
        )}

        {match?.status === 'unsolved' && (
          <div className="mt-2 pt-2 border-t border-red-200 flex gap-2">
            <button
              onClick={() => onBuild(need)}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold text-white"
              style={{ background: '#C8102E' }}
            >
              <Plus size={11} /> Build Agent
            </button>
            <button
              onClick={() => onModify(need)}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-[#4A5568] bg-white border border-red-200 hover:border-red-300"
            >
              <Edit3 size={11} /> Modify Existing
            </button>
          </div>
        )}
        {match?.status === 'partial' && (
          <div className="mt-2 pt-2 border-t border-amber-200">
            <button
              onClick={() => onModify(need)}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-amber-700 bg-white border border-amber-200 hover:border-amber-300"
            >
              <Edit3 size={11} /> Extend this agent to cover the gap
            </button>
          </div>
        )}
        {match?.status === 'solved' && (
          <div className="mt-2 pt-2 border-t border-emerald-200">
            <button
              onClick={() => onModify(need)}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-emerald-700 bg-white border border-emerald-200 hover:border-emerald-300"
            >
              <Zap size={11} /> Deploy now
            </button>
          </div>
        )}
      </div>
    </motion.div>
  )
}

// ─── Agent Flowchart (shown on hover) ────────────────────────────────────────

const SEG_COLOR = { merchant: '#0EA5E9', print: '#6B7280', b2b: '#8B5CF6', data: '#10B981', platform: '#C8102E' }

function AgentFlowChart({ agentName, segment, solvedNeeds, partialNeeds }) {
  const nodeH = 58
  const nodeW = 126
  const nodeGap = 12
  const chartW = 400
  const midX = chartW / 2
  const kernelR = 36

  const maxRows = Math.max(solvedNeeds.length, partialNeeds.length, 1)
  const rowH = nodeH + nodeGap
  const chartH = maxRows * rowH + nodeGap * 2

  const kernelCY = chartH / 2

  const leftTotalH = solvedNeeds.length * rowH - nodeGap
  const leftStartY  = (chartH - leftTotalH) / 2
  const lNodeY  = (i) => leftStartY + i * rowH
  const lNodeCY = (i) => lNodeY(i) + nodeH / 2

  const rightTotalH = partialNeeds.length * rowH - nodeGap
  const rightStartY  = (chartH - rightTotalH) / 2
  const rNodeY  = (i) => rightStartY + i * rowH
  const rNodeCY = (i) => rNodeY(i) + nodeH / 2

  const rightLeftX = chartW - nodeW
  const color = SEG_COLOR[segment] || '#1A2340'

  return (
    <div className="relative" style={{ width: chartW, height: chartH }}>
      <svg className="absolute inset-0 pointer-events-none" width={chartW} height={chartH}>
        {solvedNeeds.map((_, i) => (
          <line key={i}
            x1={nodeW} y1={lNodeCY(i)} x2={midX - kernelR} y2={kernelCY}
            stroke="#10B981" strokeWidth="1.5" strokeLinecap="round"
          />
        ))}
        {partialNeeds.map((_, i) => (
          <line key={i}
            x1={midX + kernelR} y1={kernelCY} x2={rightLeftX} y2={rNodeCY(i)}
            stroke="#F59E0B" strokeWidth="1.5" strokeDasharray="5 3" strokeLinecap="round"
          />
        ))}
      </svg>

      {/* Left — solved */}
      {solvedNeeds.map(({ need, match }, i) => (
        <div key={need.id} className="absolute" style={{ left: 0, top: lNodeY(i), width: nodeW, height: nodeH }}>
          <div className="w-full h-full rounded-xl border-2 border-emerald-400 bg-white shadow-sm p-2 flex flex-col justify-center">
            <div className="flex items-center gap-1 mb-1">
              <CheckCircle size={10} className="text-emerald-500 flex-shrink-0" />
              <span className="text-xs font-bold text-[#1A2340] leading-tight" style={{ fontSize: '10px' }}>
                {need.label.split(' ').slice(0, 3).join(' ')}
              </span>
            </div>
            <span className="text-emerald-600 font-semibold" style={{ fontSize: '10px' }}>{match.score}% match</span>
          </div>
        </div>
      ))}

      {/* Kernel circle */}
      <div className="absolute rounded-full flex flex-col items-center justify-center"
        style={{
          left: midX - kernelR, top: kernelCY - kernelR,
          width: kernelR * 2, height: kernelR * 2,
          background: '#1A2340',
          boxShadow: '0 4px 16px rgba(26,35,64,0.35)',
        }}>
        <Bot size={13} className="text-white mb-0.5" />
        <p className="text-white font-bold text-center leading-tight px-1" style={{ fontSize: '7.5px' }}>
          {agentName.split(' ').slice(0, 2).join(' ')}
        </p>
        <p style={{ fontSize: '6.5px' }} className="text-white/40 mt-0.5">kernel</p>
      </div>

      {/* Right — partial */}
      {partialNeeds.map(({ need, match }, i) => (
        <div key={need.id} className="absolute" style={{ left: rightLeftX, top: rNodeY(i), width: nodeW, height: nodeH }}>
          <div className="w-full h-full rounded-xl border-2 border-dashed border-amber-400 bg-amber-50 p-2 flex flex-col justify-center">
            <div className="flex items-center gap-1 mb-1">
              <AlertCircle size={10} className="text-amber-500 flex-shrink-0" />
              <span className="text-xs font-bold text-[#1A2340] leading-tight" style={{ fontSize: '10px' }}>
                {need.label.split(' ').slice(0, 3).join(' ')}
              </span>
            </div>
            <span className="text-amber-600 font-semibold" style={{ fontSize: '10px' }}>{match.score}% — gap</span>
          </div>
        </div>
      ))}
    </div>
  )
}

// ─── Agent Recommendation Card ────────────────────────────────────────────────

function AgentRecommendationCard({ agentName, agentId, segment, solvedNeeds, partialNeeds, navigate, agentPool = [] }) {
  const [view, setView] = useState(null)  // null | 'flow' | 'detail'
  const color = SEG_COLOR[segment] || '#718096'

  const handleMouseEnter = () => { if (view !== 'detail') setView('flow') }
  const handleMouseLeave = () => { if (view === 'flow') setView(null) }
  const handleClick = () => setView(v => v === 'detail' ? null : 'detail')

  // Build a lookup from the fetched pool — keyed by id
  const agentById = Object.fromEntries(agentPool.map(a => [a.id, a]))

  return (
    <div
      className="card overflow-visible relative"
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
      style={{ borderLeft: `3px solid ${color}` }}
    >
      {/* Header — always visible */}
      <button
        onClick={handleClick}
        className="w-full flex items-center justify-between px-4 py-3 text-left"
      >
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-lg flex items-center justify-center" style={{ background: `${color}18` }}>
            <Bot size={15} style={{ color }} />
          </div>
          <div>
            <p className="text-sm font-semibold text-[#1A2340]">{agentName}</p>
            <div className="flex items-center gap-2.5 mt-0.5">
              {solvedNeeds.length > 0 && (
                <span className="flex items-center gap-1 text-xs text-emerald-600 font-medium">
                  <CheckCircle size={10} /> {solvedNeeds.length} fully solved
                </span>
              )}
              {partialNeeds.length > 0 && (
                <span className="flex items-center gap-1 text-xs text-amber-600 font-medium">
                  <AlertCircle size={10} /> {partialNeeds.length} partial match
                </span>
              )}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-1.5 text-xs text-[#9BA8BA]">
          <span>{view === 'detail' ? 'Collapse' : 'Click for detail'}</span>
          <ChevronDown size={13} className={`transition-transform duration-200 ${view === 'detail' ? 'rotate-180' : ''}`} />
        </div>
      </button>

      {/* Flowchart — shown on hover, hidden when detail open */}
      <AnimatePresence>
        {view === 'flow' && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden border-t border-[#E2E8F0]"
          >
            <div className="px-4 py-3 bg-[#F7F9FF]">
              <div className="flex items-center mb-3">
                <p className="text-xs font-semibold text-[#4A5568] flex items-center gap-1.5">
                  <GitBranch size={11} /> Agent Coverage Map
                </p>
              </div>
              <div className="flex justify-center overflow-x-auto">
                <AgentFlowChart
                  agentName={agentName}
                  segment={segment}
                  solvedNeeds={solvedNeeds}
                  partialNeeds={partialNeeds}
                />
              </div>
              <div className="flex items-center gap-4 mt-2.5 pt-2 border-t border-[#E2E8F0]">
                <span className="flex items-center gap-1 text-xs text-[#718096]">
                  <span className="inline-block w-5 h-px bg-emerald-400 rounded" /> Fully solved
                </span>
                <span className="flex items-center gap-1 text-xs text-[#718096]">
                  <span className="inline-block w-5 h-0.5 border-t-2 border-dashed border-amber-400" /> Partial match
                </span>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Detail view — shown on click */}
      <AnimatePresence>
        {view === 'detail' && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden border-t border-[#E2E8F0]"
          >
            <div className="px-4 py-3 space-y-2">

              {/* Fully solved needs */}
              {solvedNeeds.length > 0 && (
                <div>
                  <p className="text-xs font-semibold text-emerald-600 uppercase tracking-wider mb-2 flex items-center gap-1">
                    <CheckCircle size={10} /> Fully solved by this agent
                  </p>
                  {solvedNeeds.map(({ need, match }) => (
                    <div key={need.id} className="flex items-start gap-2.5 px-3 py-2.5 rounded-xl bg-emerald-50 border border-emerald-200 mb-1.5">
                      <CheckCircle size={12} className="text-emerald-500 mt-0.5 flex-shrink-0" />
                      <div className="flex-1 min-w-0">
                        <p className="text-xs font-semibold text-[#1A2340]">{need.label}</p>
                        <p className="text-xs text-emerald-700 mt-0.5 leading-relaxed">{match.solves}</p>
                      </div>
                      <span className="text-xs font-bold text-emerald-600 flex-shrink-0">{match.score}%</span>
                    </div>
                  ))}
                </div>
              )}

              {/* Partial needs */}
              {partialNeeds.length > 0 && (
                <div>
                  <p className="text-xs font-semibold text-amber-600 uppercase tracking-wider mb-2 flex items-center gap-1">
                    <AlertCircle size={10} /> Partially covered — gaps remain
                  </p>
                  {partialNeeds.map(({ need, match }) => (
                    <div key={need.id} className="px-3 py-2.5 rounded-xl bg-amber-50 border border-amber-200 mb-1.5">
                      <div className="flex items-start gap-2.5">
                        <AlertCircle size={12} className="text-amber-500 mt-0.5 flex-shrink-0" />
                        <div className="flex-1 min-w-0">
                          <p className="text-xs font-semibold text-[#1A2340]">{need.label}</p>
                          <p className="text-xs text-amber-700 mt-0.5">{match.solves}</p>
                          <p className="text-xs text-red-500 mt-0.5">⚠ Gap: {match.gap}</p>
                        </div>
                        <span className="text-xs font-bold text-amber-600 flex-shrink-0">{match.score}%</span>
                      </div>
                      <button
                        onClick={(e) => { e.stopPropagation(); navigate('/builder', { state: { template: agentById[agentId] || { name: agentName } } }) }}
                        className="mt-2 w-full flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold text-amber-700 bg-white border border-amber-300 hover:bg-amber-100 transition-all"
                      >
                        <Edit3 size={10} /> Modify this agent to fill the gap →
                      </button>
                    </div>
                  ))}
                </div>
              )}

              {/* Actions */}
              <div className="flex gap-2 pt-1">
                <button
                  onClick={(e) => { e.stopPropagation(); navigate('/merchant') }}
                  className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-semibold text-white transition-all"
                  style={{ background: color }}
                >
                  <Play size={11} /> Deploy Now
                </button>
                <button
                  onClick={(e) => { e.stopPropagation(); navigate('/builder', { state: { template: agentById[agentId] } }) }}
                  className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium text-[#4A5568] bg-white border border-[#E2E8F0] hover:bg-[#F7F8FA] transition-all"
                >
                  <Edit3 size={11} /> Open in Builder
                </button>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

// ─── Intelligent Workflow Visual Components ──────────────────────────────────

const NODE_STYLE = {
  full:    { ring: '#10B981', bg: '#F0FDF4', text: '#065F46', label: 'Ready'       },
  partial: { ring: '#F59E0B', bg: '#FFFBEB', text: '#92400E', label: 'Needs Work'  },
  none:    { ring: '#EF4444', bg: '#FEF2F2', text: '#991B1B', label: 'Build Needed'},
}

function WorkflowGraphNode({ step, index, isSelected, onClick }) {
  const st = NODE_STYLE[step.status] || NODE_STYLE.none
  return (
    <motion.button
      initial={{ opacity: 0, scale: 0.75 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ delay: index * 0.1 }}
      onClick={onClick}
      className="flex flex-col items-center gap-1.5 focus:outline-none group"
    >
      {/* Circle node */}
      <div
        className="w-14 h-14 rounded-full flex items-center justify-center border-4 transition-all duration-200 shadow-sm group-hover:shadow-lg"
        style={{
          borderColor: st.ring,
          background: isSelected ? st.ring : '#fff',
          transform: isSelected ? 'scale(1.12)' : 'scale(1)',
        }}
      >
        <span className="text-sm font-extrabold" style={{ color: isSelected ? '#fff' : st.ring }}>
          {index + 1}
        </span>
      </div>
      {/* Agent name */}
      <p className="text-center text-xs font-semibold text-[#1A2340] max-w-[76px] leading-tight line-clamp-2" title={step.agent_name || 'New Agent'}>
        {step.agent_name || 'New Agent'}
      </p>
      {/* Status pill */}
      <span className="text-xs font-semibold px-2 py-0.5 rounded-full whitespace-nowrap" style={{ background: st.bg, color: st.text, border: `1px solid ${st.ring}40` }}>
        {st.label}
      </span>
    </motion.button>
  )
}

function WorkflowFlowDiagram({ result, navigate }) {
  const [selectedIndex, setSelectedIndex] = useState(null)

  if (!result) return null
  if (!result.chain?.length) return null

  const chain        = result.chain
  const selectedStep = selectedIndex !== null ? chain[selectedIndex] : null

  const fullCount    = chain.filter(s => s.status === 'full').length
  const partialCount = chain.filter(s => s.status === 'partial').length
  const noneCount    = chain.filter(s => s.status === 'none').length

  const scenarioColor  = noneCount > 0 ? '#EF4444' : partialCount > 0 ? '#F59E0B' : '#10B981'
  const scenarioBg     = noneCount > 0 ? '#FEF2F2' : partialCount > 0 ? '#FFFBEB' : '#F0FDF4'
  const scenarioBorder = noneCount > 0 ? '#FECACA' : partialCount > 0 ? '#FDE68A' : '#BBF7D0'
  const ScenarioIcon   = noneCount > 0 ? XCircle   : partialCount > 0 ? AlertCircle : CheckCircle
  const scenarioLabel  = noneCount > 0 ? 'Build Required' : partialCount > 0 ? 'Partial Match — Needs Work' : 'Workflow Ready to Deploy'

  return (
    <motion.div
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35 }}
      className="rounded-2xl border-2 overflow-hidden bg-white"
      style={{ borderColor: scenarioBorder }}
    >
      {/* ── Status banner ── */}
      <div className="px-4 py-3 flex items-start gap-2.5" style={{ background: scenarioBg }}>
        <ScenarioIcon size={16} style={{ color: scenarioColor }} className="mt-0.5 flex-shrink-0" />
        <div className="flex-1 min-w-0">
          <p className="text-sm font-bold" style={{ color: scenarioColor }}>{scenarioLabel}</p>
          {result.summary && (
            <p className="text-xs text-[#718096] mt-0.5 leading-relaxed">{result.summary}</p>
          )}
          <div className="flex items-center gap-3 mt-2">
            {fullCount    > 0 && <span className="flex items-center gap-1 text-xs font-semibold text-emerald-600"><CheckCircle size={9}/> {fullCount} ready</span>}
            {partialCount > 0 && <span className="flex items-center gap-1 text-xs font-semibold text-amber-600"><AlertCircle size={9}/> {partialCount} partial</span>}
            {noneCount    > 0 && <span className="flex items-center gap-1 text-xs font-semibold text-red-500"><XCircle size={9}/> {noneCount} missing</span>}
          </div>
        </div>
      </div>

      {/* ── Horizontal node graph ── */}
      <div className="px-4 py-5 bg-white border-t" style={{ borderColor: scenarioBorder }}>
        <p className="text-xs font-bold text-[#9BA8BA] uppercase tracking-wider mb-4 flex items-center gap-1.5">
          <GitMerge size={11} /> Agent Chain — tap a node to inspect
        </p>
        <div className="flex items-start overflow-x-auto pb-2 gap-0">
          {chain.map((step, i) => (
            <div key={step.step} className="flex items-center flex-shrink-0">
              <WorkflowGraphNode
                step={step}
                index={i}
                isSelected={selectedIndex === i}
                onClick={() => setSelectedIndex(selectedIndex === i ? null : i)}
              />
              {i < chain.length - 1 && (
                <div className="flex items-center mx-2 mb-9 flex-shrink-0">
                  <div className="w-5 h-0.5 bg-[#CBD5E0]" />
                  <div style={{
                    borderLeft:   '6px solid #CBD5E0',
                    borderTop:    '4px solid transparent',
                    borderBottom: '4px solid transparent',
                    width: 0, height: 0,
                  }} />
                </div>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* ── Step summary list ── */}
      <div className="px-4 py-3 bg-[#F7F8FA] border-t" style={{ borderColor: '#E2E8F0' }}>
        <p className="text-xs font-bold text-[#9BA8BA] uppercase tracking-wider mb-2">Workflow Steps</p>
        <div className="space-y-2">
          {chain.map((step, i) => {
            const st = NODE_STYLE[step.status] || NODE_STYLE.none
            return (
              <div key={step.step} className="flex items-start gap-2">
                <span
                  className="mt-0.5 w-4 h-4 rounded-full flex-shrink-0 flex items-center justify-center text-white font-bold"
                  style={{ background: st.ring, fontSize: 9 }}
                >
                  {i + 1}
                </span>
                <p className="text-xs leading-relaxed">
                  <span className="font-semibold text-[#1A2340]">{step.agent_name || `Step ${i + 1}`}</span>
                  <span className="text-[#9BA8BA]"> — {step.description}</span>
                </p>
              </div>
            )
          })}
        </div>
      </div>

      {/* ── Selected node detail panel ── */}
      <AnimatePresence>
        {selectedStep && (
          <motion.div
            key={selectedIndex}
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.22 }}
            className="overflow-hidden border-t-2"
            style={{ borderColor: NODE_STYLE[selectedStep.status]?.ring || '#CBD5E0' }}
          >
            <div className="px-4 py-4 bg-white space-y-3">
              {/* Panel header */}
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span
                    className="w-5 h-5 rounded-full flex items-center justify-center text-white font-bold text-xs flex-shrink-0"
                    style={{ background: NODE_STYLE[selectedStep.status]?.ring || '#CBD5E0' }}
                  >
                    {selectedStep.step}
                  </span>
                  <p className="text-sm font-bold text-[#1A2340]">{selectedStep.title}</p>
                </div>
                {selectedStep.match_score > 0 && (
                  <span className="text-xs font-semibold text-[#9BA8BA]">{selectedStep.match_score}% match</span>
                )}
              </div>

              {/* Covers */}
              {selectedStep.covers && (
                <div className="rounded-lg bg-emerald-50 border border-emerald-200 px-3 py-2">
                  <p className="text-xs font-bold text-emerald-700 uppercase tracking-wider mb-1">Covers</p>
                  <p className="text-xs text-emerald-800 leading-relaxed">✓ {selectedStep.covers}</p>
                </div>
              )}

              {/* Gap */}
              {selectedStep.gap && (
                <div className="rounded-lg bg-amber-50 border border-amber-200 px-3 py-2">
                  <p className="text-xs font-bold text-amber-700 uppercase tracking-wider mb-1">Gap</p>
                  <p className="text-xs text-amber-800 leading-relaxed">⚠ {selectedStep.gap}</p>
                </div>
              )}

              {/* Suggestion */}
              {selectedStep.suggestion && (
                <div className="rounded-lg bg-blue-50 border border-blue-200 px-3 py-2">
                  <p className="text-xs font-bold text-blue-700 uppercase tracking-wider mb-1">Suggestion</p>
                  <p className="text-xs text-blue-800 leading-relaxed">→ {selectedStep.suggestion}</p>
                </div>
              )}

              {/* Tools */}
              {(selectedStep.tools || []).length > 0 && (
                <div>
                  <p className="text-xs font-bold text-[#9BA8BA] uppercase tracking-wider mb-1.5">Tools</p>
                  <div className="flex flex-wrap gap-1">
                    {selectedStep.tools.map(t => (
                      <span key={t} className="text-xs px-2 py-0.5 rounded-md bg-[#F0F4FF] border border-[#C7D2FE] text-[#4338CA] font-medium">{t}</span>
                    ))}
                  </div>
                </div>
              )}

              {/* Action: partial → modify */}
              {selectedStep.status === 'partial' && (
                <button
                  onClick={() => navigate('/builder', {
                    state: {
                      prefill: {
                        name:        selectedStep.agent_name,
                        description: selectedStep.agent_role,
                        suggestion:  selectedStep.suggestion,
                        gap:         selectedStep.gap,
                        tools:       selectedStep.tools,
                      },
                    },
                  })}
                  className="w-full flex items-center justify-center gap-2 py-2.5 rounded-xl text-sm font-bold text-white hover:opacity-90 transition-all"
                  style={{ background: '#F59E0B' }}
                >
                  <Edit3 size={14} /> Modify Agent / Add Tool
                </button>
              )}

              {/* Action: none → build from scratch */}
              {selectedStep.status === 'none' && (
                <button
                  onClick={() => navigate('/builder', {
                    state: {
                      prefill: {
                        name:        selectedStep.title,
                        description: selectedStep.description,
                        suggestion:  selectedStep.suggestion,
                      },
                    },
                  })}
                  className="w-full flex items-center justify-center gap-2 py-2.5 rounded-xl text-sm font-bold text-white hover:opacity-90 transition-all"
                  style={{ background: '#EF4444' }}
                >
                  <Plus size={14} /> Build from Scratch
                </button>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* ── Deploy action (all ready) ── */}
      {noneCount === 0 && partialCount === 0 && (
        <div className="px-4 py-3 border-t bg-emerald-50 flex gap-2" style={{ borderColor: '#BBF7D0' }}>
          <button
            className="flex-1 flex items-center justify-center gap-2 py-2.5 rounded-xl text-sm font-bold text-white hover:opacity-90 transition-all"
            style={{ background: '#10B981' }}
          >
            <Play size={14} /> Deploy Full Workflow
          </button>
        </div>
      )}

      {/* ── Spec doc (full build needed) ── */}
      {result.spec_doc && (
        <div className="px-4 py-4 border-t bg-red-50 space-y-3" style={{ borderColor: '#FECACA' }}>
          <p className="text-xs font-bold text-red-600 uppercase tracking-wider flex items-center gap-1.5">
            <FileText size={11} /> Velox Build Specification
          </p>
          <div className="rounded-xl border border-red-200 bg-white p-3 space-y-2">
            <p className="text-sm font-bold text-[#1A2340]">{result.spec_doc.title}</p>
            <p className="text-xs text-[#718096] leading-relaxed">{result.spec_doc.problem}</p>
            {(result.spec_doc.steps_to_build || []).map((s, idx) => (
              <div key={idx} className="rounded-lg border border-[#E2E8F0] p-2.5 mt-2">
                <div className="flex items-center justify-between mb-1">
                  <p className="text-xs font-bold text-[#1A2340]">Step {s.step} — {s.agent_name}</p>
                  {s.estimated_roi && <span className="text-xs text-emerald-600 font-semibold">💰 {s.estimated_roi}</span>}
                </div>
                <p className="text-xs text-[#718096] mb-1.5">{s.role}</p>
                <div className="flex flex-wrap gap-1">
                  {(s.tools || []).map(t => (
                    <span key={t} className="text-xs px-1.5 py-0.5 rounded bg-red-50 border border-red-200 text-red-600 font-medium">{t}</span>
                  ))}
                </div>
              </div>
            ))}
          </div>
          {result.spec_doc.next_step && (
            <p className="text-xs text-[#9BA8BA] italic leading-relaxed">{result.spec_doc.next_step}</p>
          )}
          <button
            onClick={() => navigate('/agent-builder', { state: { prefill: result.spec_doc } })}
            className="w-full flex items-center justify-center gap-2 py-2.5 rounded-xl text-sm font-bold text-white hover:opacity-90 transition-all"
            style={{ background: '#C8102E' }}
          >
            <ArrowRight size={14} /> Open Velox Platform &amp; Build from Scratch
          </button>
        </div>
      )}
    </motion.div>
  )
}

// ─── Pipeline sub-components ─────────────────────────────────────────────────

function PipelineTriggerNode({ label }) {
  return (
    <div className="flex items-center gap-3 px-4 py-3 rounded-2xl border-2 border-dashed border-violet-300 bg-violet-50">
      <div className="w-9 h-9 rounded-xl bg-violet-100 flex items-center justify-center flex-shrink-0">
        <Zap size={17} className="text-violet-600" />
      </div>
      <div>
        <p className="text-[10px] font-bold uppercase tracking-widest text-violet-500 mb-0.5">Trigger</p>
        <p className="text-sm font-semibold text-[#1A2340]">{label}</p>
      </div>
    </div>
  )
}

function PipelineArrow() {
  return (
    <div className="flex justify-center py-1">
      <div className="flex flex-col items-center">
        <div className="w-px h-5" style={{ background: 'linear-gradient(to bottom, #CBD5E0, #C8102E88)' }} />
        <div style={{
          width: 0, height: 0,
          borderLeft: '5px solid transparent',
          borderRight: '5px solid transparent',
          borderTop: '6px solid #C8102E',
          opacity: 0.55,
        }} />
      </div>
    </div>
  )
}

function PipelineOutputNode({ label }) {
  return (
    <div className="flex items-center gap-3 px-4 py-3 rounded-2xl border-2 border-emerald-300 bg-emerald-50">
      <div className="w-9 h-9 rounded-xl bg-emerald-100 flex items-center justify-center flex-shrink-0">
        <CheckCircle size={17} className="text-emerald-600" />
      </div>
      <div>
        <p className="text-[10px] font-bold uppercase tracking-widest text-emerald-500 mb-0.5">Output</p>
        <p className="text-sm font-semibold text-[#1A2340]">{label}</p>
      </div>
    </div>
  )
}

const STEP_STATUS = {
  full:    { color: '#10B981', bg: '#F0FDF4', border: '#BBF7D0', pill: '#D1FAE5', label: 'Ready',        btnBg: '#10B981', btnText: 'View in Discover Hub'    },
  partial: { color: '#F59E0B', bg: '#FFFBEB', border: '#FDE68A', pill: '#FEF3C7', label: 'Needs Work',   btnBg: '#F59E0B', btnText: 'Build in Collaboration' },
  none:    { color: '#C8102E', bg: '#FEF2F2', border: '#FECACA', pill: '#FEE2E2', label: 'Build Needed', btnBg: '#C8102E', btnText: 'Build Agent'             },
}

// ─── Generate a structured agent spec from a workflow step ────────────────────
function generateAgentSpec(step) {
  const name  = step.agent_name || step.title || 'New Agent'
  const role  = step.agent_role || step.description || `Automation agent for the ${name} step`
  const tools = step.tools || []
  const toolLines = tools.length
    ? tools.map(t => `  - ${t}`).join('\n')
    : '  - (to be configured in Velox)'

  const responsibilities = [
    step.covers       ? `  - ${step.covers}` : `  - Process all incoming requests for this workflow step`,
    step.gap          ? `  - Fill the identified gap: ${step.gap}` : null,
    step.suggestion   ? `  - ${step.suggestion}` : null,
    '  - Maintain a complete audit trail for every action taken',
    '  - Escalate unresolvable exceptions to the human oversight team immediately',
  ].filter(Boolean).join('\n')

  const systemPrompt = `You are ${name}, an intelligent automation agent in the Deluxe Corporation DLX_AGENTIC_OS platform.

ROLE
${role}

RESPONSIBILITIES
${responsibilities}

TOOLS
${toolLines}

GUARDRAILS
  - Never perform irreversible actions without explicit human approval
  - Always log actor, timestamp, and rationale for every decision
  - On uncertainty — pause, flag, and surface to a human reviewer
  - Adhere to all applicable data privacy and compliance requirements

PIPELINE CONTEXT
  This agent is Step ${step.step || '?'} in a multi-agent workflow analysed by Nova AI.
  Expected output: ${step.covers || 'Processed result forwarded to the next pipeline step'}`

  return { name, description: step.description || step.covers || '', role, systemPrompt: systemPrompt.trim(), tools, model: 'claude-sonnet-4-6' }
}

// ─── Agent Spec Modal (shown before entering Velox) ──────────────────────────
function AgentSpecModal({ step, index, onClose, onOpenInVelox }) {
  const spec = generateAgentSpec(step)
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ backdropFilter: 'blur(8px)', background: 'rgba(10,18,40,0.72)' }}
      onClick={e => e.target === e.currentTarget && onClose()}
    >
      <motion.div
        initial={{ opacity: 0, scale: 0.95, y: 20 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.95, y: 20 }}
        transition={{ duration: 0.22, ease: 'easeOut' }}
        className="bg-white rounded-2xl w-full max-w-2xl flex flex-col overflow-hidden"
        style={{ maxHeight: '88vh', boxShadow: '0 30px 80px rgba(0,0,0,0.45)' }}
      >
        {/* ── Header ── */}
        <div className="px-6 py-5 flex-shrink-0"
          style={{ background: 'linear-gradient(135deg,#1A2340 0%,#2D3A5C 100%)' }}>
          <div className="flex items-start justify-between gap-4">
            <div className="flex items-start gap-3 flex-1 min-w-0">
              <div className="w-10 h-10 rounded-xl bg-white/10 flex items-center justify-center flex-shrink-0">
                <Terminal size={17} className="text-white" />
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-1 flex-wrap">
                  <span className="text-xs font-bold uppercase tracking-widest text-white/40">Step {index + 1} · Agent Spec</span>
                  <span className="px-2 py-0.5 rounded-full text-xs font-bold bg-red-500/20 text-red-300 border border-red-500/30">Build Needed</span>
                </div>
                <p className="text-white font-bold text-lg leading-snug">{spec.name}</p>
                {spec.role && <p className="text-white/45 text-xs mt-1 leading-relaxed line-clamp-2">{spec.role}</p>}
              </div>
            </div>
            <button onClick={onClose}
              className="w-7 h-7 rounded-lg bg-white/10 hover:bg-white/20 flex items-center justify-center flex-shrink-0 transition-all mt-0.5">
              <X size={13} className="text-white" />
            </button>
          </div>
        </div>

        {/* ── Scrollable body ── */}
        <div className="flex-1 overflow-y-auto px-6 py-5 space-y-5">

          {/* Tools */}
          {spec.tools.length > 0 && (
            <div>
              <p className="text-xs font-bold uppercase tracking-widest text-[#9BA8BA] mb-2.5 flex items-center gap-1.5">
                <Zap size={10} /> Authorised Tools
              </p>
              <div className="flex flex-wrap gap-1.5">
                {spec.tools.map(t => (
                  <span key={t} className="px-2.5 py-1 rounded-lg text-xs font-mono font-semibold bg-[#EFF6FF] border border-[#BFDBFE] text-[#1D4ED8]">{t}</span>
                ))}
              </div>
            </div>
          )}

          {/* System Prompt — dark code editor style */}
          <div>
            <p className="text-xs font-bold uppercase tracking-widest text-[#9BA8BA] mb-2.5 flex items-center gap-1.5">
              <Terminal size={10} /> System Prompt
            </p>
            <div className="rounded-xl overflow-hidden" style={{ background: '#0F172A', border: '1px solid #1E293B' }}>
              {/* Window chrome */}
              <div className="flex items-center gap-2 px-4 py-2.5" style={{ borderBottom: '1px solid #1E293B' }}>
                <div className="flex gap-1.5">
                  <div className="w-2.5 h-2.5 rounded-full bg-[#FF5F56]" />
                  <div className="w-2.5 h-2.5 rounded-full bg-[#FFBD2E]" />
                  <div className="w-2.5 h-2.5 rounded-full bg-[#27C93F]" />
                </div>
                <span className="text-xs font-mono" style={{ color: 'rgba(255,255,255,0.2)' }}>system_prompt.txt</span>
              </div>
              <pre className="px-4 py-4 text-xs leading-relaxed overflow-auto whitespace-pre-wrap font-mono"
                style={{ color: '#94A3B8', maxHeight: '240px' }}>
                {spec.systemPrompt}
              </pre>
            </div>
          </div>

          {/* Recommended model */}
          <div className="flex items-center gap-3 px-4 py-3 rounded-xl bg-[#F7F8FA] border border-[#E2E8F0]">
            <Cpu size={14} className="text-[#8B5CF6] flex-shrink-0" />
            <div>
              <p className="text-xs font-semibold text-[#1A2340]">Recommended Model</p>
              <p className="text-xs text-[#718096] mt-0.5">
                <span className="font-mono text-[#4338CA]">claude-sonnet-4-6</span>
                {' '}— optimal balance of speed &amp; reasoning for automation
              </p>
            </div>
          </div>
        </div>

        {/* ── Footer ── */}
        <div className="px-6 py-4 border-t border-[#E2E8F0] flex items-center justify-between flex-shrink-0"
          style={{ background: '#F7F8FA', borderRadius: '0 0 1rem 1rem' }}>
          <p className="text-xs text-[#718096]">This spec is pre-filled — you can refine before deploying.</p>
          <div className="flex items-center gap-2">
            <button onClick={onClose}
              className="px-4 py-2 rounded-lg text-xs font-medium text-[#4A5568] bg-white border border-[#E2E8F0] hover:bg-[#F7F8FA] transition-all">
              Close
            </button>
            <button
              onClick={() => onOpenInVelox(spec)}
              className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-bold text-white transition-all hover:opacity-90 active:scale-95"
              style={{ background: 'linear-gradient(135deg,#C8102E 0%,#9B0D24 100%)' }}
            >
              <Terminal size={12} /> Open in Builder →
            </button>
          </div>
        </div>
      </motion.div>
    </div>
  )
}

// ─── Agent Pipeline Card ──────────────────────────────────────────────────────
function AgentPipelineCard({ step, index, navigate, isBuilt, isSkipped, onOpenSpec, onSkip }) {
  const s = STEP_STATUS[step.status] || STEP_STATUS.none

  // ── Built state ──
  if (isBuilt) {
    return (
      <motion.div
        initial={{ opacity: 0 }} animate={{ opacity: 1 }}
        transition={{ duration: 0.22 }}
        className="rounded-2xl border-2 overflow-hidden border-emerald-300"
      >
        <div className="h-1 bg-emerald-500" />
        <div className="px-4 py-3 flex items-center gap-3 bg-emerald-50">
          <div className="w-9 h-9 rounded-full bg-white flex items-center justify-center flex-shrink-0 shadow-sm"
            style={{ border: '2.5px solid #10B981' }}>
            <CheckCircle size={16} className="text-emerald-500" />
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <p className="text-sm font-bold text-[#1A2340] truncate">{step.agent_name || step.title}</p>
              <span className="flex-shrink-0 px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-200 text-emerald-800">✓ Built</span>
            </div>
            <p className="text-xs text-emerald-700 mt-0.5">Agent configured &amp; saved to this workflow</p>
          </div>
        </div>
      </motion.div>
    )
  }

  // ── Skipped state ──
  if (isSkipped) {
    return (
      <motion.div
        initial={{ opacity: 0 }} animate={{ opacity: 0.55 }}
        transition={{ duration: 0.22 }}
        className="rounded-2xl border-2 overflow-hidden border-[#E2E8F0]"
      >
        <div className="h-1 bg-[#CBD5E0]" />
        <div className="px-4 py-3 flex items-center gap-3">
          <div className="w-9 h-9 rounded-full bg-[#F7F8FA] flex items-center justify-center flex-shrink-0"
            style={{ border: '2.5px solid #CBD5E0' }}>
            <span className="text-sm font-black text-[#9BA8BA]">{index + 1}</span>
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <p className="text-sm font-bold text-[#9BA8BA] truncate">{step.agent_name || step.title}</p>
              <span className="flex-shrink-0 px-2 py-0.5 rounded-full text-[10px] font-bold bg-[#F0F2F5] text-[#9BA8BA]">Skipped</span>
            </div>
            <p className="text-xs text-[#CBD5E0] mt-0.5">This step will not be built</p>
          </div>
        </div>
      </motion.div>
    )
  }

  // ── Active / normal state ──
  const handleAction = () => {
    if (step.status === 'full') {
      navigate('/agent-pool')
    } else {
      onOpenSpec(step, index)
    }
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.08, duration: 0.28 }}
      className="rounded-2xl border-2 overflow-hidden"
      style={{ borderColor: s.border }}
    >
      {/* Colour accent bar */}
      <div className="h-1" style={{ background: s.color }} />

      {/* Card header */}
      <div className="px-4 py-3 flex items-center gap-3" style={{ background: s.bg }}>
        <div
          className="w-9 h-9 rounded-full bg-white flex items-center justify-center flex-shrink-0 shadow-sm font-black text-sm"
          style={{ border: `2.5px solid ${s.color}`, color: s.color }}
        >
          {index + 1}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <p className="text-sm font-bold text-[#1A2340] truncate">{step.agent_name || step.title}</p>
            <span className="flex-shrink-0 px-2 py-0.5 rounded-full text-[10px] font-bold"
              style={{ color: s.color, background: s.pill }}>
              {s.label}
            </span>
          </div>
          {step.description && (
            <p className="text-xs text-[#718096] mt-0.5 line-clamp-2 leading-relaxed">{step.description}</p>
          )}
        </div>
      </div>

      {/* Card body */}
      <div className="px-4 pb-4 pt-3 bg-white space-y-2.5">
        {/* Tools */}
        {(step.tools || []).length > 0 && (
          <div className="flex flex-wrap gap-1">
            {step.tools.map(t => (
              <span key={t} className="px-2 py-0.5 rounded-md text-[10px] font-mono bg-[#F0F4FF] border border-[#C7D2FE] text-[#4338CA]">{t}</span>
            ))}
          </div>
        )}
        {step.covers && (
          <div className="px-3 py-2 rounded-xl bg-emerald-50 border border-emerald-200">
            <p className="text-xs text-emerald-700 leading-relaxed">✓ {step.covers}</p>
          </div>
        )}
        {step.gap && (
          <div className="px-3 py-2 rounded-xl bg-amber-50 border border-amber-200">
            <p className="text-xs text-amber-700 leading-relaxed">⚠ Gap: {step.gap}</p>
          </div>
        )}
        {step.suggestion && step.status !== 'full' && (
          <div className="px-3 py-2 rounded-xl bg-blue-50 border border-blue-200">
            <p className="text-xs text-blue-700 leading-relaxed">→ {step.suggestion}</p>
          </div>
        )}

        {/* CTA row — action button + skip */}
        <div className="flex items-center gap-2">
          <button
            onClick={handleAction}
            className="flex-1 flex items-center justify-center gap-1.5 py-2.5 rounded-xl text-xs font-bold text-white transition-all hover:opacity-90 active:scale-[0.98]"
            style={{ background: s.btnBg }}
          >
            {step.status === 'none'    && <><Terminal size={11} /> {s.btnText}</>}
            {step.status === 'partial' && <><Edit3    size={11} /> {s.btnText}</>}
            {step.status === 'full'    && <><Play     size={11} /> {s.btnText}</>}
          </button>
          {/* Skip option — only for steps that need building */}
          {step.status !== 'full' && (
            <button
              onClick={() => onSkip(index)}
              className="px-3 py-2.5 rounded-xl text-xs font-medium text-[#9BA8BA] border border-[#E2E8F0] hover:text-[#718096] hover:border-[#CBD5E0] bg-white transition-all whitespace-nowrap"
            >
              Skip
            </button>
          )}
        </div>
      </div>
    </motion.div>
  )
}

function ToolNode({ step, index, isExpanded, onToggle, navigate, isBuilt, isSkipped, onOpenSpec, onSkip }) {
  const s = STEP_STATUS[step.status] || STEP_STATUS.none
  const tools = step.tools || []

  if (isBuilt) {
    return (
      <div className="flex flex-col items-center flex-shrink-0">
        <motion.div
          onClick={onToggle}
          whileHover={{ y: -2 }}
          whileTap={{ scale: 0.97 }}
          className="cursor-pointer w-36 rounded-xl border-2 p-3 transition-all border-emerald-300"
          style={{ background: '#F0FDF4', boxShadow: isExpanded ? '0 4px 16px rgba(16,185,129,0.22)' : undefined }}
        >
          <div className="flex items-center justify-between mb-2">
            <div className="w-6 h-6 rounded-full flex items-center justify-center bg-white shadow-sm" style={{ border: '2px solid #10B981' }}>
              <CheckCircle size={12} className="text-emerald-500" />
            </div>
            <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-full text-emerald-700 bg-emerald-200">Built</span>
          </div>
          <div className="flex flex-wrap gap-1 mb-2 min-h-[28px]">
            {tools.slice(0, 3).map(t => (
              <span key={t} className="px-1.5 py-0.5 rounded text-[9px] font-mono bg-[#F0F4FF] border border-[#C7D2FE] text-[#4338CA] truncate max-w-full">{t}</span>
            ))}
            {tools.length > 3 && <span className="text-[9px] text-[#9BA8BA]">+{tools.length - 3}</span>}
          </div>
          <div className="flex items-center justify-center gap-1 text-[10px] font-medium text-emerald-600">
            <ChevronDown size={9} style={{ transform: isExpanded ? 'rotate(180deg)' : 'rotate(0deg)', transition: 'transform 0.2s' }} />
            {isExpanded ? 'Hide' : 'Details'}
          </div>
        </motion.div>

        <AnimatePresence>
          {isExpanded && (
            <motion.div
              initial={{ opacity: 0, height: 0, marginTop: 0 }}
              animate={{ opacity: 1, height: 'auto', marginTop: 8 }}
              exit={{ opacity: 0, height: 0, marginTop: 0 }}
              className="w-56 rounded-xl border-2 overflow-hidden bg-white z-10 relative border-emerald-300"
              style={{ boxShadow: '0 8px 24px rgba(16,185,129,0.18)' }}
            >
              <div className="h-1 bg-emerald-500" />
              <div className="p-3 space-y-2">
                <div className="flex items-center gap-1.5">
                  <CheckCircle size={12} className="text-emerald-500 flex-shrink-0" />
                  <p className="text-sm font-bold text-[#1A2340]">{step.agent_name || step.title}</p>
                </div>
                {step.description && <p className="text-xs text-[#718096] leading-relaxed">{step.description}</p>}
                <div className="flex flex-wrap gap-1">
                  {tools.map(t => (
                    <span key={t} className="px-2 py-0.5 rounded-md text-[10px] font-mono bg-[#F0F4FF] border border-[#C7D2FE] text-[#4338CA]">{t}</span>
                  ))}
                </div>
                {step.covers && (
                  <div className="px-2.5 py-1.5 rounded-lg bg-emerald-50 border border-emerald-200">
                    <p className="text-xs text-emerald-700 leading-relaxed">✓ {step.covers}</p>
                  </div>
                )}
                <div className="pt-0.5">
                  <button
                    onClick={e => { e.stopPropagation(); navigate('/agent-pool', { state: { highlightAgent: step.agent_name } }) }}
                    className="w-full flex items-center justify-center gap-1 py-2 rounded-lg text-xs font-bold text-white transition-all hover:opacity-90 active:scale-[0.98]"
                    style={{ background: '#10B981' }}
                  >
                    <Play size={10} /> View in Discover Hub
                  </button>
                </div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    )
  }

  if (isSkipped) {
    return (
      <div className="flex flex-col items-center flex-shrink-0 w-36 opacity-50">
        <div className="w-full rounded-xl border-2 p-3 text-center border-[#E2E8F0]">
          <div className="w-7 h-7 rounded-full mx-auto mb-2 bg-[#F7F8FA] flex items-center justify-center font-black text-xs text-[#9BA8BA]" style={{ border: '2px solid #CBD5E0' }}>
            {index + 1}
          </div>
          <p className="text-xs font-bold text-[#9BA8BA] leading-tight truncate px-1">{step.agent_name || step.title}</p>
          <span className="text-[9px] font-bold text-[#9BA8BA] bg-[#F0F2F5] px-1.5 py-0.5 rounded-full mt-1.5 inline-block">Skipped</span>
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col items-center flex-shrink-0">
      <motion.div
        onClick={onToggle}
        whileHover={{ y: -2 }}
        whileTap={{ scale: 0.97 }}
        className="cursor-pointer w-36 rounded-xl border-2 p-3 transition-all"
        style={{ borderColor: isExpanded ? s.color : s.border, background: s.bg, boxShadow: isExpanded ? `0 4px 16px ${s.color}22` : undefined }}
      >
        <div className="flex items-center justify-between mb-2">
          <div className="w-6 h-6 rounded-full flex items-center justify-center font-black text-xs flex-shrink-0" style={{ border: `2px solid ${s.color}`, color: s.color }}>
            {index + 1}
          </div>
          {(step.callCount ?? 1) > 1 ? (
            <span className="flex items-center gap-0.5 text-[9px] font-bold px-1.5 py-0.5 rounded-full bg-violet-100 text-violet-700 border border-violet-300">
              <RotateCcw size={7} /> ×{step.callCount}
            </span>
          ) : (
            <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-full truncate max-w-[70px]" style={{ color: s.color, background: s.pill }}>{s.label}</span>
          )}
        </div>
        <div className="flex flex-wrap gap-1 mb-2 min-h-[28px]">
          {tools.slice(0, 3).map(t => (
            <span key={t} className="px-1.5 py-0.5 rounded text-[9px] font-mono bg-[#F0F4FF] border border-[#C7D2FE] text-[#4338CA] truncate max-w-full">{t}</span>
          ))}
          {tools.length > 3 && <span className="text-[9px] text-[#9BA8BA]">+{tools.length - 3}</span>}
        </div>
        <div className="flex items-center justify-center gap-1 text-[10px] font-medium" style={{ color: s.color }}>
          <ChevronDown size={9} style={{ transform: isExpanded ? 'rotate(180deg)' : 'rotate(0deg)', transition: 'transform 0.2s' }} />
          {isExpanded ? 'Hide' : 'Details'}
        </div>
      </motion.div>

      <AnimatePresence>
        {isExpanded && (
          <motion.div
            initial={{ opacity: 0, height: 0, marginTop: 0 }}
            animate={{ opacity: 1, height: 'auto', marginTop: 8 }}
            exit={{ opacity: 0, height: 0, marginTop: 0 }}
            className="w-56 rounded-xl border-2 overflow-hidden bg-white z-10 relative"
            style={{ borderColor: s.border, boxShadow: `0 8px 24px ${s.color}18` }}
          >
            <div className="h-1" style={{ background: s.color }} />
            <div className="p-3 space-y-2">
              <div className="flex items-center gap-2 flex-wrap">
                <p className="text-sm font-bold text-[#1A2340]">{step.agent_name || step.title}</p>
                {(step.callCount ?? 1) > 1 && (
                  <span className="flex items-center gap-1 px-2 py-0.5 rounded-full bg-violet-100 border border-violet-200 text-[10px] font-bold text-violet-700">
                    <RotateCcw size={8} /> Called {step.callCount}× — tools combined
                  </span>
                )}
              </div>
              {step.description && <p className="text-xs text-[#718096] leading-relaxed">{step.description}</p>}
              <div className="flex flex-wrap gap-1">
                {tools.map(t => (
                  <span key={t} className="px-2 py-0.5 rounded-md text-[10px] font-mono bg-[#F0F4FF] border border-[#C7D2FE] text-[#4338CA]">{t}</span>
                ))}
              </div>
              {step.covers && (
                <div className="px-2.5 py-1.5 rounded-lg bg-emerald-50 border border-emerald-200">
                  <p className="text-xs text-emerald-700 leading-relaxed">✓ {step.covers}</p>
                </div>
              )}
              {step.gap && (
                <div className="px-2.5 py-1.5 rounded-lg bg-amber-50 border border-amber-200">
                  <p className="text-xs text-amber-700 leading-relaxed">⚠ Gap: {step.gap}</p>
                </div>
              )}
              {step.suggestion && step.status !== 'full' && (
                <div className="px-2.5 py-1.5 rounded-lg bg-blue-50 border border-blue-200">
                  <p className="text-xs text-blue-700 leading-relaxed">→ {step.suggestion}</p>
                </div>
              )}
              <div className="flex flex-col gap-1.5 pt-0.5">
                <button
                  onClick={e => { e.stopPropagation(); step.status === 'full' ? navigate('/agent-pool', { state: { highlightAgent: step.agent_name } }) : onOpenSpec(step, index) }}
                  className="w-full flex items-center justify-center gap-1 py-2 rounded-lg text-xs font-bold text-white transition-all hover:opacity-90 active:scale-[0.98]"
                  style={{ background: s.btnBg }}
                >
                  {step.status === 'none'    && <><Terminal size={10} /> {s.btnText}</>}
                  {step.status === 'partial' && <><Edit3 size={10} />    {s.btnText}</>}
                  {step.status === 'full'    && <><Play size={10} />     {s.btnText}</>}
                </button>
                {step.status !== 'full' && (
                  <button
                    onClick={e => { e.stopPropagation(); onSkip(index) }}
                    className="w-full py-1.5 rounded-lg text-xs font-medium text-[#9BA8BA] border border-[#E2E8F0] hover:text-[#718096] hover:border-[#CBD5E0] bg-white transition-all"
                  >
                    Skip
                  </button>
                )}
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

function WorkflowPipelineView({ result, navigate, builtSteps, skippedSteps, onOpenSpec, onSkip, onSubmitWorkflow }) {
  const [expandedIndex, setExpandedIndex] = useState(null)
  if (!result) return null

  const chain        = result.chain || []
  const noneCount    = chain.filter(s => s.status === 'none').length
  const partialCount = chain.filter(s => s.status === 'partial').length
  const readyCount   = chain.filter(s => s.status === 'full').length

  // Complexity check: only show pipeline for 2+ steps
  if (chain.length < 2) {
    if (chain.length === 1) {
      const step = chain[0]
      const s = STEP_STATUS[step.status] || STEP_STATUS.none
      return (
        <div className="space-y-3">
          {result.summary && (
            <div className="px-4 py-3 rounded-2xl border" style={{ background: 'linear-gradient(135deg,#1A2340 0%,#2D3A5C 100%)', borderColor: '#2D3A5C' }}>
              <p className="text-sm font-semibold text-white leading-relaxed">{result.summary}</p>
            </div>
          )}
          <div className="rounded-2xl border-2 overflow-hidden" style={{ borderColor: s.border }}>
            <div className="h-1" style={{ background: s.color }} />
            <div className="px-4 py-3" style={{ background: s.bg }}>
              <p className="text-sm font-bold text-[#1A2340]">{step.agent_name || step.title}</p>
              {step.description && <p className="text-xs text-[#718096] mt-1 leading-relaxed">{step.description}</p>}
              <div className="flex flex-wrap gap-1 mt-2">
                {(step.tools || []).map(t => <span key={t} className="px-2 py-0.5 rounded-md text-[10px] font-mono bg-[#F0F4FF] border border-[#C7D2FE] text-[#4338CA]">{t}</span>)}
              </div>
            </div>
          </div>
          <p className="text-xs text-[#9BA8BA] text-center italic px-2">This task only needs a single agent — no multi-step workflow required.</p>
        </div>
      )
    }
    return null
  }

  // Deduplicate: same agent appears only once — keep worst-case status, union tools
  const displayChain = (() => {
    const seen = {}
    const merged = []
    chain.forEach((step, i) => {
      const key = step.agent_name || step.title || String(i)
      if (seen[key] !== undefined) {
        const ex = merged[seen[key]]
        ex.tools = [...new Set([...ex.tools, ...(step.tools || [])])]
        ex.indices.push(i)
        const covers = [ex.covers, step.covers].filter(Boolean)
        if (covers.length) ex.covers = [...new Set(covers)].join(' · ')
        const pri = { none: 0, partial: 1, full: 2 }
        if ((pri[step.status] ?? 0) < (pri[ex.status] ?? 2)) ex.status = step.status
      } else {
        seen[key] = merged.length
        merged.push({ ...step, tools: [...(step.tools || [])], indices: [i], callCount: 1 })
      }
    })
    return merged
  })()

  const bs = builtSteps  ?? {}
  const ss = skippedSteps ?? {}
  const buildNeededIndices = displayChain.flatMap(step => step.status !== 'full' ? step.indices : [])
  const builtOrSkippedCount = buildNeededIndices.filter(i => bs[i] || ss[i]).length
  const allComplete = buildNeededIndices.length > 0 && builtOrSkippedCount === buildNeededIndices.length

  const toggleExpand = (i) => setExpandedIndex(prev => prev === i ? null : i)

  return (
    <div className="space-y-0">
      {result.summary && (
        <motion.div
          initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }}
          className="mb-4 px-4 py-3 rounded-2xl border"
          style={{ background: 'linear-gradient(135deg,#1A2340 0%,#2D3A5C 100%)', borderColor: '#2D3A5C' }}
        >
          <p className="text-sm font-semibold text-white leading-relaxed">{result.summary}</p>
          <div className="flex items-center gap-3 mt-2 flex-wrap">
            {readyCount   > 0 && <span className="flex items-center gap-1 text-xs text-emerald-400 font-medium"><CheckCircle size={10}/> {readyCount} ready</span>}
            {partialCount > 0 && <span className="flex items-center gap-1 text-xs text-amber-400 font-medium"><AlertCircle size={10}/> {partialCount} need work</span>}
            {noneCount    > 0 && <span className="flex items-center gap-1 text-xs text-red-400 font-medium"><XCircle size={10}/> {noneCount} to build</span>}
            {builtOrSkippedCount > 0 && buildNeededIndices.length > 0 && (
              <span className="flex items-center gap-1 text-xs text-violet-400 font-medium">
                <CheckCircle size={10}/> {builtOrSkippedCount}/{buildNeededIndices.length} configured
              </span>
            )}
          </div>
        </motion.div>
      )}


      {Object.keys(bs).some(i => bs[i]) && (
        <div className="mb-4 flex items-start gap-2 px-3 py-2.5 rounded-xl bg-amber-50 border border-amber-300">
          <AlertCircle size={14} className="text-amber-600 flex-shrink-0 mt-0.5" />
          <div>
            <p className="text-xs font-semibold text-amber-700">Some agents in this workflow are under review</p>
            <p className="text-xs text-amber-600 mt-0.5">Proceed with caution — recently built agents need compliance sign-off before production.</p>
          </div>
        </div>
      )}

      {/* Horizontal pipeline — always horizontal, never vertical */}
      <div className="overflow-x-auto pb-2">
        <div className="flex items-start min-w-max px-1 py-1">
          {/* Trigger */}
          <div className="flex flex-col items-center flex-shrink-0 w-28 self-start">
            <div className="w-full px-2 py-2.5 rounded-xl border-2 border-dashed text-center" style={{ borderColor: '#A78BFA', background: '#F5F3FF' }}>
              <div className="w-7 h-7 rounded-lg bg-[#EDE9FE] flex items-center justify-center mx-auto mb-1.5">
                <Zap size={14} className="text-violet-600" />
              </div>
              <p className="text-[9px] font-bold uppercase tracking-wider text-violet-500 mb-0.5">Trigger</p>
              <p className="text-xs font-semibold text-[#1A2340] leading-tight">{result.trigger || 'Automation Triggered'}</p>
            </div>
          </div>

          {displayChain.map((step, di) => (
            <div key={step.step || di} className="flex items-start">
              <div className="flex items-center self-start mt-7 flex-shrink-0 mx-1.5">
                <div className="w-8 h-px" style={{ background: 'linear-gradient(to right,#CBD5E0,#C8102E66)' }} />
                <div style={{ width:0, height:0, borderTop:'4px solid transparent', borderBottom:'4px solid transparent', borderLeft:'5px solid #C8102E', opacity:0.6 }} />
              </div>
              <ToolNode
                step={step}
                index={step.indices[0]}
                isExpanded={expandedIndex === di}
                onToggle={() => toggleExpand(di)}
                navigate={navigate}
                isBuilt={step.indices.every(i => !!bs[i])}
                isSkipped={step.indices.every(i => !!ss[i])}
                onOpenSpec={onOpenSpec}
                onSkip={() => step.indices.forEach(i => onSkip(i))}
              />
            </div>
          ))}

          {/* Output */}
          <div className="flex items-start">
            <div className="flex items-center self-start mt-7 flex-shrink-0 mx-1.5">
              <div className="w-8 h-px" style={{ background: 'linear-gradient(to right,#CBD5E0,#10B98166)' }} />
              <div style={{ width:0, height:0, borderTop:'4px solid transparent', borderBottom:'4px solid transparent', borderLeft:'5px solid #10B981', opacity:0.6 }} />
            </div>
            <div className="flex flex-col items-center flex-shrink-0 w-28 self-start">
              <div className="w-full px-2 py-2.5 rounded-xl border-2 text-center border-emerald-300 bg-emerald-50">
                <div className="w-7 h-7 rounded-lg bg-emerald-100 flex items-center justify-center mx-auto mb-1.5">
                  <CheckCircle size={14} className="text-emerald-600" />
                </div>
                <p className="text-[9px] font-bold uppercase tracking-wider text-emerald-500 mb-0.5">Output</p>
                <p className="text-xs font-semibold text-[#1A2340] leading-tight">{result.output || 'Process Complete'}</p>
              </div>
            </div>
          </div>
        </div>

      </div>

      <AnimatePresence>
        {allComplete && (
          <motion.div
            initial={{ opacity: 0, y: 14 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 14 }}
            transition={{ duration: 0.3, delay: 0.1 }}
            className="mt-5 rounded-2xl border-2 border-emerald-300 overflow-hidden"
            style={{ background: 'linear-gradient(135deg,#F0FDF4 0%,#DCFCE7 100%)' }}
          >
            <div className="p-5 text-center">
              <div className="w-12 h-12 rounded-full bg-white border-2 border-emerald-300 flex items-center justify-center mx-auto mb-3 shadow-sm">
                <CheckCircle size={22} className="text-emerald-500" />
              </div>
              <p className="text-base font-bold text-emerald-800 mb-1">All agents configured! 🎉</p>
              <p className="text-xs text-emerald-600 leading-relaxed max-w-xs mx-auto mb-4">
                Every step in this workflow has been built or marked as optional. Submit it for stakeholder approval.
              </p>
              <button
                onClick={onSubmitWorkflow}
                className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl text-sm font-bold text-white transition-all hover:opacity-90 active:scale-95"
                style={{ background: 'linear-gradient(135deg,#10B981 0%,#059669 100%)', boxShadow: '0 4px 14px rgba(16,185,129,0.35)' }}
              >
                <Send size={14} /> Send for Approval →
              </button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

// ─── Main component ──────────────────────────────────────────────────────────

// Deluxe segment name → short key used for color coding
const SEGMENT_KEY = {
  'Merchant Services': 'merchant',
  'Print':             'print',
  'B2B Payments':      'b2b',
  'Data Solutions':    'data',
}

export default function ImagineStudio() {
  const navigate   = useNavigate()
  const location   = useLocation()
  const { addToast, addConfluencePage, novaSession, setNovaSession, clearNovaSession, addPendingWorkflow } = useStore()

  // Restore from Zustand store (persists while tab is open, cleared on refresh)
  const s = novaSession

  const [input, setInput]       = useState('')
  const [phase, setPhase]       = useState(s?.phase ?? 0)
  const [messages, setMessages] = useState(() => s?.messages ?? [])
  const [typing, setTyping]     = useState(false)
  const [buildSugg, setBuildSugg] = useState(s?.buildSugg ?? null)
  const [showDocPreview, setShowDocPreview] = useState(false)
  const [docTitle, setDocTitle]   = useState('')
  const [docDesc,  setDocDesc]    = useState('')
  const [docSysP,  setDocSysP]    = useState('')
  const [novaResults, setNovaResults] = useState(s?.novaResults ?? null)
  const [novaSummary, setNovaSummary] = useState(s?.novaSummary ?? null)
  const [answeredIds, setAnsweredIds] = useState(() => new Set(s?.answeredIds ?? []))
  const [sessionId, setSessionId] = useState(() => s?.sessionId ?? crypto.randomUUID())
  const [agentPool, setAgentPool] = useState([])
  const [workflowResult, setWorkflowResult] = useState(s?.workflowResult ?? null)
  const [workflowLoading, setWorkflowLoading] = useState(false)
  const [specModal,    setSpecModal]    = useState(null)            // { step, index } | null
  const [builtSteps,   setBuiltSteps]   = useState(s?.builtSteps   ?? {})  // { [idx]: true }
  const [skippedSteps, setSkippedSteps] = useState(s?.skippedSteps ?? {})  // { [idx]: true }
  const [chatCollapsed, setChatCollapsed] = useState(false)
  const [pipelineCollapsed, setPipelineCollapsed] = useState(false)

  const chatEndRef   = useRef(null)
  const inputRef     = useRef(null)
  const problemRef   = useRef('')   // captures the first user message for workflow analysis

  // Fetch agent pool from backend — single source of truth
  useEffect(() => {
    fetch('/api/nova/agents')
      .then(r => r.ok ? r.json() : [])
      .then(data => setAgentPool(Array.isArray(data) ? data : []))
      .catch(() => {})
  }, [])

  // Auto-save session to Zustand on every state change
  useEffect(() => {
    if (phase === 0 && messages.length === 0) return // nothing to save
    setNovaSession({
      phase,
      messages,
      buildSugg,
      novaResults,
      novaSummary,
      sessionId,
      answeredIds:  [...answeredIds],
      workflowResult,
      builtSteps,
      skippedSteps,
    })
  }, [phase, messages, buildSugg, novaResults, novaSummary, workflowResult, builtSteps, skippedSteps])

  // Collapse pipeline during conversation; collapse chat + expand pipeline when analysis lands
  useEffect(() => {
    if (phase >= 1 && phase < 5) {
      setPipelineCollapsed(true)
      setChatCollapsed(false)
    } else if (phase === 5) {
      setChatCollapsed(true)
      setPipelineCollapsed(false)
    } else if (phase === 0) {
      setChatCollapsed(false)
      setPipelineCollapsed(false)
    }
  }, [phase])

  // Keep pipeline expanded once workflow result arrives
  useEffect(() => {
    if (workflowResult && !workflowLoading) {
      setChatCollapsed(true)
      setPipelineCollapsed(false)
    }
  }, [workflowResult, workflowLoading])

  // Detect return from AgentBuilder — mark the step as built
  useEffect(() => {
    const built = location.state?.builtWorkflowStep
    if (built !== undefined && built !== null) {
      setBuiltSteps(prev => ({ ...prev, [built]: true }))
      // Clear the navigation state so it doesn't re-fire on next render
      navigate(location.pathname, { replace: true, state: {} })
    }
  }, [location.state?.builtWorkflowStep])

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, typing])

  const pushMsg = (role, content, extra = {}) => {
    setMessages(prev => [...prev, { role, content, ...extra, id: Date.now() + Math.random() }])
  }

  // Strip markdown code fences and try to extract {"type":"report",...} JSON
  const tryParseReport = (msg) => {
    if (!msg) return null
    const stripped = msg.replace(/```(?:json)?/gi, '').replace(/```/g, '').trim()
    const idx = stripped.indexOf('"type"')
    if (idx === -1) return null
    let start = stripped.lastIndexOf('{', idx)
    if (start === -1) return null
    let depth = 0, end = -1
    for (let i = start; i < stripped.length; i++) {
      if (stripped[i] === '{') depth++
      else if (stripped[i] === '}') { depth--; if (depth === 0) { end = i + 1; break } }
    }
    if (end === -1) return null
    try {
      const obj = JSON.parse(stripped.slice(start, end))
      return obj?.type === 'report' ? obj : null
    } catch { return null }
  }

  const callNova = async (displayMessage, apiMessage = null) => {
    setTyping(true)
    try {
      const res = await fetch('/api/nova/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sessionId, message: apiMessage ?? displayMessage }),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      setTyping(false)

      // Frontend fallback: if server didn't parse the report, try extracting it from raw message
      if (!data.needsAnalysis && data.message) {
        const parsed = tryParseReport(data.message)
        if (parsed) {
          data.needsAnalysis = parsed
          data.message = null
        }
      }

      if (data.needsAnalysis) {
        // Store results immediately, then animate through phases
        setNovaResults(data.needsAnalysis)
        if (data.message) {
          setNovaSummary(data.message)
          pushMsg('nova', data.message)
        }
        setPhase(3)
        setTimeout(() => setPhase(4), 1200)
        // Auto-trigger workflow pipeline analysis
        setTimeout(() => {
          const problem = problemRef.current || displayMessage
          if (problem) analyzeWorkflow(problem)
        }, 1600)
        setTimeout(() => {
          setPhase(5)
          const na = data.needsAnalysis
          const isNew = na.type === 'report'
          const solved   = isNew ? na.needs.filter(n => n.coverage === 'full').length    : Object.values(na.matches || {}).filter(m => m.status === 'solved').length
          const partial  = isNew ? na.needs.filter(n => n.coverage === 'partial').length : Object.values(na.matches || {}).filter(m => m.status === 'partial').length
          const unsolved = isNew ? na.needs.filter(n => n.coverage === 'none').length    : Object.values(na.matches || {}).filter(m => m.status === 'unsolved').length
          const total    = solved + partial + unsolved
          const scanMsg  = unsolved === total
            ? `Scan complete. No existing agents cover this — **${unsolved} gap${unsolved !== 1 ? 's' : ''} identified**. See the build recommendation on the right →`
            : `Scan complete. Found **${solved} need${solved !== 1 ? 's' : ''} fully covered**, **${partial} partial match${partial !== 1 ? 'es' : ''}**, and **${unsolved} gap${unsolved !== 1 ? 's' : ''}** to build. See the full recommendation on the right →`
          pushMsg('nova', scanMsg)

          if (data.needsAnalysis.build_recommendation || data.needsAnalysis.buildSuggestion) {
            setBuildSugg(data.needsAnalysis.build_recommendation || data.needsAnalysis.buildSuggestion)
          }
        }, 2400)

      } else if (data.options?.length) {
        pushMsg('nova', data.message, { isQuestion: true, options: data.options })
        if (phase < 5) setPhase(1)
      } else {
        pushMsg('nova', data.message || "Let me ask you a few more questions to refine my analysis.")
        if (phase < 5) setPhase(1)
      }
    } catch (err) {
      setTyping(false)
      pushMsg('nova', "I'm having trouble connecting to my analysis engine. Please ensure the Nova service is running (`python server/agent.py`) and AWS Bedrock is configured.")
      setPhase(1)
    }
  }

  const handleSubmit = async () => {
    if (!input.trim() || typing) return
    const text = input.trim()
    setInput('')
    pushMsg('user', text)
    if (phase === 0) {
      setPhase(1)
      problemRef.current = text   // capture the initial problem statement
    }

    // After results are shown, append a silent re-analysis instruction so Nova
    // always produces a fresh <needs_analysis> block with the updated context.
    const apiMsg = phase >= 5
      ? `${text}\n\n[The user has provided additional context after seeing the analysis. Re-evaluate all conversation history plus this new information and produce a complete updated <needs_analysis> block. Do not skip the analysis — always output the full JSON block.]`
      : null

    await callNova(text, apiMsg)
  }

  const handleQuickFind = async () => {
    if (typing) return
    // Mid-conversation with no new input — use existing context to generate report now
    if (!input.trim()) {
      if (phase < 1) return
      const skipMsg = "I have enough context. Please generate the report now."
      pushMsg('user', skipMsg)
      const apiMsg = `[SKIP — generate report now using everything discussed so far. Do NOT ask any more questions. Immediately produce the complete report JSON based on the full conversation context.]`
      await callNova(skipMsg, apiMsg)
      return
    }
    // Has input — send it and skip remaining questions
    const text = input.trim()
    setInput('')
    pushMsg('user', text)
    setPhase(1)
    const apiMsg = `${text}\n\n[SKIP CONVERSATION — The user wants instant results. Do NOT ask any questions. Based solely on this input, immediately produce a complete report JSON with your best-effort analysis. Make reasonable assumptions about scale and segment from the context given.]`
    await callNova(text, apiMsg)
  }

  const handleDownloadReport = () => {
    if (!sc && !workflowResult) return
    const isNew = sc?.type === 'report'
    const now   = new Date()
    const date  = now.toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' })
    const time  = now.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })

    // ── Data helpers ──
    const poolByName = Object.fromEntries(agentPool.map(a => [a.name, a]))
    const needs      = isNew ? (sc?.needs || []) : []
    const chain      = workflowResult?.chain || []

    const fullCount    = chain.filter(s => s.status === 'full').length
    const partialCount = chain.filter(s => s.status === 'partial').length
    const noneCount    = chain.filter(s => s.status === 'none').length

    const coveredNeeds  = needs.filter(n => n.coverage === 'full').length
    const partialNeeds  = needs.filter(n => n.coverage === 'partial').length
    const gapNeeds      = needs.filter(n => n.coverage === 'none').length
    const roiText       = isNew ? (sc?.roi || '') : ''

    const reportTitle   = workflowResult?.summary || sc?.summary || sc?.needsTitle || 'Workflow Discovery Report'
    const initialProblem = messages.find(m => m.role === 'user')?.content || reportTitle

    // ── Pipeline HTML ──
    const statusStyle = {
      full:    { bg: '#F0FDF4', border: '#86EFAC', dot: '#22C55E', label: 'Ready',        text: '#166534' },
      partial: { bg: '#FFFBEB', border: '#FCD34D', dot: '#F59E0B', label: 'Needs Work',   text: '#92400E' },
      none:    { bg: '#FEF2F2', border: '#FCA5A5', dot: '#EF4444', label: 'Build Needed', text: '#991B1B' },
    }

    const pipelineHtml = chain.length ? `
      <div style="display:flex;flex-direction:column;align-items:flex-start;gap:0;max-width:620px;margin:0 auto;">
        <!-- Trigger node -->
        <div style="display:flex;align-items:center;gap:12px;padding:12px 18px;border-radius:14px;border:2px dashed #A78BFA;background:#F5F3FF;width:100%;margin-bottom:0;">
          <div style="width:36px;height:36px;border-radius:10px;background:#EDE9FE;display:flex;align-items:center;justify-content:center;font-size:18px;flex-shrink:0;">⚡</div>
          <div>
            <div style="font-size:9px;font-weight:800;letter-spacing:.12em;text-transform:uppercase;color:#7C3AED;margin-bottom:2px;">Trigger</div>
            <div style="font-size:13px;font-weight:700;color:#1A2340;">${workflowResult?.trigger || 'Automation Triggered'}</div>
          </div>
        </div>
        ${chain.map((step, i) => {
          const st = statusStyle[step.status] || statusStyle.none
          const toolsHtml = (step.tools || []).map(t => `<span style="display:inline-block;background:#EFF6FF;color:#1D4ED8;border:1px solid #BFDBFE;padding:1px 7px;border-radius:5px;font-size:10px;font-family:monospace;margin:2px 2px 0 0;">${t}</span>`).join('')
          return `
          <!-- Arrow -->
          <div style="display:flex;justify-content:flex-start;padding-left:22px;height:24px;align-items:center;">
            <div style="display:flex;flex-direction:column;align-items:center;">
              <div style="width:1px;height:14px;background:#CBD5E0;"></div>
              <div style="width:0;height:0;border-left:5px solid transparent;border-right:5px solid transparent;border-top:6px solid #CBD5E0;"></div>
            </div>
          </div>
          <!-- Agent step -->
          <div style="border:2px solid ${st.border};border-radius:14px;overflow:hidden;width:100%;">
            <div style="height:3px;background:${st.dot};"></div>
            <div style="padding:14px 18px;background:${st.bg};">
              <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;">
                <div style="width:30px;height:30px;border-radius:50%;background:white;border:2.5px solid ${st.dot};display:flex;align-items:center;justify-content:center;font-weight:900;font-size:13px;color:${st.dot};flex-shrink:0;">${i+1}</div>
                <div style="flex:1;">
                  <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
                    <span style="font-size:14px;font-weight:800;color:#1A2340;">${step.agent_name || step.title}</span>
                    <span style="font-size:10px;font-weight:700;padding:2px 8px;border-radius:99px;background:white;color:${st.text};border:1px solid ${st.border};">${st.label}</span>
                  </div>
                  <div style="font-size:12px;color:#718096;margin-top:2px;line-height:1.5;">${step.description || ''}</div>
                </div>
              </div>
              ${toolsHtml ? `<div style="margin-top:6px;">${toolsHtml}</div>` : ''}
              ${step.covers ? `<div style="margin-top:8px;padding:8px 12px;background:rgba(16,185,129,0.08);border-radius:8px;font-size:11px;color:#065F46;">✓ ${step.covers}</div>` : ''}
              ${step.gap    ? `<div style="margin-top:6px;padding:8px 12px;background:rgba(245,158,11,0.08);border-radius:8px;font-size:11px;color:#92400E;">⚠ Gap: ${step.gap}</div>` : ''}
              ${step.suggestion && step.status !== 'full' ? `<div style="margin-top:6px;padding:8px 12px;background:rgba(59,130,246,0.08);border-radius:8px;font-size:11px;color:#1D4ED8;">→ ${step.suggestion}</div>` : ''}
            </div>
          </div>`
        }).join('')}
        <!-- Arrow -->
        <div style="display:flex;justify-content:flex-start;padding-left:22px;height:24px;align-items:center;">
          <div style="display:flex;flex-direction:column;align-items:center;">
            <div style="width:1px;height:14px;background:#CBD5E0;"></div>
            <div style="width:0;height:0;border-left:5px solid transparent;border-right:5px solid transparent;border-top:6px solid #CBD5E0;"></div>
          </div>
        </div>
        <!-- Output node -->
        <div style="display:flex;align-items:center;gap:12px;padding:12px 18px;border-radius:14px;border:2px solid #86EFAC;background:#F0FDF4;width:100%;">
          <div style="width:36px;height:36px;border-radius:10px;background:#DCFCE7;display:flex;align-items:center;justify-content:center;font-size:18px;flex-shrink:0;">✅</div>
          <div>
            <div style="font-size:9px;font-weight:800;letter-spacing:.12em;text-transform:uppercase;color:#16A34A;margin-bottom:2px;">Output</div>
            <div style="font-size:13px;font-weight:700;color:#1A2340;">${workflowResult?.output || 'Process Complete — All Systems Updated'}</div>
          </div>
        </div>
      </div>` : ''

    // ── Needs table ──
    const needsRows = needs.map((n, i) => {
      const bgEven = i % 2 === 0 ? '#ffffff' : '#F9FAFB'
      const badge =
        n.coverage === 'full'    ? `<span style="background:#D1FAE5;color:#065F46;padding:2px 9px;border-radius:99px;font-size:10px;font-weight:700;">✓ Fully Covered</span>` :
        n.coverage === 'partial' ? `<span style="background:#FEF3C7;color:#92400E;padding:2px 9px;border-radius:99px;font-size:10px;font-weight:700;">⚠ Partial Match</span>` :
                                   `<span style="background:#FEE2E2;color:#991B1B;padding:2px 9px;border-radius:99px;font-size:10px;font-weight:700;">✗ Build Needed</span>`
      return `<tr style="background:${bgEven};">
        <td style="padding:11px 14px;font-weight:600;color:#1A2340;font-size:13px;">${n.need}</td>
        <td style="padding:11px 14px;font-size:12px;color:#4A5568;">${n.agent || '<span style="color:#CBD5E0;">—</span>'}</td>
        <td style="padding:11px 14px;">${badge}</td>
        <td style="padding:11px 14px;font-size:12px;color:#718096;line-height:1.55;">${n.note || ''}</td>
      </tr>`
    }).join('')

    // ── Agent spec cards ──
    const matchedNames   = [...new Set(needs.filter(n => n.agent && n.coverage !== 'none').map(n => n.agent))]
    const agentCardsHtml = matchedNames.map(name => {
      const a = poolByName[name]
      if (!a) return ''
      const tools  = (a.tools || []).map(t => `<span style="display:inline-block;background:#EFF6FF;color:#1D4ED8;border:1px solid #BFDBFE;padding:2px 8px;border-radius:6px;font-size:11px;font-weight:600;margin:2px 2px 0 0;">${t}</span>`).join('')
      const solves = (a.solves || []).map(s => `<div style="display:flex;align-items:flex-start;gap:6px;margin-bottom:5px;"><span style="color:#16A34A;font-weight:700;flex-shrink:0;">✓</span><span style="font-size:12px;color:#374151;">${s}</span></div>`).join('')
      const caps   = (a.capabilities || []).map(c => `<li style="font-size:12px;color:#4A5568;margin-bottom:4px;line-height:1.5;">${c}</li>`).join('')
      return `
      <div style="border:1px solid #E2E8F0;border-radius:12px;overflow:hidden;margin-bottom:20px;break-inside:avoid;">
        <div style="background:linear-gradient(135deg,#1A2340 0%,#2D3A5C 100%);padding:16px 20px;display:flex;align-items:center;justify-content:space-between;">
          <div>
            <div style="font-size:16px;font-weight:800;color:#ffffff;margin-bottom:3px;">${a.name}</div>
            <div style="font-size:11px;color:rgba(255,255,255,0.55);">${a.segment || ''} · v${a.version || '1.0'} · ${a.model || 'claude-sonnet-4-6'}</div>
          </div>
          <span style="background:${a.status === 'running' ? 'rgba(34,197,94,0.2)' : 'rgba(245,158,11,0.2)'};color:${a.status === 'running' ? '#4ADE80' : '#FCD34D'};padding:4px 10px;border-radius:99px;font-size:10px;font-weight:700;border:1px solid ${a.status === 'running' ? 'rgba(34,197,94,0.4)' : 'rgba(245,158,11,0.4)'};">${a.status === 'running' ? '● Live' : '● Standby'}</span>
        </div>
        <div style="padding:18px 20px;background:#ffffff;">
          <div style="font-size:9px;font-weight:800;text-transform:uppercase;letter-spacing:.1em;color:#9BA8BA;margin-bottom:5px;">Description</div>
          <p style="font-size:13px;color:#4A5568;line-height:1.65;margin-bottom:14px;">${a.description || ''}</p>
          ${caps ? `<div style="font-size:9px;font-weight:800;text-transform:uppercase;letter-spacing:.1em;color:#9BA8BA;margin-bottom:8px;">Capabilities</div><ul style="padding-left:16px;margin-bottom:14px;">${caps}</ul>` : ''}
          ${tools ? `<div style="font-size:9px;font-weight:800;text-transform:uppercase;letter-spacing:.1em;color:#9BA8BA;margin-bottom:6px;">Authorised Tools</div><div style="margin-bottom:14px;">${tools}</div>` : ''}
          ${solves ? `<div style="font-size:9px;font-weight:800;text-transform:uppercase;letter-spacing:.1em;color:#9BA8BA;margin-bottom:8px;">What It Solves</div><div>${solves}</div>` : ''}
          ${(a.successRate != null) ? `<div style="margin-top:12px;padding:10px 14px;background:#F7F8FA;border-radius:8px;display:flex;gap:24px;"><div><div style="font-size:10px;color:#9BA8BA;">Success Rate</div><div style="font-size:16px;font-weight:800;color:#1A2340;">${a.successRate}%</div></div><div><div style="font-size:10px;color:#9BA8BA;">Deployments</div><div style="font-size:16px;font-weight:800;color:#1A2340;">${a.deployments ?? '—'}</div></div></div>` : ''}
        </div>
      </div>`
    }).join('')

    // ── Gaps section ──
    const gaps = isNew ? (sc?.gaps || []) : []
    const gapNeedsList = needs.filter(n => n.coverage === 'none')
    const gapsHtml = gaps.length ? gaps.map((g, i) => `
      <div style="display:flex;gap:12px;align-items:flex-start;padding:12px 16px;background:${i%2===0?'#FEF2F2':'#FFF7F7'};border-radius:8px;margin-bottom:8px;">
        <span style="color:#EF4444;font-size:16px;flex-shrink:0;margin-top:-1px;">✗</span>
        <span style="font-size:13px;color:#1A2340;line-height:1.6;">${g}</span>
      </div>`).join('') : ''

    // ── Build recommendation ──
    const buildRec  = isNew ? sc?.build_recommendation : null
    const buildName = buildRec?.split(' — ')[0]
    const buildDesc = buildRec?.split(' — ').slice(1).join(' — ')
    const suggestedTools = [...new Set(gapNeedsList.flatMap(n => {
      const kw = n.need.toLowerCase()
      if (kw.includes('invoic') || kw.includes('reconcil')) return ['erp-read','payment-match','gl-write','exception-flag']
      if (kw.includes('fraud')  || kw.includes('detect'))   return ['txn-stream','anomaly-detect','hold-trigger']
      if (kw.includes('onboard')|| kw.includes('kyb'))      return ['kyb-verify','crm-write','email-send']
      if (kw.includes('churn')  || kw.includes('retention'))return ['crm-read','order-history','email-send']
      if (kw.includes('enrich') || kw.includes('data'))     return ['data-fetch','profile-update','signal-score']
      return ['api-read','data-write','notification-send']
    }))]

    // ── Conversation ──
    const convHtml = messages.filter(m => m.content).map(m =>
      m.role === 'user'
        ? `<div style="margin:10px 0;text-align:right;"><span style="display:inline-block;background:#C8102E;color:#fff;padding:9px 14px;border-radius:16px 16px 4px 16px;font-size:13px;max-width:78%;line-height:1.55;">${m.content}</span><div style="font-size:10px;color:#9BA8BA;margin-top:3px;">You</div></div>`
        : `<div style="margin:10px 0;display:flex;align-items:flex-start;gap:8px;"><div style="width:28px;height:28px;border-radius:50%;background:#F7F8FA;border:1px solid #E2E8F0;display:flex;align-items:center;justify-content:center;font-size:14px;flex-shrink:0;">✦</div><div><div style="font-size:10px;color:#9BA8BA;margin-bottom:3px;">Nova</div><span style="display:inline-block;background:#F7F8FA;border:1px solid #E2E8F0;color:#1A2340;padding:9px 14px;border-radius:4px 16px 16px 16px;font-size:13px;max-width:85%;line-height:1.6;">${m.content}</span></div></div>`
    ).join('')

    const html = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Nova Workflow Report — ${date}</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0;}
  body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#F4F5F7;color:#1A2340;font-size:14px;}
  .wrap{max-width:900px;margin:0 auto;padding:32px 20px 64px;}

  /* ── Cover ── */
  .cover{background:linear-gradient(135deg,#1A2340 0%,#2D3A5C 100%);border-radius:16px;padding:40px 48px;margin-bottom:28px;position:relative;overflow:hidden;}
  .cover::before{content:'';position:absolute;top:-40px;right:-40px;width:240px;height:240px;border-radius:50%;background:rgba(200,16,46,0.12);}
  .cover-eyebrow{font-size:10px;font-weight:800;letter-spacing:.18em;text-transform:uppercase;color:#C8102E;margin-bottom:12px;}
  .cover-title{font-size:26px;font-weight:800;color:#ffffff;line-height:1.3;margin-bottom:10px;max-width:600px;}
  .cover-sub{font-size:13px;color:rgba(255,255,255,0.55);line-height:1.6;margin-bottom:20px;}
  .cover-meta{display:flex;align-items:center;gap:20px;flex-wrap:wrap;border-top:1px solid rgba(255,255,255,0.12);padding-top:16px;margin-top:4px;}
  .cover-meta-item{font-size:11px;color:rgba(255,255,255,0.5);}
  .cover-meta-item strong{color:rgba(255,255,255,0.85);font-weight:600;}

  /* ── Stats strip ── */
  .stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:28px;}
  .stat{background:#fff;border:1px solid #E2E8F0;border-radius:12px;padding:16px 18px;}
  .stat-val{font-size:26px;font-weight:800;color:#1A2340;line-height:1;margin-bottom:4px;}
  .stat-label{font-size:11px;color:#9BA8BA;font-weight:600;text-transform:uppercase;letter-spacing:.05em;}
  .stat-dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:5px;}

  /* ── Cards ── */
  .card{background:#fff;border:1px solid #E2E8F0;border-radius:12px;overflow:hidden;margin-bottom:20px;}
  .card-header{padding:16px 20px;border-bottom:1px solid #F0F2F5;display:flex;align-items:center;gap:10px;}
  .card-header-icon{width:30px;height:30px;border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:15px;flex-shrink:0;}
  .card-header-title{font-size:13px;font-weight:700;color:#1A2340;}
  .card-header-sub{font-size:11px;color:#9BA8BA;}
  .card-body{padding:20px;}

  /* ── Summary ── */
  .summary{border-left:4px solid #C8102E;background:#FFF5F5;padding:14px 18px;border-radius:0 10px 10px 0;font-size:14px;line-height:1.75;color:#1A2340;font-weight:500;}

  /* ── Table ── */
  table{width:100%;border-collapse:collapse;font-size:12px;}
  thead{background:#1A2340;}
  th{padding:10px 14px;text-align:left;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:#fff;}
  td{padding:11px 14px;border-bottom:1px solid #F0F2F5;vertical-align:top;}

  /* ── Build rec ── */
  .build-rec{background:linear-gradient(135deg,#FFFBEB 0%,#FFF7E6 100%);border:1.5px solid #FDE68A;border-radius:12px;padding:20px 22px;}
  .build-eyebrow{font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:.12em;color:#D97706;margin-bottom:8px;}
  .build-name{font-size:18px;font-weight:800;color:#1A2340;margin-bottom:6px;}
  .build-desc{font-size:13px;color:#4A5568;line-height:1.65;margin-bottom:16px;}

  /* ── Next steps ── */
  .step-row{display:flex;align-items:flex-start;gap:14px;padding:12px 0;border-bottom:1px solid #F0F2F5;}
  .step-num{width:28px;height:28px;border-radius:50%;background:#1A2340;color:#fff;font-weight:800;font-size:12px;display:flex;align-items:center;justify-content:center;flex-shrink:0;margin-top:1px;}
  .step-title{font-size:13px;font-weight:700;color:#1A2340;margin-bottom:3px;}
  .step-desc{font-size:12px;color:#718096;line-height:1.55;}

  /* ── Print button ── */
  .print-btn{position:fixed;bottom:28px;right:28px;background:#C8102E;color:#fff;border:none;padding:10px 22px;border-radius:99px;font-size:13px;font-weight:700;cursor:pointer;box-shadow:0 4px 20px rgba(200,16,46,0.4);z-index:999;display:flex;align-items:center;gap:7px;}
  .print-btn:hover{background:#a50e26;}

  /* ── Footer ── */
  .footer{margin-top:40px;padding-top:16px;border-top:1px solid #E2E8F0;display:flex;align-items:center;justify-content:space-between;font-size:10px;color:#9BA8BA;}

  @media print{
    body{background:#fff;}
    .wrap{padding:0 20px;}
    .print-btn{display:none;}
    .cover{-webkit-print-color-adjust:exact;print-color-adjust:exact;}
    .card,.build-rec,.stat{break-inside:avoid;}
  }
</style>
</head>
<body>
<button class="print-btn" onclick="window.print()">🖨&nbsp; Print / Save PDF</button>
<div class="wrap">

  <!-- ── Cover ── -->
  <div class="cover">
    <div class="cover-eyebrow">DLX_AGENTIC_OS · Deluxe Corporation · Confidential</div>
    <div class="cover-title">${reportTitle}</div>
    <div class="cover-sub">${initialProblem !== reportTitle ? initialProblem : 'Multi-agent workflow discovery &amp; recommendation report generated by Nova.'}</div>
    <div class="cover-meta">
      <div class="cover-meta-item">Generated by <strong>Nova AI</strong></div>
      <div class="cover-meta-item">Date <strong>${date} · ${time}</strong></div>
      <div class="cover-meta-item">Status <strong style="color:#4ADE80;">⬤ Analysis Complete</strong></div>
      ${chain.length ? `<div class="cover-meta-item">Pipeline <strong>${chain.length} Agents</strong></div>` : ''}
    </div>
  </div>

  <!-- ── Stats strip ── -->
  <div class="stats">
    ${chain.length ? `
    <div class="stat">
      <div class="stat-val">${chain.length}</div>
      <div class="stat-label">Pipeline Steps</div>
    </div>
    <div class="stat">
      <div class="stat-val" style="color:#22C55E;">${fullCount}</div>
      <div class="stat-label"><span class="stat-dot" style="background:#22C55E;"></span>Ready</div>
    </div>
    <div class="stat">
      <div class="stat-val" style="color:#F59E0B;">${partialCount}</div>
      <div class="stat-label"><span class="stat-dot" style="background:#F59E0B;"></span>Needs Work</div>
    </div>
    <div class="stat">
      <div class="stat-val" style="color:#EF4444;">${noneCount}</div>
      <div class="stat-label"><span class="stat-dot" style="background:#EF4444;"></span>To Build</div>
    </div>` : `
    <div class="stat">
      <div class="stat-val">${needs.length}</div>
      <div class="stat-label">Needs Identified</div>
    </div>
    <div class="stat">
      <div class="stat-val" style="color:#22C55E;">${coveredNeeds}</div>
      <div class="stat-label"><span class="stat-dot" style="background:#22C55E;"></span>Fully Covered</div>
    </div>
    <div class="stat">
      <div class="stat-val" style="color:#F59E0B;">${partialNeeds}</div>
      <div class="stat-label"><span class="stat-dot" style="background:#F59E0B;"></span>Partial Match</div>
    </div>
    <div class="stat">
      <div class="stat-val" style="color:#EF4444;">${gapNeeds}</div>
      <div class="stat-label"><span class="stat-dot" style="background:#EF4444;"></span>Gaps</div>
    </div>`}
  </div>

  <!-- ── Problem Summary ── -->
  <div class="card">
    <div class="card-header">
      <div class="card-header-icon" style="background:#FEF2F2;">🎯</div>
      <div><div class="card-header-title">Problem Summary</div><div class="card-header-sub">Identified by Nova from your discovery session</div></div>
    </div>
    <div class="card-body">
      <div class="summary">${sc?.summary || reportTitle}</div>
      ${roiText ? `<div style="margin-top:14px;display:inline-flex;align-items:center;gap:8px;background:#F0FDF4;border:1px solid #BBF7D0;padding:8px 14px;border-radius:8px;">
        <span style="font-size:16px;">💰</span>
        <div><div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:#166534;margin-bottom:1px;">Estimated ROI</div>
        <div style="font-size:14px;font-weight:700;color:#1A2340;">${roiText}</div></div>
      </div>` : ''}
    </div>
  </div>

  <!-- ── Workflow Pipeline ── -->
  ${chain.length ? `
  <div class="card">
    <div class="card-header">
      <div class="card-header-icon" style="background:#F5F3FF;">🔀</div>
      <div><div class="card-header-title">Workflow Pipeline</div><div class="card-header-sub">${chain.length}-step multi-agent chain · Generated by Nova</div></div>
    </div>
    <div class="card-body">
      ${pipelineHtml}
    </div>
  </div>` : ''}

  <!-- ── Needs Analysis Table ── -->
  ${needsRows ? `
  <div class="card">
    <div class="card-header">
      <div class="card-header-icon" style="background:#EFF6FF;">📋</div>
      <div><div class="card-header-title">Needs Analysis &amp; Agent Coverage</div><div class="card-header-sub">${needs.length} needs identified · mapped against ${agentPool.length} registered agents</div></div>
    </div>
    <div style="overflow-x:auto;">
      <table>
        <thead><tr><th>Business Need</th><th>Matched Agent</th><th>Coverage</th><th>Notes</th></tr></thead>
        <tbody>${needsRows}</tbody>
      </table>
    </div>
  </div>` : ''}

  <!-- ── Matched Agent Specs ── -->
  ${agentCardsHtml ? `
  <div class="card">
    <div class="card-header">
      <div class="card-header-icon" style="background:#F0FDF4;">🤖</div>
      <div><div class="card-header-title">Matched Agent Specifications</div><div class="card-header-sub">Existing agents that cover identified needs</div></div>
    </div>
    <div class="card-body">
      ${agentCardsHtml}
    </div>
  </div>` : ''}

  <!-- ── Gaps ── -->
  ${gapsHtml ? `
  <div class="card">
    <div class="card-header">
      <div class="card-header-icon" style="background:#FEF2F2;">⚠️</div>
      <div><div class="card-header-title">Identified Gaps</div><div class="card-header-sub">Capabilities missing from the current agent pool</div></div>
    </div>
    <div class="card-body">${gapsHtml}</div>
  </div>` : ''}

  <!-- ── Build Recommendation ── -->
  ${buildName ? `
  <div class="card">
    <div class="card-header">
      <div class="card-header-icon" style="background:#FFFBEB;">💡</div>
      <div><div class="card-header-title">Build Recommendation</div><div class="card-header-sub">Highest-value net-new agent to close the gap</div></div>
    </div>
    <div class="card-body">
      <div class="build-rec">
        <div class="build-eyebrow">Recommended Agent</div>
        <div class="build-name">${buildName}</div>
        <div class="build-desc">${buildDesc || ''}</div>
        ${gapNeedsList.length ? `
          <div style="font-size:9px;font-weight:800;text-transform:uppercase;letter-spacing:.1em;color:#D97706;margin-bottom:8px;">Needs This Agent Must Solve</div>
          ${gapNeedsList.map(n => `<div style="display:flex;gap:8px;margin-bottom:6px;"><span style="color:#D97706;font-weight:700;">→</span><span style="font-size:13px;color:#1A2340;"><strong>${n.need}</strong>${n.note ? ' — ' + n.note : ''}</span></div>`).join('')}
        ` : ''}
        ${suggestedTools.length ? `
          <div style="font-size:9px;font-weight:800;text-transform:uppercase;letter-spacing:.1em;color:#D97706;margin:14px 0 8px;">Suggested Tools</div>
          <div>${suggestedTools.map(t => `<span style="display:inline-block;background:#FEF3C7;color:#92400E;border:1px solid #FDE68A;padding:2px 8px;border-radius:6px;font-size:11px;font-family:monospace;font-weight:600;margin:2px 2px 0 0;">${t}</span>`).join('')}</div>
        ` : ''}
      </div>
    </div>
  </div>` : ''}

  <!-- ── Next Steps ── -->
  <div class="card">
    <div class="card-header">
      <div class="card-header-icon" style="background:#F0F9FF;">🚀</div>
      <div><div class="card-header-title">Recommended Next Steps</div><div class="card-header-sub">Actions to move from discovery to production</div></div>
    </div>
    <div class="card-body">
      ${[
        noneCount > 0 || gapNeedsList.length > 0 ? { title: 'Build missing agents in Velox', desc: `${noneCount || gapNeedsList.length} agent${(noneCount || gapNeedsList.length) !== 1 ? 's' : ''} need to be built. Open Agent Builder, use the recommended spec above, and deploy to staging first.` } : null,
        partialCount > 0 || partialNeeds > 0 ? { title: 'Modify partial-match agents', desc: 'Extend existing agents with the missing tools or capabilities identified in the gap analysis above.' } : null,
        { title: 'Send workflow for approval', desc: 'Once all agents are built and validated in staging, submit the full workflow pipeline for stakeholder sign-off.' },
        { title: 'Monitor via Governance Registry', desc: 'After go-live, track SLA, audit logs, and exception rates in the Governance Registry dashboard.' },
      ].filter(Boolean).map((s, i) => `
        <div class="step-row" style="${i === 0 ? 'border-top:1px solid #F0F2F5;' : ''}">
          <div class="step-num">${i + 1}</div>
          <div><div class="step-title">${s.title}</div><div class="step-desc">${s.desc}</div></div>
        </div>`).join('')}
    </div>
  </div>

  <!-- ── Conversation Transcript ── -->
  ${convHtml ? `
  <div class="card">
    <div class="card-header">
      <div class="card-header-icon" style="background:#F5F3FF;">💬</div>
      <div><div class="card-header-title">Discovery Conversation</div><div class="card-header-sub">Full Nova chat session transcript</div></div>
    </div>
    <div class="card-body" style="background:#FAFBFC;">${convHtml}</div>
  </div>` : ''}

  <!-- ── Footer ── -->
  <div class="footer">
    <span>DLX_AGENTIC_OS · Deluxe Corporation · Confidential &amp; Internal Use Only</span>
    <span>Nova Workflow Report · ${date}</span>
  </div>

</div>
</body>
</html>`

    const newTab = window.open()
    if (newTab) {
      newTab.document.open()
      newTab.document.write(html)
      newTab.document.close()
      newTab.document.title = `Nova Report — ${date}`
    }
  }

  const handleSendToConfluence = () => {
    if (!sc) return
    const isNew    = sc.type === 'report'
    const title    = (isNew ? sc.summary : sc.needsTitle)
      ?.replace(/\s*[\(\-—]\s*assuming[^).\n]*/gi, '')
      ?.replace(/\s*\([^)]*\)/g, '')
      ?.trim() || 'Nova Discovery Report'

    // Build description from summary + needs overview
    const needs    = isNew ? (sc.needs || []) : []
    const gaps     = needs.filter(n => n.coverage === 'none')
    const covered  = needs.filter(n => n.coverage === 'full' || n.coverage === 'partial')
    const descParts = [
      sc.summary || sc.needsTitle || '',
      covered.length  ? `Covered by existing agents: ${covered.map(n => n.need).join(', ')}.` : '',
      gaps.length     ? `Gaps requiring new agents: ${gaps.map(n => n.need).join(', ')}.` : '',
    ].filter(Boolean)
    const description = descParts.join(' ')

    // Build system prompt from gaps
    const gapNeeds = gaps.length > 0 ? gaps : needs
    const systemPrompt = `You are an AI agent designed to automate the following business requirement identified via the DLX_AGENTIC_OS Imagination Studio.

BUSINESS PROBLEM: ${title}

YOUR RESPONSIBILITIES:
${gapNeeds.map((n, i) => `  ${i + 1}. ${n.need}${n.note ? ' — ' + n.note : ''}`).join('\n')}

GUARDRAILS:
  - Never take irreversible actions without human approval
  - Log every action with timestamp and rationale
  - Escalate exceptions to the appropriate team immediately
  - Comply with all applicable data handling and compliance requirements

SOURCE: Generated from Nova Discovery session in Imagination Studio`

    const page = {
      id:      `nova-${Date.now()}`,
      title,
      space:   'Imagination Studio',
      updated: 'just now',
      fromNova: true,
      content: { description, systemPrompt },
    }

    addConfluencePage(page)
    addToast({
      type:    'success',
      title:   'Document created in Confluence',
      message: `Opening Agent Analyst to build your agent…`,
    })
    setTimeout(() => navigate('/agent-analyst'), 800)
  }

  const handleOptionClick = async (msgId, answer) => {
    setAnsweredIds(prev => new Set([...prev, msgId]))
    pushMsg('user', answer)
    await callNova(answer)
  }

  const handleBuild = (need) => {
    navigate('/builder', { state: { prefill: { name: need.label, description: need.detail } } })
  }

  const handleModify = (need) => {
    const match    = novaResults?.matches?.[need.id]
    const template = match?.agentId ? agentPool.find(a => a.id === match.agentId) : null
    if (template) {
      navigate('/builder', { state: { template } })
    } else {
      navigate('/builder', { state: { prefill: { name: need.label, description: need.detail } } })
    }
  }

  const analyzeWorkflow = async (problem) => {
    if (!problem?.trim()) return
    setWorkflowLoading(true)
    try {
      // Build a rich context from the full conversation — not just the first message.
      // This gives Nova enough detail to create DISTINCT steps with different agents.
      const userTurns = messages
        .filter(m => m.role === 'user')
        .map(m => m.content)
        .filter(Boolean)
      const novaTurns = messages
        .filter(m => m.role === 'nova')
        .map(m => m.content)
        .filter(Boolean)

      // Combine initial problem + every Q&A exchange + the needs analysis summary
      const conversationContext = [
        `INITIAL PROBLEM: ${problem}`,
        userTurns.length > 1
          ? `\nADDITIONAL CONTEXT FROM CONVERSATION:\n` +
            userTurns.slice(1).map((u, i) => `User: ${u}`).join('\n')
          : '',
        novaSummary ? `\nNOVA ANALYSIS SUMMARY: ${novaSummary}` : '',
        novaResults?.needs?.length
          ? `\nIDENTIFIED NEEDS:\n` + novaResults.needs.map(n =>
              `- ${n.need || n.label}: coverage=${n.coverage || n.status}, note=${n.note || n.detail || ''}`
            ).join('\n')
          : '',
      ].filter(Boolean).join('')

      const res = await fetch('/api/nova/workflow/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ problem: conversationContext, session_id: sessionId }),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      setWorkflowResult(data)
    } catch (err) {
      pushMsg('nova', "I couldn't analyse the workflow right now. Please make sure the Nova service is running.")
    } finally {
      setWorkflowLoading(false)
    }
  }

  const handleReset = () => {
    fetch(`/api/nova/session/${sessionId}`, { method: 'DELETE' }).catch(() => {})
    clearNovaSession()
    setSessionId(crypto.randomUUID())
    setPhase(0); setMessages([]); setTyping(false); setBuildSugg(null); setInput('')
    setNovaResults(null); setNovaSummary(null); setAnsweredIds(new Set())
    setWorkflowResult(null)
    setBuiltSteps({}); setSkippedSteps({}); setSpecModal(null)
    problemRef.current = ''
  }

  const handleSendForApproval = () => {
    if (!workflowResult && !sc) return
    const chain   = workflowResult?.chain || []
    const wfName  = (workflowResult?.summary || novaSummary || 'Nova Pipeline').slice(0, 90)
    const payload = {
      name:        wfName,
      agentCount:  chain.length,
      chain,
      summary:     workflowResult?.summary || novaSummary || '',
      sessionId,
      builtSteps,    // { [idx]: true } — agents built via Velox
      skippedSteps,  // { [idx]: true } — agents skipped
    }
    addPendingWorkflow(payload)
    addToast({
      type:    'success',
      title:   'Workflow sent for approval ✓',
      message: `"${wfName.slice(0, 50)}…" is pending review on the homepage.`,
    })
    setTimeout(() => navigate('/dashboard'), 900)
  }

  // ── Derived: all build-needed agents are built or skipped ──
  const _buildNeededIdxs = (workflowResult?.chain || [])
    .map((step, i) => step.status !== 'full' ? i : -1)
    .filter(i => i !== -1)
  const allAgentsConfigured =
    _buildNeededIdxs.length > 0 &&
    _buildNeededIdxs.every(i => builtSteps[i] || skippedSteps[i])

  // ── Spec modal handlers ──
  const handleOpenSpec = (step, index) => setSpecModal({ step, index })

  const handleSkipStep = (index) => {
    setSkippedSteps(prev => ({ ...prev, [index]: true }))
  }

  const handleOpenInVelox = (spec) => {
    if (!specModal) return
    const stepIndex = specModal.index
    const step = specModal.step
    // Look up the original agent in the pool to get its author for collaboration
    const originalAgent = agentPool.find(a => a.name === (step.agent_name || step.title))
    const collaborationAuthor = step.status === 'partial' && originalAgent
      ? (originalAgent.author || 'DLX_AGENTIC_OS Team')
      : undefined
    setSpecModal(null)
    navigate('/builder', {
      state: {
        fromWorkflow:  true,
        workflowStep:  stepIndex + 1,
        prefill: {
          name:                spec.name,
          description:         spec.description,
          role:                spec.role,
          systemPrompt:        spec.systemPrompt,
          tools:               spec.tools,
          collaborationAuthor,
          suggestion:          spec.tools?.length ? `Recommended tools: ${spec.tools.join(', ')}` : undefined,
        },
      },
    })
  }

  const sc = novaResults
  // Support both new format {type:"report", needs:[{need,agent,coverage,note}]}
  // and legacy format {needs:[{id,label,detail}], matches:{...}}
  const isNewFormat = sc?.type === 'report'

  const solvedCount   = sc ? (isNewFormat
    ? sc.needs.filter(n => n.coverage === 'full').length
    : Object.values(sc.matches || {}).filter(m => m.status === 'solved').length) : 0
  const partialCount  = sc ? (isNewFormat
    ? sc.needs.filter(n => n.coverage === 'partial').length
    : Object.values(sc.matches || {}).filter(m => m.status === 'partial').length) : 0
  const unsolvedCount = sc ? (isNewFormat
    ? sc.needs.filter(n => n.coverage === 'none').length
    : Object.values(sc.matches || {}).filter(m => m.status === 'unsolved').length) : 0

  const agentSegMap = Object.fromEntries(
    agentPool.map(a => [a.name, SEGMENT_KEY[a.segment] || 'platform'])
  )

  const agentGroups = {}
  if (sc?.needs) {
    if (isNewFormat) {
      sc.needs.forEach((n, i) => {
        if (n.agent && n.coverage !== 'none') {
          if (!agentGroups[n.agent]) {
            agentGroups[n.agent] = {
              agentId: agentPool.find(a => a.name === n.agent)?.id || null,
              segment: agentSegMap[n.agent] || 'platform',
              solved: [], partial: [],
            }
          }
          const needObj  = { id: i, label: n.need, detail: n.note }
          const matchObj = { score: n.coverage === 'full' ? 94 : 70, solves: n.note, gap: n.coverage === 'partial' ? n.note : null }
          if (n.coverage === 'full') agentGroups[n.agent].solved.push({ need: needObj, match: matchObj })
          else agentGroups[n.agent].partial.push({ need: needObj, match: matchObj })
        }
      })
    } else if (sc.matches) {
      sc.needs.forEach(need => {
        const match = sc.matches[need.id]
        if (match?.agentName && match.status !== 'unsolved') {
          if (!agentGroups[match.agentName]) {
            agentGroups[match.agentName] = {
              agentId: match.agentId,
              segment: agentSegMap[match.agentName] || 'platform',
              solved: [], partial: [],
            }
          }
          if (match.status === 'solved') agentGroups[match.agentName].solved.push({ need, match })
          else agentGroups[match.agentName].partial.push({ need, match })
        }
      })
    }
  }

  const unsolvedNeeds = sc?.needs
    ? isNewFormat
      ? sc.needs.filter(n => n.coverage === 'none').map((n, i) => ({ id: i, label: n.need, detail: n.note }))
      : sc.needs.filter(n => sc?.matches?.[n.id]?.status === 'unsolved')
    : []

  return (
    <>
    {/* ── Agent Spec Modal ── */}
    <AnimatePresence>
      {specModal && (
        <AgentSpecModal
          step={specModal.step}
          index={specModal.index}
          onClose={() => setSpecModal(null)}
          onOpenInVelox={handleOpenInVelox}
        />
      )}
    </AnimatePresence>

    <motion.div
      initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3 }}
      className="h-[calc(100vh-120px)]"
      style={{
        display: 'grid',
        gap: '20px',
        gridTemplateColumns: pipelineCollapsed
          ? '1fr 48px'
          : chatCollapsed
            ? '48px 1fr'
            : '44% 1fr',
        transition: 'grid-template-columns 0.35s cubic-bezier(0.4, 0, 0.2, 1)',
      }}
    >
      {/* ── LEFT: Conversation panel ── */}
      <div className="card overflow-hidden relative flex flex-col min-w-0">
        {/* ── Collapsed strip ── */}
        {chatCollapsed && (
          <div className="flex flex-col items-center h-full cursor-pointer"
            style={{ background: 'linear-gradient(135deg, #1A2340, #2D3A5C)' }}
            onClick={() => setChatCollapsed(false)}
            title="Open Nova chat"
          >
            <div className="py-4 flex items-center justify-center w-full hover:bg-white/10 transition-all">
              <div className="w-8 h-8 rounded-lg bg-white/10 hover:bg-white/20 flex items-center justify-center transition-all">
                <ChevronLeft size={14} className="text-white/70" style={{ transform: 'rotate(180deg)' }} />
              </div>
            </div>
            <div className="flex-1 flex items-center justify-center">
              <p className="text-[9px] font-bold text-white/25 uppercase tracking-widest select-none"
                style={{ writingMode: 'vertical-lr', transform: 'rotate(180deg)' }}>
                Nova Chat
              </p>
            </div>
          </div>
        )}

        {/* ── Full chat (hidden when collapsed) ── */}
        <div className={`flex flex-col flex-1 overflow-hidden ${chatCollapsed ? 'hidden' : ''}`}>
        {/* Header */}
        <div className="px-5 py-4 border-b border-[#E2E8F0] flex items-center justify-between flex-shrink-0"
          style={{ background: 'linear-gradient(135deg, #1A2340, #2D3A5C)' }}>
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-white/10 flex items-center justify-center">
              <Sparkles size={16} className="text-amber-400" />
            </div>
            <div>
              <p className="text-white font-semibold text-sm">Nova</p>
              <p className="text-white/40 text-xs">Automation Discovery Intelligence</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {workflowResult && (
              <button onClick={() => setChatCollapsed(true)}
                className="w-7 h-7 rounded-lg bg-white/10 hover:bg-white/20 flex items-center justify-center transition-all"
                title="Collapse chat">
                <ChevronLeft size={13} className="text-white/60" />
              </button>
            )}
            {phase > 0 && (
              <button onClick={handleReset} className="flex items-center gap-1 text-white/40 hover:text-white/70 text-xs">
                <RotateCcw size={12} /> Reset
              </button>
            )}
          </div>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto px-4 py-4 space-y-3">
          {/* Idle state */}
          {phase === 0 && messages.length === 0 && (
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="space-y-4 pt-2">
              <div className="text-center py-3">
                <div className="w-12 h-12 rounded-2xl bg-amber-50 border border-amber-200 flex items-center justify-center mx-auto mb-3">
                  <Sparkles size={22} className="text-amber-500" />
                </div>
                <p className="text-sm font-semibold text-[#1A2340]">What business problem are you trying to solve?</p>
                <p className="text-xs text-[#718096] mt-1">Type it in plain English. Nova will find the right agents — or spec out what to build.</p>
              </div>
              <div>
                <p className="text-xs font-semibold text-[#718096] uppercase tracking-wider mb-2 flex items-center gap-1.5">
                  <Lightbulb size={11} /> Try an example
                </p>
                <div className="flex flex-col gap-2">
                  {EXAMPLE_PROMPTS.map((p, i) => (
                    <button
                      key={i}
                      onClick={() => setInput(p)}
                      className="text-left px-3 py-2.5 rounded-xl border border-[#E2E8F0] hover:border-[#C8102E] hover:bg-[#FDF0F2] text-xs text-[#4A5568] transition-all leading-relaxed"
                    >
                      "{p}"
                    </button>
                  ))}
                  <button
                    onClick={() => { setInput(''); setTimeout(() => inputRef.current?.focus(), 50) }}
                    className="text-left px-3 py-2.5 rounded-xl border border-dashed border-[#CBD5E0] hover:border-[#C8102E] hover:bg-[#FDF0F2] text-xs text-[#9BA8BA] hover:text-[#C8102E] transition-all leading-relaxed flex items-center gap-1.5"
                  >
                    <Edit3 size={11} className="flex-shrink-0" /> Something else entirely — describe it below
                  </button>
                </div>
              </div>
            </motion.div>
          )}

          {/* Message thread */}
          {messages.map((msg) => (
            <motion.div
              key={msg.id}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
            >
              {msg.role === 'user' ? (
                <div className="max-w-[80%] px-4 py-2.5 rounded-2xl rounded-tr-sm text-sm text-white leading-relaxed"
                  style={{ background: '#C8102E' }}>
                  {msg.content}
                </div>
              ) : msg.isQuestion ? (
                <div className="max-w-[90%] w-full">
                  <div className="px-4 py-3 rounded-2xl rounded-tl-sm bg-white border border-[#E2E8F0] mb-2">
                    <div className="flex items-center gap-1.5 mb-2">
                      <Sparkles size={12} className="text-amber-500" />
                      <span className="text-xs font-semibold text-[#718096]">Nova</span>
                    </div>
                    <p className="text-sm text-[#1A2340] leading-relaxed">{msg.content}</p>
                  </div>
                  {!answeredIds.has(msg.id) && (
                    <div className="flex flex-col gap-2">
                      <div className="flex flex-wrap gap-1.5">
                        {msg.options.map(opt => (
                          <button
                            key={opt}
                            onClick={() => handleOptionClick(msg.id, opt)}
                            className="px-3 py-1.5 rounded-lg border border-[#E2E8F0] text-xs font-medium text-[#4A5568] hover:border-[#C8102E] hover:bg-[#FDF0F2] hover:text-[#C8102E] transition-all"
                          >
                            {opt}
                          </button>
                        ))}
                        <button
                          onClick={() => { setAnsweredIds(prev => new Set([...prev, msg.id])); setTimeout(() => inputRef.current?.focus(), 50) }}
                          className="px-3 py-1.5 rounded-lg border border-dashed border-[#CBD5E0] text-xs font-medium text-[#9BA8BA] hover:border-[#C8102E] hover:bg-[#FDF0F2] hover:text-[#C8102E] transition-all flex items-center gap-1"
                        >
                          <Edit3 size={10} /> Something else — describe below
                        </button>
                      </div>

                    </div>
                  )}
                </div>
              ) : (
                <div className="max-w-[90%] px-4 py-3 rounded-2xl rounded-tl-sm bg-white border border-[#E2E8F0]">
                  <div className="flex items-center gap-1.5 mb-1">
                    <Sparkles size={12} className="text-amber-500" />
                    <span className="text-xs font-semibold text-[#718096]">Nova</span>
                  </div>
                  <p className="text-sm text-[#1A2340] leading-relaxed">
                    {msg.content.split('**').map((part, i) =>
                      i % 2 === 1 ? <strong key={i}>{part}</strong> : part
                    )}
                  </p>
                </div>
              )}
            </motion.div>
          ))}

          {typing && (
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="flex justify-start">
              <TypingIndicator />
            </motion.div>
          )}

          {/* Scanning animation */}
          {phase === 4 && (
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="flex justify-start">
              <div className="flex items-center gap-2 px-4 py-2.5 rounded-xl bg-[#F0FDF4] border border-[#BBF7D0]">
                <Loader size={13} className="text-emerald-600 animate-spin" />
                <span className="text-xs text-emerald-700 font-medium">Scanning agent pool for matches...</span>
              </div>
            </motion.div>
          )}

          <div ref={chatEndRef} />
        </div>

        {/* Input */}
        <div className="flex-shrink-0 p-4 border-t border-[#E2E8F0]">
          {/* Refinement chips — shown after results are delivered */}
          {phase >= 5 && !typing && (
            <div className="flex flex-wrap gap-1.5 mb-2.5">
              <span className="text-xs text-[#9BA8BA] self-center mr-1">Refine:</span>
              {[
                'Add more context',
                'Different segment',
                'Suggest new agents',
                'Re-analyze from scratch',
              ].map(chip => (
                <button
                  key={chip}
                  onClick={() => {
                    if (chip === 'Re-analyze from scratch') {
                      handleReset()
                    } else {
                      setInput(prev => prev ? prev : chip === 'Add more context' ? '' : chip + ' — ')
                      if (chip !== 'Add more context') document.querySelector('textarea')?.focus()
                    }
                  }}
                  className="px-2.5 py-1 rounded-full border border-[#E2E8F0] text-xs text-[#4A5568] hover:border-[#C8102E] hover:text-[#C8102E] hover:bg-[#FDF0F2] transition-all"
                >
                  {chip}
                </button>
              ))}
            </div>
          )}
          <div className="flex items-center gap-2">
            <textarea
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSubmit() } }}
              placeholder={phase >= 5 ? 'Add more context to refine the analysis…' : 'Describe your business problem…'}
              rows={1}
              disabled={typing}
              className="flex-1 h-11 px-4 rounded-xl border border-[#E2E8F0] text-sm resize-none focus:outline-none focus:border-[#C8102E] disabled:bg-[#F7F8FA] disabled:text-[#CBD5E0] leading-[2.75rem]"
            />
            <button
              onClick={handleSubmit}
              disabled={!input.trim() || typing}
              className="h-11 w-11 flex-shrink-0 rounded-xl flex items-center justify-center text-white disabled:opacity-40 transition-all hover:opacity-90 active:scale-95"
              style={{ background: '#C8102E' }}
              title="Send message"
            >
              <Send size={15} />
            </button>
            <button
              onClick={() => analyzeWorkflow(input.trim() || (messages.filter(m => m.role === 'user').pop()?.content ?? ''))}
              disabled={workflowLoading || (messages.length === 0 && !input.trim())}
              className="h-11 flex-shrink-0 flex items-center gap-1.5 px-4 rounded-xl text-xs font-semibold text-white transition-all disabled:opacity-40 whitespace-nowrap hover:opacity-90 active:scale-95"
              style={{ background: '#1A2340' }}
              title="Analyse and build a complete workflow for this problem"
            >
              {workflowLoading ? <Loader size={12} className="animate-spin" /> : <GitMerge size={12} />}
              Build Workflow
            </button>
          </div>
        </div>
        </div>{/* end full-chat wrapper */}
      </div>

      {/* ── RIGHT: Workflow Pipeline panel ── */}
      <div className="card overflow-hidden relative flex flex-col min-w-0">

        {/* ── Pipeline collapsed strip ── */}
        {pipelineCollapsed && (
          <div className="flex flex-col items-center h-full cursor-pointer"
            style={{ background: 'linear-gradient(135deg, #1A2340, #2D3A5C)' }}
            onClick={() => setPipelineCollapsed(false)}
            title="Open Workflow Pipeline"
          >
            <div className="py-4 flex items-center justify-center w-full hover:bg-white/10 transition-all">
              <div className="w-8 h-8 rounded-lg bg-white/10 hover:bg-white/20 flex items-center justify-center transition-all">
                <GitMerge size={14} className="text-violet-400" />
              </div>
            </div>
            <div className="flex-1 flex items-center justify-center">
              <p className="text-[9px] font-bold text-white/25 uppercase tracking-widest select-none"
                style={{ writingMode: 'vertical-lr', transform: 'rotate(180deg)' }}>
                Pipeline
              </p>
            </div>
          </div>
        )}

        {/* ── Full pipeline (hidden when collapsed) ── */}
        <div className={`flex flex-col flex-1 overflow-hidden ${pipelineCollapsed ? 'hidden' : ''}`}>

        {/* Panel header */}
        <div
          className="px-5 py-4 border-b border-white/10 flex items-center justify-between flex-shrink-0"
          style={{ background: 'linear-gradient(135deg, #1A2340 0%, #2D3A5C 100%)' }}
        >
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-white/10 flex items-center justify-center">
              <GitMerge size={16} className="text-violet-400" />
            </div>
            <div>
              <p className="text-white font-semibold text-sm">Workflow Pipeline</p>
              <p className="text-white/40 text-xs">
                {workflowResult
                  ? `${workflowResult.chain?.length || 0} agents · Generated by Nova`
                  : workflowLoading
                    ? 'Building pipeline…'
                    : 'Awaiting Nova analysis…'}
              </p>
            </div>
          </div>
          {chatCollapsed && (
            <button onClick={() => setPipelineCollapsed(true)}
              className="w-7 h-7 rounded-lg bg-white/10 hover:bg-white/20 flex items-center justify-center transition-all"
              title="Collapse pipeline">
              <ChevronLeft size={13} className="text-white/60" style={{ transform: 'rotate(180deg)' }} />
            </button>
          )}
        </div>

        {/* Scrollable pipeline content */}
        <div className="flex-1 overflow-y-auto px-5 py-5">

          {/* Phase 0 — idle */}
          {phase === 0 && !workflowResult && (
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}
              className="h-full flex flex-col items-center justify-center text-center py-12">
              <div className="w-14 h-14 rounded-2xl bg-[#F7F8FA] border border-[#E2E8F0] flex items-center justify-center mb-4">
                <GitMerge size={24} className="text-[#CBD5E0]" />
              </div>
              <p className="text-base font-bold text-[#1A2340] mb-2">Your workflow pipeline appears here</p>
              <p className="text-xs text-[#718096] max-w-xs leading-relaxed">
                Once Nova gathers enough context, she'll automatically design a multi-agent pipeline to solve your problem end-to-end.
              </p>
            </motion.div>
          )}

          {/* Phase 1 — gathering */}
          {phase === 1 && !workflowResult && !workflowLoading && (
            <motion.div initial={{ opacity: 0, x: 10 }} animate={{ opacity: 1, x: 0 }}
              className="flex flex-col items-center justify-center py-12 text-center">
              <div className="flex items-center gap-2 mb-3">
                <span className="w-2 h-2 rounded-full bg-amber-400 animate-pulse" />
                <span className="text-sm font-semibold text-[#1A2340]">Nova is gathering context…</span>
              </div>
              <p className="text-xs text-[#718096] max-w-xs">Answer a few more questions — she'll build your workflow automatically once she has enough information.</p>
            </motion.div>
          )}

          {/* Phases 3–4 — building needs profile */}
          {(phase === 3 || phase === 4) && sc && !workflowResult && (
            <motion.div initial={{ opacity: 0, x: 10 }} animate={{ opacity: 1, x: 0 }} className="space-y-3">
              <p className="text-xs font-bold text-[#9BA8BA] uppercase tracking-wider mb-3 flex items-center gap-1.5">
                <Layers size={11} /> Needs identified — designing pipeline
              </p>
              {sc.needs.map((need, i) => (
                <motion.div
                  key={i} initial={{ opacity: 0, x: 10 }} animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: i * 0.07 }}
                  className="flex items-center gap-3 px-3 py-2.5 rounded-xl bg-[#F7F8FA] border border-[#E2E8F0]"
                >
                  <CheckCircle size={13} className="text-emerald-500 flex-shrink-0" />
                  <p className="text-sm text-[#1A2340] font-medium">{isNewFormat ? need.need : need.label}</p>
                </motion.div>
              ))}
              {phase === 4 && (
                <div className="flex items-center gap-2.5 px-3 py-2.5 rounded-xl bg-violet-50 border border-violet-200">
                  <Loader size={13} className="text-violet-600 animate-spin flex-shrink-0" />
                  <p className="text-xs text-violet-700 font-medium">Building workflow pipeline…</p>
                </div>
              )}
            </motion.div>
          )}

          {/* Loading — workflow analysis in progress */}
          {workflowLoading && (
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}
              className="flex flex-col items-center justify-center py-16 gap-3">
              <div className="relative w-14 h-14">
                <div className="absolute inset-0 rounded-full border-4 border-[#1A2340]/10" />
                <div className="absolute inset-0 rounded-full border-4 border-[#C8102E] border-t-transparent animate-spin" />
                <div className="absolute inset-0 flex items-center justify-center">
                  <GitMerge size={18} className="text-[#C8102E]" />
                </div>
              </div>
              <p className="text-sm font-bold text-[#1A2340]">Designing your pipeline…</p>
              <p className="text-xs text-[#9BA8BA]">Nova is chaining agents to solve your problem</p>
            </motion.div>
          )}

          {/* ✅ Main: Workflow pipeline view */}
          {!workflowLoading && workflowResult && (
            <WorkflowPipelineView
              result={workflowResult}
              navigate={navigate}
              builtSteps={builtSteps}
              skippedSteps={skippedSteps}
              onOpenSpec={handleOpenSpec}
              onSkip={handleSkipStep}
              onSubmitWorkflow={handleSendForApproval}
            />
          )}

          {/* Phase 5 — analysis done but no workflow yet (prompt to trigger) */}
          {phase >= 5 && sc && !workflowResult && !workflowLoading && (
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="text-center py-10">
              <div className="w-12 h-12 rounded-2xl bg-violet-50 border border-violet-200 flex items-center justify-center mx-auto mb-3">
                <GitMerge size={20} className="text-violet-500" />
              </div>
              <p className="text-sm font-semibold text-[#1A2340] mb-1">Analysis complete</p>
              <p className="text-xs text-[#718096] mb-4">Click to generate the full multi-agent workflow pipeline</p>
              <button
                onClick={() => analyzeWorkflow(problemRef.current || messages.find(m => m.role === 'user')?.content || '')}
                className="flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-bold text-white mx-auto transition-all hover:opacity-90"
                style={{ background: 'linear-gradient(135deg,#1A2340,#2D3A5C)' }}
              >
                <GitMerge size={14} /> Build Workflow Pipeline
              </button>
            </motion.div>
          )}
        </div>

        {/* Sticky footer — Export Report only */}
        {workflowResult && (
          <div className="px-5 py-4 border-t border-[#E2E8F0] flex-shrink-0"
            style={{ background: '#F7F8FA' }}>
            <button
              onClick={handleDownloadReport}
              className="w-full flex items-center justify-center gap-1.5 py-2.5 rounded-xl text-sm font-semibold border border-[#E2E8F0] text-[#4A5568] bg-white hover:bg-[#F0F4FF] hover:border-[#C7D2FE] transition-all"
            >
              <Download size={14} /> Export Report
            </button>
          </div>
        )}
        </div>{/* end full-pipeline wrapper */}
      </div>

    </motion.div>
    </>
  )
}
