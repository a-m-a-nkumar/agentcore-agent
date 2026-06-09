import { Bell, ChevronRight } from 'lucide-react'
import { useLocation } from 'react-router-dom'

const breadcrumbs = {
  '/dashboard':    ['DLX_AGENTIC_OS', 'Homepage'],
  '/orchestration':['DLX_AGENTIC_OS', 'Workflow Orchestration'],
  '/merchant':     ['DLX_AGENTIC_OS', 'Merchant Services', 'Onboarding Workflow'],
  '/print':        ['DLX_AGENTIC_OS', 'Print', 'Churn Prevention'],
  '/b2b':          ['DLX_AGENTIC_OS', 'B2B Payments', 'Reconciliation'],
  '/data':          ['DLX_AGENTIC_OS', 'Data Solutions', 'Enrichment'],
  '/common-agents': ['DLX_AGENTIC_OS', 'Shared Workflows', 'Common Workflows'],
  '/governance':   ['DLX_AGENTIC_OS', 'Platform', 'Governance Registry'],
  '/executive':    ['DLX_AGENTIC_OS', 'Platform', 'Executive Deck'],
  '/builder':      ['DLX_AGENTIC_OS', 'Build', 'Solution Builder'],
  '/studio':       ['DLX_AGENTIC_OS', 'Discover', 'Imagination Studio'],
  '/agent-pool':   ['DLX_AGENTIC_OS', 'Discover', 'Discover Hub'],
}

export default function TopBar() {
  const location = useLocation()
  const crumbs = breadcrumbs[location.pathname] || ['DLX_AGENTIC_OS']

  return (
    <header
      className="h-14 flex items-center justify-between px-6 bg-white"
      style={{ borderBottom: '1px solid #E2E8F0', borderLeft: '4px solid #C8102E' }}
    >
      {/* Breadcrumb */}
      <div className="flex items-center gap-1.5">
        {crumbs.map((crumb, i) => (
          <span key={i} className="flex items-center gap-1.5">
            {i > 0 && <ChevronRight size={12} className="text-[#CBD5E0]" />}
            <span className={`text-sm ${i === crumbs.length - 1 ? 'text-[#1A2340] font-semibold' : 'text-[#718096]'}`}>
              {crumb}
            </span>
          </span>
        ))}
      </div>

      {/* Right side */}
      <div className="flex items-center gap-2">
        <button className="w-8 h-8 rounded-lg bg-[#F7F8FA] flex items-center justify-center hover:bg-[#EDF0F5] relative">
          <Bell size={15} className="text-[#4A5568]" />
          <span className="absolute top-1 right-1 w-2 h-2 rounded-full bg-[#C8102E]" />
        </button>
      </div>
    </header>
  )
}
