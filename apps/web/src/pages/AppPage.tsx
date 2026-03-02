import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { useAuth } from '@/auth/AuthProvider';
import { Button } from '@/components/ui/Button';

export function AppPage() {
  const { user, logoutCurrentUser } = useAuth();
  const navigate = useNavigate();

  const [isLoggingOut, setIsLoggingOut] = useState(false);

  async function handleLogout() {
    setIsLoggingOut(true);
    try {
      await logoutCurrentUser();
      navigate('/login', { replace: true });
    } finally {
      setIsLoggingOut(false);
    }
  }

  return (
    <main className="min-h-screen bg-gradient-to-br from-sky-50 via-cyan-50 to-emerald-100 p-6">
      <section className="mx-auto mt-12 w-full max-w-2xl rounded-lg border bg-card p-8 text-card-foreground shadow-sm">
        <h1 className="text-2xl font-semibold tracking-tight">VoiceVault App</h1>
        <p className="mt-2 text-sm text-muted-foreground">Authenticated session is active.</p>
        <dl className="mt-6 rounded-md border bg-background p-4 text-sm">
          <div className="flex justify-between gap-3">
            <dt className="text-muted-foreground">Email</dt>
            <dd className="font-medium">{typeof user?.email === 'string' ? user.email : 'Unknown'}</dd>
          </div>
        </dl>
        <div className="mt-6">
          <Button onClick={handleLogout} disabled={isLoggingOut} variant="outline">
            {isLoggingOut ? 'Logging out...' : 'Logout'}
          </Button>
        </div>
      </section>
    </main>
  );
}
