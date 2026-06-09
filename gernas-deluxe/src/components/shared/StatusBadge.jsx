export default function StatusBadge({ status }) {
  const styles = {
    running:   'bg-green-100 text-green-800 border border-green-200',
    idle:      'bg-gray-100 text-gray-600 border border-gray-200',
    error:     'bg-red-100 text-red-800 border border-red-200',
    pending:   'bg-yellow-100 text-yellow-800 border border-yellow-200',
    completed: 'bg-blue-100 text-blue-800 border border-blue-200',
    watching:  'bg-slate-100 text-slate-600 border border-slate-200',
    contacted: 'bg-green-100 text-green-800 border border-green-200',
    escalated: 'bg-orange-100 text-orange-800 border border-orange-200',
    in_progress: 'bg-blue-100 text-blue-800 border border-blue-200',
    alert:     'bg-red-100 text-red-800 border border-red-200',
    success:   'bg-green-100 text-green-800 border border-green-200',
  }
  const labels = {
    running: 'Running', idle: 'Idle', error: 'Error', pending: 'Pending',
    completed: 'Completed', watching: 'Watching', contacted: 'Contacted',
    escalated: 'Escalated', in_progress: 'In Progress', alert: 'Alert', success: 'Success',
  }

  return (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium ${styles[status] || styles.idle}`}>
      {status === 'running' && (
        <span className="agent-pulse inline-block w-1.5 h-1.5 rounded-full bg-green-500" />
      )}
      {labels[status] || status}
    </span>
  )
}
