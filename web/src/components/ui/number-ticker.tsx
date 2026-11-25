// Adapted from Magic UI's NumberTicker (https://magicui.design, MIT): restyled for this page
// and made to count again whenever the value changes, not only the first time it scrolls into view.
import { useMotionValue, useReducedMotion, useSpring } from 'motion/react';
import { useEffect, useRef, type ComponentPropsWithoutRef } from 'react';
import { cn } from '@/lib/utils';

interface NumberTickerProps extends ComponentPropsWithoutRef<'span'> { value: number }

export function NumberTicker({ value, className, ...props }: NumberTickerProps) {
  const ref = useRef<HTMLSpanElement>(null);
  const still = useReducedMotion();
  const motionValue = useMotionValue(0);
  const spring = useSpring(motionValue, { damping: 34, stiffness: 190 });

  useEffect(() => {
    if (still) { if (ref.current) ref.current.textContent = String(value); return; }
    motionValue.jump(0);
    motionValue.set(value);
  }, [motionValue, value, still]);

  useEffect(() => spring.on('change', (latest) => {
    if (ref.current) ref.current.textContent = String(Math.round(latest));
  }), [spring]);

  return <span ref={ref} className={cn('inline-block tabular-nums', className)} aria-label={String(value)} {...props}>0</span>;
}
