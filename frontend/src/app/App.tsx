import { useEffect, useRef, useState } from "react";
import { RouterProvider } from "react-router-dom";
import type { ReactNode } from "react";
import { ToastContainer } from "react-toastify";
import "react-toastify/dist/ReactToastify.css";
import { AuthProvider } from "../app/client-dashboard/contexts/AuthContext";
import {
  OrgConfigProvider,
  useOrg,
} from "../app/client-dashboard/contexts/OrgConfigContext";
import { TenantConfigProvider } from "../app/client-dashboard/contexts/TenantConfigContext";
import { useAuth } from "../app/client-dashboard/contexts/useAuth";
import { router } from "../app/client-dashboard/routes";
import SplashScreen from "./SplashScreen";
import WelcomeScreen from "./WelcomeScreen";

/**
 * Shows WelcomeScreen once, right after a successful login (isAuthenticated
 * false -> true), never on a mount/reload where the session was already
 * authenticated. Must sit inside AuthProvider + OrgConfigProvider for
 * useAuth()/useOrg().
 */
function PostLoginWelcome() {
  const { isAuthenticated } = useAuth();
  const { organizationName } = useOrg();
  const [showWelcome, setShowWelcome] = useState(false);
  const wasAuthRef = useRef<boolean | null>(null);

  useEffect(() => {
    if (wasAuthRef.current === null) {
      wasAuthRef.current = isAuthenticated;
      return;
    }
    if (isAuthenticated && !wasAuthRef.current) {
      setShowWelcome(true);
    }
    wasAuthRef.current = isAuthenticated;
  }, [isAuthenticated]);

  if (!showWelcome) return null;
  return (
    <WelcomeScreen
      orgName={organizationName || "your organization"}
      onFinish={() => setShowWelcome(false)}
    />
  );
}

/**
 * Bridges AuthContext -> TenantConfigProvider.
 *
 * TenantConfigProvider needs the authenticated user's organization_id, but it
 * is mounted inside AuthProvider, so it cannot read auth state via props from
 * App() directly — useAuth() only works below AuthProvider in the tree. This
 * connector is the single place that reads user.organization_id and forwards
 * it, so TenantConfigProvider itself stays decoupled from AuthContext and
 * remains reusable/testable with any orgId source.
 */
function AuthenticatedTenantConfig({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  return (
    <TenantConfigProvider orgId={user?.organization_id ?? undefined}>
      {children}
    </TenantConfigProvider>
  );
}

export default function App() {
  const [showSplash, setShowSplash] = useState(true);
  return (
    <>
      {showSplash && <SplashScreen onFinish={() => setShowSplash(false)} />}
      <AuthProvider>
        <ToastContainer position="top-right" />
        <AuthenticatedTenantConfig>
          <OrgConfigProvider>
            <PostLoginWelcome />
            <RouterProvider router={router} />
          </OrgConfigProvider>
        </AuthenticatedTenantConfig>
      </AuthProvider>
    </>
  );
}
