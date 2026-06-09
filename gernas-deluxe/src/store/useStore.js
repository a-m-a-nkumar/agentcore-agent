import { create } from 'zustand'

function computeAdvanced(m) {
  const nextStep = Math.min(m.currentStep + 1, 6)
  const isCompleted = nextStep >= 6
  return {
    ...m,
    currentStep: nextStep,
    status: isCompleted ? 'completed' : 'in_progress',
    steps: m.steps.map((step, i) => {
      if (i + 1 < nextStep)  return { ...step, status: 'completed' }
      if (i + 1 === nextStep) return { ...step, status: isCompleted ? 'completed' : 'running', completedAt: isCompleted || step.status === 'running' ? new Date().toLocaleTimeString() : null }
      if (i + 1 === nextStep + 1 && !isCompleted) return { ...step, status: 'running' }
      return step
    })
  }
}

const useStore = create((set) => ({
  agents: [],
  metrics: null,
  merchants: [],
  tasks: [],
  selectedMerchant: null,
  toasts: [],
  builtAgents: [],
  confluencePages: [],   // pages sent from Imagination Studio → Agent Analyst
  deployedAgents: [],    // agents submitted via Agent Analyst phase journey

  // Imagination Studio session — persists during SPA navigation, clears on refresh
  novaSession: null,
  setNovaSession: (s) => set({ novaSession: s }),
  clearNovaSession: () => set({ novaSession: null }),

  // Workflow approvals — submitted from Imagination Studio "Send for Approval"
  pendingWorkflows: [],
  addPendingWorkflow: (wf) => set((s) => ({
    pendingWorkflows: [
      ...s.pendingWorkflows,
      { ...wf, id: wf.id || Date.now(), status: 'pending', submittedAt: new Date().toISOString() },
    ],
  })),
  updateWorkflowStatus: (id, status) => set((s) => ({
    pendingWorkflows: s.pendingWorkflows.map(w => w.id === id ? { ...w, status } : w),
  })),

  setAgents:    (agents)    => set({ agents }),
  setMetrics:   (metrics)   => set({ metrics }),
  setMerchants: (merchants) => set({ merchants }),
  setTasks:     (tasks)     => set({ tasks }),

  addBuiltAgent: (agent) => set((s) => ({
    builtAgents: [...s.builtAgents, agent],
  })),

  approveAgent: (agentId) => set((s) => {
    const updatedAgents = s.builtAgents.map(a =>
      a.id === agentId ? { ...a, status: 'active', approvedAt: new Date().toISOString() } : a
    )
    // Names of all active built agents after this approval
    const activeNames = new Set(updatedAgents.filter(a => a.status === 'active').map(a => a.name))
    // Auto-approve pending workflows whose build-needed steps are all now approved
    const updatedWorkflows = (s.pendingWorkflows || []).map(wf => {
      if (wf.status !== 'pending') return wf
      const chain = wf.chain || []
      const buildNeeded = chain.filter(step => step.status && step.status !== 'full')
      const allApproved = buildNeeded.length === 0 ||
        buildNeeded.every(step => activeNames.has(step.agent_name || step.title || ''))
      return allApproved ? { ...wf, status: 'approved', autoApprovedAt: new Date().toISOString() } : wf
    })
    return { builtAgents: updatedAgents, pendingWorkflows: updatedWorkflows }
  }),

  rejectAgent: (agentId) => set((s) => ({
    builtAgents: s.builtAgents.map(a =>
      a.id === agentId ? { ...a, status: 'rejected', rejectedAt: new Date().toISOString() } : a
    ),
  })),

  rejectWorkflow: (wfId) => set((s) => ({
    pendingWorkflows: s.pendingWorkflows.map(w =>
      w.id === wfId ? { ...w, status: 'rejected', rejectedAt: new Date().toISOString() } : w
    ),
  })),

  // Decisions on hardcoded platform agents/workflows (not in builtAgents/pendingWorkflows)
  platformApprovals: {},
  setPlatformApproval: (id, decision) => set((s) => ({
    platformApprovals: { ...s.platformApprovals, [id]: decision },
  })),

  autoApproveGreen: () => set((s) => ({
    builtAgents: s.builtAgents.map(a =>
      (a.status === 'under-review' && a.riskReport?.level === 'green')
        ? { ...a, status: 'active', approvedAt: new Date().toISOString() }
        : a
    ),
  })),

  addConfluencePage: (page) => set((s) => ({
    confluencePages: [page, ...s.confluencePages],
  })),

  addDeployedAgent: (agent) => set((s) => ({
    deployedAgents: [...s.deployedAgents, { ...agent, id: agent.id || Date.now() }],
  })),
  updateDeployedAgentStatus: (id, status) => set((s) => ({
    deployedAgents: s.deployedAgents.map(a => a.id === id ? { ...a, status } : a),
  })),

  setSelectedMerchant: (m) => set({ selectedMerchant: m }),

  addToast: (toast) => set((s) => ({
    toasts: [...s.toasts, { ...toast, id: Date.now() }]
  })),
  removeToast: (id) => set((s) => ({
    toasts: s.toasts.filter((t) => t.id !== id)
  })),

  advanceMerchantStep: (merchantId) => set((s) => {
    const updated = s.merchants.map((m) =>
      m.id === merchantId ? computeAdvanced(m) : m
    )
    const updatedMerchant = updated.find((m) => m.id === merchantId)
    return {
      merchants: updated,
      selectedMerchant: s.selectedMerchant?.id === merchantId ? updatedMerchant : s.selectedMerchant,
    }
  }),
}))

export default useStore
