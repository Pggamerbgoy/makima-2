import React, { useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { CheckCircle2, Circle, Loader2, AlertCircle, ChevronDown, ChevronRight, ListChecks } from 'lucide-react';
import type { PlanMilestone } from '../types/chat';

export const MilestoneChecklist: React.FC<{ milestones?: PlanMilestone[]; title?: string }> = ({
  milestones = [],
  title = 'Execution Plan',
}) => {
  const [open, setOpen] = useState(true);
  const prevDoneRef = useRef(false);

  const list = milestones || [];
  const completedCount = list.filter((m) => m.status === 'completed').length;
  const isAllDone = list.length > 0 && completedCount === list.length;
  const progressPct = list.length > 0 ? Math.round((completedCount / list.length) * 100) : 0;

  useEffect(() => {
    const was = prevDoneRef.current;
    prevDoneRef.current = isAllDone;
    if (isAllDone && !was) {
      window.dispatchEvent(new CustomEvent('makima-plan-complete'));
    }
  }, [isAllDone]);

  if (list.length === 0) return null;

  return (
    <div
      className="milestone-checklist"
      style={{
        margin: '10px 0',
        padding: '8px 12px',
        background: 'var(--bg-surface-elevated)',
        border: '1px solid var(--border-subtle)',
        borderRadius: '8px',
        fontSize: '0.85rem',
      }}
    >
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          background: 'none',
          border: 'none',
          color: 'var(--text-secondary, #94a3b8)',
          cursor: 'pointer',
          padding: 0,
          width: '100%',
          textAlign: 'left',
          fontWeight: 600,
        }}
      >
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <ListChecks size={14} color="var(--primary)" />
        <span style={{ color: 'var(--text-primary)' }}>{title}</span>
        <span
          key={String(isAllDone)}
          className={isAllDone ? 'celebrate-pop' : undefined}
          style={{
            marginLeft: 'auto',
            fontSize: '0.75rem',
            padding: '2px 6px',
            borderRadius: '4px',
            background: isAllDone ? 'var(--success-subtle)' : 'var(--primary-subtle)',
            color: isAllDone ? 'var(--success)' : 'var(--primary)',
          }}
        >
          {completedCount}/{list.length}
        </span>
      </button>

      <div
        aria-hidden="true"
        style={{
          marginTop: '8px',
          height: '4px',
          borderRadius: 'var(--radius-full)',
          background: 'var(--bg-surface-active)',
          overflow: 'hidden',
        }}
      >
        <motion.div
          className={isAllDone ? undefined : 'shimmer-bar'}
          initial={{ width: 0 }}
          animate={{ width: `${progressPct}%` }}
          transition={{ type: 'spring', stiffness: 200, damping: 28 }}
          style={{
            height: '100%',
            borderRadius: 'var(--radius-full)',
            background: isAllDone ? 'var(--success)' : undefined,
          }}
        />
      </div>

      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            key="milestone-list"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.22, ease: [0.16, 1, 0.3, 1] }}
            style={{ overflow: 'hidden' }}
          >
            <div style={{ marginTop: '8px', display: 'flex', flexDirection: 'column', gap: '6px' }}>
              {list.map((m, index) => {
                const isCompleted = m.status === 'completed';
                const isInProgress = m.status === 'in_progress';
                const isFailed = m.status === 'failed';

                return (
                  <motion.div
                    key={m.id}
                    initial={{ opacity: 0, x: -8 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ delay: Math.min(index * 0.04, 0.3), duration: 0.25, ease: [0.16, 1, 0.3, 1] }}
                    style={{
                      display: 'flex',
                      alignItems: 'flex-start',
                      gap: '8px',
                      opacity: isCompleted ? 0.75 : 1,
                      color: isCompleted ? 'var(--text-secondary, #94a3b8)' : 'var(--text-primary, #f1f5f9)',
                    }}
                  >
                    <div style={{ marginTop: '2px', flexShrink: 0 }}>
                      {isCompleted ? (
                        <span className="check-pop" style={{ display: 'inline-flex' }}>
                          <CheckCircle2 size={14} color="var(--success)" />
                        </span>
                      ) : isInProgress ? (
                        <Loader2 size={14} color="var(--primary)" className="spin" />
                      ) : isFailed ? (
                        <AlertCircle size={14} color="var(--danger)" />
                      ) : (
                        <Circle size={14} color="var(--text-muted)" />
                      )}
                    </div>
                    <div style={{ flex: 1, textDecoration: isCompleted ? 'line-through' : 'none', transition: 'opacity var(--dur-base)' }}>
                      <span>{m.title}</span>
                      {m.result_summary && (
                        <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '2px' }}>
                          {m.result_summary}
                        </div>
                      )}
                    </div>
                  </motion.div>
                );
              })}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
};
