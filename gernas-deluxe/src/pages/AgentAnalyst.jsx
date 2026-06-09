import { useState, useRef, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { motion, AnimatePresence } from 'framer-motion'
import useStore from '../store/useStore'
import {
  FileText, BookOpen, Briefcase, Layout, Code2, FlaskConical,
  HelpCircle, MessageSquare, ChevronLeft, Search,
  Sparkles, Bot, RefreshCw, ChevronRight, CheckCircle, AlertCircle, X,
  Shield, Wrench, Brain, Play, RotateCcw, Rocket, Settings, Users,
  Clock, Server, Globe, ArrowRight, Check, Plus, Zap, Lock,
  Download, Copy, ExternalLink, PartyPopper
} from 'lucide-react'

/* ─── Sidebar tools ──────────────────────────────────────────────────────────── */
const TOOLS = [
  { id: 'brd',          label: 'BRD Assistant',    sub: 'Create & manage BRDs',            icon: FileText    },
  { id: 'confluence',   label: 'Confluence',        sub: 'Browse & integrate docs',         icon: BookOpen    },
  { id: 'jira',         label: 'Jira',              sub: 'Project tracking & issues',       icon: Briefcase   },
  { id: 'architecture', label: 'Architecture',      sub: 'Technical architecture planning', icon: Layout      },
  { id: 'pair-prog',    label: 'Pair Programming',  sub: 'MCP setup & IDE integration',     icon: Code2       },
  { id: 'agent-analyst',label: 'Agent Analyst',     sub: 'Build agents from docs',          icon: Bot, isNew: true },
  { id: 'testing',      label: 'Testing',           sub: 'Test scenarios & Katalon',        icon: FlaskConical},
]

/* ─── Confluence pages ───────────────────────────────────────────────────────── */
const CONFLUENCE_PAGES = [
  {
    id: 'p1', title: 'SMB Merchant Onboarding — Process BRD', space: 'Merchant Services', updated: '2 days ago',
    content: {
      description: 'Automates the full SMB merchant onboarding lifecycle — from application submission to first live transaction. Handles KYB verification, risk scoring, terminal provisioning, document collection, and banking account verification.',
      systemPrompt: `You are the SMB Onboarding Agent deployed by Deluxe Corporation on the DLX_AGENTIC_OS platform.\n\nSEGMENT: Merchant Services\nPURPOSE: Automate the SMB merchant onboarding lifecycle end-to-end.\n\nYOUR RESPONSIBILITIES:\n  - Run KYB (Know Your Business) verification against government and commercial databases\n  - Score merchant risk across 12 signals: credit, fraud history, industry risk\n  - Provision and configure payment terminals\n  - Collect, validate, and store onboarding documents securely\n  - Verify banking accounts via micro-deposit\n\nAUTHORISED TOOLS:\n  - kyb-verify, crm-write, email-send, terminal-config, bank-verify\n\nGUARDRAILS:\n  - Never approve an application with risk score > 85 without human review\n  - Log every verification step with timestamp and rationale\n  - Escalate SAR-level flags to Compliance immediately\n  - Comply with PCI DSS v4.0 and BSA/AML requirements`,
    },
  },
  {
    id: 'p2', title: 'Invoice Reconciliation — Finance Ops Spec', space: 'B2B Payments', updated: '5 days ago',
    content: {
      description: 'Eliminates manual invoice-to-payment matching across multiple ERP systems. Auto-posts matched transactions to the GL and flags exceptions for human review. Integrates natively with SAP, Oracle, and NetSuite.',
      systemPrompt: `You are the Invoice Reconciliation Agent deployed by Deluxe Corporation.\n\nSEGMENT: B2B Payments\nPURPOSE: Automate invoice-to-payment matching and GL posting across ERP systems.\n\nYOUR RESPONSIBILITIES:\n  - Match invoices to payments across SAP, Oracle, and NetSuite\n  - Flag unmatched items with suggested resolution\n  - Write journal entries to General Ledger\n  - Provide real-time reconciliation with full audit trail\n\nAUTHORISED TOOLS:\n  - erp-read, payment-match, gl-write, exception-flag\n\nGUARDRAILS:\n  - Never write-off amounts > $10,000 without CFO approval\n  - All GL postings require dual-control verification\n  - Comply with SOX Section 302 and NACHA operating rules\n  - Maintain immutable audit log for every transaction`,
    },
  },
  {
    id: 'p3', title: 'Churn Prevention — Print Revenue Playbook', space: 'Print & Retention', updated: '1 week ago',
    content: {
      description: 'Monitors print customer order patterns to detect early churn signals (60+ days out) and automatically triggers retention campaigns before customers leave.',
      systemPrompt: `You are the Churn Prevention Agent deployed by Deluxe Corporation.\n\nSEGMENT: Print & Retention\nPURPOSE: Detect churn signals early and trigger automated retention campaigns.\n\nYOUR RESPONSIBILITIES:\n  - Monitor order frequency and recency across all print customers\n  - Score churn risk using purchase pattern analysis\n  - Execute targeted email retention campaigns\n  - Escalate high-value at-risk accounts to sales team\n\nAUTHORISED TOOLS:\n  - crm-read, order-history, email-send, sales-alert\n\nGUARDRAILS:\n  - Never apply discounts > 20% without Sales VP approval\n  - Respect customer communication preferences and opt-outs\n  - Comply with CCPA data handling requirements\n  - Campaign sends limited to 3 per customer per 30-day window`,
    },
  },
  {
    id: 'p4', title: 'Fraud Detection — Real-Time Risk Architecture', space: 'Merchant Services', updated: '3 days ago',
    content: {
      description: 'Provides sub-second real-time fraud detection across the merchant transaction stream with 99.8% accuracy. Automatically holds suspicious accounts and generates compliance reports.',
      systemPrompt: `You are the Fraud Detection Agent deployed by Deluxe Corporation.\n\nSEGMENT: Merchant Services\nPURPOSE: Real-time fraud detection and automated compliance response.\n\nYOUR RESPONSIBILITIES:\n  - Monitor live transaction stream for anomaly signals\n  - Hold accounts on high-confidence fraud detection (>95% threshold)\n  - Generate compliance alerts and regulatory notifications\n  - Maintain SAR documentation for BSA/AML requirements\n\nAUTHORISED TOOLS:\n  - txn-stream, anomaly-detect, hold-trigger, compliance-alert\n\nGUARDRAILS:\n  - Account freeze requires confidence score > 0.95\n  - SAR filing must be reviewed by Compliance Officer before submission\n  - Never notify law enforcement directly — escalate to Legal\n  - Comply with PCI DSS v4.0, BSA/AML, and SOX`,
    },
  },
  {
    id: 'p5', title: 'Data Enrichment — SMB Profile Pipeline', space: 'Data Solutions', updated: '4 days ago',
    content: {
      description: 'Continuously enriches SMB customer profiles with firmographic, revenue, and payment behavior signals to improve segmentation, propensity scoring, and upsell targeting.',
      systemPrompt: `You are the Data Enrichment Agent deployed by Deluxe Corporation.\n\nSEGMENT: Data Solutions\nPURPOSE: Continuously enrich SMB profiles for better segmentation and targeting.\n\nYOUR RESPONSIBILITIES:\n  - Fetch firmographic and revenue data from external enrichment sources\n  - Score payment behavior trends for propensity modeling\n  - Refresh and de-duplicate customer profiles continuously\n  - Push updated segments to CRM and marketing systems\n\nAUTHORISED TOOLS:\n  - data-fetch, profile-update, signal-score, warehouse-write\n\nGUARDRAILS:\n  - Only use approved data vendors from the Data Governance list\n  - PII fields must be hashed before writing to analytics warehouse\n  - Comply with CCPA — honor all opt-out flags\n  - Refresh cycle capped at once per 24 hours per profile`,
    },
  },
]

/* ─── Phase config ───────────────────────────────────────────────────────────── */
const PHASES = [
  { id: 1, label: 'Agent Designer'       },
  { id: 2, label: 'Simulation & Testing' },
  { id: 3, label: 'Deployment'           },
]

const P1_SECTIONS = [
  { id: 'identity',     label: 'Agent Identity',     icon: Bot    },
  { id: 'tools',        label: 'Tools',              icon: Wrench },
  { id: 'intelligence', label: 'Intelligence Layer', icon: Brain  },
  { id: 'guardrails',   label: 'Guardrails',         icon: Shield },
]

const TEST_SCENARIOS = [
  { id: 't1', name: 'Core Logic Validation',     desc: 'Verifies primary workflow execution paths',        result: 'pass'    },
  { id: 't2', name: 'Tool Connectivity Check',   desc: 'Confirms all authorized tools respond correctly',  result: 'pass'    },
  { id: 't3', name: 'Edge Case: Empty Input',    desc: 'Handles null and malformed inputs gracefully',     result: 'pass'    },
  { id: 't4', name: 'Load Test: 100 req/s',      desc: 'Performance under concurrent request load',        result: 'warning', note: 'P95 latency: 340ms — within SLA threshold' },
  { id: 't5', name: 'PII Detection & Masking',   desc: 'Ensures all PII fields are detected and masked',   result: 'pass'    },
  { id: 't6', name: 'Guardrail Enforcement',     desc: 'Validates all safety guardrails are applied',      result: 'pass'    },
  { id: 't7', name: 'Compliance Rule Check',     desc: 'Verifies regulatory compliance rules are enforced',result: 'pass'    },
]

/* ─── Helpers ────────────────────────────────────────────────────────────────── */
const SKILLS_MAP = {
  kyb:     ['KYB Verification',   'Risk Scoring',      'Terminal Provisioning', 'Document Validation',  'Bank Verification'  ],
  invoice: ['Invoice Matching',   'GL Posting',        'Exception Flagging',    'Audit Trail',          'ERP Integration'    ],
  churn:   ['Churn Scoring',      'Email Campaigns',   'Sales Alerting',        'Order Analytics',      'Retention Logic'    ],
  fraud:   ['Anomaly Detection',  'Account Hold',      'SAR Filing',            'Compliance Alerts',    'Txn Monitoring'     ],
  data:    ['Profile Enrichment', 'Signal Scoring',    'De-duplication',        'Segmentation',         'CRM Sync'           ],
  default: ['Data Processing',    'API Integration',   'Workflow Automation',   'Monitoring',           'Reporting'          ],
}

function getSkills(prompt = '') {
  if (prompt.includes('KYB'))    return SKILLS_MAP.kyb
  if (prompt.includes('Invoice'))return SKILLS_MAP.invoice
  if (prompt.includes('Churn'))  return SKILLS_MAP.churn
  if (prompt.includes('Fraud'))  return SKILLS_MAP.fraud
  if (prompt.includes('nrich'))  return SKILLS_MAP.data
  return SKILLS_MAP.default
}

function extractTools(prompt = '') {
  const m = prompt.match(/AUTHORISED TOOLS:\s*\n\s*- (.+)/)
  if (!m) return ['data-read', 'api-call', 'workflow-trigger', 'audit-log']
  return m[1].split(',').map(t => t.trim())
}

function extractGuardrails(prompt = '') {
  const lines = prompt.split('\n')
  const idx   = lines.findIndex(l => l.includes('GUARDRAILS:'))
  if (idx === -1) return []
  return lines.slice(idx + 1).filter(l => l.trim().startsWith('-')).slice(0, 5).map(l => l.replace(/^[\s-]+/, ''))
}

function toSlug(name = '') {
  return name.toLowerCase().replace(/\s+/g, '-').replace(/[^a-z0-9-]/g, '')
}

/* ─── Clean agent name from raw title ──────────────────────────────────────── */
function cleanAgentName(raw = '') {
  const base = raw.includes('—') ? raw.split('—')[0].trim() : raw.trim()
  const words = base.split(/\s+/)
  // Looks like an agent / assistant already → keep it
  if (/agent|assistant|bot|engine|service|system/i.test(base)) return base
  // Reads like a sentence (contains common verbs) → extract subject + append "Agent"
  const verbMatch = base.match(/^(.{3,30}?)\s+(?:needs?|wants?|is|are|has|have|will|should|can|must|does|gets?|helps?)\b/i)
  if (verbMatch) return verbMatch[1].trim() + ' Agent'
  // Too many words → take first 4
  if (words.length > 4) return words.slice(0, 4).join(' ') + ' Agent'
  return base
}

/* ─── Check if a question is within the agent's configured scope ────────────── */
function isInScope(question, systemPrompt) {
  if (!systemPrompt) return true
  const content = systemPrompt.toLowerCase()
  const qWords  = question.toLowerCase().split(/\W+/).filter(w => w.length > 3)
  // If any meaningful question word appears in the system prompt → in scope
  return qWords.some(w => content.includes(w))
}

/* ─── Domain detector ───────────────────────────────────────────────────────── */
function detectDomain(prompt = '') {
  if (/churn|retention|cancel|print customer/i.test(prompt))           return 'churn'
  if (/KYB|onboard|merchant|terminal|kyc/i.test(prompt))              return 'onboarding'
  if (/fraud|anomaly|transaction.{0,30}hold|SAR/i.test(prompt))       return 'fraud'
  if (/invoice|reconcil|GL posting|ERP|SAP|Oracle|NetSuite/i.test(prompt)) return 'invoice'
  if (/enrich|firmograph|segmentation|propensity/i.test(prompt))      return 'enrichment'
  return 'general'
}

/* ─── Domain-specific rich responses ───────────────────────────────────────── */
function getDomainResponse(q, domain, guardrails) {

  // ── CHURN domain ────────────────────────────────────────────────────────────
  if (domain === 'churn') {
    if (/churn|reason|why.*leav|why.*cancel|why.*leave|signals?|indicator/i.test(q)) {
      return `Based on my analysis of print customer behavior, the primary **churn signals** I continuously monitor are:\n\n• **Declining order frequency** — Customers who haven't ordered in 45+ days show 3× higher churn risk\n• **Reduced spend velocity** — A >20% drop in monthly spend is a strong early-warning indicator\n• **Product mix narrowing** — Customers consolidating from multiple SKUs to a single line signal disengagement\n• **Dormancy after price inquiry** — Request for pricing followed by silence is a high-confidence churn predictor\n• **Falling campaign engagement** — Dropping open/click rates on communications\n• **Competitive migration pattern** — Reduced exclusive ordering, split across vendors\n\nI score these signals daily and trigger automated retention campaigns **60+ days before** customers formally churn.`
    }
    if (/campaign|retention strategy|prevent|how do you retain/i.test(q)) {
      return `My retention campaigns are **tiered automatically by risk score**:\n\n• **Score 60–74%**: Personalized re-engagement email with relevant product spotlight\n• **Score 75–85%**: Targeted loyalty offer + dedicated account manager call scheduled\n• **Score >85%**: High-value escalation to Sales VP with full account context and recommended action\n\nAll campaigns respect CCPA opt-out preferences and are **capped at 3 per customer per 30-day window** to avoid fatigue.\n\nMy guardrail: ${guardrails[0] || 'discount thresholds require approval before application'}\n\nAverage result: **18–22% reduction** in churn rate in accounts I manage.`
    }
    if (/score|predict|model|how.*detect/i.test(q)) {
      return `I use a **multi-signal churn scoring model** on historical purchase patterns:\n\n**Input signals:**\n• Order recency (days since last order)\n• Order frequency trend (30/60/90-day rolling averages)\n• Spend velocity change month-over-month\n• Product breadth (distinct SKUs ordered)\n• Campaign response rate history\n\n**Output:** A 0–100 risk score, recalculated **daily per customer**, capped at once per 24 hours per my data guardrail.\n\nCustomers above **60** enter the automated retention workflow. Above **85** triggers Sales escalation.`
    }
  }

  // ── ONBOARDING domain ───────────────────────────────────────────────────────
  if (domain === 'onboarding') {
    if (/document|require|what.*need|kyb|kyc|submit/i.test(q)) {
      return `For SMB merchant onboarding, I collect and validate the following:\n\n**Always required:**\n• Business registration certificate / Articles of Incorporation\n• EIN / Tax ID documentation\n• Principal owner government-issued ID\n• Voided business check or bank letter for account verification\n• Signed merchant agreement\n\n**Conditionally required:**\n• Processing history (existing volume merchants)\n• Financial statements (high-risk or high-volume applications)\n• Business license (industry-specific)\n\nAll docs are validated via **\`kyb-verify\`** against government & commercial databases. Incomplete applications enter a pending queue with automated follow-up.`
    }
    if (/risk|score|approve|decline|threshold/i.test(q)) {
      return `I evaluate merchant risk across **12 signals**:\n\n• Credit history & chargeback ratio\n• Fraud and dispute history\n• Industry risk category (MCC code)\n• Business age and financial stability\n• Geographic risk indicators\n• Processing volume vs. reported revenue\n• Principal owner personal credit\n• Beneficial ownership structure\n• OFAC sanctions & watchlist checks\n• Web presence & online reputation\n\n**Key guardrail:** ${guardrails[0] || 'Applications with risk score > 85 require human review before approval.'}\n\nThis threshold is enforced at the platform level and cannot be bypassed.`
    }
    if (/how long|timeline|how fast|duration/i.test(q)) {
      return `**Typical onboarding timeline:**\n\n• **Standard application** (complete docs, low risk): 2–4 hours automated processing\n• **Pending document** collection: Up to 3 business days (automated follow-up every 24h)\n• **Manual review** (risk score >85 or KYB flag): 1–2 business days with Compliance\n• **Terminal provisioning** post-approval: Same-day to 48 hours depending on hardware shipment\n\nAll timelines are tracked with SLA alerts. Breaches trigger escalation to the onboarding ops team.`
    }
  }

  // ── FRAUD domain ────────────────────────────────────────────────────────────
  if (domain === 'fraud') {
    if (/detect|how.*work|signal|indicator|identify|spot/i.test(q)) {
      return `I use **real-time ML anomaly detection** on the live transaction stream. Primary fraud signals:\n\n• **Velocity anomalies** — Unusual transaction frequency in a short window\n• **Geographic impossibility** — Transactions from distant locations in impossible timeframes\n• **Amount pattern deviation** — Transactions far outside a merchant's established range\n• **BIN clustering** — Multiple cards from the same Bank Identification Number in rapid succession\n• **Device/IP fingerprinting** — Repeat use of flagged devices or proxied IPs\n• **Behavioral biometrics** — Deviation from the customer's established interaction patterns\n\nDetection runs in **sub-second**. ${guardrails[0] || 'Accounts are auto-held at >95% confidence — lower scores go to review queue.'}`
    }
    if (/action|what happen|response|hold|freeze/i.test(q)) {
      return `When fraud is detected, my response is **tiered by confidence score**:\n\n• **70–84% confidence** → Flag transaction, add to monitoring queue, alert fraud analyst\n• **85–94% confidence** → Soft hold on new transactions, customer notification sent\n• **>95% confidence** → Hard account freeze, immediate compliance alert generated\n\n**Guardrail:** ${guardrails[0] || 'Account freeze requires confidence score > 0.95.'}\n**Guardrail:** ${guardrails[1] || 'SAR filing requires Compliance Officer review before submission.'}\n\nI never notify law enforcement directly — all such escalations go through Legal.`
    }
  }

  // ── INVOICE domain ──────────────────────────────────────────────────────────
  if (domain === 'invoice') {
    if (/match|reconcil|how.*work|process|logic/i.test(q)) {
      return `I perform **automated 3-way matching** across SAP, Oracle, and NetSuite:\n\n**Matching logic:**\n1. ✅ **Exact match** — Invoice = PO = GR amount → auto-post to GL\n2. ✅ **Tolerance match** — Variance within approved tolerance → auto-post with notation\n3. ⚠️ **Partial match** — Partial delivery or split invoice → flag and request confirmation\n4. ❌ **No match** — Missing PO, amount discrepancy, or duplicate → route to exception queue\n\nExceptions include a **suggested resolution** (e.g., "Possible duplicate — matches INV-20241103 from same vendor").\n\n**Guardrail:** ${guardrails[0] || 'Write-offs > $10,000 require CFO approval.'}`
    }
    if (/exception|error|mismatch|dispute/i.test(q)) {
      return `I handle invoice exceptions with a **structured resolution workflow**:\n\n**Common exception types:**\n• Duplicate invoice detection (same vendor, amount, date)\n• PO number missing or invalid\n• Amount mismatch outside tolerance\n• Currency conversion discrepancies\n• Goods not received (GR) not confirmed\n\nEach exception is logged with a unique ID, assigned to the relevant AP team member, and tracked to resolution. ${guardrails[1] || 'All GL postings require dual-control verification.'}\n\nFull audit trail maintained per SOX Section 302 requirements.`
    }
  }

  // ── ENRICHMENT domain ───────────────────────────────────────────────────────
  if (domain === 'enrichment') {
    if (/source|data|where|enrich|signal|what.*use/i.test(q)) {
      return `I enrich SMB profiles from **multiple approved data sources**:\n\n**External sources:**\n• Firmographic data: D&B Hoovers, ZoomInfo (size, revenue, industry, employee count)\n• Payment behavior: Internal transaction history & trend modeling\n• Web presence: Domain age, social signals, review platform scores\n• Credit signals: Business credit file from approved bureaus only\n\n**Enrichment outputs generated:**\n• Updated revenue band & employee count\n• Industry segment + sub-segment classification\n• Propensity scores: upsell, cross-sell, and churn risk\n• CRM segment membership tags for marketing\n\n**Guardrail:** ${guardrails[0] || 'Only approved Data Governance vendors are used.'}\n**Guardrail:** PII fields are hashed before writing to the analytics warehouse.`
    }
    if (/segment|target|propensit|score/i.test(q)) {
      return `I produce **three propensity scores** per SMB profile:\n\n• **Upsell score (0–100)** — Likelihood to purchase a higher-tier product\n• **Cross-sell score (0–100)** — Likelihood to adopt an adjacent product line\n• **Churn risk score (0–100)** — Probability of attrition in next 90 days\n\nScores are pushed to CRM and marketing automation systems after each enrichment cycle (capped at once per 24 hours per profile).\n\nSegments with upsell score >75 are surfaced to the sales team via the **\`sales-alert\`** integration for targeted outreach.`
    }
  }

  return null // no domain match — fall through to generic handlers
}

/* ─── Tool label map ────────────────────────────────────────────────────────── */
const TOOL_LABELS = {
  'kyb-verify':      'KYB verification against government & commercial databases',
  'crm-write':       'write customer records to CRM systems',
  'crm-read':        'read customer data from CRM systems',
  'email-send':      'send automated email communications',
  'terminal-config': 'configure and provision payment terminals',
  'bank-verify':     'verify banking accounts via micro-deposit',
  'erp-read':        'read from ERP systems (SAP, Oracle, NetSuite)',
  'payment-match':   'match payments to invoices automatically',
  'gl-write':        'write journal entries to General Ledger',
  'exception-flag':  'flag exceptions for human review',
  'txn-stream':      'monitor live transaction streams in real-time',
  'anomaly-detect':  'detect anomalous patterns with ML scoring',
  'hold-trigger':    'trigger account holds on fraud detection',
  'compliance-alert':'generate compliance alerts & regulatory reports',
  'data-fetch':      'fetch firmographic data from external enrichment sources',
  'profile-update':  'update customer profiles with enriched signals',
  'signal-score':    'score payment behavior & propensity signals',
  'warehouse-write': 'write processed data to analytics warehouse',
  'order-history':   'retrieve historical order & purchase data',
  'sales-alert':     'send high-priority alerts to the sales team',
}

/* ─── Prompt parse helpers ──────────────────────────────────────────────────── */
function extractResponsibilities(prompt = '') {
  const lines = prompt.split('\n')
  const idx   = lines.findIndex(l => l.includes('RESPONSIBILITIES:'))
  if (idx === -1) return []
  return lines.slice(idx + 1).filter(l => l.trim().startsWith('-')).slice(0, 5).map(l => l.replace(/^[\s-]+/, '').trim())
}
function extractPurpose(prompt = '') {
  const m = prompt.match(/PURPOSE:\s*(.+)/); return m ? m[1].trim() : ''
}
function extractSegment(prompt = '') {
  const m = prompt.match(/SEGMENT:\s*(.+)/); return m ? m[1].trim() : 'Enterprise'
}
function extractCompliance(prompt = '') {
  return (prompt.match(/Comply with (.+)/g) || []).map(m => m.replace('Comply with ', '').replace(/[.,]$/, ''))
}

/* ─── Intelligent response generator ───────────────────────────────────────── */
function generateAgentResponse(question, systemPrompt, agentName) {
  const q              = question.toLowerCase().trim()
  const responsibilities = extractResponsibilities(systemPrompt)
  const tools          = extractTools(systemPrompt)
  const guardrails     = extractGuardrails(systemPrompt)
  const purpose        = extractPurpose(systemPrompt)
  const segment        = extractSegment(systemPrompt)
  const compliance     = extractCompliance(systemPrompt)
  const domain         = detectDomain(systemPrompt)
  const displayName    = cleanAgentName(agentName)

  // ── Try domain-specific response first ──
  const domainResp = getDomainResponse(q, domain, guardrails)
  if (domainResp) return domainResp

  // ── Out-of-scope check (skip for greetings and meta questions) ──
  const isMetaQ = /^(hi|hello|hey|what can you|who are you|introduce|capabilities|tool|guardrail|comply|audit|performance|latency|security|why|purpose|error|fail|escalat|human review)/i.test(q)
  if (!isMetaQ && !isInScope(q, systemPrompt)) {
    const capList = responsibilities.slice(0, 3).map(r => `• ${r}`).join('\n')
    return `That request is **outside my configured scope**. I'm specifically designed to ${purpose.toLowerCase() || 'handle enterprise workflows'}.\n\n**Here's what I CAN help with:**\n${capList}\n\nAsk me something related to ${domain !== 'general' ? `**${domain}** workflows` : 'my core responsibilities'} and I'll give you a detailed answer.`
  }

  // Greeting
  if (/^(hi|hello|hey|good\s|howdy|sup\b)/.test(q)) {
    return `Hello! I'm **${displayName}**, an enterprise AI agent deployed on DLX_AGENTIC_OS by Deluxe Corporation.\n\nMy purpose is to ${purpose.toLowerCase() || 'automate business workflows with precision and compliance'}.\n\nHow can I assist you today?`
  }

  // Capabilities / introduction
  if (q.includes('what can you do') || q.includes('capabilities') || q.includes('what do you do') || q.includes('introduce') || q.includes('who are you')) {
    return `I am **${displayName}**, operating in the **${segment}** segment.\n\n**Core responsibilities:**\n${responsibilities.map(r => `• ${r}`).join('\n')}\n\n**Authorized tools (${tools.length}):** ${tools.join(', ')}\n\nI operate with full audit logging and comply with all applicable regulatory standards.`
  }

  // Tools & integrations
  if (q.includes('tool') || q.includes('integration') || (q.includes('system') && q.includes('connect'))) {
    return `I have access to **${tools.length} authorized tools**:\n\n${tools.map(t => `• \`${t}\` — ${TOOL_LABELS[t] || 'specialized integration'}`).join('\n')}\n\nAll tool invocations are logged with timestamp, input, output, and decision rationale for full auditability.`
  }

  // Guardrails / restrictions
  if (q.includes('guardrail') || q.includes('limitation') || q.includes('restrict') || q.includes('cannot') || q.includes("can't") || q.includes('not allowed') || q.includes('boundary')) {
    return `My **mandatory guardrails** ensure safe, compliant operation:\n\n${guardrails.map((g, i) => `${i + 1}. ${g}`).join('\n')}\n\nThese rules are enforced at the platform level and cannot be overridden at runtime. Any violation triggers an immediate escalation.`
  }

  // Compliance / regulations
  if (q.includes('comply') || q.includes('compliance') || q.includes('regulation') || q.includes('pci') || q.includes('sox') || q.includes('aml') || q.includes('ccpa') || q.includes('bsa')) {
    return `Regulatory compliance is a first-class concern in my design.\n\n${compliance.length > 0 ? `**Applicable standards:**\n${compliance.map(r => `• ${r}`).join('\n')}\n\n` : ''}I maintain immutable audit logs, enforce data handling policies, and escalate regulatory flags to the Compliance Officer before taking further action.`
  }

  // Escalation / human review
  if (q.includes('escalat') || q.includes('human review') || q.includes('approval') || q.includes('hand off')) {
    return `I automatically escalate to human review when:\n\n• **Risk or confidence thresholds** in my guardrails are exceeded\n• **Regulatory flags** (SAR, AML, fraud signals) are triggered above threshold\n• **High-value actions** exceed authorization limits\n• I encounter **ambiguous edge cases** outside my training domain\n\nAll escalations include full context, audit trail, and a recommended action for the human reviewer.`
  }

  // Risk / scoring
  if (q.includes('risk') || q.includes('score') || q.includes('threshold')) {
    const rg = guardrails.find(g => /risk|score|threshold/i.test(g)) || guardrails[0] || 'Risk thresholds are defined per guardrail policy.'
    return `I perform **multi-signal risk assessment** as part of my core workflow.\n\n**Key threshold rule:** ${rg}\n\nScoring considers behavioral patterns, historical data, and real-time signals to produce a confidence-weighted result. All scoring decisions are logged with full rationale.`
  }

  // Error / failure handling
  if (q.includes('error') || q.includes('fail') || q.includes('exception') || q.includes('down') || q.includes('crash')) {
    return `I handle failures with a **graceful degradation** strategy:\n\n• **Tool failures** → retry with exponential backoff (max 3 attempts)\n• **Validation errors** → flag for human review with full error context\n• **Timeout conditions** → return partial results with status annotation\n• **Critical failures** → trigger on-call alert and preserve full state for recovery\n\nAll error events are shipped to the OPIK observability platform with stack trace and input context.`
  }

  // Audit / logging
  if (q.includes('audit') || q.includes('log') || q.includes('track') || q.includes('monitor') || q.includes('observ')) {
    return `I maintain **comprehensive audit trails** for every action:\n\n• Unique execution ID + timestamp per run\n• Full input/output for every tool call\n• Decision rationale at each workflow step\n• Escalation events with complete context\n• Performance metrics (latency, token usage, success rate)\n\nAll logs are shipped to **OPIK** with structured JSON format and retained per regulatory requirements.`
  }

  // Performance / latency
  if (q.includes('fast') || q.includes('latenc') || q.includes('performance') || q.includes('speed') || q.includes('how long')) {
    return `**Performance characteristics:**\n\n• **P50 latency:** ~120 ms per execution\n• **P95 latency:** ~340 ms (within SLA)\n• **Throughput:** up to 100 req/s (auto-scaled)\n• **Availability:** 99.9% SLA target\n• **Cold start:** <2 s (pre-warmed containers)\n\nAll metrics are monitored in real-time via the OPIK dashboard.`
  }

  // Purpose / why / goal
  if (q.includes('why') || q.includes('purpose') || q.includes('goal') || q.includes('objective') || q.includes('benefit')) {
    return `**Purpose:** ${purpose}\n\n**Business value delivered:**\n• Eliminates manual processing bottlenecks\n• Reduces human error in high-volume workflows\n• Ensures 100% compliance with audit requirements\n• Scales elastically with business demand\n• Provides real-time visibility into process health\n\nI'm designed to augment human teams — not replace them — by handling routine tasks while escalating complex decisions.`
  }

  // Security / data protection
  if (q.includes('security') || q.includes('pii') || q.includes('data protect') || q.includes('sensitive') || q.includes('encrypt')) {
    return `**Security and data protection practices:**\n\n• **PII detection & masking** applied on all inputs and outputs\n• **Data encrypted** in transit (TLS 1.3) and at rest (AES-256)\n• **Access control** enforced via IAM roles — principle of least privilege\n• **Secrets management** via AWS Secrets Manager — no hardcoded credentials\n• Compliance with applicable data regulations (${compliance.join(', ') || 'enterprise data governance standards'})`
  }

  // Smart contextual fallback — draw from responsibilities
  const relevantResp = responsibilities.find(r => {
    const rLower = r.toLowerCase()
    return q.split(' ').some(word => word.length > 4 && rLower.includes(word))
  })

  if (relevantResp) {
    return `Based on my core responsibilities in the **${segment}** segment, here's what I do related to your question:\n\n**Relevant capability:** ${relevantResp}\n\n${purpose ? `This supports my overall objective: *${purpose}*\n\n` : ''}I handle this by using my authorized tools: **${tools.slice(0, 3).join(', ')}**${tools.length > 3 ? `, and ${tools.length - 3} more` : ''}, with full audit logging at every step.\n\nWould you like to know more about how I handle a specific part of this workflow?`
  }

  return `Great question for the **${segment}** domain. My primary objective is to **${purpose.toLowerCase() || 'automate this business workflow'}**.\n\n**What I'd do for this scenario:**\n${responsibilities.slice(0, 3).map((r, i) => `${i + 1}. ${r}`).join('\n')}\n\nAll actions use my ${tools.length} authorized tools with a full audit trail.\n\nAsk me about a specific workflow step and I'll give you a detailed answer.`
}

/* ─── Phase num helper (for stepper with 'query' state) ────────────────────── */
const phaseIsActive = (pId, phase) => phase === pId || (phase === 'query' && pId === 2)
const phaseIsDone   = (pId, phase) => typeof phase === 'number' ? phase > pId : (phase === 'query' && pId < 2)

/* ─── Phase Stepper ─────────────────────────────────────────────────────────── */
function PhaseStepper({ phase }) {
  return (
    <div className="flex items-center gap-1 px-5 py-3 border-b border-gray-200 bg-[#F8F9FB] flex-shrink-0 overflow-x-auto">
      {PHASES.map((p, i) => {
        const active = phaseIsActive(p.id, phase)
        const done   = phaseIsDone(p.id, phase)
        return (
          <div key={p.id} className="flex items-center gap-1">
            <div className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-semibold whitespace-nowrap transition-all ${
              active ? 'bg-[#1A2340] text-white' : done ? 'text-emerald-600' : 'text-gray-400'
            }`}>
              <span className={`w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold flex-shrink-0 ${
                active ? 'bg-white text-[#1A2340]' : done ? 'bg-emerald-100 text-emerald-600' : 'bg-gray-200 text-gray-500'
              }`}>
                {done ? <Check size={9} /> : p.id}
              </span>
              Phase {p.id}: {p.label}
              {phase === 'query' && p.id === 2 && (
                <span className="ml-1 text-[9px] font-bold opacity-70">· Query Test</span>
              )}
            </div>
            {i < PHASES.length - 1 && (
              <div className={`h-px w-5 flex-shrink-0 ${done ? 'bg-emerald-300' : 'bg-gray-200'}`} />
            )}
          </div>
        )
      })}
    </div>
  )
}

/* ─── Phase 1: Agent Designer ───────────────────────────────────────────────── */
const SECTION_ORDER = ['identity', 'tools', 'intelligence', 'guardrails']

function Phase1({ agentName, setAgentName, agentVersion, setAgentVersion, author, setAuthor,
                  valueStream, setValueStream, description, setDesc, systemPrompt, setSysP,
                  skills, selectedSkills, toggleSkill, tools, guardrails,
                  onBack, onContinue }) {
  const [section, setSection]     = useState('identity')
  const [visited, setVisited]     = useState(new Set(['identity']))

  const currentIdx = SECTION_ORDER.indexOf(section)
  const isLast     = currentIdx === SECTION_ORDER.length - 1

  const goNext = () => {
    if (isLast) { onContinue(); return }
    const next = SECTION_ORDER[currentIdx + 1]
    setSection(next)
    setVisited(prev => new Set([...prev, next]))
  }

  const goPrev = () => {
    if (currentIdx === 0) { onBack(); return }
    setSection(SECTION_ORDER[currentIdx - 1])
  }

  const jumpTo = (id) => {
    // only allow jumping to already-visited sections
    if (visited.has(id)) setSection(id)
  }

  return (
    <div className="flex flex-1 overflow-hidden">
      {/* Mini left nav */}
      <div className="w-44 border-r border-gray-100 flex flex-col flex-shrink-0 bg-[#FAFAFA]">
        <div className="px-3 pt-4 pb-2">
          <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400">Configuration</p>
        </div>
        <nav className="flex-1 px-2 space-y-0.5">
          {P1_SECTIONS.map((s, i) => {
            const isActive    = section === s.id
            const isDone      = visited.has(s.id) && !isActive
            const isReachable = visited.has(s.id)
            const isLocked    = !isReachable
            return (
              <button key={s.id}
                onClick={() => jumpTo(s.id)}
                disabled={isLocked}
                className={`w-full flex items-center gap-2 px-3 py-2.5 rounded-lg text-left transition-all text-xs font-semibold ${
                  isActive    ? 'bg-[#FEF2F2] text-[#C8102E] border-l-2 border-[#C8102E]' :
                  isDone      ? 'text-emerald-600 hover:bg-emerald-50 cursor-pointer'       :
                  isLocked    ? 'text-gray-300 cursor-not-allowed'                          :
                                'text-gray-500 hover:bg-gray-100'
                }`}>
                {/* Status indicator */}
                <div className={`w-4 h-4 rounded-full flex items-center justify-center flex-shrink-0 text-[9px] font-bold ${
                  isActive ? 'bg-[#C8102E] text-white'         :
                  isDone   ? 'bg-emerald-100 text-emerald-600' :
                             'bg-gray-200 text-gray-400'
                }`}>
                  {isDone ? <Check size={8} /> : i + 1}
                </div>
                <span className="truncate">{s.label}</span>
              </button>
            )
          })}
        </nav>

        {/* Progress indicator */}
        <div className="px-3 py-3 border-t border-gray-100">
          <div className="flex gap-1">
            {SECTION_ORDER.map((id, i) => (
              <div key={id} className={`flex-1 h-1 rounded-full transition-all ${
                id === section ? 'bg-[#C8102E]' :
                visited.has(id) && id !== section ? 'bg-emerald-400' :
                'bg-gray-200'
              }`} />
            ))}
          </div>
          <p className="text-[9px] text-gray-400 mt-1.5 text-center">
            Step {currentIdx + 1} of {SECTION_ORDER.length}
          </p>
        </div>
      </div>

      {/* Section content */}
      <div className="flex-1 flex flex-col overflow-hidden">
        <div className="flex-1 overflow-y-auto px-5 py-4">
          <AnimatePresence mode="wait">
            <motion.div key={section} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.18 }}>

              {/* ── Agent Identity ── */}
              {section === 'identity' && (
                <div className="space-y-4 max-w-xl">
                  <p className="text-xs font-bold text-gray-400 uppercase tracking-widest mb-4">Agent Identity</p>

                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className="block text-xs font-bold text-gray-500 uppercase tracking-wider mb-1.5">Agent Name <span className="text-red-500">*</span></label>
                      <input value={agentName} onChange={e => setAgentName(e.target.value)}
                        className="w-full px-3 py-2 rounded-lg border border-gray-200 text-sm font-semibold text-gray-800 focus:outline-none focus:border-purple-400 bg-white" />
                    </div>
                    <div>
                      <label className="block text-xs font-bold text-gray-500 uppercase tracking-wider mb-1.5">Agent Version</label>
                      <input value={agentVersion} onChange={e => setAgentVersion(e.target.value)}
                        className="w-full px-3 py-2 rounded-lg border border-gray-200 text-sm text-gray-700 focus:outline-none focus:border-purple-400 bg-white" />
                    </div>
                    <div>
                      <label className="block text-xs font-bold text-gray-500 uppercase tracking-wider mb-1.5">Author</label>
                      <input value={author} onChange={e => setAuthor(e.target.value)}
                        className="w-full px-3 py-2 rounded-lg border border-gray-200 text-sm text-gray-700 focus:outline-none focus:border-purple-400 bg-white" />
                    </div>
                    <div>
                      <label className="block text-xs font-bold text-gray-500 uppercase tracking-wider mb-1.5">Value Stream</label>
                      <input value={valueStream} onChange={e => setValueStream(e.target.value)}
                        className="w-full px-3 py-2 rounded-lg border border-gray-200 text-sm text-gray-700 focus:outline-none focus:border-purple-400 bg-white" />
                    </div>
                  </div>

                  <div>
                    <label className="block text-xs font-bold text-gray-500 uppercase tracking-wider mb-1.5">Description <span className="text-red-500">*</span></label>
                    <textarea value={description} onChange={e => setDesc(e.target.value)} rows={3}
                      className="w-full px-3 py-2 rounded-lg border border-gray-200 text-sm text-gray-700 leading-relaxed focus:outline-none focus:border-purple-400 resize-none bg-white" />
                  </div>

                  <div>
                    <div className="flex items-center justify-between mb-1.5">
                      <label className="text-xs font-bold text-gray-500 uppercase tracking-wider">System Prompt <span className="text-red-500">*</span></label>
                      <span className="text-xs text-gray-400 font-mono">{systemPrompt.length} chars</span>
                    </div>
                    <textarea value={systemPrompt} onChange={e => setSysP(e.target.value)} rows={10}
                      className="w-full px-3 py-2 rounded-lg border border-gray-200 text-xs font-mono text-gray-700 leading-relaxed focus:outline-none focus:border-purple-400 resize-none bg-white" />
                  </div>

                  {/* Agent Skills */}
                  <div>
                    <label className="block text-xs font-bold text-gray-500 uppercase tracking-wider mb-2">Agent Skills <span className="text-red-500">*</span></label>
                    <div className="flex flex-wrap gap-2">
                      {skills.map((skill, i) => (
                        <button key={i} onClick={() => toggleSkill(skill)}
                          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-all ${
                            selectedSkills.has(skill)
                              ? 'bg-[#1A2340] text-white'
                              : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                          }`}>
                          {selectedSkills.has(skill) && <Check size={10} />}
                          {skill}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
              )}

              {/* ── Tools ── */}
              {section === 'tools' && (
                <div className="space-y-3 max-w-xl">
                  <p className="text-xs font-bold text-gray-400 uppercase tracking-widest mb-4">Authorized Tools</p>
                  {tools.map((tool, i) => (
                    <div key={i} className="flex items-center justify-between px-4 py-3 rounded-xl border border-gray-200 bg-white">
                      <div className="flex items-center gap-3">
                        <div className="w-8 h-8 rounded-lg bg-blue-50 border border-blue-100 flex items-center justify-center">
                          <Wrench size={13} className="text-blue-600" />
                        </div>
                        <div>
                          <p className="text-sm font-semibold text-gray-800 font-mono">{tool}</p>
                          <p className="text-xs text-gray-400 mt-0.5">Enabled · Authorized</p>
                        </div>
                      </div>
                      <span className="flex items-center gap-1 px-2 py-1 rounded-full text-xs font-semibold bg-emerald-50 text-emerald-600 border border-emerald-100">
                        <Check size={9} /> Active
                      </span>
                    </div>
                  ))}
                  <button className="flex items-center gap-2 px-3 py-2 rounded-lg border border-dashed border-gray-300 text-xs text-gray-400 hover:border-gray-400 hover:text-gray-500 transition-all w-full justify-center">
                    <Plus size={11} /> Add tool integration
                  </button>
                </div>
              )}

              {/* ── Intelligence Layer ── */}
              {section === 'intelligence' && (
                <div className="space-y-4 max-w-xl">
                  <p className="text-xs font-bold text-gray-400 uppercase tracking-widest mb-4">Intelligence Layer</p>
                  <div className="space-y-2">
                    {[
                      { id: 'm1', name: 'Claude Sonnet 4.5',     provider: 'Anthropic',  badge: 'Recommended', selected: true  },
                      { id: 'm2', name: 'Claude Opus 4.7',       provider: 'Anthropic',  badge: 'High Accuracy', selected: false },
                      { id: 'm3', name: 'GPT-4 Turbo',           provider: 'OpenAI',     badge: null,          selected: false },
                    ].map(model => (
                      <div key={model.id} className={`flex items-center justify-between px-4 py-3 rounded-xl border cursor-pointer transition-all ${
                        model.selected ? 'border-[#1A2340] bg-[#F7F8FA]' : 'border-gray-200 bg-white hover:border-gray-300'
                      }`}>
                        <div className="flex items-center gap-3">
                          <div className={`w-3.5 h-3.5 rounded-full border-2 flex items-center justify-center ${
                            model.selected ? 'border-[#C8102E]' : 'border-gray-300'
                          }`}>
                            {model.selected && <div className="w-1.5 h-1.5 rounded-full bg-[#C8102E]" />}
                          </div>
                          <div>
                            <p className="text-sm font-semibold text-gray-800">{model.name}</p>
                            <p className="text-xs text-gray-400">{model.provider}</p>
                          </div>
                        </div>
                        {model.badge && (
                          <span className={`px-2 py-0.5 rounded-full text-[10px] font-bold ${
                            model.selected ? 'bg-[#C8102E]/10 text-[#C8102E]' : 'bg-gray-100 text-gray-500'
                          }`}>{model.badge}</span>
                        )}
                      </div>
                    ))}
                  </div>

                  <div className="grid grid-cols-2 gap-4 pt-2">
                    <div>
                      <label className="block text-xs font-bold text-gray-500 uppercase tracking-wider mb-1.5">Temperature</label>
                      <div className="flex items-center gap-3">
                        <input type="range" min="0" max="100" defaultValue="30" className="flex-1 accent-[#C8102E]" />
                        <span className="text-xs font-mono text-gray-600 w-8">0.3</span>
                      </div>
                    </div>
                    <div>
                      <label className="block text-xs font-bold text-gray-500 uppercase tracking-wider mb-1.5">Max Tokens</label>
                      <input type="number" defaultValue="4096"
                        className="w-full px-3 py-2 rounded-lg border border-gray-200 text-sm text-gray-700 focus:outline-none focus:border-purple-400 bg-white" />
                    </div>
                  </div>
                </div>
              )}

              {/* ── Guardrails ── */}
              {section === 'guardrails' && (
                <div className="space-y-3 max-w-xl">
                  <p className="text-xs font-bold text-gray-400 uppercase tracking-widest mb-4">Safety & Compliance Guardrails</p>
                  {guardrails.map((rule, i) => (
                    <div key={i} className="flex items-start gap-3 px-4 py-3 rounded-xl border border-gray-200 bg-white">
                      <div className="w-7 h-7 rounded-lg bg-red-50 border border-red-100 flex items-center justify-center flex-shrink-0 mt-0.5">
                        <Lock size={12} className="text-[#C8102E]" />
                      </div>
                      <div className="flex-1">
                        <p className="text-sm text-gray-800 leading-relaxed">{rule}</p>
                        <span className="text-[10px] font-bold text-[#C8102E] uppercase tracking-wide mt-1 inline-block">Mandatory</span>
                      </div>
                    </div>
                  ))}
                  <button className="flex items-center gap-2 px-3 py-2 rounded-lg border border-dashed border-gray-300 text-xs text-gray-400 hover:border-gray-400 transition-all w-full justify-center">
                    <Plus size={11} /> Add custom guardrail
                  </button>
                </div>
              )}

            </motion.div>
          </AnimatePresence>
        </div>

        {/* Phase 1 footer */}
        <div className="flex-shrink-0 px-5 py-3 border-t border-gray-100 bg-[#FAFAFA] flex items-center justify-between">
          <button className="text-xs text-blue-600 font-medium hover:underline">Find Similar Agents</button>
          <div className="flex items-center gap-2">
            <button onClick={goPrev}
              className="px-4 py-2 rounded-lg border border-gray-200 text-xs font-semibold text-gray-600 hover:bg-gray-50 transition-all">
              Back
            </button>
            <button onClick={goNext}
              className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-semibold text-white transition-all hover:opacity-90"
              style={{ background: '#C8102E' }}>
              {isLast ? (
                <> Proceed to Simulation <ArrowRight size={11} /> </>
              ) : (
                <> Continue to {P1_SECTIONS[currentIdx + 1]?.label} <ArrowRight size={11} /> </>
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

/* ─── Phase 2: Simulation & Testing ─────────────────────────────────────────── */
function Phase2({ onBack, onContinue }) {
  const [testState, setTestState] = useState('idle') // idle | running | done
  const [visibleCount, setVisibleCount] = useState(0)

  const runTests = () => {
    setTestState('running')
    setVisibleCount(0)
    let count = 0
    const interval = setInterval(() => {
      count++
      setVisibleCount(count)
      if (count >= TEST_SCENARIOS.length) {
        clearInterval(interval)
        setTestState('done')
      }
    }, 350)
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="flex-1 overflow-y-auto px-5 py-4">
        <div className="max-w-xl space-y-4">

          {/* Header */}
          <div className="flex items-center justify-between">
            <div>
              <p className="text-xs font-bold text-gray-400 uppercase tracking-widest">Simulation & Testing</p>
              <p className="text-sm text-gray-600 mt-1">Run automated test scenarios against your agent configuration</p>
            </div>
            <button onClick={runTests} disabled={testState === 'running'}
              className="flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-semibold text-white disabled:opacity-50 transition-all"
              style={{ background: testState === 'running' ? '#9CA3AF' : '#1A2340' }}>
              {testState === 'running'
                ? <><RefreshCw size={12} className="animate-spin" /> Running…</>
                : testState === 'done'
                  ? <><RotateCcw size={12} /> Re-run</>
                  : <><Play size={12} /> Run Test Suite</>}
            </button>
          </div>

          {/* Test cards */}
          <div className="space-y-2">
            {TEST_SCENARIOS.map((t, i) => {
              const isVisible = testState === 'done' || (testState === 'running' && i < visibleCount)
              const isRunning = testState === 'running' && i === visibleCount
              return (
                <div key={t.id} className={`flex items-center gap-3 px-4 py-3 rounded-xl border transition-all ${
                  isVisible
                    ? t.result === 'warning' ? 'border-amber-200 bg-amber-50' : 'border-emerald-200 bg-emerald-50'
                    : isRunning ? 'border-blue-200 bg-blue-50 animate-pulse'
                    : 'border-gray-200 bg-white'
                }`}>
                  <div className="w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0">
                    {isRunning && <RefreshCw size={13} className="text-blue-500 animate-spin" />}
                    {isVisible && t.result === 'pass'    && <CheckCircle  size={14} className="text-emerald-600" />}
                    {isVisible && t.result === 'warning' && <AlertCircle  size={14} className="text-amber-500"   />}
                    {!isRunning && !isVisible            && <div className="w-3 h-3 rounded-full bg-gray-200" />}
                  </div>
                  <div className="flex-1">
                    <p className="text-sm font-semibold text-gray-800">{t.name}</p>
                    <p className="text-xs text-gray-500 mt-0.5">{t.desc}</p>
                    {isVisible && t.note && <p className="text-xs text-amber-600 font-medium mt-0.5">{t.note}</p>}
                  </div>
                  <span className={`text-[10px] font-bold uppercase px-2 py-1 rounded-full ${
                    isRunning   ? 'bg-blue-100 text-blue-600'        :
                    !isVisible  ? 'bg-gray-100 text-gray-400'        :
                    t.result === 'warning' ? 'bg-amber-100 text-amber-600' :
                    'bg-emerald-100 text-emerald-600'
                  }`}>
                    {isRunning ? 'Running' : !isVisible ? 'Not Run' : t.result === 'warning' ? 'Warning' : 'Passed'}
                  </span>
                </div>
              )
            })}
          </div>

          {/* Summary */}
          {testState === 'done' && (
            <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}
              className="flex items-center gap-3 px-4 py-3 rounded-xl bg-emerald-50 border border-emerald-200">
              <CheckCircle size={16} className="text-emerald-600 flex-shrink-0" />
              <div>
                <p className="text-sm font-semibold text-emerald-800">6 Passed · 1 Warning · 0 Failed</p>
                <p className="text-xs text-emerald-600 mt-0.5">Agent is ready to proceed to Non-Prod Deployment</p>
              </div>
            </motion.div>
          )}
        </div>
      </div>

      <div className="flex-shrink-0 px-5 py-3 border-t border-gray-100 bg-[#FAFAFA] flex items-center justify-end gap-2">
        <button onClick={onBack} className="px-4 py-2 rounded-lg border border-gray-200 text-xs font-semibold text-gray-600 hover:bg-gray-50 transition-all">Back</button>
        <button onClick={onContinue} disabled={testState !== 'done'}
          className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-semibold text-white disabled:opacity-40 transition-all hover:opacity-90"
          style={{ background: '#C8102E' }}>
          Continue <ArrowRight size={11} />
        </button>
      </div>
    </div>
  )
}

/* ─── Manual Query Test (between Phase 2 and Phase 3) ───────────────────────── */
const DOMAIN_QUESTIONS = {
  churn:      ['Why do customers churn?', 'How do you score churn risk?', 'What retention campaigns do you run?', 'What signals indicate churn?'],
  onboarding: ['What documents are required?', 'How is risk scored?', 'What is the onboarding timeline?', 'When do you escalate?'],
  fraud:      ['How do you detect fraud?', 'What happens when fraud is found?', 'What is the hold threshold?', 'How do you handle false positives?'],
  invoice:    ['How does 3-way matching work?', 'How do you handle exceptions?', 'What triggers a GL write-off?', 'How is duplicate detection done?'],
  enrichment: ['What data sources do you use?', 'How are propensity scores calculated?', 'How often do profiles refresh?', 'How is PII protected?'],
  general:    ['What can you do?', 'What are your guardrails?', 'Which tools do you use?', 'How do you handle errors?'],
}

function ManualQueryPhase({ systemPrompt, agentName, onBack, onContinue }) {
  const displayName = cleanAgentName(agentName)
  const domain      = detectDomain(systemPrompt)
  const purpose     = extractPurpose(systemPrompt)

  // Stable session ID for this test session — new one each time the component mounts
  const sessionId = useRef(`mq-${Date.now()}-${Math.random().toString(36).slice(2)}`)

  const domainHint = {
    churn:       'churn signals, retention strategies, and scoring models',
    onboarding:  'KYB requirements, risk scoring, and merchant timelines',
    fraud:       'fraud detection signals, confidence thresholds, and response actions',
    invoice:     'matching logic, exception handling, and GL posting rules',
    enrichment:  'data sources, propensity scores, and enrichment pipeline',
    general:     'my capabilities, tools, guardrails, and workflows',
  }[domain] || 'my capabilities and workflows'

  const [messages, setMessages] = useState([
    {
      id: 1, role: 'agent', live: false,
      text: `Hello! I'm **${displayName}**${purpose ? ` — ${purpose.toLowerCase()}` : ''}.\n\nI'm ready for manual testing. Ask me anything about ${domainHint}. This is your chance to verify my intelligence before deployment.`,
    }
  ])
  const [input, setInput]     = useState('')
  const [typing, setTyping]   = useState(false)
  const [novaOnline, setNovaOnline] = useState(true)   // tracks if backend responded OK
  const bottomRef             = useRef(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, typing])

  const sendMessage = async (text) => {
    const q = typeof text === 'string' ? text : input
    if (!q.trim() || typing) return

    setMessages(prev => [...prev, { id: Date.now(), role: 'user', text: q.trim() }])
    setInput('')
    setTyping(true)

    try {
      const res = await fetch('/api/nova/agent/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          sessionId:    sessionId.current,
          message:      q.trim(),
          systemPrompt: systemPrompt,
        }),
      })

      if (!res.ok) throw new Error(`HTTP ${res.status}`)

      const data = await res.json()
      const reply = data.message || data.reply || 'No response received.'
      setNovaOnline(true)
      setMessages(prev => [...prev, { id: Date.now() + 1, role: 'agent', live: true, text: reply }])
    } catch {
      // Backend unreachable — fall back to local response engine
      setNovaOnline(false)
      const fallback = generateAgentResponse(q, systemPrompt, agentName)
      setMessages(prev => [...prev, {
        id: Date.now() + 1, role: 'agent', live: false,
        text: fallback,
      }])
    } finally {
      setTyping(false)
    }
  }

  const userCount = messages.filter(m => m.role === 'user').length

  /* Simple inline markdown renderer: **bold**, `code`, bullet lines */
  const renderText = (text) =>
    text.split('\n').map((line, i) => {
      if (!line) return <div key={i} className="h-2" />
      const parts = line.split(/(\*\*[^*]+\*\*|`[^`]+`)/)
      return (
        <p key={i} className="leading-relaxed">
          {parts.map((part, j) => {
            if (part.startsWith('**') && part.endsWith('**'))
              return <strong key={j}>{part.slice(2, -2)}</strong>
            if (part.startsWith('`') && part.endsWith('`'))
              return <code key={j} className="font-mono text-[11px] bg-black/10 px-1 rounded">{part.slice(1, -1)}</code>
            return <span key={j}>{part}</span>
          })}
        </p>
      )
    })

  return (
    <div className="flex-1 flex flex-col overflow-hidden">

      {/* Header bar */}
      <div className="flex-shrink-0 px-5 py-3 border-b border-gray-200 bg-[#FAFAFA] flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <div className="w-7 h-7 rounded-lg bg-violet-50 border border-violet-200 flex items-center justify-center">
            <MessageSquare size={13} className="text-violet-600" />
          </div>
          <div>
            <p className="text-sm font-semibold text-gray-800">Manual Query Test</p>
            <p className="text-xs text-gray-400">Verify agent intelligence before Non-Prod deployment</p>
          </div>
        </div>
        {novaOnline ? (
          <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-emerald-50 border border-emerald-200">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 inline-block" style={{ animation: 'pulse 1.5s infinite' }} />
            <span className="text-xs font-semibold text-emerald-700">Live · Powered by Claude</span>
          </div>
        ) : (
          <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-amber-50 border border-amber-200">
            <span className="w-1.5 h-1.5 rounded-full bg-amber-400 inline-block" />
            <span className="text-xs font-semibold text-amber-700">Offline · Local Mode</span>
          </div>
        )}
      </div>

      {/* Suggested questions — domain-aware */}
      <div className="flex-shrink-0 px-5 py-2 border-b border-gray-100 flex items-center gap-2 overflow-x-auto">
        <span className="text-[10px] font-bold text-gray-400 uppercase tracking-wide whitespace-nowrap flex-shrink-0">Try:</span>
        {(DOMAIN_QUESTIONS[domain] || DOMAIN_QUESTIONS.general).map((q, i) => (
          <button key={i} onClick={() => sendMessage(q)} disabled={typing}
            className="flex-shrink-0 px-2.5 py-1 rounded-full text-xs font-medium bg-gray-100 text-gray-600 hover:bg-violet-50 hover:text-violet-700 border border-transparent hover:border-violet-200 transition-all disabled:opacity-40">
            {q}
          </button>
        ))}
      </div>

      {/* Chat messages */}
      <div className="flex-1 overflow-y-auto px-5 py-4 space-y-3 bg-[#F9FAFB]">
        {messages.map(msg => (
          <motion.div key={msg.id} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.2 }}
            className={`flex items-start gap-2.5 ${msg.role === 'user' ? 'flex-row-reverse' : ''}`}>

            {/* Avatar */}
            <div className={`w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0 ${
              msg.role === 'agent' ? 'bg-[#1A2340]' : 'bg-violet-100'
            }`}>
              {msg.role === 'agent'
                ? <Bot size={13} className="text-white" />
                : <Users size={12} className="text-violet-600" />}
            </div>

            {/* Bubble */}
            <div className={`max-w-[78%] px-4 py-3 text-sm rounded-2xl ${
              msg.role === 'agent'
                ? 'bg-white border border-gray-200 text-gray-800 rounded-tl-sm shadow-sm'
                : 'bg-[#C8102E] text-white rounded-tr-sm'
            }`}>
              {renderText(msg.text)}
              {msg.role === 'agent' && msg.live && (
                <span className="inline-flex items-center gap-0.5 mt-1.5 px-1.5 py-0.5 rounded text-[9px] font-bold bg-violet-50 text-violet-500 border border-violet-100">
                  <Sparkles size={8} /> Claude
                </span>
              )}
            </div>
          </motion.div>
        ))}

        {/* Typing indicator */}
        {typing && (
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}
            className="flex items-start gap-2.5">
            <div className="w-7 h-7 rounded-lg bg-[#1A2340] flex items-center justify-center flex-shrink-0">
              <Bot size={13} className="text-white" />
            </div>
            <div className="px-4 py-3 bg-white border border-gray-200 rounded-2xl rounded-tl-sm shadow-sm flex items-center gap-1.5">
              {[0, 150, 300].map(d => (
                <span key={d} className="w-1.5 h-1.5 rounded-full bg-gray-400"
                  style={{ animation: `bounce 1s infinite ${d}ms` }} />
              ))}
            </div>
          </motion.div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Input bar */}
      <div className="flex-shrink-0 px-5 py-3 border-t border-gray-200 bg-white">
        <div className="flex items-center gap-2">
          <input
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(input) } }}
            placeholder={`Ask ${displayName} a question…`}
            disabled={typing}
            className="flex-1 px-4 py-2.5 rounded-xl border border-gray-200 text-sm focus:outline-none focus:border-violet-400 bg-white disabled:opacity-60"
          />
          <button onClick={() => sendMessage(input)} disabled={!input.trim() || typing}
            className="w-9 h-9 rounded-xl flex items-center justify-center text-white flex-shrink-0 transition-all disabled:opacity-40"
            style={{ background: '#C8102E' }}>
            <ArrowRight size={14} />
          </button>
        </div>
      </div>

      {/* Footer */}
      <div className="flex-shrink-0 px-5 py-3 border-t border-gray-100 bg-[#FAFAFA] flex items-center justify-between">
        <p className="text-xs text-gray-400">
          {userCount === 0 ? 'Optional: test your agent before proceeding' : `${userCount} quer${userCount !== 1 ? 'ies' : 'y'} tested`}
        </p>
        <div className="flex items-center gap-2">
          <button onClick={onBack}
            className="px-4 py-2 rounded-lg border border-gray-200 text-xs font-semibold text-gray-600 hover:bg-gray-50 transition-all">
            Back
          </button>
          <button onClick={onContinue}
            className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-semibold text-white transition-all hover:opacity-90"
            style={{ background: '#C8102E' }}>
            Proceed to Deploy <ArrowRight size={11} />
          </button>
        </div>
      </div>
    </div>
  )
}

/* ─── Phase 3: Deploy (merged Non-Prod + Approval) ──────────────────────────── */
function DeployPhase({ agentName, agentVersion = '1.0.0', description = '', onBack }) {
  const [submitted, setSubmitted]       = useState(false)
  const [approvalState, setApprovalState] = useState('idle')   // idle | sending | sent
  const [copied, setCopied]             = useState(false)
  const [showApiModal, setShowApiModal] = useState(false)
  const addDeployedAgent = useStore(s => s.addDeployedAgent)
  const slug = toSlug(agentName)
  const displayName = cleanAgentName(agentName)

  const sendApproval = () => {
    setApprovalState('sending')
    setTimeout(() => {
      addDeployedAgent({
        id:          slug + '-' + Date.now(),
        name:        displayName,
        slug,
        status:      'pending-approval',
        submittedAt: new Date().toLocaleString(),
      })
      setApprovalState('sent')
    }, 1200)
  }

  const configJson = JSON.stringify({
    agent:       { id: slug, name: agentName, version: agentVersion, description, framework: 'DLX_AGENTIC_OS v2.1', endpoint: `https://agents.deluxe.com/v1/${slug}` },
    deployment:  { status: 'pending-approval', submittedAt: new Date().toISOString(), regions: ['us-east-1', 'us-west-2'], sla: '99.9%', autoScaling: { min: 2, max: 20 } },
    observability: { provider: 'OPIK', tracing: true, metrics: true, logging: 'structured' },
  }, null, 2)

  const sourceCode =
`import os
from dlx_agents import AgentClient

client = AgentClient(
    agent_id="${slug}",
    endpoint="https://agents.deluxe.com/v1/${slug}",
    api_key=os.environ["DLX_API_KEY"]
)

response = client.run(input={"query": "Your input here"}, stream=False)
print(response.output)`

  const handleDownload = () => {
    const blob = new Blob([configJson], { type: 'application/json' })
    const url  = URL.createObjectURL(blob)
    const a    = document.createElement('a')
    a.href = url; a.download = `${slug}-config.json`; a.click()
    URL.revokeObjectURL(url)
  }

  const handleCopy = () => {
    navigator.clipboard.writeText(sourceCode).catch(() => {})
    setCopied(true)
    setTimeout(() => setCopied(false), 2500)
  }

  const DEPLOY_CONFIG = [
    { label: 'Endpoint',      value: `https://agents.deluxe.com/v1/${slug}`, icon: Server   },
    { label: 'Regions',       value: 'us-east-1, us-west-2',                 icon: Globe    },
    { label: 'SLA',           value: '99.9% Uptime',                         icon: Zap      },
    { label: 'Auto-scaling',  value: '2 – 20 instances',                     icon: Settings },
    { label: 'Observability', value: 'OPIK Full-stack Tracing',              icon: Shield   },
  ]

  const API_ENDPOINTS = [
    { method: 'POST', path: `/v1/${slug}/run`,     desc: 'Execute the agent with an input payload' },
    { method: 'GET',  path: `/v1/${slug}/status`,  desc: 'Get current agent status and health'     },
    { method: 'GET',  path: `/v1/${slug}/logs`,    desc: 'Retrieve recent execution logs'          },
  ]

  return (
    <div className="flex-1 flex flex-col overflow-hidden relative">

      {/* ── API Docs Modal ── */}
      <AnimatePresence>
        {showApiModal && (
          <div className="absolute inset-0 z-30 flex items-center justify-center p-6"
            style={{ backdropFilter: 'blur(4px)', background: 'rgba(0,0,0,0.35)' }}
            onClick={e => e.target === e.currentTarget && setShowApiModal(false)}>
            <motion.div initial={{ opacity: 0, scale: 0.95, y: 12 }} animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.95, y: 12 }} transition={{ duration: 0.2 }}
              className="bg-white rounded-2xl shadow-2xl w-full max-w-lg flex flex-col overflow-hidden"
              style={{ maxHeight: '80vh' }}>
              <div className="px-5 py-4 border-b border-gray-200 flex items-center justify-between flex-shrink-0"
                style={{ background: 'linear-gradient(135deg, #1A2340 0%, #2D3A5C 100%)' }}>
                <div className="flex items-center gap-2.5">
                  <BookOpen size={14} className="text-white" />
                  <p className="text-sm font-semibold text-white">API Reference — {agentName}</p>
                </div>
                <button onClick={() => setShowApiModal(false)}
                  className="w-6 h-6 rounded-lg bg-white/10 hover:bg-white/20 flex items-center justify-center transition-all">
                  <X size={12} className="text-white" />
                </button>
              </div>
              <div className="flex-1 overflow-y-auto px-5 py-4 space-y-2">
                {API_ENDPOINTS.map((ep, i) => (
                  <div key={i} className="flex items-start gap-3 px-3 py-3 rounded-xl border border-gray-100 bg-[#F9FAFB]">
                    <span className={`px-2 py-0.5 rounded text-[10px] font-bold flex-shrink-0 mt-0.5 ${
                      ep.method === 'POST' ? 'bg-blue-100 text-blue-700' : 'bg-emerald-100 text-emerald-700'
                    }`}>{ep.method}</span>
                    <div>
                      <code className="text-xs font-mono text-gray-800">{ep.path}</code>
                      <p className="text-xs text-gray-500 mt-0.5">{ep.desc}</p>
                    </div>
                  </div>
                ))}
              </div>
              <div className="px-5 py-3 border-t border-gray-100 bg-amber-50 flex-shrink-0">
                <p className="text-xs text-amber-700"><span className="font-bold">Auth:</span> <code className="font-mono">Authorization: Bearer DLX_API_KEY</code></p>
              </div>
            </motion.div>
          </div>
        )}
      </AnimatePresence>

      {!submitted ? (
        /* ─── Pre-submit view ─── */
        <>
          <div className="flex-1 overflow-y-auto px-5 py-4">
            <div className="max-w-xl space-y-5">
              <p className="text-xs font-bold text-gray-400 uppercase tracking-widest">Deployment</p>

              {/* Deploy config */}
              <div className="rounded-xl border border-gray-200 overflow-hidden">
                {DEPLOY_CONFIG.map((row, i) => (
                  <div key={i} className={`flex items-center gap-3 px-4 py-3 ${i < DEPLOY_CONFIG.length - 1 ? 'border-b border-gray-100' : ''}`}>
                    <row.icon size={13} className="text-gray-400 flex-shrink-0" />
                    <span className="text-xs font-semibold text-gray-500 w-28 flex-shrink-0">{row.label}</span>
                    <span className="text-xs text-gray-800 font-mono flex-1">{row.value}</span>
                  </div>
                ))}
              </div>

              {/* Guardrails check */}
              <div className="flex items-center gap-2 px-4 py-2.5 rounded-lg bg-[#F7F8FA] border border-gray-200">
                <CheckCircle size={13} className="text-emerald-500 flex-shrink-0" />
                <p className="text-xs text-gray-600 font-medium">All guardrails configured and validated</p>
              </div>

              {/* Approval section */}
              {approvalState === 'idle' && (
                <button onClick={sendApproval}
                  className="w-full flex items-center justify-center gap-2 px-4 py-3 rounded-xl text-sm font-semibold text-white transition-all hover:opacity-90"
                  style={{ background: 'linear-gradient(135deg, #C8102E 0%, #9B0D24 100%)' }}>
                  <Rocket size={14} /> Send for Approval
                </button>
              )}
              {approvalState === 'sending' && (
                <div className="w-full flex items-center justify-center gap-2 px-4 py-3 rounded-xl text-sm font-semibold text-white bg-gray-400">
                  <RefreshCw size={14} className="animate-spin" /> Sending…
                </div>
              )}
              {approvalState === 'sent' && (
                <motion.div initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }}
                  className="flex items-center gap-3 px-4 py-3 rounded-xl bg-amber-50 border border-amber-200">
                  <CheckCircle size={16} className="text-amber-500 flex-shrink-0" />
                  <div>
                    <p className="text-sm font-semibold text-amber-800">Approval request sent!</p>
                    <p className="text-xs text-amber-600 mt-0.5">"{displayName}" is now on your Dashboard · <strong>Check Approval Status</strong></p>
                  </div>
                </motion.div>
              )}
            </div>
          </div>

          <div className="flex-shrink-0 px-5 py-3 border-t border-gray-100 bg-[#FAFAFA] flex items-center justify-between">
            <button onClick={onBack} className="px-4 py-2 rounded-lg border border-gray-200 text-xs font-semibold text-gray-600 hover:bg-gray-50 transition-all">Back</button>
            <div className="flex items-center gap-2">
              {approvalState === 'idle' && (
                <button onClick={sendApproval}
                  className="flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-semibold text-white transition-all hover:opacity-90"
                  style={{ background: 'linear-gradient(135deg, #C8102E 0%, #9B0D24 100%)' }}>
                  <Rocket size={12} /> Send for Approval
                </button>
              )}
              {approvalState === 'sending' && (
                <div className="flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-semibold text-white bg-gray-400">
                  <RefreshCw size={12} className="animate-spin" /> Sending…
                </div>
              )}
              {approvalState === 'sent' && (
                <button onClick={() => setSubmitted(true)}
                  className="flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-semibold text-white transition-all hover:opacity-90"
                  style={{ background: 'linear-gradient(135deg, #1A2340 0%, #2D3A5C 100%)' }}>
                  <CheckCircle size={12} /> Done — View Resources
                </button>
              )}
            </div>
          </div>
        </>
      ) : (
        /* ─── Completion screen ─── */
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.3 }}
          className="flex-1 flex flex-col overflow-y-auto">

          {/* Success hero */}
          <div className="px-6 py-8 flex flex-col items-center text-center flex-shrink-0"
            style={{ background: 'linear-gradient(160deg, #1A2340 0%, #2D3A5C 60%, #1A2340 100%)' }}>
            <motion.div initial={{ scale: 0.5, opacity: 0 }} animate={{ scale: 1, opacity: 1 }}
              transition={{ delay: 0.1, type: 'spring', stiffness: 200 }}
              className="w-14 h-14 rounded-2xl bg-emerald-400/20 border border-emerald-400/30 flex items-center justify-center mb-4">
              <CheckCircle size={26} className="text-emerald-400" />
            </motion.div>
            <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.2 }}>
              <h2 className="text-white font-bold text-lg mb-1">Agent Submitted for Approval!</h2>
              <p className="text-white/60 text-sm mb-3">Approvers have been notified · You'll receive an update within 24 hours</p>
              <code className="text-xs font-mono text-white/50 bg-white/10 px-3 py-1 rounded-full border border-white/10">
                https://agents.deluxe.com/v1/{slug}
              </code>
            </motion.div>
          </div>

          {/* Three option cards */}
          <div className="px-6 py-5 flex-1">
            <p className="text-xs font-bold text-gray-400 uppercase tracking-widest mb-4 text-center">What would you like to do next?</p>
            <div className="grid grid-cols-3 gap-3">

              <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.2 }}
                className="flex flex-col rounded-2xl border border-gray-200 overflow-hidden hover:border-blue-200 hover:shadow-md transition-all group cursor-pointer"
                onClick={handleDownload}>
                <div className="px-4 pt-5 pb-3 flex-1">
                  <div className="w-10 h-10 rounded-xl bg-blue-50 border border-blue-100 flex items-center justify-center mb-3 group-hover:bg-blue-100 transition-all">
                    <Download size={17} className="text-blue-600" />
                  </div>
                  <p className="text-sm font-bold text-gray-800 mb-1">Download Config</p>
                  <p className="text-xs text-gray-500 leading-relaxed">Export your agent configuration as a JSON file</p>
                </div>
                <div className="px-4 pb-4">
                  <button className="w-full flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg text-xs font-semibold text-blue-700 bg-blue-50 border border-blue-100 hover:bg-blue-100 transition-all">
                    <Download size={11} /> Download .json
                  </button>
                </div>
              </motion.div>

              <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.28 }}
                className="flex flex-col rounded-2xl border border-gray-200 overflow-hidden hover:border-purple-200 hover:shadow-md transition-all group">
                <div className="px-4 pt-5 pb-3 flex-1">
                  <div className="w-10 h-10 rounded-xl bg-purple-50 border border-purple-100 flex items-center justify-center mb-3 group-hover:bg-purple-100 transition-all">
                    <Code2 size={17} className="text-purple-600" />
                  </div>
                  <p className="text-sm font-bold text-gray-800 mb-1">Copy Source Code</p>
                  <p className="text-xs text-gray-500 leading-relaxed">Python SDK snippet ready for your codebase</p>
                </div>
                <div className="mx-4 mb-3 rounded-lg bg-gray-900 p-2.5 overflow-hidden">
                  <pre className="text-[9px] font-mono text-gray-300 leading-relaxed" style={{ maxHeight: 44 }}>
                    <span className="text-purple-400">from</span>{' dlx_agents '}
                    <span className="text-purple-400">import</span>{' AgentClient\n'}
                    <span className="text-blue-400">client</span>{` = AgentClient(\n  agent_id="${slug}")`}
                  </pre>
                </div>
                <div className="px-4 pb-4">
                  <button onClick={handleCopy}
                    className={`w-full flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg text-xs font-semibold border transition-all ${
                      copied ? 'text-emerald-700 bg-emerald-50 border-emerald-100' : 'text-purple-700 bg-purple-50 border-purple-100 hover:bg-purple-100'
                    }`}>
                    {copied ? <><CheckCircle size={11} /> Copied!</> : <><Copy size={11} /> Copy Code</>}
                  </button>
                </div>
              </motion.div>

              <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.36 }}
                className="flex flex-col rounded-2xl border border-gray-200 overflow-hidden hover:border-emerald-200 hover:shadow-md transition-all group cursor-pointer"
                onClick={() => setShowApiModal(true)}>
                <div className="px-4 pt-5 pb-3 flex-1">
                  <div className="w-10 h-10 rounded-xl bg-emerald-50 border border-emerald-100 flex items-center justify-center mb-3 group-hover:bg-emerald-100 transition-all">
                    <BookOpen size={17} className="text-emerald-600" />
                  </div>
                  <p className="text-sm font-bold text-gray-800 mb-1">API Documentation</p>
                  <p className="text-xs text-gray-500 leading-relaxed">Explore endpoints, parameters, and usage examples</p>
                </div>
                <div className="mx-4 mb-3 space-y-1">
                  {[['POST', '/run'], ['GET', '/status'], ['GET', '/logs']].map(([m, p]) => (
                    <div key={p} className="flex items-center gap-2">
                      <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded ${m === 'POST' ? 'bg-blue-100 text-blue-700' : 'bg-emerald-100 text-emerald-700'}`}>{m}</span>
                      <code className="text-[10px] font-mono text-gray-600">/v1/{slug}{p}</code>
                    </div>
                  ))}
                </div>
                <div className="px-4 pb-4">
                  <button className="w-full flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg text-xs font-semibold text-emerald-700 bg-emerald-50 border border-emerald-100 hover:bg-emerald-100 transition-all">
                    <ExternalLink size={11} /> Open Docs
                  </button>
                </div>
              </motion.div>

            </div>
          </div>
        </motion.div>
      )}
    </div>
  )
}

/* ─── Agent Analyst Module ───────────────────────────────────────────────────── */
function AgentAnalystModule() {
  const [search, setSearch]           = useState('')
  const [selected, setSelected]       = useState(null)
  const [agentName, setAgentName]     = useState('')
  const [agentVersion, setAgentVersion] = useState('1.0.0')
  const [author, setAuthor]           = useState('DLX Platform Team')
  const [valueStream, setValueStream] = useState('')
  const [description, setDesc]        = useState('')
  const [systemPrompt, setSysP]       = useState('')
  const [syncing, setSyncing]         = useState(false)
  const [fetched, setFetched]         = useState(false)
  const [phase, setPhase]             = useState(null) // null | 1 | 2 | 'query' | 3
  const [selectedSkills, setSelectedSkills] = useState(new Set())
  const navigate = useNavigate()

  const storePages = useStore(s => s.confluencePages)
  const allPages   = [...storePages, ...CONFLUENCE_PAGES]
  const filtered   = allPages.filter(p =>
    p.title.toLowerCase().includes(search.toLowerCase()) ||
    p.space.toLowerCase().includes(search.toLowerCase())
  )

  const handleSelect = (page) => {
    setSelected(page); setFetched(false); setPhase(null)
    setAgentName(''); setDesc(''); setSysP('')
  }

  const handleFetch = () => {
    if (!selected) return
    setSyncing(true)
    setTimeout(() => {
      const name = selected.title.split('—')[0].trim()
      setAgentName(name)
      setDesc(selected.content.description)
      setSysP(selected.content.systemPrompt)
      setValueStream(selected.space)
      const skills = getSkills(selected.content.systemPrompt)
      setSelectedSkills(new Set(skills)) // all pre-selected
      setFetched(true)
      setSyncing(false)
    }, 900)
  }

  const toggleSkill = (skill) => {
    setSelectedSkills(prev => {
      const next = new Set(prev)
      next.has(skill) ? next.delete(skill) : next.add(skill)
      return next
    })
  }

  const skills     = getSkills(systemPrompt)
  const tools      = extractTools(systemPrompt)
  const guardrails = extractGuardrails(systemPrompt)

  return (
    <div className="flex-1 bg-gray-100 p-4 flex gap-4 overflow-hidden" style={{ minHeight: 0 }}>

      {/* ── Left panel: page list ── */}
      <div className="w-72 flex-shrink-0 bg-white rounded-xl border border-gray-200 flex flex-col overflow-hidden">
        <div className="p-3 border-b border-gray-100">
          <div className="relative">
            <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
            <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search pages"
              className="w-full pl-8 pr-3 py-2 rounded-lg border border-gray-200 text-sm focus:outline-none focus:border-gray-300 bg-white" />
          </div>
        </div>
        <div className="flex-1 overflow-y-auto">
          {filtered.length === 0 ? (
            <p className="text-sm text-gray-400 text-center px-4 py-10">No pages found.</p>
          ) : filtered.map(page => (
            <button key={page.id} onClick={() => handleSelect(page)}
              className={`w-full text-left px-4 py-3 border-b border-gray-50 hover:bg-gray-50 transition-colors ${
                selected?.id === page.id ? 'bg-blue-50 border-l-[3px] border-l-blue-500' : ''}`}>
              <div className="flex items-start gap-2.5">
                <FileText size={13} className={`flex-shrink-0 mt-0.5 ${selected?.id === page.id ? 'text-blue-500' : 'text-gray-400'}`} />
                <div className="min-w-0">
                  <div className="flex items-center gap-1.5 flex-wrap">
                    <p className={`text-sm font-medium leading-snug ${selected?.id === page.id ? 'text-blue-700' : 'text-gray-800'}`}>
                      {page.title}
                    </p>
                    {page.fromNova && (
                      <span className="px-1.5 py-0.5 rounded text-[9px] font-bold bg-purple-100 text-purple-700 leading-none flex-shrink-0">Nova</span>
                    )}
                  </div>
                  <p className="text-xs text-gray-400 mt-0.5">{page.space} · {page.updated}</p>
                </div>
              </div>
            </button>
          ))}
        </div>
      </div>

      {/* ── Right panel ── */}
      <div className="flex-1 bg-white rounded-xl border border-gray-200 flex flex-col overflow-hidden">

        {/* Phase stepper — only when in phase journey */}
        {phase !== null && <PhaseStepper phase={phase} />}

        {/* Action bar — only when NOT in phase journey */}
        {phase === null && (
          <div className="px-5 py-3.5 border-b border-gray-200 flex items-center justify-between flex-shrink-0">
            <span className="text-sm font-semibold text-gray-800">{selected ? selected.title : 'Select a page'}</span>
            <div className="flex items-center gap-2">
              <button onClick={handleFetch} disabled={!selected || syncing}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold text-white disabled:opacity-30 transition-all"
                style={{ background: syncing ? '#9CA3AF' : '#8B5CF6' }}>
                {syncing
                  ? <><RefreshCw size={11} className="animate-spin" /> Fetching…</>
                  : <><Sparkles size={11} /> Auto-fetch from Doc</>}
              </button>
              {fetched && (
                <button onClick={() => setPhase(1)}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold text-white transition-all hover:opacity-90"
                  style={{ background: '#C8102E' }}>
                  Build Agent
                </button>
              )}
            </div>
          </div>
        )}

        {/* ── Content based on state ── */}

        {/* Pre-fetch: empty / prompt state */}
        {phase === null && (
          <div className="flex-1 overflow-y-auto p-5">
            {!selected ? (
              <div className="h-full flex items-center justify-center">
                <p className="text-sm text-gray-400">Select a page to view details</p>
              </div>
            ) : !fetched ? (
              <div className="h-full flex items-center justify-center">
                <div className="text-center">
                  <div className="w-12 h-12 rounded-xl bg-purple-50 border border-purple-100 flex items-center justify-center mx-auto mb-3">
                    <Sparkles size={20} className="text-purple-400" />
                  </div>
                  <p className="text-sm font-semibold text-gray-700 mb-1">"{selected.title}"</p>
                  <p className="text-xs text-gray-400 mb-4">Click <strong>Auto-fetch from Doc</strong> to extract agent configuration</p>
                  <button onClick={handleFetch} disabled={syncing}
                    className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold text-white mx-auto"
                    style={{ background: '#8B5CF6' }}>
                    <Sparkles size={13} /> Auto-fetch from Doc
                  </button>
                </div>
              </div>
            ) : (
              <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} className="space-y-4 max-w-2xl">
                <div className="flex items-center gap-2.5 px-4 py-2.5 rounded-lg bg-emerald-50 border border-emerald-200">
                  <CheckCircle size={14} className="text-emerald-600 flex-shrink-0" />
                  <p className="text-sm text-emerald-700 font-medium">Agent configuration extracted from Confluence document</p>
                </div>
                <div>
                  <label className="block text-xs font-bold text-gray-400 uppercase tracking-wider mb-1.5">Agent Name</label>
                  <input value={agentName} onChange={e => setAgentName(e.target.value)}
                    className="w-full px-3 py-2.5 rounded-lg border border-gray-200 text-sm font-semibold text-gray-800 focus:outline-none focus:border-purple-400 bg-white" />
                </div>
                <div>
                  <label className="block text-xs font-bold text-gray-400 uppercase tracking-wider mb-1.5">Description</label>
                  <textarea value={description} onChange={e => setDesc(e.target.value)} rows={3}
                    className="w-full px-3 py-2.5 rounded-lg border border-gray-200 text-sm text-gray-700 leading-relaxed focus:outline-none focus:border-purple-400 resize-none bg-white" />
                </div>
                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="text-xs font-bold text-gray-400 uppercase tracking-wider">System Prompt</label>
                    <span className="text-xs text-gray-400 font-mono">{systemPrompt.length} chars</span>
                  </div>
                  <textarea value={systemPrompt} onChange={e => setSysP(e.target.value)} rows={10}
                    className="w-full px-3 py-2.5 rounded-lg border border-gray-200 text-xs font-mono text-gray-700 leading-relaxed focus:outline-none focus:border-purple-400 resize-none bg-white" />
                </div>
                <div className="flex items-center gap-3 pt-1">
                  <button onClick={() => setPhase(1)}
                    className="flex items-center gap-2 px-5 py-2.5 rounded-lg text-white text-sm font-bold transition-all hover:opacity-90"
                    style={{ background: '#C8102E' }}>
                    Build Agent from this Doc
                  </button>
                  <button onClick={() => { setFetched(false); setSelected(null) }}
                    className="px-4 py-2.5 rounded-lg border border-gray-200 text-sm text-gray-500 hover:bg-gray-50 transition-colors">
                    Clear
                  </button>
                </div>
              </motion.div>
            )}
          </div>
        )}

        {/* Phase 1 */}
        {phase === 1 && (
          <Phase1
            agentName={agentName} setAgentName={setAgentName}
            agentVersion={agentVersion} setAgentVersion={setAgentVersion}
            author={author} setAuthor={setAuthor}
            valueStream={valueStream} setValueStream={setValueStream}
            description={description} setDesc={setDesc}
            systemPrompt={systemPrompt} setSysP={setSysP}
            skills={skills} selectedSkills={selectedSkills} toggleSkill={toggleSkill}
            tools={tools} guardrails={guardrails}
            onBack={() => setPhase(null)}
            onContinue={() => setPhase(2)}
          />
        )}

        {/* Phase 2 — Automated tests */}
        {phase === 2 && (
          <Phase2 onBack={() => setPhase(1)} onContinue={() => setPhase('query')} />
        )}

        {/* Phase 2 → Manual Query Test */}
        {phase === 'query' && (
          <ManualQueryPhase
            systemPrompt={systemPrompt}
            agentName={agentName}
            onBack={() => setPhase(2)}
            onContinue={() => setPhase(3)}
          />
        )}

        {/* Phase 3 — Deploy */}
        {phase === 3 && (
          <DeployPhase
            agentName={agentName}
            agentVersion={agentVersion}
            description={description}
            onBack={() => setPhase('query')}
          />
        )}

      </div>
    </div>
  )
}

/* ─── Placeholder modules ────────────────────────────────────────────────────── */
function PlaceholderModule({ tool }) {
  return (
    <div className="flex-1 flex items-center justify-center bg-gray-50">
      <div className="text-center">
        <div className="w-14 h-14 rounded-2xl bg-white border border-gray-200 flex items-center justify-center mx-auto mb-4">
          <tool.icon size={24} className="text-gray-300" />
        </div>
        <p className="text-sm font-semibold text-gray-600">{tool.label}</p>
        <p className="text-xs text-gray-400 mt-1">{tool.sub}</p>
      </div>
    </div>
  )
}

/* ─── Main Page ──────────────────────────────────────────────────────────────── */
export default function AgentAnalyst() {
  const [active, setActive]           = useState('agent-analyst')
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const activeTool = TOOLS.find(t => t.id === active)

  return (
    <div className="-m-6 flex overflow-hidden bg-white"
      style={{ height: 'calc(100vh - 56px)', fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif' }}>

      {/* ── Velox tools sidebar ── */}
      {sidebarOpen ? (
        <aside className="w-48 border-r flex flex-col flex-shrink-0" style={{ background: '#FAFAFA', borderColor: '#E5E7EB' }}>
          <div className="px-4 pt-4 pb-3 flex-shrink-0 flex items-start justify-between" style={{ borderBottom: '1px solid #F0F0F0' }}>
            <div>
              <p className="text-lg font-black tracking-tight leading-none" style={{ color: '#C8102E' }}>Velox</p>
              <p className="text-[10px] mt-0.5 leading-tight" style={{ color: '#9CA3AF' }}>Drive Engineering Excellence @dlx</p>
            </div>
            <button onClick={() => setSidebarOpen(false)} className="transition-colors mt-0.5" style={{ color: '#D1D5DB' }}
              onMouseEnter={e => e.currentTarget.style.color = '#6B7280'}
              onMouseLeave={e => e.currentTarget.style.color = '#D1D5DB'}>
              <ChevronLeft size={15} />
            </button>
          </div>

          <div className="px-3 pt-3 pb-1">
            <span className="text-[10px] font-bold uppercase tracking-widest" style={{ color: '#9CA3AF' }}>Tools</span>
          </div>

          <nav className="flex-1 px-2 space-y-0.5 overflow-y-auto">
            {TOOLS.map(tool => (
              <button key={tool.id} onClick={() => setActive(tool.id)}
                className="w-full flex items-start gap-2.5 px-3 py-2 rounded-lg text-left transition-all"
                style={active === tool.id ? { background: '#FEF2F2', borderLeft: '2px solid #C8102E', paddingLeft: '10px' } : {}}
                onMouseEnter={e => { if (active !== tool.id) e.currentTarget.style.background = '#F3F4F6' }}
                onMouseLeave={e => { if (active !== tool.id) e.currentTarget.style.background = '' }}>
                <tool.icon size={14} className="flex-shrink-0 mt-0.5" style={{ color: active === tool.id ? '#C8102E' : '#9CA3AF' }} />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1">
                    <span className="text-xs font-semibold leading-tight" style={{ color: active === tool.id ? '#C8102E' : '#374151' }}>{tool.label}</span>
                    {tool.isNew && (
                      <span className="px-1 py-0.5 rounded text-[9px] font-bold leading-none" style={{ background: '#FEF3C7', color: '#D97706' }}>new</span>
                    )}
                  </div>
                  <p className="text-[10px] mt-0.5 leading-tight" style={{ color: '#9CA3AF' }}>{tool.sub}</p>
                </div>
              </button>
            ))}
          </nav>

          <div className="px-2 pb-3 pt-2 space-y-0.5" style={{ borderTop: '1px solid #F0F0F0' }}>
            <button className="w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-left transition-colors hover:bg-gray-100">
              <HelpCircle size={14} style={{ color: '#9CA3AF' }} />
              <span className="text-xs" style={{ color: '#6B7280' }}>Support</span>
            </button>
            <button className="w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-left transition-colors hover:bg-gray-100">
              <MessageSquare size={14} style={{ color: '#9CA3AF' }} />
              <span className="text-xs" style={{ color: '#6B7280' }}>Contact Us</span>
            </button>
          </div>
        </aside>
      ) : (
        <button onClick={() => setSidebarOpen(true)}
          className="w-7 border-r border-gray-200 flex items-center justify-center hover:bg-gray-50 transition-colors flex-shrink-0">
          <ChevronRight size={13} className="text-gray-400" />
        </button>
      )}

      {/* ── Main content ── */}
      <div className="flex-1 flex flex-col overflow-hidden">
        <AnimatePresence mode="wait">
          <motion.div key={active} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            transition={{ duration: 0.15 }} className="flex-1 flex flex-col overflow-hidden">
            {active === 'agent-analyst' && <AgentAnalystModule />}
            {active !== 'agent-analyst' && <PlaceholderModule tool={activeTool} />}
          </motion.div>
        </AnimatePresence>
      </div>
    </div>
  )
}
