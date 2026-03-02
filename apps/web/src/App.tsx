import { Link, Navigate, Route, Routes } from 'react-router-dom';

function Layout({ title }: { title: string }) {
  return (
    <main className="page">
      <section className="card">
        <h1>{title}</h1>
        <p>Vite + React skeleton route is active.</p>
        <nav className="nav">
          <Link to="/login">Login</Link>
          <Link to="/signup">Sign up</Link>
          <Link to="/app">App</Link>
        </nav>
      </section>
    </main>
  );
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Layout title="Login" />} />
      <Route path="/signup" element={<Layout title="Sign Up" />} />
      <Route path="/app" element={<Layout title="App" />} />
      <Route path="*" element={<Navigate to="/login" replace />} />
    </Routes>
  );
}
