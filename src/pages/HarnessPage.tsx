import { useState, useEffect } from "react";
import { MainLayout } from "@/components/layout/MainLayout";
import { Settings, LayoutDashboard, GitBranch, Rocket, FileText, FileCode2 } from "lucide-react";
import SettingsSection from "@/components/harness/SettingsSection";
import OverviewSection from "@/components/harness/OverviewSection";
import PipelinesSection from "@/components/harness/PipelinesSection";
import DeploymentsSection from "@/components/harness/DeploymentsSection";
import LogsSection from "@/components/harness/LogsSection";
import { TerraformGeneratorCore } from "@/pages/TerraformGeneratorPage";
import ChatWidget from "@/components/harness/ChatWidget";
import { extractAccountId, type HarnessCredentials } from "@/services/harnessApi";

type Section = "settings" | "overview" | "pipelines" | "deployments" | "logs" | "terraform";

const STORAGE_KEY = "harness_credentials_v1";

const navItems: { id: Section; label: string; icon: React.ElementType }[] = [
  { id: "settings", label: "Settings", icon: Settings },
  { id: "overview", label: "Overview", icon: LayoutDashboard },
  { id: "pipelines", label: "Pipelines", icon: GitBranch },
  { id: "deployments", label: "Deployments", icon: Rocket },
  { id: "logs", label: "Logs", icon: FileText },
  { id: "terraform", label: "Terraform", icon: FileCode2 },
];

const emptyCredentials: HarnessCredentials = {
  apiKey: "",
  accountId: "",
  orgId: "",
  projectId: "",
};

export default function HarnessPage() {
  const [activeSection, setActiveSection] = useState<Section>("settings");
  const [navParams, setNavParams] = useState<Record<string, string>>({});
  const [credentials, setCredentials] = useState<HarnessCredentials>(emptyCredentials);

  useEffect(() => {
    const saved = sessionStorage.getItem(STORAGE_KEY);
    if (saved) {
      try {
        const parsed: HarnessCredentials = JSON.parse(saved);
        if (parsed.apiKey) {
          setCredentials(parsed);
          setActiveSection("overview");
        }
      } catch {
        // ignore malformed storage
      }
    }
  }, []);

  const handleSaveCredentials = (creds: HarnessCredentials) => {
    const withId = { ...creds, accountId: creds.accountId || extractAccountId(creds.apiKey) };
    setCredentials(withId);
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(withId));
    setActiveSection("overview");
  };

  const handleNavigate = (section: string, params?: Record<string, string>) => {
    setNavParams(params || {});
    setActiveSection(section as Section);
  };

  const hasCredentials = !!credentials.apiKey && !!credentials.orgId && !!credentials.projectId;

  return (
    <MainLayout currentView="harness">
      <div className="flex overflow-hidden" style={{ height: "calc(100vh - 64px)" }}>
        {/* Internal left nav */}
        <div className="w-44 border-r border-border bg-muted/10 flex flex-col flex-shrink-0 overflow-y-auto">
          <div className="px-4 py-3 border-b border-border">
            <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Harness
            </h2>
          </div>
          <nav className="flex-1 p-2 space-y-0.5">
            {navItems.map(({ id, label, icon: Icon }) => (
              <button
                key={id}
                onClick={() => handleNavigate(id)}
                className={[
                  "w-full flex items-center gap-2.5 px-3 py-2 rounded-md text-sm transition-colors text-left cursor-pointer",
                  activeSection === id
                    ? "bg-accent text-accent-foreground font-medium"
                    : "text-muted-foreground hover:bg-accent hover:text-accent-foreground",
                ].join(" ")}
              >
                <Icon className="w-4 h-4 flex-shrink-0" />
                {label}
              </button>
            ))}
          </nav>

          {hasCredentials && (
            <div className="p-3 border-t border-border">
              <div className="text-xs text-muted-foreground truncate">
                <div className="font-medium truncate">{credentials.orgId}</div>
                <div className="truncate">{credentials.projectId}</div>
              </div>
            </div>
          )}
        </div>

        {/* Section content */}
        <div className="flex-1 overflow-auto">
          {activeSection === "settings" && (
            <SettingsSection credentials={credentials} onSave={handleSaveCredentials} />
          )}

          {activeSection === "overview" && hasCredentials && (
            <OverviewSection credentials={credentials} onNavigate={handleNavigate} />
          )}

          {activeSection === "pipelines" && hasCredentials && (
            <PipelinesSection credentials={credentials} />
          )}

          {activeSection === "deployments" && hasCredentials && (
            <DeploymentsSection credentials={credentials} onNavigate={handleNavigate} />
          )}

          {activeSection === "logs" && hasCredentials && (
            <LogsSection
              credentials={credentials}
              preloadedExecutionId={navParams.executionId}
            />
          )}

          {activeSection === "terraform" && (
            <TerraformGeneratorCore />
          )}

          {activeSection !== "settings" && !hasCredentials && (
            <div className="flex flex-col items-center justify-center h-64 gap-3">
              <p className="text-sm text-muted-foreground">
                Configure your Harness credentials to get started
              </p>
              <button
                onClick={() => setActiveSection("settings")}
                className="text-sm text-primary underline"
              >
                Go to Settings
              </button>
            </div>
          )}
        </div>
      </div>

      {hasCredentials && <ChatWidget credentials={credentials} />}
    </MainLayout>
  );
}
