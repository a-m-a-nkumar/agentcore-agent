import { motion } from 'framer-motion'
import { Download } from 'lucide-react'
import useStore from '../store/useStore'
import SavingsBar from '../components/charts/SavingsBar'

const comparisons = [
  { process: 'Merchant Onboarding',  before: '5–7 days',  after: '2.3 hours',  reduction: '97%' },
  { process: 'Invoice Reconciliation', before: '3 days',  after: '48 minutes', reduction: '99%' },
  { process: 'Churn Detection',       before: '90 days',  after: '12 days',    reduction: '87%' },
  { process: 'Data Enrichment',       before: '2 weeks',  after: '4 hours',    reduction: '96%' },
  { process: 'Fraud Response',        before: '24 hours', after: '< 1 minute', reduction: '99%' },
]

export default function ExecutiveDeck() {
  const { metrics, addToast } = useStore()

  const handleDownload = () => {
    addToast({ type: 'info', title: 'Generating PDF', message: 'Executive summary sent to your email.' })
  }

  if (!metrics) return <div className="skeleton h-32 rounded-xl" />

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3 }}
      className="space-y-8"
    >
      {/* Hero */}
      <div
        className="relative rounded-2xl p-10 overflow-hidden text-center"
        style={{ background: 'linear-gradient(135deg, #1A2340 0%, #2D3A5C 100%)' }}
      >
        <div className="absolute top-0 left-0 right-0 h-1" style={{ background: '#C8102E' }} />
        <p className="text-white/50 text-xs uppercase tracking-widest mb-3">DLX_AGENTIC_OS — Executive Summary</p>
        <p className="font-display text-4xl font-bold text-white">Operational Transformation</p>
        <p className="text-white/60 text-sm mt-3">30-day operational baseline · Deluxe Corporation · DLX_AGENTIC_OS</p>
        <button
          onClick={handleDownload}
          className="mt-6 inline-flex items-center gap-2 px-5 py-2.5 rounded-xl text-white text-sm font-semibold border border-white/20 hover:bg-white/10 transition-all"
        >
          <Download size={14} /> Download Executive Summary
        </button>
      </div>

      {/* Agent coverage chart */}
      <div className="card p-6">
        <h2 className="font-semibold text-[#1A2340] text-sm mb-1">Agent Activity by Business Segment</h2>
        <p className="text-xs text-[#718096] mb-4">Tasks and automation coverage attributed to DLX_AGENTIC_OS agents</p>
        <SavingsBar data={metrics.segments} />
      </div>

      {/* Before / After */}
      <div className="card overflow-hidden">
        <div className="px-6 py-4 border-b border-[#E2E8F0]">
          <h2 className="font-semibold text-[#1A2340] text-sm">Process Transformation — Before vs. After</h2>
        </div>
        <table>
          <thead>
            <tr>
              <th>Process</th>
              <th>Manual (Before)</th>
              <th>DLX_AGENTIC_OS (After)</th>
              <th>Time Reduction</th>
            </tr>
          </thead>
          <tbody>
            {comparisons.map((row, i) => (
              <tr key={i}>
                <td className="font-semibold text-[#1A2340]">{row.process}</td>
                <td>
                  <span className="px-2 py-0.5 rounded-md bg-gray-100 text-gray-500 text-sm line-through">{row.before}</span>
                </td>
                <td>
                  <span className="px-2 py-0.5 rounded-md bg-emerald-50 border border-emerald-200 text-emerald-700 text-sm font-semibold">{row.after}</span>
                </td>
                <td>
                  <div className="flex items-center gap-2">
                    <div className="h-1.5 rounded-full flex-1 bg-[#E2E8F0] max-w-[80px]">
                      <div className="h-full rounded-full bg-emerald-500" style={{ width: row.reduction }} />
                    </div>
                    <span className="text-sm font-bold text-emerald-600">{row.reduction}</span>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

    </motion.div>
  )
}
