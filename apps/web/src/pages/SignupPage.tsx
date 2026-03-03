import { FormEvent, useState } from 'react';
import { Link, Navigate, useNavigate } from 'react-router-dom';

import { getAuthErrorMessage, useAuth } from '@/auth/AuthProvider';
import { Button } from '@/components/ui/Button';

export function SignupPage() {
  const { isAuthenticated, signupWithCredentials } = useAuth();
  const navigate = useNavigate();

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (isAuthenticated) {
    return <Navigate to="/app" replace />;
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setIsSubmitting(true);
    setError(null);

    try {
      await signupWithCredentials({ email, password });
      navigate('/app', { replace: true });
    } catch (submitError) {
      setError(getAuthErrorMessage(submitError));
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <main className="mono-page">
      <a href="#signup-content" className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:bg-foreground focus:px-4 focus:py-3 focus:text-background">
        Skip to signup form
      </a>
      <section id="signup-content" className="mono-shell max-w-4xl">
        <p className="mono-kicker">VoiceVault Enrollment</p>
        <h1 className="mt-3 font-display text-5xl leading-none tracking-[-0.05em] md:text-7xl">REGISTER</h1>
        <div className="mt-6 flex items-center gap-4">
          <div className="h-2 w-24 bg-foreground" />
          <div className="h-4 w-4 border-2 border-foreground" />
        </div>
        <p className="mono-dropcap mt-8 text-lg leading-relaxed">Start capturing and searching your voice notes.</p>
        <form className="mt-6 space-y-4" onSubmit={handleSubmit}>
          <label className="block text-sm font-medium uppercase tracking-[0.1em] text-foreground">
            Email
            <input
              className="mt-2 h-11 w-full px-3 text-sm outline-none"
              type="email"
              autoComplete="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
            />
          </label>
          <label className="block text-sm font-medium uppercase tracking-[0.1em] text-foreground">
            Password
            <input
              className="mt-2 h-11 w-full px-3 text-sm outline-none"
              type="password"
              autoComplete="new-password"
              minLength={8}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
          </label>
          {error ? <p className="border-2 border-foreground px-3 py-2 text-sm italic">{error}</p> : null}
          <Button type="submit" className="w-full" disabled={isSubmitting}>
            {isSubmitting ? 'Creating account...' : 'Create account'}
          </Button>
        </form>
        <p className="mt-5 border-t border-foreground pt-4 text-sm text-muted-foreground">
          Already have an account?{' '}
          <Link className="font-medium text-foreground underline decoration-1 underline-offset-2 hover:no-underline focus-visible:outline focus-visible:outline-3 focus-visible:outline-foreground focus-visible:outline-offset-2" to="/login">
            Log in
          </Link>
          .
        </p>
      </section>
    </main>
  );
}
