import { useEffect, useRef, useState } from 'react'
import { TrendingUp, TrendingDown } from 'lucide-react'

function useCountUp(target, duration = 1200) {
  const [value, setValue] = useState(0)
  const raf = useRef(null)

  useEffect(() => {
    const start = performance.now()
    const animate = (now) => {
      const progress = Math.min((now - start) / duration, 1)
      const eased = 1 - Math.pow(1 - progress, 3)
      setValue(Math.round(eased * target))
      if (progress < 1) raf.current = requestAnimationFrame(animate)
    }
    raf.current = requestAnimationFrame(animate)
    return () => cancelAnimationFrame(raf.current)
  }, [target, duration])

  return value
}

export default function MetricTile({ label, value, prefix = '', suffix = '', trend, trendLabel, color = 'default', icon: Icon }) {
  const animated = useCountUp(typeof value === 'number' ? value : 0)
  const display = typeof value === 'number' ? animated : value

  const colorMap = {
    red:     { number: 'text-[#C8102E]', bg: 'from-red-50 to-white' },
    green:   { number: 'text-emerald-600', bg: 'from-emerald-50 to-white' },
    blue:    { number: 'text-blue-600', bg: 'from-blue-50 to-white' },
    purple:  { number: 'text-purple-600', bg: 'from-purple-50 to-white' },
    default: { number: 'text-[#1A2340]', bg: 'from-gray-50 to-white' },
  }
  const c = colorMap[color] || colorMap.default

  const trendPositive = trend && !trend.startsWith('-')

  return (
    <div className={`card p-5 bg-gradient-to-br ${c.bg}`}>
      <div className="flex items-start justify-between mb-3">
        <p className="text-xs font-semibold text-[#718096] uppercase tracking-wider">{label}</p>
        {Icon && (
          <div className="w-8 h-8 rounded-lg bg-white shadow-sm flex items-center justify-center">
            <Icon size={16} className="text-[#718096]" />
          </div>
        )}
      </div>
      <p className={`font-display text-3xl font-bold ${c.number} mb-1`}>
        {prefix}{typeof display === 'number' ? display.toLocaleString() : display}{suffix}
      </p>
      {trend && (
        <div className="flex items-center gap-1 mt-2">
          {trendPositive
            ? <TrendingUp size={13} className="text-emerald-500" />
            : <TrendingDown size={13} className="text-red-400" />}
          <span className={`text-xs font-medium ${trendPositive ? 'text-emerald-600' : 'text-red-500'}`}>{trend}</span>
          {trendLabel && <span className="text-xs text-[#718096]">{trendLabel}</span>}
        </div>
      )}
    </div>
  )
}
