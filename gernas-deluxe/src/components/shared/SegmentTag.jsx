const segments = {
  merchant: { label: 'Merchant Svc',  color: '#0EA5E9', bg: '#E0F2FE' },
  print:    { label: 'Print',          color: '#6B7280', bg: '#F3F4F6' },
  b2b:      { label: 'B2B Payments',   color: '#8B5CF6', bg: '#EDE9FE' },
  data:     { label: 'Data Solutions', color: '#10B981', bg: '#D1FAE5' },
}

export default function SegmentTag({ segment }) {
  const s = segments[segment] || { label: segment, color: '#718096', bg: '#F3F4F6' }
  return (
    <span
      className="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium"
      style={{ color: s.color, background: s.bg }}
    >
      {s.label}
    </span>
  )
}
