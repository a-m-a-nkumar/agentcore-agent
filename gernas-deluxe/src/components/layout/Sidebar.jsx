import { NavLink } from 'react-router-dom'
import {
  LayoutDashboard, GitBranch, CreditCard, Printer,
  FileText, Database, Shield, TrendingUp, Hammer,
  Sparkles, Layers, Share2
} from 'lucide-react'

const navItems = [
  { path: '/dashboard',    label: 'Homepage',            icon: LayoutDashboard, badge: null },
  { divider: true, label: 'DISCOVER' },
  { path: '/studio',       label: 'Imagination Studio', icon: Sparkles,        badge: 'AI',     accent: '#F59E0B' },
  { path: '/agent-pool',   label: 'Discover Hub',           icon: Layers,          badge: null },
  { divider: true, label: 'OPERATE' },
  { path: '/orchestration',label: 'Workflow Orchestration', icon: GitBranch,       badge: '6 active' },
  { divider: true, label: 'BUILD' },
  { path: '/builder',       label: 'Solution Builder',       icon: Hammer,          badge: 'new', accent: '#F59E0B' },
  { divider: true, label: 'SHARED WORKFLOWS' },
  { path: '/common-agents', label: 'Common Workflows',          icon: Share2,   color: '#7C3AED', badge: '4 workflows' },
  { divider: true, label: 'SEGMENTS' },
  { path: '/merchant',     label: 'Merchant Services',   icon: CreditCard,      color: '#0EA5E9' },
  { path: '/print',        label: 'Print & Retention',   icon: Printer,         color: '#6B7280' },
  { path: '/b2b',          label: 'B2B Payments',        icon: FileText,        color: '#8B5CF6' },
  { path: '/data',          label: 'Data Solutions',         icon: Database, color: '#10B981' },
  { divider: true, label: 'PLATFORM' },
  { path: '/governance',   label: 'Governance Registry', icon: Shield,          badge: null },
]

export default function Sidebar() {
  return (
    <aside className="w-60 min-h-screen flex flex-col" style={{ background: '#1A2340' }}>
      {/* Brand block */}
      <div className="px-5 py-6 border-b border-white/10">
        <div className="flex items-center gap-2.5 mb-3">
          <div className="w-8 h-8 rounded-lg flex items-center justify-center" style={{ background: '#C8102E' }}>
            <span className="text-white font-black text-xs tracking-tight leading-none">dlx</span>
          </div>
          <div>
            <p className="text-white font-bold text-sm leading-tight tracking-wide">DLX_AGENTIC_OS</p>
            <p className="text-white/40 text-xs">Agentic Platform</p>
          </div>
        </div>
        <div className="flex items-center justify-between mt-3 px-3 py-2 rounded-lg" style={{ background: 'rgba(255,255,255,0.06)' }}>
          <div>
            <p className="text-white/50 text-xs uppercase tracking-widest mb-0.5">Client</p>
            <p className="text-white text-sm font-semibold">Deluxe Corp</p>
          </div>
          <span className="px-2 py-0.5 rounded-full text-xs font-bold text-white" style={{ background: '#C8102E' }}>DLX</span>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 px-3 py-4 overflow-y-auto">
        {navItems.map((item, i) => {
          if (item.divider) {
            return (
              <p key={i} className="text-white/30 text-xs font-semibold uppercase tracking-widest px-3 pt-5 pb-2">
                {item.label}
              </p>
            )
          }
          const Icon = item.icon
          return (
            <NavLink
              key={item.path}
              to={item.path}
              className={({ isActive }) =>
                `flex items-center gap-3 px-3 py-2.5 rounded-lg mb-0.5 text-sm transition-all duration-150 group
                ${isActive
                  ? 'text-white font-semibold'
                  : 'text-white/60 hover:text-white hover:bg-white/5'
                }`
              }
              style={({ isActive }) => isActive ? {
                background: 'rgba(200,16,46,0.15)',
                borderLeft: '3px solid #C8102E',
                paddingLeft: '9px',
              } : {}}
            >
              <Icon
                size={16}
                style={item.color ? { color: item.color } : {}}
                className={!item.color ? 'group-hover:text-white' : ''}
              />
              <span className="flex-1">{item.label}</span>
              {item.badge && (
                <span
                  className="px-1.5 py-0.5 rounded-full text-xs font-bold"
                  style={item.accent
                    ? { background: `${item.accent}30`, color: item.accent }
                    : { background: 'rgba(255,255,255,0.10)', color: 'rgba(255,255,255,0.70)' }}
                >
                  {item.badge}
                </span>
              )}
            </NavLink>
          )
        })}
      </nav>

      {/* Footer */}
      <div className="px-5 py-4 border-t border-white/10">
        <p className="text-white/30 text-xs">Powered by DLX_AGENTIC_OS</p>
        <p className="text-white/20 text-xs">v2.4.1 — claude-sonnet-4-6</p>
      </div>
    </aside>
  )
}
