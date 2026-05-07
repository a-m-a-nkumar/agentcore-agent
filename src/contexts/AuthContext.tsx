import { createContext, useContext, useState, useEffect, useCallback, useRef, ReactNode } from "react";
import msalInstance, { ensureMsalInitialized, loginWithAzureAD, getUserInfo, logout as azureLogout, getAccessToken, isAuthenticated as checkAzureAuth } from "@/services/authService";
import { toast } from "sonner";

// Inactivity timeout — auto-logout after 5 minutes of no activity
const INACTIVITY_TIMEOUT_MS = 300_000;

// ── Environment-aware RBAC ──
// SiriusAI: no RBAC — everyone gets all modules
// Deluxe:   Azure AD group-based access control
const IS_SIRIUSAI = import.meta.env.VITE_THEME === "siriusai";

const BUSINESS_GROUP_OID = "be88c38e-8a45-4026-ac85-f0f850b8cc03";
const TECH_GROUP_OID = "670e52fc-59cc-4a13-b89c-c91367c7060c";

const ALL_MODULES = ["brd", "confluence", "jira", "design", "pair-programming", "testing", "harness"];

// Dev bypass: set VITE_DEV_BYPASS_AUTH=true in .env to skip Azure AD login locally
const DEV_BYPASS_AUTH = import.meta.env.VITE_DEV_BYPASS_AUTH === "true";
const DEV_USER = {
  id: "dev-user",
  email: import.meta.env.VITE_DEV_USER_EMAIL || "dev@local",
  name: import.meta.env.VITE_DEV_USER_NAME || "Dev User",
  groups: [] as string[],
  allowedModules: ALL_MODULES,
};

const GROUP_MODULE_MAP: Record<string, string[]> = {
  [BUSINESS_GROUP_OID]: ["brd", "confluence", "jira"],
  [TECH_GROUP_OID]: ["design", "pair-programming", "testing", "confluence", "jira", "harness"],
};

function computeAllowedModules(groups: string[]): string[] {
  if (IS_SIRIUSAI) return ALL_MODULES;
  const modules = new Set<string>();
  groups.forEach((g) => (GROUP_MODULE_MAP[g] || []).forEach((m) => modules.add(m)));
  return Array.from(modules);
}

interface User {
  id: string;
  email: string;
  name: string;
  groups: string[];
  allowedModules: string[];
}

interface AuthContextType {
  user: User | null;
  isAuthenticated: boolean;
  accessToken: string | null;
  login: () => Promise<void>;
  logout: () => Promise<void>;
  isLoading: boolean;
  hasModuleAccess: (moduleId: string) => boolean;
  isBusinessUser: boolean;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const AuthProvider = ({ children }: { children: ReactNode }) => {
  const [user, setUser] = useState<User | null>(null);
  const [accessToken, setAccessToken] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const buildUser = (userInfo: { id: string; email: string; name: string; groups: string[] }): User => {
    const allowed = computeAllowedModules(userInfo.groups);
    return { ...userInfo, allowedModules: allowed };
  };

  // Initialize auth — handle redirect response when Azure sends user back
  useEffect(() => {
    if (DEV_BYPASS_AUTH) {
      setUser(DEV_USER);
      setIsLoading(false);
      return;
    }
    const initializeAuth = async () => {
      try {
        await ensureMsalInitialized();

        // CRITICAL: handle redirect response when Azure redirects back after login
        const redirectResult = await msalInstance.handleRedirectPromise();
        if (redirectResult) {
          const userInfo = getUserInfo();
          if (userInfo) {
            setUser(buildUser(userInfo));
            setAccessToken(redirectResult.idToken);
          }
          setIsLoading(false);
          return;
        }

        // Already logged in (token in localStorage from previous session)
        if (checkAzureAuth()) {
          const userInfo = getUserInfo();
          if (userInfo) {
            setUser(buildUser(userInfo));
            const token = await getAccessToken();
            setAccessToken(token);
          }
        }
      } catch (error) {
        console.error("Error initializing auth:", error);
      } finally {
        setIsLoading(false);
      }
    };

    initializeAuth();
  }, []);

  const login = async () => {
    if (DEV_BYPASS_AUTH) {
      setUser(DEV_USER);
      return;
    }
    try {
      setIsLoading(true);
      const response = await loginWithAzureAD();
      if (response) {
        const userInfo = getUserInfo();
        if (userInfo) {
          setUser(buildUser(userInfo));
          setAccessToken(response.accessToken);
        }
      }
    } catch (error) {
      console.error("Login error:", error);
      throw error;
    } finally {
      setIsLoading(false);
    }
  };

  const logout = async () => {
    try {
      setIsLoading(true);
      await azureLogout();
      setUser(null);
      setAccessToken(null);
    } catch (error) {
      console.error("Logout error:", error);
      setUser(null);
      setAccessToken(null);
    } finally {
      setIsLoading(false);
    }
  };

  const hasModuleAccess = useCallback(
    (moduleId: string): boolean => {
      if (IS_SIRIUSAI) return true;
      if (!user) return false;
      return user.allowedModules.includes(moduleId);
    },
    [user]
  );

  // ── Inactivity auto-logout ──
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!user) return;

    const resetTimer = () => {
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => {
        // Clear MSAL accounts locally without triggering Azure redirect
        const accounts = msalInstance.getAllAccounts();
        accounts.forEach((a) => msalInstance.setActiveAccount(null));
        localStorage.clear();

        setUser(null);
        setAccessToken(null);
        toast("Your session has ended", {
          description: "You were signed out after being inactive. Please log in again to continue.",
          duration: Infinity,
          icon: "🔒",
        });
      }, INACTIVITY_TIMEOUT_MS);
    };

    const events: (keyof WindowEventMap)[] = ["mousemove", "keydown", "scroll", "mousedown", "touchstart"];
    events.forEach((e) => window.addEventListener(e, resetTimer));
    resetTimer();

    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
      events.forEach((e) => window.removeEventListener(e, resetTimer));
    };
  }, [user]);

  return (
    <AuthContext.Provider
      value={{
        user,
        isAuthenticated: !!user,
        accessToken,
        login,
        logout,
        isLoading,
        hasModuleAccess,
        isBusinessUser: IS_SIRIUSAI ? true : !!user?.groups.includes(BUSINESS_GROUP_OID),
      }}
    >
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
};
