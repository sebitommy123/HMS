/**
 * "AI can see what you see" — client side.
 *
 * The chat panel and the main app view live in the same browser. This provider
 * assembles a live description of what the user is looking at (route + entity +
 * bounded on-screen "observations") and publishes it (debounced) to the AI
 * service, keyed by the CURRENTLY-OPEN conversation. The agent pulls it on
 * demand via get_current_view / read_observation.
 *
 * Multi-user note: publishing is keyed by conversation id, so each browser only
 * ever writes to its own open chat. There is no shared/global "current view".
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useLocation } from "react-router-dom";

import { putViewContext, type Observation, type ViewContext } from "@/api/viewContext";

const PUBLISH_DEBOUNCE_MS = 350;

interface Identity {
  title?: string;
  entity?: ViewContext["entity"];
}

interface ViewCtxValue {
  setActiveConversation: (id: string | null) => void;
  setIdentity: (id: Identity | null) => void;
  setObservation: (key: string, obs: Observation | null) => void;
}

const NOOP: ViewCtxValue = {
  setActiveConversation: () => {},
  setIdentity: () => {},
  setObservation: () => {},
};

const Ctx = createContext<ViewCtxValue>(NOOP);

export function ViewContextProvider({ children }: { children: ReactNode }) {
  const location = useLocation();
  const [identity, setIdentityState] = useState<Identity | null>(null);
  const [activeConversation, setActiveConversationState] = useState<string | null>(null);
  const observations = useRef(new Map<string, Observation>());
  const [obsVersion, setObsVersion] = useState(0);

  const setObservation = useCallback((key: string, obs: Observation | null) => {
    if (obs === null) {
      if (observations.current.delete(key)) setObsVersion((v) => v + 1);
    } else {
      observations.current.set(key, obs);
      setObsVersion((v) => v + 1);
    }
  }, []);

  // Publish (debounced) whenever the route, identity, observations, or the
  // active conversation change. No active conversation → nothing to publish to.
  useEffect(() => {
    if (!activeConversation) return;
    const view: ViewContext = {
      route: location.pathname + location.search,
      title: identity?.title ?? null,
      entity: identity?.entity ?? null,
      observations: Object.fromEntries(
        [...observations.current.entries()].map(([key, o]) => [
          key,
          {
            description: o.description,
            kind: o.kind,
            data: o.data,
            updated_at: Date.now() / 1000,
          },
        ]),
      ),
    };
    const t = setTimeout(() => void putViewContext(activeConversation, view), PUBLISH_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [location.pathname, location.search, identity, obsVersion, activeConversation]);

  const value = useMemo<ViewCtxValue>(
    () => ({
      setActiveConversation: setActiveConversationState,
      setIdentity: setIdentityState,
      setObservation,
    }),
    [setObservation],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

function useViewCtx(): ViewCtxValue {
  return useContext(Ctx);
}

/**
 * Called by the chat panel with whichever conversation is currently open. That
 * conversation becomes the publish target; passing null (panel closed / on the
 * full-screen chat routes) pauses publishing.
 */
export function useActiveConversation(conversationId: string | null): void {
  const { setActiveConversation } = useViewCtx();
  useEffect(() => {
    setActiveConversation(conversationId);
    return () => setActiveConversation(null);
  }, [conversationId, setActiveConversation]);
}

/** A page declares WHAT it's showing (a human title + a machine entity). */
export function useViewIdentity(
  title: string | undefined,
  entity?: ViewContext["entity"],
): void {
  const { setIdentity } = useViewCtx();
  const entityKey = entity ? JSON.stringify(entity) : "";
  useEffect(() => {
    setIdentity({ title, entity: entity ?? null });
    return () => setIdentity(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [title, entityKey, setIdentity]);
}

/**
 * A page registers an on-screen data blob the agent can pull. Pass
 * data=undefined when there's nothing yet (e.g. a preview that hasn't run); it
 * registers once data arrives and clears on unmount. Keep payloads bounded — a
 * screenful, not a firehose (the server caps them regardless).
 */
export function useObservation(
  key: string,
  meta: { description: string; kind: Observation["kind"] },
  data: unknown,
): void {
  const { setObservation } = useViewCtx();
  const dataKey = data === undefined ? "" : safeStringify(data);
  useEffect(() => {
    if (data === undefined) {
      setObservation(key, null);
      return;
    }
    setObservation(key, { description: meta.description, kind: meta.kind, data });
    return () => setObservation(key, null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, meta.description, meta.kind, dataKey, setObservation]);
}

function safeStringify(v: unknown): string {
  try {
    return JSON.stringify(v) ?? "";
  } catch {
    return String(v);
  }
}
