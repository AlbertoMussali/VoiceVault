import * as React from 'react';
import { cva, type VariantProps } from 'class-variance-authority';

import { cn } from '@/lib/utils';

const buttonVariants = cva(
  'inline-flex min-h-11 items-center justify-center whitespace-nowrap border-2 border-foreground text-sm uppercase tracking-[0.1em] transition-none duration-0 focus-visible:outline focus-visible:outline-3 focus-visible:outline-foreground focus-visible:outline-offset-3 disabled:pointer-events-none disabled:opacity-50',
  {
    variants: {
      variant: {
        default: 'bg-primary text-primary-foreground hover:bg-background hover:text-foreground',
        secondary: 'bg-secondary text-secondary-foreground hover:bg-foreground hover:text-background',
        outline: 'bg-background text-foreground hover:bg-foreground hover:text-background'
      },
      size: {
        default: 'px-6 py-3',
        sm: 'min-h-10 px-4 py-2 text-xs',
        lg: 'min-h-12 px-8 py-4'
      }
    },
    defaultVariants: {
      variant: 'default',
      size: 'default'
    }
  }
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {}

export function Button({ className, variant, size, ...props }: ButtonProps) {
  return <button className={cn(buttonVariants({ variant, size, className }))} {...props} />;
}
