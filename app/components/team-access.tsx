'use client';

import { useEffect, useState, type FormEvent, type ReactNode } from 'react';
import { LockKeyhole, Zap } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

type AccessControls = { onSessionExpired: () => void; onLogout: () => Promise<void> };

export function TeamAccess({ children }: { children: (controls: AccessControls) => ReactNode }) {
  const [state, setState] = useState<'checking' | 'locked' | 'open' | 'unavailable'>('checking');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);

  async function checkSession(signal?: AbortSignal) {
    setState('checking');
    setMessage('');
    try {
      const response = await fetch('/api/dashboard/session', { cache: 'no-store', signal });
      if (!response.ok) throw new Error('Servicio de acceso no disponible.');
      const session = await response.json();
      setState(session.authenticated === true ? 'open' : 'locked');
    } catch (error) {
      if (signal?.aborted) return;
      setState('unavailable');
      setMessage('No podemos comprobar el acceso en este momento. Los datos permanecen protegidos.');
    }
  }

  useEffect(() => {
    const controller = new AbortController();
    void checkSession(controller.signal);
    return () => controller.abort();
  }, []);

  async function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const password = String(new FormData(form).get('password') ?? '');
    form.reset();
    setBusy(true);
    setMessage('');
    try {
      const response = await fetch('/api/dashboard/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ password }), cache: 'no-store' });
      if (response.status === 429) { setMessage('Demasiados intentos. Espera un minuto antes de volver a probar.'); return; }
      if (response.status === 401) { setMessage('La contraseña no es correcta. Vuelve a intentarlo.'); return; }
      if (!response.ok) throw new Error('No disponible');
      // Verificar la cookie HttpOnly antes de mostrar el dashboard.
      await checkSession();
    } catch {
      setMessage('No se pudo iniciar sesión. Inténtalo de nuevo.');
    } finally {
      setBusy(false);
    }
  }

  async function logout() {
    setBusy(true);
    try {
      const response = await fetch('/api/dashboard/logout', { method: 'POST', cache: 'no-store' });
      if (!response.ok) throw new Error('No disponible');
      setState('locked');
      setMessage('Sesión cerrada.');
    } catch {
      setState('unavailable');
      setMessage('No se pudo confirmar el cierre de sesión. Vuelve a comprobar el acceso.');
    } finally {
      setBusy(false);
    }
  }

  if (state === 'open') return children({ onSessionExpired: () => { setState('locked'); setMessage('La sesión ha caducado. Introduce de nuevo la contraseña.'); }, onLogout: logout });

  return (
    <main className="access-shell">
      <section className="access-card" aria-labelledby="access-title" aria-busy={busy || state === 'checking'}>
        <div className="brand-lockup"><div className="brand-mark"><Zap aria-hidden="true" /></div><div><p>TFM · Mercado eléctrico</p><h1>Pulso Energía</h1></div></div>
        <div className="access-icon"><LockKeyhole aria-hidden="true" /></div>
        <p className="section-label">Espacio del equipo</p>
        <h2 id="access-title">La señal empieza aquí.</h2>
        <p className="access-intro">Accede a las previsiones, compara los modelos y consulta el plan BESS.</p>
        {state === 'checking' ? <p role="status">Comprobando acceso…</p> : state === 'unavailable' ? (
          <><p role="alert" className="access-message">{message}</p><Button size="lg" onClick={() => void checkSession()}>Volver a comprobar</Button></>
        ) : (
          <form onSubmit={login}>
            <label htmlFor="team-password">Contraseña del equipo</label>
            <Input id="team-password" name="password" type="password" autoComplete="current-password" maxLength={128} required disabled={busy} aria-describedby="access-message" />
            <p id="access-message" role="status" className="access-message">{message}</p>
            <Button type="submit" size="lg" disabled={busy}>{busy ? 'Entrando…' : 'Entrar al dashboard'}</Button>
          </form>
        )}
        <p className="access-note">Acceso restringido · La sesión caduca a las 8 horas.<br />Solicita la contraseña a la persona que administra el equipo.</p>
      </section>
    </main>
  );
}
