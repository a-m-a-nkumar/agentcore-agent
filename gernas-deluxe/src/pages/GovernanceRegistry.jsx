import React, { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Shield, CheckCircle, ChevronDown, ChevronUp,
  Clock, Bot, Zap, GitMerge, AlertCircle, X,
  FileText, Terminal, Lock, ToggleRight, ToggleLeft,
} from 'lucide-react'
import useStore from '../store/useStore'
import { INCOMPLETE_WORKFLOWS, UNDER_REVIEW_AGENTS } from '../data/platformData'

/* ── Compliance framework data ─────────────────────────────────────────────── */
const FRAMEWORKS = [
  {
    label: 'PCI DSS v4.0',
    desc:  'Payment security',
    color: '#0EA5E9',
    detail: 'The Payment Card Industry Data Security Standard v4.0 mandates security controls for all entities that store, process, or transmit cardholder data. For DLX_AGENTIC_OS agents, this requires encrypted data transmission, strict access control, and regular security testing of any agent that handles payment terminal configurations, transaction streams, or cardholder information. Agents must log all access to payment data and support quarterly security scans.',
  },
  {
    label: 'SOX',
    desc:  'Financial controls',
    color: '#8B5CF6',
    detail: 'The Sarbanes-Oxley Act Section 404 requires public companies to maintain and assess internal controls over financial reporting. Agents performing GL journal entries, invoice matching, or financial data writes must maintain complete audit trails, enforce segregation of duties, and require human approval for material financial transactions. All financial write operations must be logged with actor, timestamp, and rationale for external auditor review.',
  },
  {
    label: 'BSA / AML',
    desc:  'Anti-money laundering',
    color: '#C8102E',
    detail: 'The Bank Secrecy Act and Anti-Money Laundering regulations require financial institutions to detect, prevent, and report suspicious activity. Agents performing KYB verification, transaction monitoring, and account holds must follow OFAC screening requirements and file Suspicious Activity Reports (SARs) when regulatory thresholds are met. All KYB/KYC decisions must be recorded with supporting evidence and retained for a minimum of five years for regulatory examination.',
  },
  {
    label: 'NACHA',
    desc:  'ACH payment rules',
    color: '#F59E0B',
    detail: 'The National Automated Clearing House Association governs ACH electronic fund transfers in the United States. Agents that initiate ACH credits or debits must comply with NACHA Operating Rules, including pre-notification requirements, return item processing, and exposure limit controls. All ACH transactions require dual authorisation for amounts exceeding defined thresholds, and same-day ACH transactions are subject to additional scrutiny and routing controls.',
  },
  {
    label: 'CCPA',
    desc:  'Data privacy',
    color: '#10B981',
    detail: 'The California Consumer Privacy Act grants California residents the right to know, delete, and opt out of the sale of their personal information. Agents processing California customer data must implement data minimisation principles, support consumer rights requests within 45 days, and maintain records of data processing activities and disclosures. Agents may not share personal information with third parties without explicit consent and must honour opt-out signals from the Global Privacy Control.',
  },
  {
    label: 'SOC 2 Type II',
    desc:  'Security & uptime',
    color: '#6366F1',
    detail: 'A Service Organization Control 2 Type II audit verifies that security, availability, processing integrity, confidentiality, and privacy controls operate effectively over a defined period (typically 6–12 months). DLX_AGENTIC_OS agents must meet criteria for logical access controls, change management, risk assessment, and incident response. Any agent deployed to production must be covered by the platform\'s SOC 2 boundary and subject to annual third-party auditor review.',
  },
]

/* ── Risk helpers (mirrors AgentBuilder logic, applied to workflow chains) ─── */
const HIGH_RISK_TOOLS = ['txn-stream','hold-trigger','gl-write','payment-post','account-freeze','swift-api','sanctions-check','aml-screen','anomaly-detect','compliance-alert']
const MED_RISK_TOOLS  = ['crm-write','erp-write','profile-update','warehouse-write','approval-route','kyb-verify','kyb-api','payment-match','dispute-api']

function generateWorkflowRisk(chain = []) {
  const allTools   = chain.flatMap(s => s.tools || [])
  const allText    = chain.map(s => (s.agent_name || '') + ' ' + (s.description || '')).join(' ').toLowerCase()
  const highCount  = allTools.filter(t => HIGH_RISK_TOOLS.includes(t)).length
  const medCount   = allTools.filter(t => MED_RISK_TOOLS.includes(t)).length
  const hasFinancial  = highCount > 0 || allText.includes('payment') || allText.includes('invoice') || allText.includes('financial')
  const hasCompliance = allText.includes('fraud') || allText.includes('kyc') || allText.includes('aml') || allText.includes('compliance')
  const hasIrreversible = allTools.some(t => ['hold-trigger','account-freeze','gl-write','swift-api','payment-post'].includes(t))
  const level = (highCount >= 2 || (highCount >= 1 && (hasFinancial || hasCompliance))) ? 'red'
    : (highCount >= 1 || medCount >= 2 || hasFinancial || hasCompliance) ? 'yellow' : 'green'
  const score = Math.min(
    level === 'red' ? 70 + highCount * 5 : level === 'yellow' ? 35 + medCount * 8 + highCount * 10 : 10 + medCount * 5, 100
  )
  return {
    level, score,
    sections: [
      { title: 'Data Access',      risk: hasFinancial ? 'high' : medCount > 0 ? 'medium' : 'low',
        finding: hasFinancial ? 'Workflow includes agents with financial system write access.' : medCount > 0 ? 'Moderate data access across CRM and operational systems.' : 'Read-only or notification-only tool access.' },
      { title: 'Financial Impact', risk: allTools.some(t => ['gl-write','payment-post'].includes(t)) ? 'high' : hasFinancial ? 'medium' : 'low',
        finding: allTools.some(t => ['gl-write','payment-post'].includes(t)) ? 'Direct financial write operations detected in the pipeline.' : hasFinancial ? 'Indirect financial impact via payment matching.' : 'No financial write operations.' },
      { title: 'Compliance',       risk: hasCompliance ? 'high' : highCount > 0 ? 'medium' : 'low',
        finding: hasCompliance ? 'Workflow operates in a regulated compliance domain (KYC/AML/Fraud).' : highCount > 0 ? 'Some tools may trigger regulatory obligations.' : 'No compliance-regulated operations detected.' },
      { title: 'Reversibility',    risk: hasIrreversible ? 'high' : medCount > 0 ? 'medium' : 'low',
        finding: hasIrreversible ? 'Contains irreversible operations — account holds or GL writes require guardrails.' : medCount > 0 ? 'Write operations are reversible with moderate effort.' : 'All operations are reversible or read-only.' },
    ],
    recommendation: level === 'red'
      ? 'High-risk workflow. Requires senior security review and explicit CISO sign-off before production.'
      : level === 'yellow'
        ? 'Medium-risk workflow. Standard compliance review required. Ensure human approval guardrails are active on all write operations.'
        : 'Low-risk workflow. Routine review sufficient. Enable audit logging as best practice.',
  }
}

const REPORT_LEVEL = {
  green:  { bg: '#F0FDF4', text: '#16A34A', border: '#86EFAC', badge: 'bg-emerald-100 text-emerald-700 border border-emerald-300', dot: '#22C55E', label: 'Low Risk'    },
  yellow: { bg: '#FFFBEB', text: '#D97706', border: '#FCD34D', badge: 'bg-amber-100 text-amber-700 border border-amber-300',     dot: '#F59E0B', label: 'Medium Risk' },
  red:    { bg: '#FEF2F2', text: '#DC2626', border: '#FCA5A5', badge: 'bg-red-100 text-red-700 border border-red-300',           dot: '#EF4444', label: 'High Risk'   },
}
const SECTION_RISK = {
  low:    { color: '#16A34A', bg: '#F0FDF4', border: '#BBF7D0' },
  medium: { color: '#D97706', bg: '#FFFBEB', border: '#FDE68A' },
  high:   { color: '#DC2626', bg: '#FEF2F2', border: '#FECACA' },
}

/* ── Risk card (shared between built agents and uploaded workflows) ─────────── */
function RiskReportCard({ report, tools, rl, title, subtitle, onApprove, onReject, approveLabel = 'Approve', isOpen, onToggle, scoreLabel = 'Risk Score / 100', chain, systemPrompt, guardrails }) {
  const STEP_STATUS_STYLE = {
    full:    { color: '#6B7280', bg: '#F9FAFB', border: '#D1D5DB', label: 'Ready'       },
    partial: { color: '#F59E0B', bg: '#FFFBEB', border: '#FDE68A', label: 'Needs Work'  },
    none:    { color: '#EF4444', bg: '#FEF2F2', border: '#FECACA', label: 'Build Needed'},
  }

  const [selectedStep, setSelectedStep] = useState(null)

  const GUARDRAIL_LABELS = {
    humanReview:      'Human review required',
    auditLog:         'Audit logging enabled',
    rateLimitEmail:   'Email rate-limit active',
    dryRunMode:       'Dry-run mode',
    alertOnException: 'Alert on exception',
  }

  return (
    <div className="card overflow-hidden border-2" style={{ borderColor: rl.border }}>

      {/* ── Step detail modal ── */}
      <AnimatePresence>
        {selectedStep && (
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
            style={{ backdropFilter: 'blur(10px)', background: 'rgba(10,18,40,0.72)' }}
            onClick={e => e.target === e.currentTarget && setSelectedStep(null)}>
            <motion.div initial={{ opacity: 0, scale: 0.95, y: 16 }} animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.95, y: 16 }} transition={{ duration: 0.2 }}
              className="bg-white rounded-3xl overflow-hidden"
              style={{ width: '92vw', maxWidth: 520, maxHeight: '88vh', boxShadow: '0 40px 80px rgba(0,0,0,0.35)' }}>
              {/* Modal header */}
              <div className="px-6 py-5 flex items-center justify-between"
                style={{ background: 'linear-gradient(135deg,#1A2340 0%,#2D3A5C 100%)' }}>
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 rounded-2xl bg-white/10 flex items-center justify-center flex-shrink-0">
                    <Bot size={18} className="text-white" />
                  </div>
                  <div>
                    <p className="text-white font-bold text-sm">{selectedStep.agent_name || selectedStep.title}</p>
                    <p className="text-white/50 text-xs mt-0.5">Step {selectedStep._index + 1} · {STEP_STATUS_STYLE[selectedStep.status]?.label || 'Ready'}</p>
                  </div>
                </div>
                <button onClick={() => setSelectedStep(null)}
                  className="w-8 h-8 rounded-xl bg-white/10 hover:bg-white/20 flex items-center justify-center transition-all">
                  <X size={14} className="text-white" />
                </button>
              </div>
              {/* Stats row */}
              <div className="grid border-b border-[#E2E8F0]" style={{ gridTemplateColumns: selectedStep.sla_minutes ? '1fr 1fr 1fr' : '1fr 1fr' }}>
                <div className="px-5 py-3.5">
                  <p className="text-xs text-[#718096] mb-0.5">Status</p>
                  <p className="text-sm font-bold" style={{ color: STEP_STATUS_STYLE[selectedStep.status]?.color || '#6B7280' }}>
                    {STEP_STATUS_STYLE[selectedStep.status]?.label || 'Ready'}
                  </p>
                </div>
                <div className="px-5 py-3.5 border-l border-[#E2E8F0]">
                  <p className="text-xs text-[#718096] mb-0.5">Tools</p>
                  <p className="text-sm font-bold text-[#1A2340]">{(selectedStep.tools || []).length} authorised</p>
                </div>
                {selectedStep.sla_minutes && (
                  <div className="px-5 py-3.5 border-l border-[#E2E8F0]">
                    <p className="text-xs text-[#718096] mb-0.5">SLA</p>
                    <p className="text-sm font-bold text-[#1A2340]">{selectedStep.sla_minutes} min</p>
                  </div>
                )}
              </div>
              {/* Body */}
              <div className="p-6 space-y-5 overflow-y-auto" style={{ maxHeight: 'calc(88vh - 200px)' }}>
                {selectedStep.description && (
                  <p className="text-sm text-[#4A5568] leading-relaxed">{selectedStep.description}</p>
                )}
                {(selectedStep.tools || []).length > 0 && (
                  <div>
                    <p className="text-xs font-semibold text-[#718096] uppercase tracking-wider mb-2.5">Authorised Tools</p>
                    <div className="flex flex-wrap gap-1.5">
                      {selectedStep.tools.map(t => (
                        <span key={t} className="px-2.5 py-1 rounded-lg text-xs font-mono bg-[#F0F4FF] border border-[#C7D2FE] text-[#4338CA]">{t}</span>
                      ))}
                    </div>
                  </div>
                )}
                {selectedStep.covers && (
                  <div className="px-3 py-2.5 rounded-xl bg-[#F9FAFB] border border-[#E5E7EB]">
                    <p className="text-xs font-semibold text-[#6B7280] mb-1">What this agent covers</p>
                    <p className="text-xs text-[#4A5568] leading-relaxed">✓ {selectedStep.covers}</p>
                  </div>
                )}
                {selectedStep.gap && (
                  <div className="px-3 py-2.5 rounded-xl bg-amber-50 border border-amber-200">
                    <p className="text-xs font-semibold text-amber-700 mb-1">Known gap</p>
                    <p className="text-xs text-amber-600 leading-relaxed">⚠ {selectedStep.gap}</p>
                  </div>
                )}
              </div>
            </motion.div>
          </div>
        )}
      </AnimatePresence>
      <button className="w-full px-5 py-4 flex items-center gap-4 text-left hover:bg-[#F7F9FF] transition-all"
        onClick={onToggle}>
        <div className="w-10 h-10 rounded-xl flex items-center justify-center flex-shrink-0" style={{ background: rl.bg, border: '1.5px solid ' + rl.border }}>
          <Bot size={17} style={{ color: rl.text }} />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <p className="text-sm font-bold text-[#1A2340]">{title}</p>
            <span className={'px-2 py-0.5 rounded-full text-[10px] font-bold ' + rl.badge}>{rl.label}</span>
            <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-amber-50 text-amber-700 border border-amber-200">Pending Approval</span>
          </div>
          <p className="text-xs text-[#718096] mt-0.5 line-clamp-1">{subtitle}</p>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          {report && <span className="text-xs text-[#9BA8BA]">Score: {report.score}/100</span>}
          <div className="flex items-center gap-1.5">
            <button onClick={e => { e.stopPropagation(); onApprove() }}
              className="px-3 py-1.5 rounded-lg text-xs font-bold text-white bg-emerald-500 hover:bg-emerald-600 transition-all">
              {approveLabel}
            </button>
            {onReject && (
              <button onClick={e => { e.stopPropagation(); onReject() }}
                className="px-3 py-1.5 rounded-lg text-xs font-bold text-white bg-red-500 hover:bg-red-600 transition-all">
                Reject
              </button>
            )}
          </div>
          {isOpen ? <ChevronUp size={14} className="text-[#9BA8BA]" /> : <ChevronDown size={14} className="text-[#9BA8BA]" />}
        </div>
      </button>

      <AnimatePresence>
        {isOpen && (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }} className="overflow-hidden">
            <div className="border-t" style={{ borderColor: rl.border }}>

              {/* ── Workflow Pipeline (uploaded configs) ── */}
              {chain && chain.length > 0 && (
                <div className="px-5 py-4 border-b border-[#F0F2F5]" style={{ background: '#FAFBFC' }}>
                  <p className="text-xs font-bold uppercase tracking-wider text-[#1A2340] mb-3 flex items-center gap-1.5">
                    <GitMerge size={11} /> Workflow Pipeline — {chain.length} Agent{chain.length !== 1 ? 's' : ''}
                  </p>
                  <div className="space-y-2">
                    {chain.map((step, i) => {
                      const st = STEP_STATUS_STYLE[step.status] || STEP_STATUS_STYLE.full
                      const stepTools = step.tools || []
                      return (
                        <motion.div key={i} whileHover={{ y: -1 }} whileTap={{ scale: 0.98 }}
                          onClick={() => setSelectedStep({ ...step, _index: i })}
                          className="rounded-xl border overflow-hidden cursor-pointer hover:shadow-md transition-all"
                          style={{ borderColor: st.border }}>
                          <div className="px-3 py-2.5 flex items-start gap-3" style={{ background: st.bg }}>
                            <div className="w-6 h-6 rounded-full flex items-center justify-center flex-shrink-0 font-black text-xs mt-0.5"
                              style={{ border: '2px solid ' + st.color, color: st.color, background: 'white' }}>
                              {i + 1}
                            </div>
                            <div className="flex-1 min-w-0">
                              <div className="flex items-center gap-2 flex-wrap mb-1">
                                <p className="text-xs font-bold text-[#1A2340]">{step.agent_name || step.title || 'Agent ' + (i + 1)}</p>
                                <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-full" style={{ color: st.color, background: st.color + '18' }}>{st.label}</span>
                              </div>
                              {step.description && <p className="text-[11px] text-[#718096] leading-relaxed mb-1.5">{step.description}</p>}
                              {stepTools.length > 0 && (
                                <div className="flex flex-wrap gap-1">
                                  {stepTools.map(t => (
                                    <span key={t} className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-white border border-[#E2E8F0] text-[#4338CA]">{t}</span>
                                  ))}
                                </div>
                              )}
                              {step.covers && (
                                <p className="text-[10px] text-emerald-700 mt-1.5">✓ {step.covers}</p>
                              )}
                              {step.gap && (
                                <p className="text-[10px] text-amber-700 mt-0.5">⚠ Gap: {step.gap}</p>
                              )}
                              <p className="text-[9px] text-[#9BA8BA] mt-1.5">Click to view details →</p>
                            </div>
                          </div>
                        </motion.div>
                      )
                    })}
                  </div>
                </div>
              )}

              {/* ── Risk Assessment ── */}
              <div className="px-5 py-4" style={{ background: rl.bg }}>
                <div className="flex items-start justify-between mb-4">
                  <div>
                    <p className="text-xs font-bold uppercase tracking-wider text-[#1A2340]">Risk Assessment Report</p>
                    <p className="text-[10px] text-[#9BA8BA] mt-0.5">Auto-generated at submission time</p>
                  </div>
                  <div className="text-right">
                    <p className="text-2xl font-black" style={{ color: rl.text }}>{report.score}</p>
                    <p className="text-[10px] text-[#9BA8BA]">{scoreLabel}</p>
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-3 mb-4">
                  {(report.sections || []).map(s => {
                    const sr = SECTION_RISK[s.risk] || SECTION_RISK.low
                    return (
                      <div key={s.title} className="rounded-xl p-3 border" style={{ background: sr.bg, borderColor: sr.border }}>
                        <div className="flex items-center gap-2 mb-1.5">
                          <div className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: sr.color }} />
                          <p className="text-xs font-bold" style={{ color: sr.color }}>{s.title}</p>
                          <span className="ml-auto text-[9px] font-bold uppercase px-1.5 py-0.5 rounded-full" style={{ background: sr.color + '20', color: sr.color }}>{s.risk}</span>
                        </div>
                        <p className="text-[11px] text-[#4A5568] leading-relaxed">{s.finding}</p>
                      </div>
                    )
                  })}
                </div>
                <div className="rounded-xl p-3 border bg-white" style={{ borderColor: rl.border }}>
                  <p className="text-[10px] font-bold uppercase tracking-wide text-[#9BA8BA] mb-1">Recommendation</p>
                  <p className="text-xs text-[#4A5568] leading-relaxed">{report.recommendation}</p>
                </div>
              </div>

              {/* ── System Prompt (built agents) ── */}
              {systemPrompt && (
                <div className="px-5 py-4 border-t border-[#F0F2F5]">
                  <p className="text-xs font-bold uppercase tracking-wider text-[#1A2340] mb-3 flex items-center gap-1.5">
                    <Terminal size={11} /> System Prompt
                  </p>
                  <div className="rounded-xl overflow-hidden" style={{ background: '#0F172A', border: '1px solid #1E293B' }}>
                    <div className="flex items-center gap-2 px-4 py-2" style={{ borderBottom: '1px solid #1E293B' }}>
                      <div className="flex gap-1.5">
                        <div className="w-2 h-2 rounded-full bg-[#FF5F56]" />
                        <div className="w-2 h-2 rounded-full bg-[#FFBD2E]" />
                        <div className="w-2 h-2 rounded-full bg-[#27C93F]" />
                      </div>
                      <span className="text-[10px] font-mono text-white/20">system_prompt.txt</span>
                    </div>
                    <pre className="px-4 py-3 text-[11px] leading-relaxed overflow-auto whitespace-pre-wrap font-mono max-h-48"
                      style={{ color: '#94A3B8' }}>
                      {systemPrompt}
                    </pre>
                  </div>
                </div>
              )}

              {/* ── Guardrails (built agents) ── */}
              {guardrails && (
                <div className="px-5 py-4 border-t border-[#F0F2F5]">
                  <p className="text-xs font-bold uppercase tracking-wider text-[#1A2340] mb-3 flex items-center gap-1.5">
                    <Lock size={11} /> Guardrails
                  </p>
                  <div className="grid grid-cols-2 gap-2">
                    {Object.entries(guardrails).map(([key, active]) => (
                      <div key={key} className={'flex items-center gap-2 px-3 py-2 rounded-lg border text-xs transition-all ' +
                        (active ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-[#E2E8F0] bg-[#F7F8FA] text-[#9BA8BA]')}>
                        {active
                          ? <ToggleRight size={13} className="text-emerald-500 flex-shrink-0" />
                          : <ToggleLeft  size={13} className="text-[#CBD5E0] flex-shrink-0" />}
                        <span className="font-medium">{GUARDRAIL_LABELS[key] || key}</span>
                        <span className={'ml-auto font-bold text-[10px] ' + (active ? 'text-emerald-600' : 'text-[#CBD5E0]')}>{active ? 'ON' : 'OFF'}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* ── All Tools ── */}
              {(tools || []).length > 0 && (
                <div className="px-5 py-4 border-t border-[#F0F2F5]">
                  <p className="text-[10px] font-bold uppercase tracking-wide text-[#9BA8BA] mb-2">All Tools in Pipeline</p>
                  <div className="flex flex-wrap gap-1">
                    {[...new Set(tools)].map(t => (
                      <span key={t} className="px-2 py-0.5 rounded-md text-[10px] font-mono bg-[#F7F8FA] border border-[#E2E8F0] text-[#4A5568]">{t}</span>
                    ))}
                  </div>
                </div>
              )}

            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

/* ── Main component ─────────────────────────────────────────────────────────── */
export default function GovernanceRegistry() {
  const { addToast, builtAgents = [], approveAgent, autoApproveGreen, rejectAgent, rejectWorkflow,
          pendingWorkflows = [], updateWorkflowStatus, platformApprovals = {}, setPlatformApproval } = useStore()

  const [activeTab,      setActiveTab]      = useState('reviewer')
  const [reviewExpanded, setReviewExpanded] = useState(null)
  const [openFramework,  setOpenFramework]  = useState(null)

  // Helper to decide on a platform item
  const decidePlatform = (id, decision) => setPlatformApproval(id, decision)

  // Platform agents enriched with risk reports
  const platformAgentsWithRisk = UNDER_REVIEW_AGENTS.map(a => ({
    ...a, isPlatform: true,
    riskReport: generateWorkflowRisk([{ tools: a.tools, agent_name: a.name, description: a.description }]),
  }))

  // Split by decision
  const pendingPlatformAgents   = platformAgentsWithRisk.filter(a => !platformApprovals[a.id])
  const approvedPlatformAgents  = platformAgentsWithRisk.filter(a => platformApprovals[a.id] === 'approved')
  const rejectedPlatformAgents  = platformAgentsWithRisk.filter(a => platformApprovals[a.id] === 'rejected')

  // Under-review = pending platform agents + pending built agents
  const underReviewAgents = [
    ...pendingPlatformAgents,
    ...builtAgents.filter(a => a.status === 'under-review'),
  ]
  // Approved agents = approved platform + active built agents
  const approvedAgents = [
    ...approvedPlatformAgents,
    ...builtAgents.filter(a => a.status === 'active'),
  ]

  // User-submitted workflows
  const pendingUploaded    = pendingWorkflows.filter(w => w.status !== 'approved' && w.status !== 'rejected')
  const autoApprovedWfs    = pendingWorkflows.filter(w => w.status === 'approved' && w.autoApprovedAt)

  // Platform catalog workflows enriched with risk reports
  const platformWfsWithRisk = INCOMPLETE_WORKFLOWS.map(wf => ({
    id: wf.id, name: wf.name, summary: wf.description, isPlatform: true,
    chain: wf.agents.map(a => ({ agent_name: a.name, description: a.role, tools: a.tools, status: a.status })),
    riskReport: generateWorkflowRisk(wf.agents.map(a => ({ tools: a.tools, agent_name: a.name }))),
    allTools: wf.agents.flatMap(a => a.tools || []),
  }))

  const pendingPlatformWfs   = platformWfsWithRisk.filter(w => !platformApprovals[w.id])
  const approvedPlatformWfs  = platformWfsWithRisk.filter(w => platformApprovals[w.id] === 'approved')
  const rejectedPlatformWfs  = platformWfsWithRisk.filter(w => platformApprovals[w.id] === 'rejected')

  // All pending workflows (platform + user-submitted)
  const uploadedWithRisk = [
    ...pendingPlatformWfs,
    ...pendingUploaded.map(wf => ({
      ...wf, isPlatform: false,
      riskReport: generateWorkflowRisk(wf.chain || []),
      allTools: (wf.chain || []).flatMap(s => s.tools || []),
    })),
  ]

  // Approved workflows
  const approvedWorkflowsList = [
    ...approvedPlatformWfs,
    ...pendingWorkflows.filter(w => w.status === 'approved'),
  ]

  const totalPending = underReviewAgents.length + uploadedWithRisk.length
  const totalApproved = approvedAgents.length + approvedWorkflowsList.length
  const greenCount   = underReviewAgents.filter(a => a.riskReport?.level === 'green').length
    + uploadedWithRisk.filter(w => w.riskReport?.level === 'green').length

  return (
    <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3 }}
      className="space-y-6">

      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="font-display text-xl font-bold text-[#1A2340]">Agent Governance & Compliance</h1>
        </div>
        <div className="flex items-center gap-3">
          {totalPending > 0 && (
            <div className="flex items-center gap-1.5 px-3 py-2 rounded-xl bg-amber-50 border border-amber-200">
              <Clock size={14} className="text-amber-600" />
              <span className="text-sm font-semibold text-amber-700">{totalPending} awaiting review</span>
            </div>
          )}
          <div className="flex items-center gap-2 px-3 py-2 rounded-xl bg-emerald-50 border border-emerald-200">
            <Shield size={15} className="text-emerald-600" />
            <span className="text-sm font-semibold text-emerald-700">All Frameworks Compliant</span>
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 p-1 rounded-xl w-fit border" style={{ background: '#F7F8FA', borderColor: '#E2E8F0' }}>
        <button onClick={() => setActiveTab('registry')}
          className={'px-4 py-2 rounded-lg text-xs font-semibold transition-all ' + (activeTab === 'registry' ? 'bg-white text-[#1A2340] shadow-sm border border-[#E2E8F0]' : 'text-[#9BA8BA] hover:text-[#4A5568]')}>
          Compliance
        </button>
        <button onClick={() => setActiveTab('reviewer')}
          className={'flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-semibold transition-all ' + (activeTab === 'reviewer' ? 'bg-[#1A2340] text-white shadow-sm' : 'text-[#4A5568] hover:bg-white hover:shadow-sm')}>
          Reviewer
          {totalPending > 0
            ? <span className="px-1.5 py-0.5 rounded-full text-[10px] font-bold bg-amber-400 text-white">{totalPending}</span>
            : <span className="px-1.5 py-0.5 rounded-full text-[10px] font-bold bg-[#E2E8F0] text-[#9BA8BA]">0</span>
          }
        </button>
      </div>

      {/* ── Compliance tab ── */}
      {activeTab === 'registry' && (
        <div className="space-y-3">
          <p className="text-xs text-[#718096]">Click any framework card to read its compliance requirements for DLX_AGENTIC_OS agents.</p>
          <div className="grid grid-cols-3 gap-4">
            {FRAMEWORKS.map(f => {
              const isOpen = openFramework === f.label
              return (
                <motion.div key={f.label} layout
                  className="card overflow-hidden cursor-pointer hover:shadow-md transition-all"
                  style={{ borderColor: isOpen ? f.color : undefined, border: isOpen ? `2px solid ${f.color}` : undefined }}
                  onClick={() => setOpenFramework(isOpen ? null : f.label)}>
                  <div className="p-4">
                    <div className="flex items-center justify-between mb-3">
                      <div className="w-10 h-10 rounded-xl flex items-center justify-center" style={{ background: `${f.color}15` }}>
                        <CheckCircle size={18} style={{ color: f.color }} />
                      </div>
                      <div className="flex items-center gap-1.5">
                        <span className="text-[10px] font-bold px-2 py-0.5 rounded-full" style={{ background: `${f.color}15`, color: f.color }}>Compliant</span>
                        {isOpen ? <ChevronUp size={13} style={{ color: f.color }} /> : <ChevronDown size={13} className="text-[#CBD5E0]" />}
                      </div>
                    </div>
                    <p className="text-sm font-bold text-[#1A2340]">{f.label}</p>
                    <p className="text-xs text-[#9BA8BA] mt-0.5">{f.desc}</p>
                  </div>
                  <AnimatePresence>
                    {isOpen && (
                      <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }}
                        transition={{ duration: 0.22 }} className="overflow-hidden">
                        <div className="px-4 pb-4 pt-1 border-t" style={{ borderColor: `${f.color}30`, background: `${f.color}06` }}>
                          <p className="text-xs text-[#4A5568] leading-relaxed">{f.detail}</p>
                        </div>
                      </motion.div>
                    )}
                  </AnimatePresence>
                </motion.div>
              )
            })}
          </div>
        </div>
      )}

      {/* ── Reviewer tab ── */}
      {activeTab === 'reviewer' && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm font-semibold text-[#1A2340]">Pending Review</p>
              <p className="text-xs text-[#718096] mt-0.5">Built agents and uploaded workflow configs awaiting compliance sign-off</p>
            </div>
            {greenCount > 0 && (
              <button
                onClick={() => {
                  autoApproveGreen()
                  uploadedWithRisk.filter(w => w.riskReport?.level === 'green').forEach(w => updateWorkflowStatus(w.id, 'approved'))
                  addToast({ type: 'success', title: greenCount + ' item' + (greenCount !== 1 ? 's' : '') + ' auto-approved', message: 'All low-risk items are now active.' })
                }}
                className="flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-bold text-white transition-all hover:opacity-90"
                style={{ background: 'linear-gradient(135deg,#10B981,#059669)' }}>
                <Zap size={13} /> Auto-approve all green ({greenCount})
              </button>
            )}
          </div>

          {totalPending === 0 ? (
            <div className="card p-12 text-center">
              <div className="w-12 h-12 rounded-2xl bg-emerald-50 border border-emerald-200 flex items-center justify-center mx-auto mb-3">
                <CheckCircle size={22} className="text-emerald-500" />
              </div>
              <p className="text-sm font-semibold text-[#1A2340] mb-1">Nothing pending review</p>
              <p className="text-xs text-[#718096]">Build an agent in Solution Builder, or upload a workflow config from the Homepage.</p>
            </div>
          ) : (
            <div className="space-y-5">

              {/* ── Uploaded workflow configs ── */}
              {uploadedWithRisk.length > 0 && (
                <div className="space-y-3">
                  <div className="flex items-center gap-2">
                    <GitMerge size={13} className="text-[#9BA8BA]" />
                    <p className="text-xs font-bold text-[#9BA8BA] uppercase tracking-wider">Workflows Under Review ({uploadedWithRisk.length})</p>
                  </div>
                  {uploadedWithRisk.map(wf => {
                    const rl     = REPORT_LEVEL[wf.riskReport.level]
                    const isOpen = reviewExpanded === wf.id
                    return (
                      <RiskReportCard
                        key={wf.id}
                        report={wf.riskReport}
                        tools={wf.allTools}
                        chain={wf.chain || []}
                        rl={rl}
                        title={wf.name}
                        subtitle={wf.summary || `${wf.agentCount || (wf.chain || []).length} agents · Uploaded workflow config`}
                        approveLabel="Approve"
                        isOpen={isOpen}
                        onToggle={() => setReviewExpanded(isOpen ? null : wf.id)}
                        onApprove={() => {
                          if (wf.isPlatform) decidePlatform(wf.id, 'approved')
                          else updateWorkflowStatus(wf.id, 'approved')
                          addToast({ type: 'success', title: 'Workflow approved', message: wf.name + ' is now live in Discover Hub.' })
                        }}
                        onReject={() => {
                          if (wf.isPlatform) decidePlatform(wf.id, 'rejected')
                          else rejectWorkflow(wf.id)
                          addToast({ type: 'error', title: 'Workflow rejected', message: wf.name + ' has been rejected.' })
                        }}
                      />
                    )
                  })}
                </div>
              )}

              {/* ── Auto-approved workflows ── */}
              {autoApprovedWfs.length > 0 && (
                <div className="space-y-2">
                  <div className="flex items-center gap-2">
                    <CheckCircle size={13} className="text-emerald-500" />
                    <p className="text-xs font-bold text-[#9BA8BA] uppercase tracking-wider">Auto-Approved Workflows ({autoApprovedWfs.length})</p>
                    <span className="text-[10px] text-emerald-600 font-medium">— all pipeline agents cleared compliance</span>
                  </div>
                  {autoApprovedWfs.map(wf => (
                    <div key={wf.id} className="flex items-center gap-3 px-4 py-3 rounded-xl bg-emerald-50 border border-emerald-200">
                      <CheckCircle size={16} className="text-emerald-500 flex-shrink-0" />
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-semibold text-[#1A2340]">{wf.name}</p>
                        <p className="text-xs text-emerald-600 mt-0.5">Automatically approved — all pipeline agents cleared compliance review</p>
                      </div>
                      <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-200 text-emerald-800">Auto-Approved</span>
                    </div>
                  ))}
                </div>
              )}

              {/* ── Built agents under review ── */}
              {underReviewAgents.length > 0 && (
                <div className="space-y-3">
                  <div className="flex items-center gap-2">
                    <Bot size={13} className="text-[#9BA8BA]" />
                    <p className="text-xs font-bold text-[#9BA8BA] uppercase tracking-wider">Built Agents ({underReviewAgents.length})</p>
                  </div>
                  {underReviewAgents.map(agent => {
                    const rl     = REPORT_LEVEL[agent.riskReport?.level || 'yellow']
                    const isOpen = reviewExpanded === agent.id
                    return (
                      <RiskReportCard
                        key={agent.id}
                        report={agent.riskReport}
                        tools={agent.tools}
                        systemPrompt={agent.systemPrompt}
                        guardrails={agent.guardrails}
                        rl={rl}
                        title={agent.name}
                        subtitle={agent.description || 'Custom agent'}
                        approveLabel="Approve"
                        isOpen={isOpen}
                        onToggle={() => setReviewExpanded(isOpen ? null : agent.id)}
                        onApprove={() => {
                          if (agent.isPlatform) decidePlatform(agent.id, 'approved')
                          else approveAgent(agent.id)
                          addToast({ type: 'success', title: 'Agent approved', message: agent.name + ' is now active in Discover Hub.' })
                        }}
                        onReject={() => {
                          if (agent.isPlatform) decidePlatform(agent.id, 'rejected')
                          else rejectAgent(agent.id)
                          addToast({ type: 'error', title: 'Agent rejected', message: agent.name + ' has been rejected.' })
                        }}
                      />
                    )
                  })}
                </div>
              )}

            </div>
          )}

          {/* ── Approved section ── */}
          {(approvedAgents.length > 0 || approvedWorkflowsList.length > 0) && (
            <div className="mt-6 space-y-3">
              <div className="flex items-center gap-2 pb-1 border-b border-[#E2E8F0]">
                <CheckCircle size={14} className="text-emerald-500" />
                <p className="text-xs font-bold text-[#1A2340] uppercase tracking-wider">Approved ({approvedAgents.length + approvedWorkflowsList.length})</p>
              </div>
              {[...approvedWorkflowsList, ...approvedAgents].map(item => (
                <div key={item.id} className="flex items-center gap-3 px-4 py-3 rounded-xl bg-emerald-50 border border-emerald-200">
                  <CheckCircle size={16} className="text-emerald-500 flex-shrink-0" />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-semibold text-[#1A2340]">{item.name}</p>
                    <p className="text-xs text-emerald-600 mt-0.5">{item.summary || item.description || 'Approved and active in Discover Hub'}</p>
                  </div>
                  <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-200 text-emerald-800">Approved</span>
                </div>
              ))}
            </div>
          )}

          {/* ── Rejected section ── */}
          {(rejectedPlatformAgents.length > 0 || rejectedPlatformWfs.length > 0) && (
            <div className="mt-4 space-y-3">
              <div className="flex items-center gap-2 pb-1 border-b border-[#F0F2F5]">
                <div className="w-3 h-3 rounded-full bg-red-400 flex-shrink-0" />
                <p className="text-xs font-bold text-[#9BA8BA] uppercase tracking-wider">Rejected ({rejectedPlatformAgents.length + rejectedPlatformWfs.length})</p>
              </div>
              {[...rejectedPlatformWfs, ...rejectedPlatformAgents].map(item => (
                <div key={item.id} className="flex items-center gap-3 px-4 py-3 rounded-xl bg-[#F7F8FA] border border-[#E2E8F0] opacity-60">
                  <div className="w-4 h-4 rounded-full bg-red-200 flex items-center justify-center flex-shrink-0">
                    <div className="w-1.5 h-1.5 rounded-full bg-red-500" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-semibold text-[#718096] line-through">{item.name}</p>
                    <p className="text-xs text-[#9BA8BA] mt-0.5">Removed from Discover Hub</p>
                  </div>
                  <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-red-100 text-red-700">Rejected</span>
                </div>
              ))}
            </div>
          )}

        </div>
      )}

    </motion.div>
  )
}
