import { useEffect } from 'react'
import { CheckCircle, Info, AlertTriangle, X } from 'lucide-react'
import useStore from '../../store/useStore'

function ToastItem({ toast }) {
  const removeToast = useStore((s) => s.removeToast)

  useEffect(() => {
    const timer = setTimeout(() => removeToast(toast.id), 3000)
    return () => clearTimeout(timer)
  }, [toast.id, removeToast])

  const styles = {
    success: { bg: 'bg-emerald-50 border-emerald-200', icon: <CheckCircle size={16} className="text-emerald-600" /> },
    info:    { bg: 'bg-blue-50 border-blue-200',       icon: <Info size={16} className="text-blue-600" /> },
    warning: { bg: 'bg-amber-50 border-amber-200',     icon: <AlertTriangle size={16} className="text-amber-600" /> },
  }
  const s = styles[toast.type] || styles.info

  return (
    <div className={`slide-in flex items-start gap-3 px-4 py-3 rounded-xl border shadow-lg ${s.bg} min-w-[300px]`}>
      {s.icon}
      <div className="flex-1">
        {toast.title && <p className="text-sm font-semibold text-[#1A2340]">{toast.title}</p>}
        <p className="text-sm text-[#4A5568]">{toast.message}</p>
      </div>
      <button onClick={() => removeToast(toast.id)} className="text-[#718096] hover:text-[#1A2340]">
        <X size={14} />
      </button>
    </div>
  )
}

export default function ToastContainer() {
  const toasts = useStore((s) => s.toasts)
  return (
    <div className="fixed bottom-6 right-6 z-50 flex flex-col gap-2">
      {toasts.map((t) => <ToastItem key={t.id} toast={t} />)}
    </div>
  )
}
