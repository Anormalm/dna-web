import { createContext, useContext, type ReactNode } from 'react';
import { useSearchParams } from 'react-router-dom';
export type DnaVersion = 'v1' | 'v2';
const Context = createContext<{ version: DnaVersion; setVersion: (v: DnaVersion) => void } | null>(
    null,
);
export function VersionProvider({ children }: { children: ReactNode }) {
    const [params, setParams] = useSearchParams();
    const version: DnaVersion = params.get('version') === 'v2' ? 'v2' : 'v1';
    const setVersion = (v: DnaVersion) => {
        const next = new URLSearchParams(params);
        if (v === 'v1') next.delete('version');
        else next.set('version', v);
        setParams(next, { replace: true });
    };
    return <Context.Provider value={{ version, setVersion }}>{children}</Context.Provider>;
}
export function useVersion() {
    const c = useContext(Context);
    if (!c) throw new Error('VersionProvider missing');
    return c;
}
