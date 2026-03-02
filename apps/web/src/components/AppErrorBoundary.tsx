import React, { type ReactNode } from 'react';

import { reportFrontendError } from '@/lib/errorReporting';

type AppErrorBoundaryProps = {
  children: ReactNode;
};

type AppErrorBoundaryState = {
  hasError: boolean;
};

export class AppErrorBoundary extends React.Component<AppErrorBoundaryProps, AppErrorBoundaryState> {
  constructor(props: AppErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError(): AppErrorBoundaryState {
    return { hasError: true };
  }

  componentDidCatch(error: Error): void {
    void reportFrontendError({
      message: error.message || 'React render failure',
      stack: error.stack,
      source: 'react.errorboundary',
      level: 'error',
      url: window.location.href,
      user_agent: window.navigator.userAgent
    });
  }

  render(): ReactNode {
    if (this.state.hasError) {
      return <div>Something went wrong. Please refresh the page.</div>;
    }

    return this.props.children;
  }
}
