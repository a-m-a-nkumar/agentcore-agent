import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import Layout              from './components/layout/Layout'
import Dashboard           from './pages/Dashboard'
import AgentOrchestration  from './pages/AgentOrchestration'
import MerchantOnboarding  from './pages/MerchantOnboarding'
import PrintRetention      from './pages/PrintRetention'
import B2BReconciliation   from './pages/B2BReconciliation'
import DataEnrichment      from './pages/DataEnrichment'
import GovernanceRegistry  from './pages/GovernanceRegistry'
import ExecutiveDeck       from './pages/ExecutiveDeck'
import AgentBuilder        from './pages/AgentBuilder'
import AgentAnalyst        from './pages/AgentAnalyst'
import ImagineStudio       from './pages/ImagineStudio'
import AgentPool           from './pages/AgentPool'
import CommonAgents        from './pages/CommonAgents'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<Navigate to="/dashboard" replace />} />
          <Route path="dashboard"      element={<Dashboard />} />
          <Route path="orchestration"  element={<AgentOrchestration />} />
          <Route path="merchant"       element={<MerchantOnboarding />} />
          <Route path="print"          element={<PrintRetention />} />
          <Route path="b2b"            element={<B2BReconciliation />} />
          <Route path="data"           element={<DataEnrichment />} />
          <Route path="common-agents"  element={<CommonAgents />} />
          <Route path="governance"     element={<GovernanceRegistry />} />
          <Route path="executive"      element={<ExecutiveDeck />} />
          <Route path="builder"        element={<AgentBuilder />} />
          <Route path="studio"         element={<ImagineStudio />} />
          <Route path="agent-pool"     element={<AgentPool />} />
          <Route path="agent-analyst"  element={<AgentAnalyst />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
