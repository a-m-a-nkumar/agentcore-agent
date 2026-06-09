import { Outlet } from 'react-router-dom'
import { useEffect } from 'react'
import Sidebar from './Sidebar'
import TopBar from './TopBar'
import ToastContainer from '../shared/Toast'
import useStore from '../../store/useStore'
import agentsData   from '../../data/agents.json'
import metricsData  from '../../data/metrics.json'
import merchantsData from '../../data/merchants.json'
import tasksData    from '../../data/tasks.json'

export default function Layout() {
  const { setAgents, setMetrics, setMerchants, setTasks } = useStore()

  useEffect(() => {
    setAgents(agentsData)
    setMetrics(metricsData)
    setMerchants(merchantsData)
    setTasks(tasksData)
  }, [])

  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <TopBar />
        <main className="flex-1 overflow-y-auto p-6" style={{ background: '#F7F8FA' }}>
          <Outlet />
        </main>
      </div>
      <ToastContainer />
    </div>
  )
}
