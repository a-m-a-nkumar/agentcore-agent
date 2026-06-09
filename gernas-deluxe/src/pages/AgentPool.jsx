import { useState, useEffect, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { useNavigate, useLocation } from 'react-router-dom'
import {
  Search, Layers, CheckCircle, AlertCircle, Zap, Bot,
  CreditCard, Printer, FileText, Database, Shield,
  Plus, ChevronRight, Play, Edit3,
  GitMerge, X, Activity, ArrowRight, Terminal,
  Plug, Package, Link2, Mail, Globe, Cpu, Tag,
  Clock, Users, BarChart2, Settings, Code, CheckCheck,
  Wrench, Server, Cloud, Lock
} from 'lucide-react'
import useStore from '../store/useStore'

const segColors = { merchant: '#0EA5E9', print: '#6B7280', b2b: '#8B5CF6', data: '#10B981', platform: '#C8102E', shared: '#F59E0B' }
const segIcons  = { merchant: CreditCard, print: Printer, b2b: FileText, data: Database, platform: Shield, shared: Layers }

/* ══════════════════════════════════════════════════════════════════════════════
   HARDCODED DATA
   ══════════════════════════════════════════════════════════════════════════════ */

/* ── Section 1: Workflows ─────────────────────────────────────────────────── */
const ALL_WORKFLOWS = [
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
      { id: 'a1', name: 'KYB Verification Agent',  role: 'Verifies business identity via D&B and KYB APIs',             tools: ['kyb-api', 'dnb-lookup', 'sanctions-check'], status: 'full' },
      { id: 'a2', name: 'Document Collection Bot', role: 'Requests, parses & validates required merchant documents',     tools: ['doc-parser', 'email-send', 'ocr-extract'],  status: 'full' },
      { id: 'a3', name: 'Risk Scoring Engine',     role: 'Scores merchant risk profile using ML-based risk model',       tools: ['risk-model', 'crm-write', 'fraud-signals'], status: 'full' },
      { id: 'a4', name: 'Approval Notifier',       role: 'Routes to approver & notifies all stakeholders on decision',  tools: ['workflow-api', 'notify-send', 'crm-update'], status: 'full' },
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
      { id: 'b1', name: 'Invoice Ingestion Agent', role: 'Parses & classifies incoming invoices from email and EDI feeds', tools: ['ocr-parser', 'email-inbox', 'edi-reader'], status: 'full' },
      { id: 'b2', name: 'PO Matching Engine',      role: 'Matches invoices to purchase orders using fuzzy matching',      tools: ['erp-read', 'match-algo', 'gl-lookup'],    status: 'full' },
      { id: 'b3', name: 'GL Posting Agent',        role: 'Posts matched invoices to general ledger with audit trail',     tools: ['gl-write', 'audit-log', 'erp-write'],     status: 'full' },
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
      { id: 'c1', name: 'Churn Predictor',       role: 'Calculates real-time churn probability using ML model',       tools: ['ml-predict', 'crm-read', 'segment-api'],   status: 'full' },
      { id: 'c2', name: 'Offer Generator',        role: 'Selects best retention offer based on customer value tier',  tools: ['offer-engine', 'pricing-api', 'ab-test'],  status: 'full' },
      { id: 'c3', name: 'Campaign Dispatcher',    role: 'Sends personalised outreach via email, SMS and push',        tools: ['email-send', 'sms-gateway', 'push-notify'], status: 'full' },
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
      { id: 'd1', name: 'Record Classifier',    role: 'Identifies record type and routes to appropriate enricher',  tools: ['classifier-api', 'schema-detect'],          status: 'full' },
      { id: 'd2', name: 'Data Enricher',        role: 'Appends firmographic, demographic & intent signals',         tools: ['clearbit-api', 'zoominfo-api', 'dnb-lookup'], status: 'full' },
      { id: 'd3', name: 'Dedup Engine',         role: 'Merges duplicate records and maintains golden record',       tools: ['match-algo', 'crm-write', 'audit-log'],     status: 'full' },
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

/* ── Section 2: Individual Agents ────────────────────────────────────────── */
const INDIVIDUAL_AGENTS = [
  /* Merchant Services */
  { id: 'ia-01', name: 'KYB Verification Agent',  segmentKey: 'merchant', segment: 'Merchant Services', status: 'active',  category: 'Risk & Compliance', usedIn: 2, successRate: 99.1, tasksToday: 423,  tools: ['kyb-api', 'dnb-lookup', 'sanctions-check'],      description: 'Verifies business identity and sanctions screening via multiple regulatory APIs.' },
  { id: 'ia-02', name: 'Document Collection Bot', segmentKey: 'merchant', segment: 'Merchant Services', status: 'active',  category: 'Onboarding',         usedIn: 3, successRate: 98.4, tasksToday: 319,  tools: ['doc-parser', 'email-send', 'ocr-extract'],        description: 'Requests, parses and validates merchant documents with OCR and validation rules.' },
  { id: 'ia-03', name: 'Risk Scoring Engine',     segmentKey: 'merchant', segment: 'Merchant Services', status: 'active',  category: 'Risk & Compliance', usedIn: 4, successRate: 97.8, tasksToday: 512,  tools: ['risk-model', 'crm-write', 'fraud-signals'],       description: 'ML-based risk scoring engine integrating fraud signals and CRM history.' },
  { id: 'ia-11', name: 'Approval Notifier',       segmentKey: 'merchant', segment: 'Merchant Services', status: 'active',  category: 'Onboarding',         usedIn: 2, successRate: 98.9, tasksToday: 211,  tools: ['workflow-api', 'notify-send', 'crm-update'],      description: 'Routes decisions to approvers and broadcasts notifications to all stakeholders.' },
  /* B2B Payments */
  { id: 'ia-13', name: 'Invoice Ingestion Agent', segmentKey: 'b2b',      segment: 'B2B Payments',      status: 'active',  category: 'Operations',         usedIn: 1, successRate: 98.1, tasksToday: 289,  tools: ['ocr-parser', 'email-inbox', 'edi-reader'],        description: 'Parses and classifies incoming invoices from email and EDI feeds automatically.' },
  { id: 'ia-04', name: 'PO Matching Engine',      segmentKey: 'b2b',      segment: 'B2B Payments',      status: 'active',  category: 'Operations',         usedIn: 2, successRate: 96.3, tasksToday: 267,  tools: ['erp-read', 'match-algo', 'gl-lookup'],            description: 'Fuzzy-matches invoices to purchase orders across multiple ERP systems.' },
  { id: 'ia-05', name: 'GL Posting Agent',        segmentKey: 'b2b',      segment: 'B2B Payments',      status: 'active',  category: 'Operations',         usedIn: 2, successRate: 99.5, tasksToday: 243,  tools: ['gl-write', 'audit-log', 'erp-write'],             description: 'Posts verified invoices to the general ledger with full audit trail.' },
  { id: 'ia-14', name: 'Exception Handler',       segmentKey: 'b2b',      segment: 'B2B Payments',      status: 'under-review', category: 'Operations',         usedIn: 1, successRate: 82.4, tasksToday: 58,   tools: ['notify-send', 'ticket-create', 'jira-api'],       description: 'Flags unmatched invoices and routes them to the finance team for manual review.' },
  /* Print & Retention */
  { id: 'ia-06', name: 'Churn Predictor',         segmentKey: 'print',    segment: 'Print & Retention', status: 'active',  category: 'Revenue',            usedIn: 1, successRate: 95.2, tasksToday: 178,  tools: ['ml-predict', 'crm-read', 'segment-api'],         description: 'Real-time churn probability scoring using behavioural and usage signals.' },
  { id: 'ia-15', name: 'Offer Generator',         segmentKey: 'print',    segment: 'Print & Retention', status: 'active',  category: 'Revenue',            usedIn: 1, successRate: 96.8, tasksToday: 162,  tools: ['offer-engine', 'pricing-api', 'ab-test'],         description: 'Selects the optimal retention offer based on customer value tier and A/B tests.' },
  { id: 'ia-07', name: 'Campaign Dispatcher',     segmentKey: 'print',    segment: 'Print & Retention', status: 'active',  category: 'Revenue',            usedIn: 1, successRate: 94.7, tasksToday: 154,  tools: ['email-send', 'sms-gateway', 'push-notify'],       description: 'Multi-channel outreach dispatcher for email, SMS and push notifications.' },
  /* Data Solutions */
  { id: 'ia-16', name: 'Record Classifier',       segmentKey: 'data',     segment: 'Data Solutions',    status: 'active',  category: 'Data',               usedIn: 1, successRate: 99.3, tasksToday: 1240, tools: ['classifier-api', 'schema-detect'],                description: 'Identifies record type and routes each record to the appropriate enrichment agent.' },
  { id: 'ia-08', name: 'Data Enricher',           segmentKey: 'data',     segment: 'Data Solutions',    status: 'active',  category: 'Data',               usedIn: 3, successRate: 99.8, tasksToday: 1240, tools: ['clearbit-api', 'zoominfo-api', 'dnb-lookup'],     description: 'Appends firmographic, demographic and intent signals from multiple data providers.' },
  { id: 'ia-09', name: 'Dedup Engine',            segmentKey: 'data',     segment: 'Data Solutions',    status: 'active',  category: 'Data',               usedIn: 2, successRate: 99.2, tasksToday: 981,  tools: ['match-algo', 'crm-write', 'audit-log'],           description: 'Merges duplicate customer records and maintains a single golden record.' },
  { id: 'ia-12', name: 'Data Lake Sync Agent',    segmentKey: 'data',     segment: 'Data Solutions',    status: 'active',  category: 'Data',               usedIn: 1, successRate: 99.9, tasksToday: 1240, tools: ['snowflake-write', 'schema-validate', 'dq-check'], description: 'Pushes enriched records to Snowflake with schema validation and data quality checks.' },
  /* Platform */
  { id: 'ia-10', name: 'Policy Monitor',          segmentKey: 'platform', segment: 'Platform',          status: 'under-review', category: 'Risk & Compliance', usedIn: 1, successRate: 94.1, tasksToday: 87,   tools: ['policy-api', 'workflow-read', 'audit-log'],       description: 'Continuously scans running workflows against governance and compliance rules.' },
  { id: 'ia-17', name: 'Violation Classifier',    segmentKey: 'platform', segment: 'Platform',          status: 'active',  category: 'Risk & Compliance', usedIn: 1, successRate: 97.3, tasksToday: 64,   tools: ['classify-api', 'risk-model'],                     description: 'Categorises detected policy violations by severity, domain and remediation path.' },
  { id: 'ia-18', name: 'Alert Dispatcher',        segmentKey: 'platform', segment: 'Platform',          status: 'under-review', category: 'Risk & Compliance', usedIn: 1, successRate: 89.6, tasksToday: 42,   tools: ['notify-send', 'jira-api', 'email-send'],          description: 'Notifies compliance officers and escalates critical violations via Jira and email.' },
  // ── Backend agents (parity with agents.json) ──
  { id: 'ia-19', name: 'SMB Onboarding Agent',        segmentKey: 'merchant', segment: 'Merchant Services', status: 'active',  category: 'Onboarding',         usedIn: 5,  successRate: 96.2, tasksToday: 47,   tools: ['kyb-verify', 'crm-write', 'email-send', 'terminal-config'],         description: 'Automates the full SMB merchant onboarding lifecycle — from KYB verification to first live transaction.' },
  { id: 'ia-20', name: 'Fraud Detection Agent',       segmentKey: 'merchant', segment: 'Merchant Services', status: 'active',  category: 'Risk & Compliance',  usedIn: 6,  successRate: 99.8, tasksToday: 3201, tools: ['txn-stream', 'anomaly-detect', 'hold-trigger', 'compliance-alert'],  description: 'Sub-second real-time fraud detection with automated account holds and SAR compliance alerts.' },
  { id: 'ia-21', name: 'Churn Prevention Agent',      segmentKey: 'print',    segment: 'Print & Retention', status: 'active',  category: 'Revenue',             usedIn: 3,  successRate: 88.7, tasksToday: 31,   tools: ['crm-read', 'order-history', 'email-send', 'sales-alert'],            description: 'Monitors print customer order patterns to detect churn signals and triggers automated retention campaigns.' },
  { id: 'ia-22', name: 'Invoice Reconciliation Agent',segmentKey: 'b2b',      segment: 'B2B Payments',      status: 'active',  category: 'Operations',          usedIn: 4,  successRate: 94.1, tasksToday: 124,  tools: ['erp-read', 'payment-match', 'gl-write', 'exception-flag'],           description: 'Eliminates manual invoice-to-payment matching across multiple ERP systems with auto GL posting.' },
  { id: 'ia-23', name: 'Data Enrichment Agent',       segmentKey: 'data',     segment: 'Data Solutions',    status: 'active',  category: 'Data',                usedIn: 5,  successRate: 99.1, tasksToday: 892,  tools: ['data-fetch', 'profile-update', 'signal-score', 'warehouse-write'],   description: 'Continuously enriches SMB customer profiles with firmographic and payment behavior signals.' },
  { id: 'ia-24', name: 'Upsell Intelligence Agent',   segmentKey: 'print',    segment: 'Print & Retention', status: 'active',  category: 'Revenue',             usedIn: 1,  successRate: 72.4, tasksToday: 18,   tools: ['crm-read', 'propensity-score', 'proposal-gen', 'sales-route'],       description: 'Scores print customers for payments migration propensity and routes leads to sales teams with proposals.' },
  // ── Net-new agents ──
  { id: 'ia-25', name: 'Terminal Provisioning Agent', segmentKey: 'merchant', segment: 'Merchant Services', status: 'active',  category: 'Onboarding',         usedIn: 2,  successRate: 97.5, tasksToday: 38,   tools: ['terminal-api', 'inventory-check', 'shipping-create', 'crm-write'],   description: 'Automates POS terminal ordering, configuration, and shipping for newly approved merchants.' },
  { id: 'ia-26', name: 'Dispute Resolution Agent',    segmentKey: 'merchant', segment: 'Merchant Services', status: 'active',  category: 'Operations',          usedIn: 2,  successRate: 89.3, tasksToday: 67,   tools: ['dispute-api', 'txn-lookup', 'evidence-collect', 'notify-send'],       description: 'Manages chargeback and dispute workflows by collecting evidence and routing for adjudication.' },
  { id: 'ia-27', name: 'Merchant KYC/AML Agent',      segmentKey: 'merchant', segment: 'Merchant Services', status: 'active',  category: 'Risk & Compliance',  usedIn: 3,  successRate: 98.1, tasksToday: 52,   tools: ['sanctions-check', 'aml-screen', 'kyc-verify', 'alert-write'],        description: 'Continuous KYC/AML screening against OFAC and global sanctions lists for all merchant accounts.' },
  { id: 'ia-28', name: 'Print Order Fulfillment Agent',segmentKey: 'print',   segment: 'Print & Retention', status: 'active',  category: 'Operations',          usedIn: 1,  successRate: 97.8, tasksToday: 214,  tools: ['order-api', 'inventory-check', 'print-queue', 'shipping-create'],    description: 'Orchestrates end-to-end print order fulfilment from receipt through production to dispatch.' },
  { id: 'ia-29', name: 'Reorder Reminder Agent',      segmentKey: 'print',    segment: 'Print & Retention', status: 'active',  category: 'Revenue',             usedIn: 1,  successRate: 91.4, tasksToday: 188,  tools: ['order-history', 'email-send', 'sms-gateway', 'crm-read'],            description: 'Predicts reorder windows and sends personalised reorder nudges to print customers at the right time.' },
  { id: 'ia-30', name: 'Direct Mail Campaign Agent',  segmentKey: 'print',    segment: 'Print & Retention', status: 'active',  category: 'Revenue',             usedIn: 1,  successRate: 83.6, tasksToday: 72,   tools: ['campaign-api', 'segment-api', 'address-verify', 'print-queue'],      description: 'Automates direct mail campaign execution with address verification and segment targeting.' },
  { id: 'ia-31', name: 'ACH Payment Processor',       segmentKey: 'b2b',      segment: 'B2B Payments',      status: 'active',  category: 'Operations',          usedIn: 2,  successRate: 99.4, tasksToday: 341,  tools: ['ach-gateway', 'bank-verify', 'payment-post', 'audit-log'],           description: 'Processes ACH credit and debit transactions with bank account verification and full audit trail.' },
  { id: 'ia-32', name: 'Wire Transfer Verifier',      segmentKey: 'b2b',      segment: 'B2B Payments',      status: 'active',  category: 'Operations',          usedIn: 1,  successRate: 99.7, tasksToday: 89,   tools: ['swift-api', 'beneficiary-check', 'sanctions-check', 'gl-write'],     description: 'Verifies wire transfer beneficiaries against sanctions lists and posts confirmed transfers to GL.' },
  { id: 'ia-33', name: 'Vendor Onboarding Agent',     segmentKey: 'b2b',      segment: 'B2B Payments',      status: 'active',  category: 'Onboarding',         usedIn: 1,  successRate: 95.2, tasksToday: 28,   tools: ['vendor-api', 'kyb-api', 'bank-verify', 'erp-write'],                 description: 'Streamlines vendor onboarding with KYB checks, bank account verification, and ERP setup.' },
  { id: 'ia-34', name: 'Purchase Order Automation',   segmentKey: 'b2b',      segment: 'B2B Payments',      status: 'active',  category: 'Operations',          usedIn: 2,  successRate: 96.8, tasksToday: 156,  tools: ['po-api', 'erp-read', 'approval-route', 'notify-send'],               description: 'Automates PO creation, approval routing, and ERP updates for B2B procurement workflows.' },
  { id: 'ia-35', name: 'Expense Report Processor',    segmentKey: 'b2b',      segment: 'B2B Payments',      status: 'under-review', category: 'Operations',         usedIn: 1,  successRate: 84.1, tasksToday: 203,  tools: ['ocr-extract', 'policy-check', 'gl-write', 'approval-route'],         description: 'Extracts expense data from receipts, validates against policy, and routes for approval.' },
  { id: 'ia-36', name: 'SMB Credit Scoring Agent',    segmentKey: 'data',     segment: 'Data Solutions',    status: 'active',  category: 'Data',                usedIn: 3,  successRate: 97.3, tasksToday: 411,  tools: ['credit-api', 'risk-model', 'bureau-lookup', 'profile-update'],       description: 'Generates real-time SMB credit scores using bureau data and proprietary underwriting risk signals.' },
  { id: 'ia-37', name: 'Lead Scoring Agent',          segmentKey: 'data',     segment: 'Data Solutions',    status: 'under-review',  category: 'Data',                usedIn: 2,  successRate: 93.6, tasksToday: 527,  tools: ['crm-read', 'ml-predict', 'segment-api', 'crm-write'],                description: 'Scores inbound and outbound leads using intent, firmographic, and behavioural signals.' },
  { id: 'ia-38', name: 'Market Segmentation Agent',   segmentKey: 'data',     segment: 'Data Solutions',    status: 'active',  category: 'Data',                usedIn: 2,  successRate: 96.1, tasksToday: 1891, tools: ['warehouse-read', 'segment-api', 'ml-predict', 'crm-write'],          description: 'Re-segments the SMB customer base using ML clustering on continuously enriched profiles.' },
  { id: 'ia-39', name: 'Data Quality Monitor',        segmentKey: 'data',     segment: 'Data Solutions',    status: 'active',  category: 'Data',                usedIn: 1,  successRate: 98.8, tasksToday: 756,  tools: ['dq-check', 'schema-validate', 'alert-write', 'audit-log'],           description: 'Monitors data pipeline quality, flags schema drift, and automatically triggers repair jobs.' },
  { id: 'ia-40', name: 'Compliance Audit Agent',      segmentKey: 'platform', segment: 'Platform',          status: 'active',  category: 'Risk & Compliance',  usedIn: 2,  successRate: 96.9, tasksToday: 144,  tools: ['audit-log', 'policy-api', 'report-gen', 'notify-send'],              description: 'Generates automated compliance audit reports and flags policy deviations for executive review.' },
  { id: 'ia-41', name: 'API Gateway Monitor',         segmentKey: 'platform', segment: 'Platform',          status: 'active',  category: 'Operations',          usedIn: 1,  successRate: 99.5, tasksToday: 8421, tools: ['gateway-api', 'rate-limit', 'alert-write', 'metric-push'],          description: 'Monitors API gateway health, enforces rate limits, and alerts on anomalous traffic patterns.' },
]

/* ── Section 3: Tools / MCP ──────────────────────────────────────────────── */
const TOOLS_MCP = [
  /* Connectors */
  { id: 't-01', name: 'email-send',       category: 'Connector',  type: 'REST API',    status: 'connected', usedBy: 8, provider: 'SendGrid',    icon: Mail,    color: '#0EA5E9', description: 'Sends transactional and campaign emails via SendGrid API.' },
  { id: 't-02', name: 'sms-gateway',      category: 'Connector',  type: 'REST API',    status: 'connected', usedBy: 3, provider: 'Twilio',      icon: Globe,   color: '#7C3AED', description: 'Outbound SMS delivery through Twilio programmable messaging.' },
  { id: 't-03', name: 'notify-send',      category: 'Connector',  type: 'REST API',    status: 'connected', usedBy: 6, provider: 'Internal',    icon: Zap,     color: '#F59E0B', description: 'Internal push notification bus for real-time stakeholder alerts.' },
  { id: 't-04', name: 'jira-api',         category: 'MCP',        type: 'MCP Server',  status: 'connected', usedBy: 4, provider: 'Atlassian',   icon: Wrench,  color: '#0052CC', description: 'Creates, updates and resolves Jira tickets from agent actions.' },
  { id: 't-05', name: 'slack-mcp',        category: 'MCP',        type: 'MCP Server',  status: 'connected', usedBy: 5, provider: 'Slack',       icon: Globe,   color: '#4A154B', description: 'Posts messages and interactive blocks to Slack channels via MCP.' },
  /* Data & Storage */
  { id: 't-06', name: 'erp-read',         category: 'Data',       type: 'REST API',    status: 'connected', usedBy: 4, provider: 'SAP',         icon: Database,color: '#10B981', description: 'Read-only access to ERP master data, GL accounts and cost centres.' },
  { id: 't-07', name: 'erp-write',        category: 'Data',       type: 'REST API',    status: 'connected', usedBy: 3, provider: 'SAP',         icon: Database,color: '#10B981', description: 'Writes transactions and journal entries back to SAP ERP.' },
  { id: 't-08', name: 'gl-write',         category: 'Data',       type: 'REST API',    status: 'connected', usedBy: 2, provider: 'Oracle',      icon: Server,  color: '#EA580C', description: 'Posts GL entries to Oracle Finance with full audit stamping.' },
  { id: 't-09', name: 'crm-write',        category: 'Data',       type: 'REST API',    status: 'connected', usedBy: 7, provider: 'Salesforce',  icon: Cloud,   color: '#00A1E0', description: 'Writes contact, opportunity and account updates to Salesforce CRM.' },
  { id: 't-10', name: 'crm-read',         category: 'Data',       type: 'REST API',    status: 'connected', usedBy: 6, provider: 'Salesforce',  icon: Cloud,   color: '#00A1E0', description: 'Reads customer profiles and activity history from Salesforce.' },
  { id: 't-11', name: 'snowflake-write',  category: 'Data',       type: 'SQL Driver',  status: 'connected', usedBy: 2, provider: 'Snowflake',   icon: Database,color: '#29B5E8', description: 'Bulk-inserts enriched records into Snowflake data warehouse.' },
  /* AI / ML */
  { id: 't-12', name: 'risk-model',       category: 'AI/ML',      type: 'Model API',   status: 'connected', usedBy: 5, provider: 'Internal',    icon: Cpu,     color: '#C8102E', description: 'Real-time risk scoring model trained on Deluxe fraud and underwriting data.' },
  { id: 't-13', name: 'ml-predict',       category: 'AI/ML',      type: 'Model API',   status: 'connected', usedBy: 3, provider: 'Internal',    icon: Cpu,     color: '#C8102E', description: 'General-purpose ML inference endpoint for classification tasks.' },
  { id: 't-14', name: 'match-algo',       category: 'AI/ML',      type: 'Internal',    status: 'connected', usedBy: 3, provider: 'Internal',    icon: CheckCheck, color: '#8B5CF6', description: 'Fuzzy matching algorithm for record linkage and deduplication.' },
  /* Third-party data */
  { id: 't-15', name: 'kyb-api',          category: 'Third-Party',type: 'REST API',    status: 'connected', usedBy: 2, provider: 'Middesk',     icon: Lock,    color: '#059669', description: 'Know-Your-Business verification checks via Middesk API.' },
  { id: 't-16', name: 'dnb-lookup',       category: 'Third-Party',type: 'REST API',    status: 'connected', usedBy: 3, provider: 'D&B',         icon: Globe,   color: '#1D4ED8', description: 'Dun & Bradstreet firmographic data enrichment and credit signals.' },
  { id: 't-17', name: 'clearbit-api',     category: 'Third-Party',type: 'REST API',    status: 'connected', usedBy: 2, provider: 'Clearbit',    icon: Globe,   color: '#6366F1', description: 'Company and contact enrichment from Clearbit Enrichment API.' },
  { id: 't-18', name: 'sanctions-check',  category: 'Third-Party',type: 'REST API',    status: 'connected', usedBy: 2, provider: 'Refinitiv',   icon: Shield,  color: '#DC2626', description: 'OFAC and global sanctions screening via Refinitiv World-Check.' },
  { id: 't-19', name: 'ocr-extract',      category: 'Third-Party',type: 'REST API',    status: 'warning',   usedBy: 3, provider: 'AWS Textract',icon: Code,    color: '#F59E0B', description: 'Document text and data extraction via AWS Textract OCR engine.' },
  { id: 't-20', name: 'audit-log',        category: 'Connector',  type: 'Internal',    status: 'connected', usedBy: 9, provider: 'Internal',    icon: Settings,color: '#64748B', description: 'Immutable audit trail writer for all agent actions and decisions.' },
]

/* ══════════════════════════════════════════════════════════════════════════════
   CANVAS COMPONENTS (shared)
   ══════════════════════════════════════════════════════════════════════════════ */
function CanvasTrigger({ label, color }) {
  return (
    <div className="flex flex-col items-center flex-shrink-0" style={{ width: 120 }}>
      <div className="w-16 h-16 rounded-2xl flex items-center justify-center shadow-sm"
        style={{ background: `${color}12`, border: `2px dashed ${color}55` }}>
        <Zap size={22} style={{ color }} />
      </div>
      <p className="text-[10px] font-bold uppercase tracking-widest mt-1.5 mb-1" style={{ color }}>Trigger</p>
      <p className="text-xs text-center font-semibold text-[#1A2340] leading-tight px-1">{label}</p>
    </div>
  )
}
function CanvasConnector({ color }) {
  return (
    <div className="flex items-center flex-shrink-0 mx-1 mt-[-18px]" style={{ width: 44 }}>
      <div className="flex-1 h-0.5" style={{ background: `${color}45` }} />
      <div style={{ borderLeft: `7px solid ${color}55`, borderTop: '4px solid transparent', borderBottom: '4px solid transparent', width: 0, height: 0 }} />
    </div>
  )
}
function CanvasAgentNode({ agent, index, color, isSelected, onClick }) {
  const statusDot = agent.status === 'full' ? '#10B981' : '#F59E0B'
  return (
    <motion.div onClick={onClick} whileHover={{ scale: 1.06 }} whileTap={{ scale: 0.97 }}
      className="flex flex-col items-center flex-shrink-0 cursor-pointer" style={{ width: 120 }}>
      <div className="relative w-16 h-16 rounded-2xl flex items-center justify-center shadow-md transition-all duration-200"
        style={{ background: isSelected ? color : 'white', border: `2.5px solid ${isSelected ? color : color + '45'}`, boxShadow: isSelected ? `0 8px 24px ${color}30` : '0 2px 10px rgba(0,0,0,0.07)' }}>
        <Bot size={22} style={{ color: isSelected ? 'white' : color }} />
        <div className="absolute -top-1.5 -right-1.5 w-5 h-5 rounded-full border-2 border-white flex items-center justify-center" style={{ background: statusDot }}>
          <span className="text-white font-black" style={{ fontSize: 8 }}>{index + 1}</span>
        </div>
      </div>
      <p className="text-[10px] font-bold uppercase tracking-wider mt-1.5 mb-1" style={{ color: isSelected ? color : '#9BA8BA' }}>Agent {index + 1}</p>
      <p className="text-xs text-center font-semibold leading-tight px-1" style={{ color: isSelected ? '#1A2340' : '#4A5568' }}>
        {agent.name.split(' ').slice(0, 3).join(' ')}
      </p>
    </motion.div>
  )
}
function CanvasOutput({ label }) {
  return (
    <div className="flex flex-col items-center flex-shrink-0" style={{ width: 120 }}>
      <div className="w-16 h-16 rounded-2xl flex items-center justify-center shadow-sm bg-emerald-50" style={{ border: '2px solid #BBF7D0' }}>
        <CheckCircle size={22} className="text-emerald-500" />
      </div>
      <p className="text-[10px] font-bold uppercase tracking-widest mt-1.5 mb-1 text-emerald-500">Output</p>
      <p className="text-xs text-center font-semibold text-[#1A2340] leading-tight px-1">{label}</p>
    </div>
  )
}

/* ══════════════════════════════════════════════════════════════════════════════
   WORKFLOW DETAIL MODAL
   ══════════════════════════════════════════════════════════════════════════════ */
function WorkflowDetailModal({ workflow, onClose, onViewAgent }) {
  const navigate = useNavigate()
  const [selectedAgent, setSelectedAgent] = useState(null)
  const color  = segColors[workflow.segmentKey] || '#C8102E'
  const agents = workflow.agents || []

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ backdropFilter: 'blur(10px)', background: 'rgba(10,18,40,0.72)' }}
      onClick={e => e.target === e.currentTarget && onClose()}>
      <motion.div initial={{ opacity: 0, scale: 0.93, y: 24 }} animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.93, y: 24 }} transition={{ duration: 0.25, ease: 'easeOut' }}
        className="bg-white rounded-3xl shadow-2xl flex flex-col overflow-hidden"
        style={{ width: '92vw', maxWidth: 920, maxHeight: '90vh', boxShadow: '0 40px 100px rgba(0,0,0,0.45)' }}>
        {/* Header */}
        <div className="px-7 py-5 flex items-center justify-between flex-shrink-0"
          style={{ background: 'linear-gradient(135deg, #1A2340 0%, #2D3A5C 100%)' }}>
          <div className="flex items-center gap-3.5">
            <div className="w-11 h-11 rounded-2xl flex items-center justify-center flex-shrink-0"
              style={{ background: `${color}25`, border: `1.5px solid ${color}40` }}>
              <GitMerge size={21} style={{ color }} />
            </div>
            <div>
              <p className="text-white font-bold text-base leading-tight">{workflow.name}</p>
              <div className="flex items-center gap-3 mt-1">
                {workflow.status === 'incomplete' ? (
                  <span className="flex items-center gap-1.5 text-xs text-amber-400 font-semibold">
                    <span className="w-1.5 h-1.5 rounded-full bg-amber-400 inline-block" />Incomplete
                  </span>
                ) : (
                  <span className="flex items-center gap-1.5 text-xs text-emerald-400 font-semibold">
                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse inline-block" />Live
                  </span>
                )}
                <span className="text-white/40 text-xs">{agents.length} agents</span>
                <span className="text-white/40 text-xs">·</span>
                <span className="text-white/40 text-xs">{workflow.segment}</span>
              </div>
            </div>
          </div>
          <button onClick={onClose} className="w-9 h-9 rounded-xl bg-white/10 hover:bg-white/20 flex items-center justify-center transition-all">
            <X size={15} className="text-white" />
          </button>
        </div>

        {/* Stats bar */}
        <div className="grid grid-cols-4 border-b border-[#E2E8F0] flex-shrink-0">
          {[
            { label: 'Tasks Today',  value: workflow.tasksPerDay?.toLocaleString(), color },
            { label: 'SLA',          value: `${workflow.sla}%`,   color: '#10B981' },
            { label: 'Last Run',     value: workflow.lastRun,     color: '#718096' },
            { label: 'Avg Duration', value: workflow.avgRunTime,  color: '#718096' },
          ].map((s, i) => (
            <div key={s.label} className={`px-6 py-3.5 ${i > 0 ? 'border-l border-[#E2E8F0]' : ''}`}>
              <p className="text-xs text-[#718096] mb-0.5">{s.label}</p>
              <p className="text-xl font-bold" style={{ color: s.color }}>{s.value}</p>
            </div>
          ))}
        </div>

        {/* Pipeline canvas */}
        <div className="flex-1 overflow-auto px-7 py-7" style={{ background: 'linear-gradient(135deg,#F7F9FF 0%,#EEF2FF 100%)' }}>
          <p className="text-xs font-bold uppercase tracking-widest text-[#9BA8BA] mb-6 flex items-center gap-1.5">
            <GitMerge size={11} /> Full Pipeline — click an agent node to inspect · jump to agent profile
          </p>
          <div className="flex items-start overflow-x-auto pb-6">
            <CanvasTrigger label={workflow.trigger} color={color} />
            <CanvasConnector color={color} />
            {agents.map((agent, i) => (
              <div key={agent.id} className="flex items-start">
                <CanvasAgentNode agent={agent} index={i} color={color}
                  isSelected={selectedAgent?.id === agent.id}
                  onClick={() => setSelectedAgent(a => a?.id === agent.id ? null : agent)} />
                {i < agents.length - 1 && <CanvasConnector color={color} />}
              </div>
            ))}
            <CanvasConnector color={color} />
            <CanvasOutput label={workflow.output} />
          </div>
          <AnimatePresence>
            {selectedAgent && (
              <motion.div key={selectedAgent.id} initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: 6 }} transition={{ duration: 0.2 }}
                className="mt-2 rounded-2xl border-2 bg-white overflow-hidden" style={{ borderColor: color + '40' }}>
                <div className="h-1" style={{ background: color }} />
                <div className="p-5">
                  <div className="flex items-center gap-3 mb-4">
                    <div className="w-10 h-10 rounded-xl flex items-center justify-center flex-shrink-0" style={{ background: `${color}15` }}>
                      <Bot size={19} style={{ color }} />
                    </div>
                    <div className="flex-1">
                      <p className="font-bold text-sm text-[#1A2340]">{selectedAgent.name}</p>
                      <p className="text-xs text-[#718096] mt-0.5">{selectedAgent.role}</p>
                    </div>
                    <span className="px-2.5 py-1 rounded-full text-xs font-bold"
                      style={{ background: selectedAgent.status === 'full' ? '#D1FAE5' : '#FEF3C7', color: selectedAgent.status === 'full' ? '#065F46' : '#92400E' }}>
                      {selectedAgent.status === 'full' ? '✓ Active' : '⚠ Needs Work'}
                    </span>
                  </div>
                  <div className="flex items-end justify-between gap-4">
                    <div className="flex-1">
                      <p className="text-xs font-semibold text-[#718096] uppercase tracking-wider mb-2">Authorised Tools</p>
                      <div className="flex flex-wrap gap-1.5">
                        {(selectedAgent.tools || []).map(t => (
                          <span key={t} className="px-2.5 py-1 rounded-lg text-xs font-mono bg-[#F0F4FF] border border-[#C7D2FE] text-[#4338CA]">{t}</span>
                        ))}
                      </div>
                    </div>
                    {/* Jump to individual agent card */}
                    {onViewAgent && (
                      <motion.button
                        whileHover={{ x: 3 }}
                        whileTap={{ scale: 0.96 }}
                        onClick={() => onViewAgent(selectedAgent.name)}
                        className="flex-shrink-0 flex items-center gap-1.5 px-4 py-2 rounded-xl text-xs font-bold text-white transition-all hover:opacity-90"
                        style={{ background: `linear-gradient(135deg, ${color} 0%, ${color}CC 100%)`, boxShadow: `0 4px 12px ${color}35` }}
                      >
                        View Agent Profile <ChevronRight size={12} />
                      </motion.button>
                    )}
                  </div>
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        {/* Footer */}
        <div className="px-7 py-4 border-t border-[#E2E8F0] flex items-center gap-3 flex-shrink-0 bg-white">
          <button className="flex items-center gap-2 px-5 py-2.5 rounded-xl text-sm font-bold text-white hover:opacity-90 transition-all" style={{ background: color }}>
            <Play size={13} /> Deploy to Production
          </button>
          <button onClick={() => navigate('/agent-analyst')}
            className="flex items-center gap-2 px-5 py-2.5 rounded-xl text-sm font-semibold border border-[#E2E8F0] text-[#4A5568] hover:bg-[#F7F8FA] transition-all">
            <Edit3 size={13} /> Edit Workflow
          </button>
          <button onClick={onClose} className="ml-auto flex items-center gap-2 px-5 py-2.5 rounded-xl text-sm font-medium text-[#718096] hover:text-[#4A5568] transition-all">
            Close
          </button>
        </div>
      </motion.div>
    </div>
  )
}

/* ══════════════════════════════════════════════════════════════════════════════
   AGENT DETAIL MODAL
   ══════════════════════════════════════════════════════════════════════════════ */
function AgentDetailModal({ agent, onClose }) {
  const color = segColors[agent.segmentKey] || '#718096'
  const Icon  = segIcons[agent.segmentKey] || Bot
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ backdropFilter: 'blur(10px)', background: 'rgba(10,18,40,0.72)' }}
      onClick={e => e.target === e.currentTarget && onClose()}>
      <motion.div initial={{ opacity: 0, scale: 0.95, y: 16 }} animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.95, y: 16 }} transition={{ duration: 0.2 }}
        className="bg-white rounded-3xl shadow-2xl overflow-hidden"
        style={{ width: '92vw', maxWidth: 560, maxHeight: '88vh', boxShadow: '0 40px 80px rgba(0,0,0,0.35)' }}>
        {/* Header */}
        <div className="px-6 py-5 flex items-center justify-between flex-shrink-0"
          style={{ background: 'linear-gradient(135deg, #1A2340 0%, #2D3A5C 100%)' }}>
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-2xl flex items-center justify-center" style={{ background: `${color}25` }}>
              <Bot size={19} style={{ color }} />
            </div>
            <div>
              <p className="text-white font-bold text-sm">{agent.name}</p>
              <p className="text-white/50 text-xs mt-0.5">{agent.segment}</p>
            </div>
          </div>
          <button onClick={onClose} className="w-8 h-8 rounded-xl bg-white/10 hover:bg-white/20 flex items-center justify-center transition-all">
            <X size={14} className="text-white" />
          </button>
        </div>
        {/* Stats bar */}
        <div className="grid grid-cols-3 border-b border-[#E2E8F0]">
          {[
            { label: 'Success Rate', value: `${agent.successRate}%`, c: '#10B981' },
            { label: 'Tasks Today',  value: agent.tasksToday.toLocaleString(), c: color },
            { label: 'Used in',      value: `${agent.usedIn} workflows`, c: '#718096' },
          ].map((s, i) => (
            <div key={s.label} className={`px-5 py-3.5 ${i > 0 ? 'border-l border-[#E2E8F0]' : ''}`}>
              <p className="text-xs text-[#718096] mb-0.5">{s.label}</p>
              <p className="text-lg font-bold" style={{ color: s.c }}>{s.value}</p>
            </div>
          ))}
        </div>
        {/* Body */}
        <div className="p-6 space-y-5 overflow-y-auto" style={{ maxHeight: 'calc(88vh - 200px)' }}>
          <p className="text-sm text-[#4A5568]">{agent.description}</p>
          <div>
            <p className="text-xs font-semibold text-[#718096] uppercase tracking-wider mb-2.5">Category</p>
            <span className="px-3 py-1 rounded-full text-xs font-bold" style={{ background: `${color}15`, color }}>{agent.category}</span>
          </div>
          {(agent.author || true) && (
            <div>
              <p className="text-xs font-semibold text-[#718096] uppercase tracking-wider mb-2.5">Author</p>
              <div className="flex items-center gap-1.5 px-3 py-2 rounded-xl bg-[#F7F8FA] border border-[#E2E8F0]">
                <Tag size={12} className="text-[#9BA8BA]" />
                <span className="text-sm font-semibold text-[#1A2340]">{agent.author || 'DLX_AGENTIC_OS Team'}</span>
              </div>
            </div>
          )}
          <div>
            <p className="text-xs font-semibold text-[#718096] uppercase tracking-wider mb-2.5">Authorised Tools</p>
            <div className="flex flex-wrap gap-1.5">
              {agent.tools.map(t => (
                <span key={t} className="px-2.5 py-1 rounded-lg text-xs font-mono bg-[#F0F4FF] border border-[#C7D2FE] text-[#4338CA]">{t}</span>
              ))}
            </div>
          </div>
          <div>
            <p className="text-xs font-semibold text-[#718096] uppercase tracking-wider mb-2.5">Status</p>
            {agent.status === 'active'
              ? <span className="flex items-center gap-1.5 text-sm text-emerald-600 font-semibold"><span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse inline-block" />Approved</span>
              : <span className="flex items-center gap-1.5 text-sm text-amber-600 font-semibold"><span className="w-2 h-2 rounded-full bg-amber-500 inline-block" />Under Review</span>
            }
          </div>
        </div>
      </motion.div>
    </div>
  )
}

/* ══════════════════════════════════════════════════════════════════════════════
   SECTION CARD COMPONENTS
   ══════════════════════════════════════════════════════════════════════════════ */

/* ── Workflow Card ── */
function WorkflowCard({ wf, isHighlighted, onClick }) {
  const color  = segColors[wf.segmentKey] || '#C8102E'
  const agents = wf.agents || []
  return (
    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} whileHover={{ y: -2 }}
      transition={{ duration: 0.2 }} onClick={onClick} className="cursor-pointer rounded-2xl border-2 overflow-hidden transition-all bg-white"
      style={{ borderColor: isHighlighted ? color : '#E2E8F0', boxShadow: isHighlighted ? `0 0 0 3px ${color}25` : undefined }}>
      <div className="h-1.5" style={{ background: color }} />
      <div className="p-5">
        {/* Title row */}
        <div className="flex items-start justify-between gap-3 mb-3">
          <div className="flex items-center gap-2.5">
            <div className="w-9 h-9 rounded-xl flex items-center justify-center flex-shrink-0" style={{ background: `${color}15` }}>
              <GitMerge size={16} style={{ color }} />
            </div>
            <div>
              <p className="text-sm font-bold text-[#1A2340] leading-tight">{wf.name}</p>
              <p className="text-xs text-[#718096] mt-0.5 leading-relaxed line-clamp-1">{wf.description}</p>
            </div>
          </div>
          {wf.status === 'rejected' ? (
            <span className="flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-bold bg-red-50 text-red-700 border border-red-200 flex-shrink-0">
              <span className="w-1.5 h-1.5 rounded-full bg-red-500 inline-block" />Rejected
            </span>
          ) : wf.status === 'incomplete' ? (
            <span className="flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-bold bg-amber-50 text-amber-700 border border-amber-200 flex-shrink-0">
              <span className="w-1.5 h-1.5 rounded-full bg-amber-500 inline-block" />Under Review
            </span>
          ) : (
            <span className="flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-bold bg-emerald-50 text-emerald-600 border border-emerald-200 flex-shrink-0">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse inline-block" />Live
            </span>
          )}
        </div>
        {/* Mini pipeline */}
        <div className="flex items-center gap-1.5 mb-4 overflow-x-auto pb-1">
          <div className="flex-shrink-0 px-2.5 py-1 rounded-lg text-[10px] font-semibold border"
            style={{ background: `${color}08`, borderColor: `${color}30`, color }}>⚡ Trigger</div>
          {agents.map((agent) => (
            <div key={agent.id} className="flex items-center gap-1.5 flex-shrink-0">
              <ArrowRight size={10} className="text-[#CBD5E0]" />
              <div className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-[10px] font-semibold border"
                style={{ background: agent.status === 'full' ? '#F0FDF4' : '#FFFBEB', borderColor: agent.status === 'full' ? '#BBF7D0' : '#FDE68A', color: agent.status === 'full' ? '#065F46' : '#92400E' }}>
                <Bot size={9} />{agent.name.split(' ')[0]}
              </div>
            </div>
          ))}
          <div className="flex items-center gap-1.5 flex-shrink-0">
            <ArrowRight size={10} className="text-[#CBD5E0]" />
            <div className="px-2.5 py-1 rounded-lg text-[10px] font-semibold border bg-emerald-50 border-emerald-200 text-emerald-700">✓ Output</div>
          </div>
        </div>
        {/* Stats */}
        <div className="flex items-center justify-between pt-3 border-t border-[#F0F2F5]">
          <div className="flex items-center gap-3">
            <span className="flex items-center gap-1 text-xs text-emerald-600 font-semibold"><Activity size={10} /> {wf.tasksPerDay.toLocaleString()} tasks/day</span>
            <span className="text-xs font-semibold text-[#1A2340]">{wf.sla}% SLA</span>
            <span className="text-xs text-[#718096]">{agents.length} agents</span>
          </div>
          <span className="text-xs text-[#C8102E] font-semibold flex items-center gap-1">View pipeline <ChevronRight size={11} /></span>
        </div>
      </div>
    </motion.div>
  )
}

/* ── Individual Agent Card ── */
function AgentCard({ agent, onClick }) {
  const { author = null, isCustom = false } = agent
  const color = segColors[agent.segmentKey] || '#718096'
  const Icon  = segIcons[agent.segmentKey] || Bot
  return (
    <motion.div whileHover={{ y: -2 }} transition={{ duration: 0.15 }}
      className="rounded-2xl border border-[#E2E8F0] bg-white overflow-hidden cursor-pointer hover:border-[#CBD5E0] hover:shadow-md transition-all"
      onClick={onClick}>
      <div className="h-1" style={{ background: color }} />
      <div className="p-4">
        <div className="flex items-start gap-3 mb-3">
          <div className="w-9 h-9 rounded-xl flex items-center justify-center flex-shrink-0" style={{ background: `${color}15` }}>
            <Bot size={16} style={{ color }} />
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-bold text-[#1A2340] leading-tight truncate">{agent.name}</p>
            <div className="flex items-center gap-1.5 mt-0.5 flex-wrap">
              <span className="text-[10px] px-2 py-0.5 rounded-full font-semibold" style={{ background: `${color}15`, color }}>{agent.segment}</span>
              {agent.status === 'active'
                ? <span className="flex items-center gap-1 text-[10px] text-emerald-600 font-semibold"><span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse inline-block" />Approved</span>
                : <span className="flex items-center gap-1 text-[10px] text-amber-600 font-semibold"><span className="w-1.5 h-1.5 rounded-full bg-amber-500 inline-block" />Under Review</span>
              }
            </div>
          </div>
        </div>
        <p className="text-xs text-[#718096] mb-3 line-clamp-2 leading-relaxed">{agent.description}</p>
        {/* Tools */}
        <div className="flex flex-wrap gap-1 mb-3">
          {agent.tools.slice(0, 3).map(t => (
            <span key={t} className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-[#F0F4FF] text-[#4338CA] border border-[#C7D2FE]">{t}</span>
          ))}
          {agent.tools.length > 3 && <span className="px-1.5 py-0.5 rounded text-[10px] text-[#718096]">+{agent.tools.length - 3}</span>}
        </div>
        {/* Author */}
        {(author || isCustom) && (
          <div className="flex items-center gap-1 mb-2">
            <Tag size={9} className="text-[#9BA8BA]" />
            <span className="text-[10px] text-[#9BA8BA]">{author || 'DLX_AGENTIC_OS Team'}</span>
          </div>
        )}
        {/* Stats */}
        <div className="grid grid-cols-2 gap-2 pt-3 border-t border-[#F0F2F5]">
          <div>
            <p className="text-sm font-bold text-emerald-600">{agent.successRate}%</p>
            <p className="text-[10px] text-[#718096]">Success rate</p>
          </div>
          <div>
            <p className="text-sm font-bold text-[#1A2340]">{agent.tasksToday.toLocaleString()}</p>
            <p className="text-[10px] text-[#718096]">Tasks today</p>
          </div>
        </div>
      </div>
    </motion.div>
  )
}

/* ── Tool Detail Modal ── */
function ToolDetailModal({ tool, onClose }) {
  const Icon = tool.icon
  const catColors = {
    Connector:     { bg: '#EFF6FF', border: '#BFDBFE', text: '#1D4ED8' },
    MCP:           { bg: '#F5F3FF', border: '#DDD6FE', text: '#6D28D9' },
    Data:          { bg: '#ECFDF5', border: '#A7F3D0', text: '#065F46' },
    'AI/ML':       { bg: '#FEF2F2', border: '#FECACA', text: '#991B1B' },
    'Third-Party': { bg: '#FFFBEB', border: '#FDE68A', text: '#92400E' },
  }
  const catStyle = catColors[tool.category] || catColors.Connector
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ backdropFilter: 'blur(10px)', background: 'rgba(10,18,40,0.72)' }}
      onClick={e => e.target === e.currentTarget && onClose()}>
      <motion.div initial={{ opacity: 0, scale: 0.95, y: 16 }} animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.95, y: 16 }} transition={{ duration: 0.2 }}
        className="bg-white rounded-3xl shadow-2xl overflow-hidden"
        style={{ width: '92vw', maxWidth: 520, maxHeight: '88vh', boxShadow: '0 40px 80px rgba(0,0,0,0.35)' }}>
        {/* Header */}
        <div className="px-6 py-5 flex items-center justify-between flex-shrink-0"
          style={{ background: 'linear-gradient(135deg, #1A2340 0%, #2D3A5C 100%)' }}>
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-2xl flex items-center justify-center flex-shrink-0" style={{ background: `${tool.color}25` }}>
              <Icon size={19} style={{ color: tool.color }} />
            </div>
            <div>
              <p className="text-white font-bold text-sm font-mono">{tool.name}</p>
              <p className="text-white/50 text-xs mt-0.5">{tool.provider} · {tool.type}</p>
            </div>
          </div>
          <button onClick={onClose} className="w-8 h-8 rounded-xl bg-white/10 hover:bg-white/20 flex items-center justify-center transition-all">
            <X size={14} className="text-white" />
          </button>
        </div>
        {/* Stats bar */}
        <div className="grid grid-cols-3 border-b border-[#E2E8F0]">
          {[
            { label: 'Status',    value: tool.status === 'connected' ? 'Connected' : 'Warning', c: tool.status === 'connected' ? '#10B981' : '#F59E0B' },
            { label: 'Used by',   value: `${tool.usedBy} agents`, c: tool.color },
            { label: 'Category',  value: tool.category, c: '#718096' },
          ].map((s, i) => (
            <div key={s.label} className={`px-5 py-3.5 ${i > 0 ? 'border-l border-[#E2E8F0]' : ''}`}>
              <p className="text-xs text-[#718096] mb-0.5">{s.label}</p>
              <p className="text-sm font-bold" style={{ color: s.c }}>{s.value}</p>
            </div>
          ))}
        </div>
        {/* Body */}
        <div className="p-6 space-y-5 overflow-y-auto" style={{ maxHeight: 'calc(88vh - 220px)' }}>
          <p className="text-sm text-[#4A5568] leading-relaxed">{tool.description}</p>
          <div>
            <p className="text-xs font-semibold text-[#718096] uppercase tracking-wider mb-2.5">Type</p>
            <span className="px-3 py-1 rounded-full text-xs font-bold" style={{ background: `${tool.color}15`, color: tool.color }}>{tool.type}</span>
          </div>
          <div>
            <p className="text-xs font-semibold text-[#718096] uppercase tracking-wider mb-2.5">Category</p>
            <span className="px-3 py-1 rounded-full text-xs font-bold border" style={catStyle}>{tool.category}</span>
          </div>
          <div>
            <p className="text-xs font-semibold text-[#718096] uppercase tracking-wider mb-2.5">Provider</p>
            <span className="text-sm font-semibold text-[#1A2340]">{tool.provider}</span>
          </div>
          <div className="pt-1">
            <div className="flex items-center gap-2 px-4 py-3 rounded-xl" style={{ background: tool.status === 'connected' ? '#F0FDF4' : '#FFFBEB', border: `1px solid ${tool.status === 'connected' ? '#BBF7D0' : '#FDE68A'}` }}>
              <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: tool.status === 'connected' ? '#22C55E' : '#F59E0B' }} />
              <span className="text-xs font-semibold" style={{ color: tool.status === 'connected' ? '#16A34A' : '#D97706' }}>
                {tool.status === 'connected' ? 'Integration active — all agents can use this tool' : 'Warning — check credentials or connection'}
              </span>
            </div>
          </div>
        </div>
      </motion.div>
    </div>
  )
}

/* ── Tool / MCP Card ── */
function ToolCard({ tool, onClick }) {
  const Icon = tool.icon
  const catColors = {
    Connector:     { bg: '#EFF6FF', border: '#BFDBFE', text: '#1D4ED8' },
    MCP:           { bg: '#F5F3FF', border: '#DDD6FE', text: '#6D28D9' },
    Data:          { bg: '#ECFDF5', border: '#A7F3D0', text: '#065F46' },
    'AI/ML':       { bg: '#FEF2F2', border: '#FECACA', text: '#991B1B' },
    'Third-Party': { bg: '#FFFBEB', border: '#FDE68A', text: '#92400E' },
  }
  const catStyle = catColors[tool.category] || catColors.Connector
  return (
    <motion.div whileHover={{ y: -2 }} whileTap={{ scale: 0.97 }} transition={{ duration: 0.15 }}
      onClick={onClick} className="rounded-2xl border border-[#E2E8F0] bg-white overflow-hidden hover:border-[#CBD5E0] hover:shadow-md transition-all cursor-pointer">
      <div className="p-4">
        <div className="flex items-start justify-between gap-2 mb-3">
          <div className="flex items-center gap-2.5">
            <div className="w-9 h-9 rounded-xl flex items-center justify-center flex-shrink-0" style={{ background: `${tool.color}15` }}>
              <Icon size={16} style={{ color: tool.color }} />
            </div>
            <div>
              <p className="text-sm font-bold text-[#1A2340] font-mono">{tool.name}</p>
              <p className="text-[10px] text-[#718096]">{tool.provider} · {tool.type}</p>
            </div>
          </div>
          {tool.status === 'connected'
            ? <span className="flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-50 text-emerald-700 border border-emerald-200 flex-shrink-0">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse inline-block" />Connected
              </span>
            : <span className="flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold bg-amber-50 text-amber-700 border border-amber-200 flex-shrink-0">
                <span className="w-1.5 h-1.5 rounded-full bg-amber-500 inline-block" />Warning
              </span>
          }
        </div>
        <p className="text-xs text-[#718096] mb-3 line-clamp-2 leading-relaxed">{tool.description}</p>
        <div className="flex items-center justify-between">
          <span className="px-2 py-0.5 rounded text-[10px] font-semibold border" style={catStyle}>{tool.category}</span>
          <span className="text-[10px] text-[#9BA8BA] flex items-center gap-1"><Link2 size={9} />{tool.usedBy} agents</span>
        </div>
      </div>
    </motion.div>
  )
}

/* ══════════════════════════════════════════════════════════════════════════════
   MAIN PAGE
   ══════════════════════════════════════════════════════════════════════════════ */
export default function AgentPool() {
  const location    = useLocation()
  const storeBuilt        = useStore(s => s.builtAgents)
  const pendingWorkflows  = useStore(s => s.pendingWorkflows) || []
  const platformApprovals = useStore(s => s.platformApprovals) || {}
  const [tab,          setTab]          = useState('workflows')
  const [statusFilter, setStatusFilter] = useState('all')
  const [wfSearch,  setWfSearch]  = useState('')
  const [agSearch,  setAgSearch]  = useState('')
  const [toolSearch,setToolSearch]= useState('')
  const [toolCat,   setToolCat]   = useState('All')
  const [detailWf,   setDetailWf]   = useState(null)
  const [detailAg,   setDetailAg]   = useState(null)
  const [detailTool, setDetailTool] = useState(null)

  const highlightId    = location.state?.highlightId    || null
  const highlightAgent = location.state?.highlightAgent || null
  const contentRef     = useRef(null)

  /* Merge built agents (newest first) with the static catalog — must be before effects */
  const allAgents = [
    ...storeBuilt.map(a => ({
      id:          a.id,
      name:        a.name,
      segmentKey:  a.segmentKey || 'platform',
      segment:     a.segment    || 'Platform',
      status:      a.status === 'running' ? 'active' : (a.status || 'active'),
      category:    a.category   || 'Custom',
      usedIn:      a.deployments || 0,
      successRate: a.successRate ?? 100,
      tasksToday:  a.tasksToday  || 0,
      tools:       a.tools || a.capabilities || [],
      description: a.description || '',
      isCustom:    true,
    })).reverse(),
    ...INDIVIDUAL_AGENTS
      .filter(a => platformApprovals[a.id] !== 'rejected')
      .map(a => ({
        ...a,
        status: platformApprovals[a.id] === 'approved' ? 'active'
               : a.status,
      })),
  ]

  useEffect(() => {
    if (highlightId) {
      const wf = ALL_WORKFLOWS.find(w => w.id === highlightId)
      if (wf) { setTab('workflows'); setDetailWf(wf) }
    }
  }, [highlightId])

  useEffect(() => {
    if (highlightAgent) {
      const agent = allAgents.find(a => a.name === highlightAgent)
      if (agent) { setTab('agents'); setDetailAg(agent) }
    }
  }, [highlightAgent])

  /* Reset search when tab changes */
  useEffect(() => {
    setWfSearch(''); setAgSearch(''); setToolSearch(''); setToolCat('All')
    contentRef.current?.scrollTo({ top: 0, behavior: 'smooth' })
  }, [tab])

  // User-submitted workflows from Imagination Studio and Dashboard uploads
  const userWorkflows = pendingWorkflows.map(wf => ({
    id:          wf.id,
    name:        wf.name,
    description: wf.summary || '',
    segmentKey:  'platform',
    segment:     'User Submitted',
    status:      wf.status === 'approved' ? 'live' : wf.status === 'rejected' ? 'rejected' : 'incomplete',
    agentCount:  wf.agentCount || (wf.chain || []).length || 1,
    sla:         null,
    tasksPerDay: 0,
    lastRun:     'Not yet deployed',
    avgRunTime:  '—',
    trigger:     'From Imagination Studio',
    output:      wf.status === 'approved' ? 'Approved & Live' : 'Pending Approval',
    agents:      (wf.chain || []).map((s, i) => ({
      id: 'uw-' + i,
      name: s.agent_name || s.name || ('Agent ' + (i + 1)),
      role: s.description || '',
      tools: s.tools || [],
      status: s.status || 'full',
    })),
    isUserSubmitted: true,
  }))
  const allDisplayWorkflows = [
    ...userWorkflows,
    ...ALL_WORKFLOWS
      .filter(wf => platformApprovals[wf.id] !== 'rejected')
      .map(wf => ({
        ...wf,
        status: platformApprovals[wf.id] === 'approved' ? 'live' : wf.status,
      })),
  ]

  /* Filtered data */
  const filteredWf = allDisplayWorkflows.filter(w =>
    !wfSearch || w.name.toLowerCase().includes(wfSearch.toLowerCase()) ||
    w.segment.toLowerCase().includes(wfSearch.toLowerCase())
  )
  const filteredAg = allAgents.filter(a => {
    const matchSearch = !agSearch
      || a.name.toLowerCase().includes(agSearch.toLowerCase())
      || a.segment.toLowerCase().includes(agSearch.toLowerCase())
      || a.category.toLowerCase().includes(agSearch.toLowerCase())
    const matchStatus = statusFilter === 'all' ? true
      : statusFilter === 'under-review' ? a.status === 'under-review'
      : (a.status === 'active' || a.status === 'approved')
    return matchSearch && matchStatus
  })
  const toolCategories = ['All', ...Array.from(new Set(TOOLS_MCP.map(t => t.category)))]
  const filteredTools = TOOLS_MCP.filter(t => {
    const mCat = toolCat === 'All' || t.category === toolCat
    const mSearch = !toolSearch || t.name.toLowerCase().includes(toolSearch.toLowerCase()) ||
      t.provider.toLowerCase().includes(toolSearch.toLowerCase())
    return mCat && mSearch
  })

  /* Pipeline → Agent profile navigation */
  const handleViewAgent = (agentName) => {
    const match = allAgents.find(a => a.name === agentName)
    setDetailWf(null)
    if (match) {
      setTimeout(() => {
        setTab('agents')
        setDetailAg(match)
      }, 180)
    }
  }

  /* Summary stats */
  const liveCount       = allDisplayWorkflows.filter(w => w.status === 'live').length
  const incompleteCount = allDisplayWorkflows.filter(w => w.status === 'incomplete' || w.status === 'rejected').length
  const activeAgents    = allAgents.filter(a => a.status === 'active').length
  const connectedTools  = TOOLS_MCP.filter(t => t.status === 'connected').length

  /* Tab config */
  const TABS = [
    { key: 'workflows', label: 'Workflows',        value: allDisplayWorkflows.length, sub: `${liveCount} live · ${incompleteCount} incomplete`, icon: GitMerge, color: '#C8102E' },
    { key: 'agents',    label: 'Individual Agents', value: allAgents.length,         sub: `${activeAgents} approved`,                          icon: Bot,      color: '#0EA5E9' },
    { key: 'tools',     label: 'Tools & MCPs',      value: TOOLS_MCP.length,         sub: `${connectedTools} connected`,                       icon: Plug,     color: '#8B5CF6' },
  ]
  const activeTab = TABS.find(t => t.key === tab)

  return (
    <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3 }}
      className="flex flex-col" style={{ minHeight: '100%' }}>

      {/* Modals */}
      <AnimatePresence>
        {detailWf && <WorkflowDetailModal workflow={detailWf} onClose={() => setDetailWf(null)} onViewAgent={handleViewAgent} />}
      </AnimatePresence>
      <AnimatePresence>
        {detailAg && <AgentDetailModal agent={detailAg} onClose={() => setDetailAg(null)} />}
      </AnimatePresence>
      <AnimatePresence>
        {detailTool && <ToolDetailModal tool={detailTool} onClose={() => setDetailTool(null)} />}
      </AnimatePresence>

      {/* ── Page Header ── */}
      <div className="mb-6">
        <p className="text-sm text-[#718096]">Explore workflows, individual agents and tool integrations available across Deluxe business segments</p>
      </div>

      {/* ── Tab nav strip — sticky ── */}
      <div
        className="sticky top-0 z-20 -mx-6 px-6 py-3 mb-6"
        style={{
          background: 'rgba(247,248,250,0.97)',
          backdropFilter: 'blur(12px)',
          WebkitBackdropFilter: 'blur(12px)',
          borderBottom: '1px solid #E2E8F0',
          boxShadow: '0 4px 20px rgba(26,35,64,0.06)',
        }}
      >
        <div className="grid grid-cols-3 gap-3">
          {TABS.map((t, i) => {
            const Icon     = t.icon
            const isActive = tab === t.key
            const isTab    = !!t.key
            return (
              <motion.div
                key={i}
                whileHover={isTab ? { y: -1 } : {}}
                whileTap={isTab ? { scale: 0.97 } : {}}
                onClick={isTab ? () => setTab(t.key) : undefined}
                className="rounded-xl px-4 py-3 flex items-center gap-3 transition-all border"
                style={{
                  cursor: isTab ? 'pointer' : 'default',
                  background:   isActive ? t.color : 'white',
                  borderColor:  isActive ? t.color : '#E2E8F0',
                  boxShadow:    isActive ? `0 6px 20px ${t.color}30` : undefined,
                  borderLeft:   !isActive && isTab ? `3px solid ${t.color}` : undefined,
                }}
              >
                <div className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0"
                  style={{ background: isActive ? 'rgba(255,255,255,0.2)' : `${t.color}12` }}>
                  <Icon size={15} style={{ color: isActive ? 'white' : t.color }} />
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-lg font-bold leading-tight" style={{ color: isActive ? 'white' : '#1A2340' }}>{t.value}</p>
                  <p className="text-[10px] truncate" style={{ color: isActive ? 'rgba(255,255,255,0.8)' : '#718096' }}>{t.label}</p>
                  <p className="text-[9px] truncate" style={{ color: isActive ? 'rgba(255,255,255,0.6)' : '#9BA8BA' }}>{t.sub}</p>
                </div>
                {isTab && !isActive && (
                  <ChevronRight size={12} style={{ color: t.color }} className="opacity-50 flex-shrink-0" />
                )}
                {isActive && (
                  <CheckCircle size={14} className="flex-shrink-0 text-white opacity-80" />
                )}
              </motion.div>
            )
          })}
        </div>
      </div>

      {/* ── Tab content — animated page switch ── */}
      <div ref={contentRef} className="flex-1">
        <AnimatePresence mode="wait">

          {/* ── TAB 1: WORKFLOWS ── */}
          {tab === 'workflows' && (
            <motion.div key="workflows"
              initial={{ opacity: 0, x: -16 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 16 }}
              transition={{ duration: 0.2 }}>
              {/* Section header */}
              <div className="flex items-center gap-3 mb-5">
                <div className="flex items-center gap-2.5">
                  <div className="w-7 h-7 rounded-lg flex items-center justify-center" style={{ background: '#FDF0F2' }}>
                    <GitMerge size={14} className="text-[#C8102E]" />
                  </div>
                  <div>
                    <p className="text-base font-bold text-[#1A2340]">Workflows</p>
                    <p className="text-xs text-[#718096]">End-to-end automated pipelines — click to view full pipeline</p>
                  </div>
                </div>
                <div className="flex-1" />
                <div className="relative w-56">
                  <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-[#718096]" />
                  <input value={wfSearch} onChange={e => setWfSearch(e.target.value)}
                    placeholder="Search workflows…"
                    className="w-full pl-8 pr-3 py-2 text-xs border border-[#E2E8F0] rounded-xl bg-white focus:outline-none focus:border-[#C8102E]" />
                </div>
                <div className="flex items-center gap-3">
                  <span className="flex items-center gap-1 text-xs text-emerald-600 font-semibold">
                    <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse inline-block" />{liveCount} Live
                  </span>
                  <span className="flex items-center gap-1 text-xs text-amber-600 font-semibold">
                    <span className="w-2 h-2 rounded-full bg-amber-500 inline-block" />{incompleteCount} Incomplete
                  </span>
                </div>
              </div>
              <div className="grid grid-cols-1 gap-4">
                {filteredWf.map(wf => (
                  <WorkflowCard key={wf.id} wf={wf} isHighlighted={highlightId === wf.id} onClick={() => setDetailWf(wf)} />
                ))}
              </div>
            </motion.div>
          )}

          {/* ── TAB 2: INDIVIDUAL AGENTS ── */}
          {tab === 'agents' && (
            <motion.div key="agents"
              initial={{ opacity: 0, x: -16 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 16 }}
              transition={{ duration: 0.2 }}>
              <div className="flex items-center gap-3 mb-5">
                <div className="flex items-center gap-2.5">
                  <div className="w-7 h-7 rounded-lg flex items-center justify-center" style={{ background: '#EFF6FF' }}>
                    <Bot size={14} style={{ color: '#0EA5E9' }} />
                  </div>
                  <div>
                    <p className="text-base font-bold text-[#1A2340]">Individual Agents</p>
                    <p className="text-xs text-[#718096]">Reusable AI agents deployed across workflows — click to inspect</p>
                  </div>
                </div>
                <div className="flex-1" />
                <div className="relative w-56">
                  <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-[#718096]" />
                  <input value={agSearch} onChange={e => setAgSearch(e.target.value)}
                    placeholder="Search agents…"
                    className="w-full pl-8 pr-3 py-2 text-xs border border-[#E2E8F0] rounded-xl bg-white focus:outline-none focus:border-[#0EA5E9]" />
                </div>
              </div>
              {/* Status filter */}
              <div className="flex items-center gap-2 pt-1 pb-2">
                {[['all','All'],['active','Approved'],['under-review','Under Review']].map(([key, label]) => {
                  const approvedCount = allAgents.filter(a => a.status === 'active' || a.status === 'approved').length
                  const urCount       = allAgents.filter(a => a.status === 'under-review').length
                  const count = key === 'active' ? approvedCount : key === 'under-review' ? urCount : null
                  const isActive = statusFilter === key
                  return (
                    <button key={key} onClick={() => setStatusFilter(key)}
                      className={'flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-semibold transition-all ' + (
                        isActive
                          ? key === 'under-review' ? 'bg-amber-100 text-amber-700 border border-amber-300' : 'bg-[#1A2340] text-white border border-[#1A2340]'
                          : 'text-[#718096] bg-[#F7F8FA] border border-[#E2E8F0] hover:bg-white'
                      )}>
                      {label}
                      {count !== null && (
                        <span className={'px-1.5 py-0.5 rounded-full text-[10px] font-bold ' + (
                          key === 'under-review'
                            ? 'bg-amber-200 text-amber-800'
                            : isActive ? 'bg-white/25 text-white' : 'bg-[#E2E8F0] text-[#718096]'
                        )}>{count}</span>
                      )}
                    </button>
                  )
                })}
              </div>
              <div className="grid grid-cols-3 gap-4">
                {filteredAg.map(agent => (
                  <AgentCard key={agent.id} agent={agent} onClick={() => setDetailAg(agent)} />
                ))}
              </div>
            </motion.div>
          )}

          {/* ── TAB 3: TOOLS / MCP ── */}
          {tab === 'tools' && (
            <motion.div key="tools"
              initial={{ opacity: 0, x: -16 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 16 }}
              transition={{ duration: 0.2 }}>
              <div className="flex items-center gap-3 mb-5 flex-wrap">
                <div className="flex items-center gap-2.5">
                  <div className="w-7 h-7 rounded-lg flex items-center justify-center" style={{ background: '#F5F3FF' }}>
                    <Plug size={14} style={{ color: '#8B5CF6' }} />
                  </div>
                  <div>
                    <p className="text-base font-bold text-[#1A2340]">Tools & MCP Integrations</p>
                    <p className="text-xs text-[#718096]">APIs, connectors and MCP servers authorised for agent use</p>
                  </div>
                </div>
                <div className="flex-1" />
                <div className="relative w-48">
                  <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-[#718096]" />
                  <input value={toolSearch} onChange={e => setToolSearch(e.target.value)}
                    placeholder="Search tools…"
                    className="w-full pl-8 pr-3 py-2 text-xs border border-[#E2E8F0] rounded-xl bg-white focus:outline-none focus:border-[#8B5CF6]" />
                </div>
                <div className="flex gap-1.5 flex-wrap">
                  {toolCategories.map(c => (
                    <button key={c} onClick={() => setToolCat(c)}
                      className="px-3 py-1 rounded-full text-xs font-medium transition-all"
                      style={toolCat === c ? { background: '#8B5CF6', color: 'white' } : { background: 'white', color: '#718096', border: '1px solid #E2E8F0' }}>
                      {c}
                    </button>
                  ))}
                </div>
              </div>
              <div className="grid grid-cols-4 gap-3">
                {filteredTools.map(tool => (
                  <ToolCard key={tool.id} tool={tool} onClick={() => setDetailTool(tool)} />
                ))}
              </div>
              <div className="mt-4 flex items-center gap-4 text-xs text-[#9BA8BA]">
                <span className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse inline-block" />Connected</span>
                <span className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-amber-500 inline-block" />Warning — check credentials</span>
                <span className="ml-auto">{connectedTools}/{TOOLS_MCP.length} integrations active</span>
              </div>
            </motion.div>
          )}

        </AnimatePresence>
      </div>

      <div className="h-4" />
    </motion.div>
  )
}
