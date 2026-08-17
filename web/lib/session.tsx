"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Me } from "@/lib/types";

/** Who is signed in. `null` while loading, `false` once known to be anonymous. */
type SessionState = Me | null | false;

interface SessionValue {
  session: SessionState;
  isAdmin: boolean;
  refresh: () => Promise<void>;
}

const SessionContext = createContext<SessionValue>({
  session: null,
  isAdmin: false,
  refresh: async () => {},
});

/**
 * Fetches the signed-in identity once and shares it with every component.
 *
 * Each consumer calling `api.me()` on mount meant one extra round trip per
 * component per navigation, and the same "am I an admin?" logic re-derived in
 * several places. One provider keeps that knowledge in a single spot; pages ask
 * a hook instead of knowing the endpoint exists.
 */
export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [session, setSession] = useState<SessionState>(null);

  const refresh = useCallback(async () => {
    try {
      setSession(await api.me());
    } catch {
      setSession(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return (
    <SessionContext.Provider
      value={{ session, isAdmin: session ? session.user.role === "admin" : false, refresh }}
    >
      {children}
    </SessionContext.Provider>
  );
}

export function useSession(): SessionValue {
  return useContext(SessionContext);
}
