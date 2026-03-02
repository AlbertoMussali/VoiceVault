import { Link, Navigate, Route, Routes } from 'react-router-dom';

import { Button } from '@/components/ui/Button';

function Layout({ title }: { title: string }) {
  return (
    <main className="min-h-screen bg-gradient-to-br from-sky-50 via-cyan-50 to-emerald-100 p-6">
      <section className="mx-auto mt-12 w-full max-w-xl rounded-lg border bg-card p-8 text-card-foreground shadow-sm">
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Tailwind, shadcn/ui primitives, router, and TanStack Query are configured.
        </p>
        <nav className="mt-6 flex flex-wrap gap-3">
          <Link to="/login">
            <Button variant="default">Login</Button>
          </Link>
          <Link to="/signup">
            <Button variant="secondary">Sign up</Button>
          </Link>
          <Link to="/app">
            <Button variant="outline">App</Button>
          </Link>
        </nav>
      </section>
    </main>
  );
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Layout title="Login" />} />
      <Route path="/signup" element={<Layout title="Sign up" />} />
      <Route path="/app" element={<Layout title="App" />} />
      <Route path="*" element={<Navigate to="/login" replace />} />
    </Routes>
  );
}
