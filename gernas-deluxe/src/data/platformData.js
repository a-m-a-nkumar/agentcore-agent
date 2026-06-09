/* ── Shared platform data — imported by AgentPool and GovernanceRegistry ─── */

export const ALL_WORKFLOWS = [
  {
    id: 'wf-001',
    name: 'SMB Merchant Onboarding Pipeline',
    description: 'Full end-to-end merchant onboarding — KYB verification, document collection, risk scoring and approval.',
    segmentKey: 'merchant', segment: 'Merchant Services',
    status: 'live', sla: 99.2, tasksPerDay: 423,
    lastRun: '2 min ago', avgRunTime: '4.2 min',
    trigger: 'Merchant Application Submitted',
    output: 'Merchant Onboarded — Live in Dashboard',
    agents: [
      { id: 'a1', name: 'KYB Verification Agent',  role: 'Verifies business identity via D&B and KYB APIs',            tools: ['kyb-api', 'dnb-lookup', 'sanctions-check'], status: 'full' },
      { id: 'a2', name: 'Document Collection Bot', role: 'Requests, parses & validates required merchant documents',    tools: ['doc-parser', 'email-send', 'ocr-extract'],  status: 'full' },
      { id: 'a3', name: 'Risk Scoring Engine',     role: 'Scores merchant risk profile using ML-based risk model',      tools: ['risk-model', 'crm-write', 'fraud-signals'], status: 'full' },
      { id: 'a4', name: 'Approval Notifier',       role: 'Routes to approver & notifies all stakeholders on decision', tools: ['workflow-api', 'notify-send', 'crm-update'], status: 'full' },
    ],
  },
  {
    id: 'wf-002',
    name: 'Invoice-to-Cash Reconciliation',
    description: 'Automated invoice ingestion, PO matching, GL posting and exception escalation for B2B payment flows.',
    segmentKey: 'b2b', segment: 'B2B Payments',
    status: 'incomplete', sla: 97.8, tasksPerDay: 289,
    lastRun: '8 min ago', avgRunTime: '2.8 min',
    trigger: 'Invoice Received (Email / EDI)',
    output: 'Payment Cleared — Ledger Updated',
    agents: [
      { id: 'b1', name: 'Invoice Ingestion Agent', role: 'Parses & classifies incoming invoices from email and EDI feeds', tools: ['ocr-parser', 'email-inbox', 'edi-reader'],  status: 'full' },
      { id: 'b2', name: 'PO Matching Engine',      role: 'Matches invoices to purchase orders using fuzzy matching',      tools: ['erp-read', 'match-algo', 'gl-lookup'],      status: 'full' },
      { id: 'b3', name: 'GL Posting Agent',        role: 'Posts matched invoices to general ledger with audit trail',     tools: ['gl-write', 'audit-log', 'erp-write'],       status: 'full' },
      { id: 'b4', name: 'Exception Handler',       role: 'Flags unmatched invoices & routes to finance team for review',  tools: ['notify-send', 'ticket-create', 'jira-api'], status: 'partial' },
    ],
  },
  {
    id: 'wf-003',
    name: 'Churn Prevention Campaign',
    description: 'Identifies at-risk customers, generates personalised retention offers and triggers multi-channel outreach.',
    segmentKey: 'print', segment: 'Print & Retention',
    status: 'live', sla: 96.4, tasksPerDay: 178,
    lastRun: '14 min ago', avgRunTime: '3.1 min',
    trigger: 'Customer Churn Score > 0.7',
    output: 'Retention Offer Sent — CRM Updated',
    agents: [
      { id: 'c1', name: 'Churn Predictor',    role: 'Calculates real-time churn probability using ML model',       tools: ['ml-predict', 'crm-read', 'segment-api'],   status: 'full' },
      { id: 'c2', name: 'Offer Generator',    role: 'Selects best retention offer based on customer value tier',  tools: ['offer-engine', 'pricing-api', 'ab-test'],  status: 'full' },
      { id: 'c3', name: 'Campaign Dispatcher',role: 'Sends personalised outreach via email, SMS and push',        tools: ['email-send', 'sms-gateway', 'push-notify'], status: 'full' },
    ],
  },
  {
    id: 'wf-004',
    name: 'Data Enrichment Pipeline',
    description: 'Enriches raw customer and company data with third-party signals, deduplicates records and syncs to data lake.',
    segmentKey: 'data', segment: 'Data Solutions',
    status: 'live', sla: 99.7, tasksPerDay: 1240,
    lastRun: '1 min ago', avgRunTime: '1.4 min',
    trigger: 'New Record Ingested (CRM / DB)',
    output: 'Enriched Record — Data Lake Synced',
    agents: [
      { id: 'd1', name: 'Record Classifier',    role: 'Identifies record type and routes to appropriate enricher',  tools: ['classifier-api', 'schema-detect'],           status: 'full' },
      { id: 'd2', name: 'Data Enricher',        role: 'Appends firmographic, demographic & intent signals',         tools: ['clearbit-api', 'zoominfo-api', 'dnb-lookup'], status: 'full' },
      { id: 'd3', name: 'Dedup Engine',         role: 'Merges duplicate records and maintains golden record',       tools: ['match-algo', 'crm-write', 'audit-log'],      status: 'full' },
      { id: 'd4', name: 'Data Lake Sync Agent', role: 'Pushes enriched records to Snowflake data lake',             tools: ['snowflake-write', 'schema-validate', 'dq-check'], status: 'full' },
    ],
  },
  {
    id: 'wf-005',
    name: 'Governance & Compliance Monitor',
    description: 'Continuously monitors workflows for policy violations, audits agent actions and raises compliance alerts.',
    segmentKey: 'platform', segment: 'Platform',
    status: 'incomplete', sla: 94.1, tasksPerDay: 87,
    lastRun: '22 min ago', avgRunTime: '5.7 min',
    trigger: 'Scheduled (Every 15 min) + Event',
    output: 'Compliance Report — Alerts Dispatched',
    agents: [
      { id: 'e1', name: 'Policy Monitor',       role: 'Scans running workflows against governance rule set',        tools: ['policy-api', 'workflow-read', 'audit-log'],  status: 'full' },
      { id: 'e2', name: 'Violation Classifier', role: 'Categorises violations by severity and domain',             tools: ['classify-api', 'risk-model'],               status: 'full' },
      { id: 'e3', name: 'Alert Dispatcher',     role: 'Notifies compliance officers and escalates critical issues', tools: ['notify-send', 'jira-api', 'email-send'],    status: 'partial' },
    ],
  },
]

export const INCOMPLETE_WORKFLOWS = ALL_WORKFLOWS.filter(w => w.status === 'incomplete')

export const UNDER_REVIEW_AGENTS = [
  {
    id: 'ia-14', name: 'Exception Handler', segment: 'B2B Payments', segmentKey: 'b2b',
    status: 'under-review', category: 'Operations', description: 'Flags unmatched invoices and routes them to the finance team for manual review.',
    tools: ['notify-send', 'ticket-create', 'jira-api'], successRate: 82.4, tasksToday: 58,
  },
  {
    id: 'ia-10', name: 'Policy Monitor', segment: 'Platform', segmentKey: 'platform',
    status: 'under-review', category: 'Risk & Compliance', description: 'Continuously scans running workflows against governance and compliance rules.',
    tools: ['policy-api', 'workflow-read', 'audit-log'], successRate: 94.1, tasksToday: 87,
  },
  {
    id: 'ia-18', name: 'Alert Dispatcher', segment: 'Platform', segmentKey: 'platform',
    status: 'under-review', category: 'Risk & Compliance', description: 'Notifies compliance officers and escalates critical violations via Jira and email.',
    tools: ['notify-send', 'jira-api', 'email-send'], successRate: 89.6, tasksToday: 42,
  },
  {
    id: 'ia-35', name: 'Expense Report Processor', segment: 'B2B Payments', segmentKey: 'b2b',
    status: 'under-review', category: 'Operations', description: 'Extracts expense data from receipts, validates against policy, and routes for approval.',
    tools: ['ocr-extract', 'policy-check', 'gl-write', 'approval-route'], successRate: 84.1, tasksToday: 203,
  },
  {
    id: 'ia-37', name: 'Lead Scoring Agent', segment: 'Data Solutions', segmentKey: 'data',
    status: 'under-review', category: 'Data', description: 'Scores inbound and outbound leads using intent, firmographic, and behavioural signals.',
    tools: ['crm-read', 'ml-predict', 'segment-api', 'crm-write'], successRate: 93.6, tasksToday: 527,
  },
]
