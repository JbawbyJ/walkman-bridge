// Red Lotus design-system primitives, ported verbatim from the Night Ops
// design bundle (RedLotusDesignSystem: Badge, Button, Input, Select, Switch).
// They style themselves with the CSS tokens in index.css, on purpose — keep
// them inline-styled so the rendered controls stay identical to the design.
import { useState } from 'react'

export function Badge({ children, tone = 'brand' }) {
  const tones = {
    brand: {
      background: 'var(--color-brand-soft)',
      color: 'var(--color-brand-strong)',
      border: '1px solid var(--rl-maroon-300)',
    },
    neutral: {
      background: 'var(--rl-gray-100)',
      color: 'var(--color-text-secondary)',
      border: 'var(--border-hairline)',
    },
    success: {
      background: 'color-mix(in oklch, var(--color-success) 15%, white)',
      color: 'var(--color-success)',
      border: '1px solid var(--color-success)',
    },
    warning: {
      background: 'color-mix(in oklch, var(--color-warning) 20%, white)',
      color: 'var(--color-warning)',
      border: '1px solid var(--color-warning)',
    },
    danger: {
      background: 'color-mix(in oklch, var(--color-danger) 15%, white)',
      color: 'var(--color-danger)',
      border: '1px solid var(--color-danger)',
    },
  }
  return (
    <span
      style={{
        ...tones[tone],
        display: 'inline-flex',
        alignItems: 'center',
        padding: '2px 10px',
        fontFamily: 'var(--font-mono)',
        fontSize: 'var(--text-2xs)',
        fontWeight: 'var(--weight-medium)',
        letterSpacing: 'var(--tracking-wider)',
        textTransform: 'uppercase',
        borderRadius: 'var(--radius-sm)',
        whiteSpace: 'nowrap',
      }}
    >
      {children}
    </span>
  )
}

export function Button({
  children,
  variant = 'primary',
  size = 'md',
  disabled = false,
  onClick,
  type = 'button',
  style: extra,
}) {
  const sizes = {
    sm: { padding: '6px 14px', fontSize: 'var(--text-xs)' },
    md: { padding: '10px 20px', fontSize: 'var(--text-sm)' },
    lg: { padding: '14px 28px', fontSize: 'var(--text-base)' },
  }
  const variants = {
    primary: {
      background: 'var(--color-brand)',
      color: 'var(--color-text-inverse)',
      border: '1px solid var(--color-brand)',
    },
    secondary: {
      background: 'transparent',
      color: 'var(--color-brand)',
      border: 'var(--border-brand)',
    },
    ghost: {
      background: 'transparent',
      color: 'var(--color-text-primary)',
      border: '1px solid transparent',
    },
    danger: {
      background: 'var(--color-danger)',
      color: '#fff',
      border: '1px solid var(--color-danger)',
    },
  }
  const [hover, setHover] = useState(false)
  const [press, setPress] = useState(false)
  const base = variants[variant] || variants.primary
  const style = {
    ...sizes[size],
    ...base,
    fontFamily: 'var(--font-body)',
    fontWeight: 'var(--weight-semibold)',
    letterSpacing: 'var(--tracking-wide)',
    borderRadius: 'var(--radius-sm)',
    cursor: disabled ? 'not-allowed' : 'pointer',
    opacity: disabled ? 0.45 : 1,
    transform: press ? 'scale(var(--scale-press))' : 'scale(1)',
    boxShadow: hover && !disabled ? 'var(--shadow-brand-glow-soft)' : 'none',
    transition:
      'transform var(--duration-fast) var(--ease-standard), box-shadow var(--duration-base) var(--ease-standard)',
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: '8px',
    ...extra,
  }
  return (
    <button
      type={type}
      disabled={disabled}
      onClick={onClick}
      style={style}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => {
        setHover(false)
        setPress(false)
      }}
      onMouseDown={() => setPress(true)}
      onMouseUp={() => setPress(false)}
    >
      {children}
    </button>
  )
}

export function Input({ label, placeholder, type = 'text', value, onChange, disabled = false, error }) {
  const [focus, setFocus] = useState(false)
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: '6px', fontFamily: 'var(--font-body)' }}>
      {label && (
        <span
          style={{
            fontSize: 'var(--text-xs)',
            fontWeight: 'var(--weight-medium)',
            color: 'var(--color-text-secondary)',
            letterSpacing: 'var(--tracking-wide)',
            textTransform: 'uppercase',
          }}
        >
          {label}
        </span>
      )}
      <input
        type={type}
        placeholder={placeholder}
        value={value}
        disabled={disabled}
        onChange={onChange}
        onFocus={() => setFocus(true)}
        onBlur={() => setFocus(false)}
        style={{
          padding: '10px 12px',
          fontSize: 'var(--text-sm)',
          fontFamily: 'var(--font-body)',
          background: disabled ? 'var(--color-bg-sunken)' : 'var(--color-bg-surface)',
          border: `1px solid ${
            error ? 'var(--color-danger)' : focus ? 'var(--color-border-brand)' : 'var(--color-border-default)'
          }`,
          borderRadius: 'var(--radius-sm)',
          outline: 'none',
          boxShadow: focus ? '0 0 0 3px var(--rl-maroon-100)' : 'none',
          transition:
            'border-color var(--duration-fast) var(--ease-standard), box-shadow var(--duration-fast) var(--ease-standard)',
          color: 'var(--color-text-primary)',
        }}
      />
      {error && <span style={{ fontSize: 'var(--text-2xs)', color: 'var(--color-danger)' }}>{error}</span>}
    </label>
  )
}

export function Select({ label, options = [], value, onChange, disabled = false }) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: '6px', fontFamily: 'var(--font-body)' }}>
      {label && (
        <span
          style={{
            fontSize: 'var(--text-xs)',
            fontWeight: 'var(--weight-medium)',
            color: 'var(--color-text-secondary)',
            letterSpacing: 'var(--tracking-wide)',
            textTransform: 'uppercase',
          }}
        >
          {label}
        </span>
      )}
      <select
        value={value}
        disabled={disabled}
        onChange={onChange}
        style={{
          padding: '10px 12px',
          fontSize: 'var(--text-sm)',
          fontFamily: 'var(--font-body)',
          background: disabled ? 'var(--color-bg-sunken)' : 'var(--color-bg-surface)',
          border: '1px solid var(--color-border-default)',
          borderRadius: 'var(--radius-sm)',
          color: 'var(--color-text-primary)',
          outline: 'none',
        }}
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  )
}

export function Switch({ checked, onChange, label, disabled = false }) {
  return (
    <label
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: '10px',
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.5 : 1,
        fontFamily: 'var(--font-body)',
        fontSize: 'var(--text-sm)',
        color: 'var(--color-text-primary)',
      }}
    >
      <span
        onClick={() => !disabled && onChange && onChange(!checked)}
        style={{
          width: 38,
          height: 20,
          borderRadius: 'var(--radius-full)',
          padding: 2,
          boxSizing: 'border-box',
          background: checked ? 'var(--color-brand)' : 'var(--rl-gray-300)',
          display: 'inline-flex',
          justifyContent: checked ? 'flex-end' : 'flex-start',
          transition: 'background var(--duration-base) var(--ease-standard)',
          flex: 'none',
        }}
      >
        <span
          style={{
            width: 16,
            height: 16,
            borderRadius: '50%',
            background: '#fff',
            transition: 'transform var(--duration-base) var(--ease-standard)',
          }}
        />
      </span>
      {label}
    </label>
  )
}
