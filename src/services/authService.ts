import { PublicClientApplication, AccountInfo, AuthenticationResult, RedirectRequest } from "@azure/msal-browser";

// Dev bypass — skip all Azure AD token logic locally
const DEV_BYPASS_AUTH = import.meta.env.VITE_DEV_BYPASS_AUTH === "true";
const DEV_TOKEN = "dev-bypass-token";

// Azure AD Configuration
const AZURE_CLIENT_ID = import.meta.env.VITE_AZURE_CLIENT_ID || "";
const AZURE_TENANT_ID = import.meta.env.VITE_AZURE_TENANT_ID || "";

if (!AZURE_CLIENT_ID || !AZURE_TENANT_ID) {
  console.warn("[AUTH] VITE_AZURE_CLIENT_ID or VITE_AZURE_TENANT_ID not set in environment variables");
}

const msalConfig = {
  auth: {
    clientId: AZURE_CLIENT_ID,
    authority: `https://login.microsoftonline.com/${AZURE_TENANT_ID}`,
    redirectUri: window.location.origin,
    navigateToLoginRequestUrl: true,
  },
  cache: {
    cacheLocation: "localStorage" as const,
    storeAuthStateInCookie: true,
  },
};  

const msalInstance = new PublicClientApplication(msalConfig);

let msalInitialized = false;

export async function ensureMsalInitialized() {
  if (!msalInitialized) {
    await msalInstance.initialize();
    msalInitialized = true;
  }
}

const loginRequest: RedirectRequest = {
  scopes: ["User.Read"],
};

// Global auth failure callback — invoked when tokens cannot be refreshed at all
type AuthFailureCallback = () => void;
let _onAuthFailure: AuthFailureCallback | null = null;

export function setOnAuthFailure(callback: AuthFailureCallback | null) {
  _onAuthFailure = callback;
}

export interface UserInfo {
  id: string;
  email: string;
  name: string;
  groups: string[];
}

/**
 * Login with Azure AD — redirect flow only (no popup, works on all browsers/OS)
 */
export async function loginWithAzureAD(): Promise<AuthenticationResult | null> {
  try {
    await ensureMsalInitialized();
    // handleRedirectPromise() is called once in AuthContext on page load — not here
    await msalInstance.loginRedirect(loginRequest);
    return null; // Page will redirect — control won't return here
  } catch (error) {
    console.error("Azure AD login error:", error);
    throw error;
  }
}

/**
 * Get access token (ID token) for API calls
 */
export async function getEffectiveToken(): Promise<string | null> {
  if (DEV_BYPASS_AUTH) return DEV_TOKEN;
  return getAccessToken();
}

export async function getAccessToken(forceRefresh = false): Promise<string | null> {
  if (DEV_BYPASS_AUTH) return DEV_TOKEN;
  try {
    await ensureMsalInitialized();
    const accounts = msalInstance.getAllAccounts();
    if (accounts.length === 0) {
      return null;
    }

    const account = accounts[0];
    const response = await msalInstance.acquireTokenSilent({
      scopes: ["User.Read"],
      account: account,
      forceRefresh,
    });
    return response.idToken;
  } catch (error) {
    console.error("Error acquiring token:", error);
    try {
      await msalInstance.acquireTokenRedirect({ scopes: ["User.Read"] });
      return null;
    } catch (redirectError) {
      console.error("Error acquiring token via redirect:", redirectError);
      return null;
    }
  }
}

/**
 * Force refresh the token (used after 401 responses)
 */
export async function refreshToken(): Promise<string | null> {
  return getAccessToken(true);
}

/**
 * Get user info from current account
 */
export function getUserInfo(): UserInfo | null {
  try {
    const accounts = msalInstance.getAllAccounts();
    if (accounts.length === 0) {
      return null;
    }

    const account = accounts[0];
    const claims = account.idTokenClaims as Record<string, unknown> | undefined;
    return {
      id: account.homeAccountId || account.localAccountId || "",
      email: account.username || "",
      name: account.name || account.username || "",
      groups: (claims?.groups as string[]) || [],
    };
  } catch (error) {
    console.error("Error getting user info:", error);
    return null;
  }
}

/**
 * Get current account
 */
export function getCurrentAccount(): AccountInfo | null {
  try {
    const accounts = msalInstance.getAllAccounts();
    return accounts.length > 0 ? accounts[0] : null;
  } catch (error) {
    console.error("Error getting current account:", error);
    return null;
  }
}

/**
 * Logout — redirect flow (no popup)
 */
export async function logout(): Promise<void> {
  try {
    await ensureMsalInitialized();
    const accounts = msalInstance.getAllAccounts();
    if (accounts.length > 0) {
      await msalInstance.logoutRedirect({
        account: accounts[0],
      });
    }
  } catch (error) {
    console.error("Error during logout:", error);
  }
}

/**
 * Check if user is authenticated
 */
export function isAuthenticated(): boolean {
  try {
    const accounts = msalInstance.getAllAccounts();
    return accounts.length > 0;
  } catch (error) {
    console.error("Error checking authentication:", error);
    return false;
  }
}

export default msalInstance;
